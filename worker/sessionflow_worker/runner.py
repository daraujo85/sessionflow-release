"""Runner / daemon do Worker (orquestração das tasks de fundo).

Este módulo amarra todos os componentes do Worker num único processo headless:

- **Discovery** (`run_forever`): reconcilia o estado das sessões tmux do host
  com o MongoDB e emite eventos de transição.
- **CommandConsumer** (`run`): consome ``sessionflow.commands`` (create / kill /
  rename / resume / input / audio) com ack manual.
- **dir_scanner.schedule_scan**: varre periodicamente os diretórios do host para
  o autocomplete da API.
- **Loop de captura de output**: para cada sessão **ativa** (inclusive externas
  reais do usuário), garante o ``pipe-pane`` e faz o poll incremental das linhas
  novas.

Captura de todas as sessões
---------------------------
O loop de captura liga ``pipe-pane -o`` em todas as sessões ativas, incluindo as
externas (reais do usuário). O ``pipe-pane`` é READ-ONLY: apenas faz tee do
output novo do pane para um arquivo; não altera o input nem o que a sessão
exibe. É o que torna o monitoramento ao vivo das sessões reais funcional (MVP).
Ver :func:`_capturable_sessions`.

Robustez
--------
O loop principal (:func:`run`) é resiliente: se Mongo/Rabbit cair durante o
boot ou em runtime, ele loga e tenta reconectar com backoff exponencial (até
``_MAX_BACKOFF``), em vez de morrer no primeiro erro. O shutdown é gracioso:
SIGINT/SIGTERM cancelam todas as tasks, e as conexões são fechadas no finally.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from libtmux.exc import LibTmuxException
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import dir_scanner, jarvis
from sessionflow_worker.command_consumer import CommandConsumer
from sessionflow_worker.discovery import Discovery
from sessionflow_worker.events import emit_event
from sessionflow_worker.host_identity import capabilities_for, detect_platform, get_host_id
from sessionflow_worker.model_discovery import (
    cache_is_fresh,
    discover_all_data,
    persist_models,
)
from sessionflow_worker.mongo import (
    ACTIVE_STATUSES,
    SESSIONS_COLLECTION,
    ensure_indexes,
    get_db,
)
from sessionflow_worker.milestones import sync_session
from sessionflow_worker.output_capture import OutputCapture
from sessionflow_worker.rabbit import connect, commands_queue_name, declare_topology
from sessionflow_worker.tmux_runtime import TmuxRuntime
from sessionflow_worker.usage import persist_usage, scrape_usage

logger = logging.getLogger("sessionflow_worker.runner")

# `.env` da raiz do repo: worker/sessionflow_worker/runner.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# Parâmetros dos loops periódicos.
DISCOVERY_INTERVAL = 5.0
DIR_SCAN_INTERVAL = 300.0
CAPTURE_INTERVAL = 0.3
# Descoberta dos modelos REAIS do host: rotina DIÁRIA (24h). No boot só
# (re)descobre se o cache ``host_models`` estiver vazio ou velho (> 24h).
MODEL_DISCOVERY_INTERVAL = 86400.0
# Raspagem do ``/usage`` do Claude (% real dos limites): a cada ~10min. Roda
# 1x no boot e depois dorme ``USAGE_INTERVAL`` entre execuções.
USAGE_INTERVAL = 600.0

# Backoff de reconexão (boot/runtime defensivos).
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0

# Watchdog do consumer de comandos. Com ``connect_robust``, uma reconexão pode
# deixar o ``queue.iterator()`` "preso" (para de entregar SEM levantar exceção):
# a task ``command_consumer`` nem termina nem erra, então o ``asyncio.wait``
# (FIRST_COMPLETED) nunca dispara e a fila acumula com 0 consumidores — o worker
# fica vivo mas surdo. O watchdog observa a VERDADE no broker (nº de
# consumidores na fila via mgmt API): 0 por ``_WATCHDOG_GRACE`` checagens
# seguidas → levanta ``ConsumerStalled``, que cai no caminho de reconexão do
# :func:`run` e reconstrói tudo (conexão/canal/consumer novos).
_WATCHDOG_INTERVAL = 30.0
_WATCHDOG_GRACE = 2
_WATCHDOG_HTTP_TIMEOUT = 5.0


class ConsumerStalled(RuntimeError):
    """Consumer de comandos sumiu da fila (0 consumidores) — força rebuild."""


def _mgmt_queue_url_and_auth(queue_name: str) -> tuple[str, str] | None:
    """Monta a URL da mgmt API p/ a fila de comandos DESTE HOST + header Basic.

    Deriva usuário/senha/host da URI AMQP (``RABBITMQ_URI_HOST`` >
    ``RABBITMQ_URI``) e troca a porta pela da mgmt (``RABBITMQ_MGMT_HOST_PORT``,
    default 15672). Retorna ``(url, authorization)`` ou ``None`` se não der pra
    montar (sem URI / parse falho) — nesse caso o watchdog não age.
    """
    uri = os.environ.get("RABBITMQ_URI_HOST") or os.environ.get("RABBITMQ_URI")
    if not uri:
        return None
    try:
        parts = urllib.parse.urlsplit(uri)
        host = parts.hostname or "127.0.0.1"
        user = urllib.parse.unquote(parts.username or "guest")
        password = urllib.parse.unquote(parts.password or "guest")
    except ValueError:
        return None
    mgmt_port = os.environ.get("RABBITMQ_MGMT_HOST_PORT", "15672")
    vhost = os.environ.get("RABBITMQ_VHOST", "/")
    vhost_enc = urllib.parse.quote(vhost, safe="")
    queue_enc = urllib.parse.quote(queue_name, safe="")
    url = f"http://{host}:{mgmt_port}/api/queues/{vhost_enc}/{queue_enc}"
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return url, f"Basic {token}"


def _commands_consumer_count(queue_name: str) -> int | None:
    """Nº de consumidores na fila de comandos DESTE HOST (via mgmt API).

    Bloqueante (urllib) — chamar via ``asyncio.to_thread``. Retorna ``None`` em
    QUALQUER falha (mgmt fora, timeout, JSON inesperado): o watchdog trata
    ``None`` como "não dá pra afirmar" e NÃO age, evitando derrubar o worker por
    uma falha do próprio check.
    """
    built = _mgmt_queue_url_and_auth(queue_name)
    if built is None:
        return None
    url, authorization = built
    req = urllib.request.Request(url, headers={"Authorization": authorization})
    try:
        with urllib.request.urlopen(req, timeout=_WATCHDOG_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    count = data.get("consumers")
    return count if isinstance(count, int) else None


async def consumer_watchdog(
    host_id: str,
    interval: float = _WATCHDOG_INTERVAL,
    grace: int = _WATCHDOG_GRACE,
) -> None:
    """Vigia se a fila de comandos DESTE HOST ainda tem consumidor vivo
    (auto-cura).

    A cada ``interval`` consulta o broker. ``count > 0`` zera o contador;
    ``count == 0`` por ``grace`` checagens seguidas levanta ``ConsumerStalled``
    (→ rebuild via :func:`run`). ``count is None`` (não deu pra checar) é
    ignorado — não conta como falha.
    """
    queue_name = commands_queue_name(host_id)
    misses = 0
    while True:
        await asyncio.sleep(interval)
        count = await asyncio.to_thread(_commands_consumer_count, queue_name)
        if count is None:
            continue  # mgmt indisponível/erro do check: não afirma nada.
        if count > 0:
            if misses:
                logger.info(
                    "watchdog: consumer de %s de volta (%d consumidor(es))",
                    queue_name,
                    count,
                )
            misses = 0
            continue
        misses += 1
        logger.warning(
            "watchdog: 0 consumidores em %s (%d/%d)",
            queue_name,
            misses,
            grace,
        )
        if misses >= grace:
            raise ConsumerStalled(
                f"{queue_name} sem consumidor após {grace} checagens; rebuild"
            )


def load_env() -> None:
    """Carrega o `.env` da raiz sem sobrescrever variáveis já presentes."""
    if _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV, override=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Configura logging estruturado básico (idempotente o suficiente)."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _capturable_sessions(
    db: AsyncIOMotorDatabase,
    host_id: str,
    collection: str = SESSIONS_COLLECTION,
) -> list[str]:
    """Nomes tmux das sessões DESTE HOST que PODEM ser capturadas.

    Critério: qualquer sessão com status ativo (≠ ``stopped``), inclusive as
    externas (reais do usuário). O ``pipe-pane`` é READ-ONLY (apenas tee do
    output novo para um arquivo), não altera input nem o que a sessão exibe —
    é o que torna o monitoramento das sessões reais funcional (MVP).

    Filtro por ``host_id`` (AD-011): sem ele, dois hosts com uma sessão tmux
    de MESMO NOME colidiriam (o worker do host B tentaria capturar/gravar
    por cima do doc da sessão do host A). ``runtime.has_session`` já filtra
    a maioria dos casos (nomes só existem no tmux local), mas o filtro no
    Mongo fecha a lacuna de colisão de nome entre hosts.
    """
    cursor = db[collection].find(
        {"status": {"$in": ACTIVE_STATUSES}, "host_id": host_id},
        projection={"tmux_name": 1},
    )
    return [doc["tmux_name"] async for doc in cursor if doc.get("tmux_name")]


async def capture_loop(
    capture: OutputCapture,
    db: AsyncIOMotorDatabase,
    runtime: TmuxRuntime,
    host_id: str,
    interval: float = CAPTURE_INTERVAL,
    collection: str = SESSIONS_COLLECTION,
) -> None:
    """Loop de captura de output das sessões do SessionFlow.

    A cada ciclo: lista as sessões capturáveis (ver :func:`_capturable_sessions`),
    garante o ``start_capture`` (idempotente) e faz o ``poll_new_lines`` de cada
    uma. Falhas por sessão são logadas e não derrubam o loop.
    """
    started: set[str] = set()
    while True:
        try:
            names = await _capturable_sessions(db, host_id, collection)
        except Exception:  # noqa: BLE001 - Mongo pode oscilar; loga e segue.
            logger.exception("capture_loop: falha ao listar sessões capturáveis")
            await asyncio.sleep(interval)
            continue

        new_lines = 0
        for name in names:
            # Salvaguarda extra: só capturamos se a sessão ainda existe no tmux.
            if not runtime.has_session(name):
                continue
            try:
                if name not in started:
                    capture.start_capture(name)
                    started.add(name)
                lines = await capture.poll_new_lines(name)
                new_lines += len(lines)
            except Exception:  # noqa: BLE001 - falha de uma sessão não mata o loop
                logger.exception("capture_loop: falha capturando sessão %s", name)

            # Espelho da TELA AO VIVO: upsert do snapshot da tela visível atual
            # (capture-pane) num doc por sessão. Tolerante a erro por-sessão e
            # independente da captura linha-a-linha acima.
            try:
                await capture.snapshot_screen(name)
            except Exception:  # noqa: BLE001 - falha de uma sessão não mata o loop
                logger.exception(
                    "capture_loop: falha no snapshot de tela da sessão %s", name
                )

        if names:
            logger.debug(
                "capture_loop: %d sessões SessionFlow, %d linhas novas",
                len(names),
                new_lines,
            )
        await asyncio.sleep(interval)


async def model_discovery_loop(
    db: AsyncIOMotorDatabase,
    interval: float = MODEL_DISCOVERY_INTERVAL,
) -> None:
    """Descobre os modelos REAIS do host como rotina **diária** (MODEL-01).

    Cadência (pedido do usuário): roda no máximo 1x/dia (``interval`` = 24h). No
    **boot** só (re)descobre se o cache ``host_models`` estiver vazio OU velho
    (> ``interval``); com cache fresco, pula e reusa o cache. Depois disso, dorme
    ``interval`` entre execuções.

    ``discover_all_data`` faz I/O bloqueante (lê configs e raspa as TUIs de
    claude/gemini via ``time.sleep`` em sessões tmux efêmeras), então roda numa
    thread para não travar o event loop. Resiliente: qualquer falha é logada e o
    loop segue — nunca derruba o daemon.
    """
    while True:
        try:
            if await cache_is_fresh(db, max_age_seconds=interval):
                logger.info(
                    "model_discovery_loop: cache host_models fresco (< %.0fh), pulando",
                    interval / 3600.0,
                )
            else:
                # Parte bloqueante (configs + scraping das TUIs) numa thread; a
                # persistência (motor) roda no loop do daemon, dono do client Mongo.
                data = await asyncio.to_thread(discover_all_data)
                await persist_models(db, data)
                logger.info("model_discovery_loop: descoberta de modelos concluída")
        except Exception:  # noqa: BLE001 - descoberta de modelos nunca derruba o daemon
            logger.exception("model_discovery_loop: falha na descoberta de modelos")
        await asyncio.sleep(interval)


async def usage_loop(
    db: AsyncIOMotorDatabase,
    interval: float = USAGE_INTERVAL,
) -> None:
    """Raspa o ``/usage`` do Claude e persiste o snapshot a cada ``interval``.

    Roda 1x no **boot** e depois dorme ``interval`` (~10min) entre execuções.
    O ``/usage`` é quota-light (não manda prompt). ``scrape_usage`` faz I/O
    bloqueante (sobe uma sessão tmux efêmera e usa ``time.sleep``), então roda
    numa thread para não travar o event loop. Resiliente: qualquer falha é
    logada e o loop segue — nunca derruba o daemon. Quando o scrape volta
    ``None`` (claude ausente/boot lento/timeout), pula a persistência do ciclo.
    """
    while True:
        try:
            usage = await asyncio.to_thread(scrape_usage)
            if usage is not None:
                await persist_usage(db, usage)
                logger.info(
                    "usage_loop: limites atualizados (session=%s%% week=%s%%)",
                    usage.get("session_pct"),
                    usage.get("week_pct"),
                )
            else:
                logger.info("usage_loop: scrape do /usage vazio; pulando este ciclo")
        except Exception:  # noqa: BLE001 - raspagem de uso nunca derruba o daemon
            logger.exception("usage_loop: falha na raspagem do /usage")
        await asyncio.sleep(interval)


WORKER_STATUS_COLLECTION = "worker_status"
HEARTBEAT_INTERVAL = 10.0
MILESTONES_INTERVAL = 6.0


async def milestones_loop(
    db: AsyncIOMotorDatabase,
    host_id: str,
    channel=None,
    interval: float = MILESTONES_INTERVAL,
) -> None:
    """Sincroniza os MARCOS (``.sessionflow/milestones.json``) das sessões
    ativas DESTE HOST.

    Para cada sessão ativa com ``work_dir``, lê o arquivo de marcos e reflete na
    coleção ``tasks`` (que a Home mostra). Best-effort: falha por sessão não
    derruba o loop. Filtro por ``host_id`` (AD-011): ``work_dir`` é um caminho
    de arquivo LOCAL — ler o de uma sessão de outro host sempre falharia (e já
    era tolerado silenciosamente), mas o filtro evita o trabalho/erro à toa.
    """
    coll = db[SESSIONS_COLLECTION]
    while True:
        try:
            sessions = await coll.find(
                {
                    "status": {"$in": list(ACTIVE_STATUSES)},
                    "work_dir": {"$nin": [None, ""]},
                    "host_id": host_id,
                },
                projection={"tmux_name": 1, "work_dir": 1},
            ).to_list(length=200)
            # Quantas sessões ativas por work_dir: o arquivo genérico
            # (sem namespace) só é atribuído quando há UMA sessão no diretório,
            # evitando colisão quando várias compartilham o mesmo repo.
            wd_counts: dict[str, int] = {}
            for s in sessions:
                wd_counts[s.get("work_dir", "")] = wd_counts.get(s.get("work_dir", ""), 0) + 1
            for s in sessions:
                sid = s.get("tmux_name") or str(s.get("_id"))
                wd = s.get("work_dir", "")
                try:
                    newly_done = await sync_session(
                        db, sid, wd, session_name=sid, allow_shared=wd_counts.get(wd, 0) <= 1
                    )
                    # Tarefa(s) recém-concluída(s) → evento "task_done" (som de
                    # vitória + destaque no card). Respeita o alto-falante da
                    # sessão (jarvis) p/ o cliente decidir tocar o som.
                    if newly_done and channel is not None:
                        jv = await jarvis.is_enabled(db, sid)
                        for mdone in newly_done:
                            await emit_event(
                                db,
                                type="task_done",
                                kind="success",
                                session_id=sid,
                                title=f"Tarefa concluída: {mdone['title']}",
                                desc="O agente marcou uma tarefa como concluída.",
                                channel=channel,
                                extra={"jarvis": jv, "task": True},
                            )
                except Exception:  # noqa: BLE001 - marco nunca derruba o loop
                    logger.debug("milestones sync falhou p/ %r", sid, exc_info=True)
        except Exception:  # noqa: BLE001
            logger.debug("milestones_loop ciclo falhou", exc_info=True)
        await asyncio.sleep(interval)


async def _backfill_legacy_host_id(db: AsyncIOMotorDatabase, host_id: str) -> None:
    """Migração 1x (idempotente): sessões sem ``host_id`` pertencem a ESTE host.

    Multi-host (AD-011): antes desta feature, nenhuma sessão tinha `host_id` —
    todas eram, na prática, do único worker que existia. Rodar isso no boot
    garante que elas continuem sendo geridas por ESTE worker (o de sempre)
    mesmo depois de um segundo host entrar em cena. É no-op depois da 1ª vez
    (não sobra doc sem `host_id` pra migrar de novo).
    """
    coll = db[SESSIONS_COLLECTION]
    result = await coll.update_many(
        {"host_id": {"$exists": False}}, {"$set": {"host_id": host_id}}
    )
    if result.modified_count:
        logger.info(
            "backfill: %d sessão(ões) legada(s) migrada(s) p/ host_id=%s",
            result.modified_count,
            host_id,
        )


async def heartbeat_loop(
    db: AsyncIOMotorDatabase,
    host_id: str,
    platform: str,
    capabilities: dict,
    interval: float = HEARTBEAT_INTERVAL,
) -> None:
    """Publica o status DESTE WORKER (multi-host) p/ a API mostrar no Perfil.

    Faz upsert de 1 doc **por host** (``_id=host_id``, não mais um único
    ``_id="worker"`` fixo) com ``hostname``/``platform``/``capabilities``/
    ``started_at``/``updated_at`` a cada ``interval``. ``started_at`` reflete o
    boot DESTE processo (re-setado a cada restart) → uptime correto;
    ``updated_at`` recente indica que o worker está vivo (online). Múltiplos
    workers (hosts diferentes) não pisam um no doc do outro.
    """
    coll = db[WORKER_STATUS_COLLECTION]
    hostname = socket.gethostname()
    started = datetime.now(timezone.utc)
    pid = os.getpid()
    while True:
        await coll.update_one(
            {"_id": host_id},
            {
                "$set": {
                    "hostname": hostname,
                    "platform": platform,
                    "capabilities": capabilities,
                    "started_at": started,
                    "updated_at": datetime.now(timezone.utc),
                    "pid": pid,
                }
            },
            upsert=True,
        )
        await asyncio.sleep(interval)


async def _build_and_run(stop: asyncio.Event) -> None:
    """Conecta infra, monta componentes e roda o gather até ``stop``.

    Levanta exceção se a infra cair — o caller (:func:`run`) trata com backoff.
    A conexão Rabbit é fechada no finally; o cliente Mongo também.
    """
    host_id = get_host_id()
    host_platform = detect_platform()
    caps = capabilities_for(host_platform)
    logger.info(
        "Identidade do host: host_id=%s platform=%s capabilities=%s",
        host_id,
        host_platform,
        caps,
    )

    db = get_db()
    await ensure_indexes(db)
    logger.info("Mongo conectado e índices garantidos (db=%s)", db.name)
    await _backfill_legacy_host_id(db, host_id)

    connection = await connect()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    await declare_topology(channel, host_id)
    logger.info("Rabbit conectado e topologia declarada (fila do host: %s)", host_id)

    runtime = TmuxRuntime()
    logger.info("TmuxRuntime inicializado")

    discovery = Discovery(runtime, db, host_id, channel=channel)
    consumer = CommandConsumer(channel, db, host_id, runtime=runtime)
    capture = OutputCapture(runtime, db, channel=channel)
    logger.info("Componentes montados: Discovery, CommandConsumer, OutputCapture")

    async def _stopper() -> None:
        await stop.wait()
        raise asyncio.CancelledError

    tasks = [
        asyncio.create_task(discovery.run_forever(interval=DISCOVERY_INTERVAL), name="discovery"),
        asyncio.create_task(consumer.run(), name="command_consumer"),
        asyncio.create_task(
            dir_scanner.schedule_scan(db, interval_seconds=DIR_SCAN_INTERVAL),
            name="dir_scanner",
        ),
        asyncio.create_task(capture_loop(capture, db, runtime, host_id), name="capture_loop"),
        asyncio.create_task(model_discovery_loop(db), name="model_discovery"),
        asyncio.create_task(usage_loop(db), name="usage_loop"),
        asyncio.create_task(heartbeat_loop(db, host_id, host_platform, caps), name="heartbeat"),
        asyncio.create_task(milestones_loop(db, host_id, channel=channel), name="milestones"),
        asyncio.create_task(consumer_watchdog(host_id), name="consumer_watchdog"),
        asyncio.create_task(_stopper(), name="stopper"),
    ]
    logger.info("Worker no ar: %d tasks rodando", len(tasks) - 1)

    try:
        # Se qualquer task terminar (erro ou stop), derrubamos as demais.
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        # Propaga a primeira exceção real (≠ CancelledError) para acionar backoff.
        #
        # IMPORTANTE: a reconexão COMPLETA do worker (fechar/reabrir Mongo +
        # Rabbit + recriar componentes) é cara e interrompe a captura de output
        # (o dashboard fica sem updates ao vivo). Ela deve ser reservada para
        # falhas de INFRA (conexão Mongo/Rabbit). Um ``LibTmuxException`` é um
        # erro TRANSITÓRIO por-sessão (sessão efêmera morta, terminal fechado
        # entre o "listar" e o "detalhar") — os loops já o contêm internamente,
        # mas, por segurança, se um chegar até aqui NÃO reconectamos: logamos e
        # deixamos o loop seguir (o ``run`` reentra em ``_build_and_run``).
        for task in done:
            exc = task.exception()
            if exc is None or isinstance(exc, asyncio.CancelledError):
                continue
            if isinstance(exc, LibTmuxException):
                logger.warning(
                    "Erro transitório de tmux na task %r; NÃO reconectando "
                    "(infra Mongo/Rabbit intacta)",
                    task.get_name(),
                    exc_info=exc,
                )
                continue
            raise exc
    finally:
        await connection.close()
        db.client.close()
        logger.info("Conexões Rabbit/Mongo fechadas")


async def run(stop: asyncio.Event | None = None) -> None:
    """Loop principal do daemon: roda os componentes com reconexão por backoff.

    Aceita um ``stop`` (asyncio.Event) injetável: quando setado, o daemon
    encerra graciosamente (cancela as tasks, fecha conexões e retorna). Sem
    ``stop``, instala handlers de SIGINT/SIGTERM que setam o evento.
    """
    load_env()
    stop = stop if stop is not None else asyncio.Event()

    loop = asyncio.get_running_loop()
    installed: list[int] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler pode não existir (ex.: Windows / loop não-main).
            logger.debug("Sem handler para sinal %s", sig)

    backoff = _INITIAL_BACKOFF
    try:
        while not stop.is_set():
            try:
                await _build_and_run(stop)
                # Saída limpa (stop setado): encerra o loop.
                if stop.is_set():
                    break
                backoff = _INITIAL_BACKOFF
            except asyncio.CancelledError:
                logger.info("Runner cancelado — encerrando")
                raise
            except Exception:  # noqa: BLE001 - infra caiu; tenta reconectar.
                logger.exception(
                    "Falha na infra do Worker; reconectando em %.1fs", backoff
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                    break  # stop chegou durante o backoff
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, _MAX_BACKOFF)
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        logger.info("Worker encerrado")


def main() -> None:
    """Entry-point síncrono: configura logging e roda o loop async."""
    configure_logging()
    logger.info("Iniciando SessionFlow Worker")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário (KeyboardInterrupt)")
