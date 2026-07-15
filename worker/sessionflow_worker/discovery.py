"""Discovery / reconciliação tmux <-> Mongo (TMUX-01, TMUX-02, TMUX-03, TMUX-12).

Reconcilia o estado das sessões tmux do host com a coleção ``sessions`` no
MongoDB. A cada ciclo:

- Lista TODAS as sessões tmux (inclui as criadas fora do SessionFlow).
- Faz **upsert** por ``tmux_name`` no Mongo, derivando o estado via
  ``derive_state`` (TMUX-12).
- Marca como ``stopped`` as sessões que existem no DB com status ativo mas
  não estão mais presentes no tmux.
- Sessões nunca vistas pelo SessionFlow recebem ``origin="externa"`` no insert.

O loop ``run_forever`` é protegido por um ``asyncio.Lock`` para garantir que
dois ciclos de reconciliação nunca rodem concorrentemente (evitando upserts /
contagens corrompidas).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import aio_pika
from libtmux.exc import LibTmuxException
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import jarvis
from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.events import EVENTS_COLLECTION, emit_event
from sessionflow_worker.metrics import claude_metrics_for
from sessionflow_worker.output_capture import (
    DEFAULT_SCREEN_COLLECTION,
    derive_activity,
    screen_wants_attention,
)
from sessionflow_worker.push_sender import send_to_all
from sessionflow_worker.mongo import ACTIVE_STATUSES, SESSIONS_COLLECTION
from sessionflow_worker.state import SessionState, derive_state
from sessionflow_worker.tmux_runtime import SessionInfo, TmuxRuntime
from sessionflow_worker.usage import read_usage

# Idade máxima do snapshot ``host_usage`` para ainda ser anexado às métricas.
# ~20min: tolera atrasos do loop (que roda a cada ~10min) sem servir % rançoso.
USAGE_MAX_AGE_SECONDS = 1200.0

# Tela parada por este tempo (s) após atividade ⇒ "bloco concluído / ocioso".
# ~2-3 ciclos de discovery (5s). Agentes mostram spinner/timer enquanto
# trabalham (tela muda), então parada real só ocorre num prompt ocioso.
IDLE_SECONDS = 12.0

logger = logging.getLogger("sessionflow_worker.discovery")

# Origem de uma sessão: criada pelo SessionFlow ou descoberta externamente.
ORIGIN_EXTERNAL = "externa"
ORIGIN_SESSIONFLOW = "sessionflow"


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Resumo de um ciclo de reconciliação.

    - ``discovered``: sessões tmux inseridas pela primeira vez no DB.
    - ``updated``: sessões tmux já conhecidas que tiveram o doc atualizado.
    - ``stopped``: sessões antes ativas no DB e ausentes agora no tmux.
    """

    discovered: int = 0
    updated: int = 0
    stopped: int = 0


# Cooldown (s) anti-flap: janela mínima entre notificações da MESMA atenção pra
# uma mesma sessão. Absorve a piscada idle↔ocupado do reflow do agente (resize)
# sem suprimir conclusões genuínas mais espaçadas.
_NOTIFY_COOLDOWN_S = 8.0


# --- Detecção de sub-agents rodando (heurística sobre a tela do Claude Code) --

# "Waiting for N background agents to finish" — o provedor dizendo quantos rodam.
_BG_AGENTS_RE = re.compile(r"(\d+)\s+background agents?\b", re.IGNORECASE)
# Nome de sub-agent (o Claude Code usa Agent "<nome>" no resumo). Filtramos os
# que aparecem como concluídos ("finished"/"done") p/ listar só os que rodam.
_AGENT_NAME_RE = re.compile(r'Agent\s+"([^"]{1,60})"')
_AGENT_DONE_RE = re.compile(r"finished|done|conclu", re.IGNORECASE)


# --- Auto-continue em erro de API transitório --------------------------------

# Banner de erro que o agente mostra e depois PARA no prompt esperando input.
# Cobre o "API Error: Server error mid-response..." do Claude Code e variantes
# transitórias comuns. Anda anexado a "API Error" p/ evitar falso-positivo.
_API_ERROR_RE = re.compile(
    r"API Error|Server error mid-response|overloaded_error|Overloaded|"
    r"Request timed out|internal server error",
    re.IGNORECASE,
)
# Liga/desliga o auto-continue (env). Default ligado.
_AUTOCONTINUE_ON = os.environ.get("SESSIONFLOW_AUTOCONTINUE", "1") not in ("0", "false", "no")
# Máx. de continues automáticos SEGUIDOS antes de desistir e avisar o usuário.
_MAX_AUTOCONTINUE = 4
# Tempo (s) que a tela com erro precisa ficar ESTÁVEL antes de mandar continue
# (garante que o agente parou de fato, não está mid-stream).
_AUTOCONT_STABLE_S = 6.0


def _screen_has_api_error(text: str) -> bool:
    """True se as últimas linhas da tela mostram um erro de API transitório."""
    if not text:
        return False
    tail = "\n".join(text.split("\n")[-15:])
    return bool(_API_ERROR_RE.search(tail))


def derive_subagents(text: str) -> tuple[int, list[str]]:
    """Extrai (qtd, nomes) de sub-agents RODANDO a partir da tela visível.

    Heurística best-effort sobre o que o Claude Code imprime:
      - "Waiting for N background agents to finish" → N (sinal mais confiável);
      - linhas ``Agent "<nome>"`` que NÃO estão marcadas como concluídas → nomes.
    Sem sinal → (0, []). Viewport-limitado (só a tela visível), então é uma
    aproximação — some quando o bloco some da tela.
    """
    if not text:
        return 0, []
    count = 0
    m = _BG_AGENTS_RE.search(text)
    if m:
        count = int(m.group(1))
    names: list[str] = []
    for line in text.split("\n"):
        mm = _AGENT_NAME_RE.search(line)
        if mm and not _AGENT_DONE_RE.search(line):
            names.append(mm.group(1).strip())
    # dedupe preservando ordem; teto p/ não estourar tooltip
    names = list(dict.fromkeys(names))[:8]
    if count == 0 and names:
        count = len(names)
    return count, names


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Discovery:
    """Reconciliador entre o runtime tmux e a coleção Mongo de sessões."""

    def __init__(
        self,
        tmux: TmuxRuntime,
        db: AsyncIOMotorDatabase,
        host_id: str,
        collection: str = SESSIONS_COLLECTION,
        events_collection: str = EVENTS_COLLECTION,
        channel: aio_pika.abc.AbstractChannel | None = None,
    ) -> None:
        self._tmux = tmux
        self._db = db
        self._host_id = host_id
        self._collection = collection
        self._events_collection = events_collection
        self._channel = channel
        self._lock = asyncio.Lock()
        # Detecção de atenção (notificações), por sessão e em memória:
        #   _attn: último estado notificado ("waiting" | "idle" | None)
        #   _scr_sig / _scr_since: assinatura da tela e desde quando não muda
        #   _scr_active: sessões que já mudaram a tela ao menos uma vez
        self._attn: dict[str, str | None] = {}
        self._scr_sig: dict[str, int] = {}
        self._scr_since: dict[str, float] = {}
        self._scr_active: set[str] = set()
        # Cooldown anti-flap: (atenção, instante monotonic) da última notificação
        # por sessão. Evita re-notificar/re-falar a MESMA atenção em rajada quando
        # a tela pisca idle↔ocupado (ex.: reflow do agente após um resize).
        self._last_notified: dict[str, tuple[str, float]] = {}
        # Auto-continue em erro de API: estado por sessão (assinatura da tela com
        # erro, desde quando estável, quantos continues seguidos, último frame já
        # tratado). Ver _maybe_auto_continue.
        self._autocont: dict[str, dict] = {}

    @property
    def collection(self) -> str:
        return self._collection

    @property
    def events_collection(self) -> str:
        return self._events_collection

    # -- reconciliação ----------------------------------------------------

    async def reconcile_once(self) -> ReconcileReport:
        """Executa um ciclo de reconciliação, protegido por lock.

        O lock garante que duas chamadas concorrentes (p.ex. uma chamada manual
        sobreposta ao ``run_forever``) sejam serializadas e não corrompam as
        contagens / upserts.
        """
        async with self._lock:
            return await self._reconcile()

    async def _reconcile(self) -> ReconcileReport:
        sessions = self._tmux.list_sessions()
        coll = self._db[self._collection]

        # Limites REAIS do host (% sessão/semana) — leitura BARATA e única por
        # ciclo do doc ``host_usage`` (NUNCA raspamos o /usage aqui; isso é do
        # usage_loop). Anexado só às sessões claude. Best-effort: falha → None.
        try:
            limits = await read_usage(self._db, max_age_seconds=USAGE_MAX_AGE_SECONDS)
        except Exception:  # noqa: BLE001 - leitura de limites nunca derruba o ciclo
            logger.debug("reconcile: leitura de host_usage falhou", exc_info=True)
            limits = None

        discovered = 0
        updated = 0
        present_names: set[str] = set()

        for info in sessions:
            # Uma sessão que some no meio do ciclo (LibTmuxException) NÃO pode
            # derrubar a reconciliação das demais. ``list_sessions`` já tolera
            # sumiço; aqui blindamos também o upsert por-sessão.
            try:
                was_new = await self._upsert_session(info, limits=limits)
            except LibTmuxException:
                logger.warning(
                    "reconcile: sessão %r sumiu durante o upsert; pulando",
                    info.name,
                    exc_info=True,
                )
                continue
            present_names.add(info.name)
            if was_new:
                discovered += 1
            else:
                updated += 1

        stopped = await self._mark_missing_stopped(coll, present_names)

        return ReconcileReport(
            discovered=discovered,
            updated=updated,
            stopped=stopped,
        )

    async def _upsert_session(
        self, info: SessionInfo, *, limits: dict | None = None
    ) -> bool:
        """Faz upsert de uma sessão tmux viva. Retorna True se inseriu (nova).

        ``limits`` é o snapshot ``host_usage`` (% real dos limites) lido uma vez
        por ciclo; quando presente, é anexado a ``metrics["limits"]`` das
        sessões claude.

        Emite eventos nas transições:
            - sessão nova descoberta -> ``created`` / ``info``;
            - status passa a ``detached`` -> ``detached`` / ``warning``.
        """
        coll = self._db[self._collection]
        now = _now()

        # Estado anterior (antes do upsert) p/ detectar transições.
        prev = await coll.find_one(
            {"tmux_name": info.name},
            projection={"status": 1, "screen_sig": 1},
        )
        prev_status = prev.get("status") if prev else None
        prev_sig = prev.get("screen_sig") if prev else None

        # Sessão viva = tmux presente E o AGENTE (claude/codex/...) ainda na
        # árvore de processos do pane. Antes era ``pane_pid is not None``, mas o
        # pane sempre tem PID (o shell), então sessões cujo claude saiu — sobrou
        # só o zsh — apareciam "running" e o comando ia pro vazio. agent_type é
        # inferido da cmdline (contém "claude ..." só enquanto o agente vive).
        state = derive_state(
            tmux_present=True,
            attached=info.attached,
            agent_alive=info.agent_type is not AgentType.UNKNOWN,
            exit_code=None,
        )

        # Atenção: numa sessão ativa, a tela pode indicar que o agente espera
        # uma resposta (status vira ``waiting_input``) ou que terminou o bloco e
        # ficou ocioso. ``attention`` ∈ {"waiting","idle",None} é a transição.
        status_value = state.value
        attention: str | None = None
        # Rótulo fino do que o agente faz (derivado da tela). Só faz sentido p/
        # sessões vivas; quando a sessão é marcada stopped/detached fica "".
        activity = ""
        # ``last_activity_at``: instante (wall-clock) da ÚLTIMA atividade REAL —
        # quando a tela mudou de fato. Não confundir com ``updated_at``, que é
        # batido todo ciclo. Permite ao app mostrar "última atividade há X".
        # Assinatura ESTÁVEL (md5) p/ comparar entre ciclos e sobreviver a
        # restart do worker (``hash()`` nativo é aleatório por processo).
        screen_sig: str | None = None
        screen_changed = False
        subagents = 0
        subagent_names: list[str] = []
        if state is SessionState.RUNNING:
            screen_text = await self._screen_text(info.name)
            screen_sig = hashlib.md5(
                screen_text.encode("utf-8", "ignore")
            ).hexdigest()
            screen_changed = screen_sig != prev_sig
            if screen_wants_attention(screen_text, info.agent_type):
                status_value = SessionState.WAITING_INPUT.value
                attention = "waiting"
            elif self._screen_idle(info.name, screen_text):
                attention = "idle"
            activity = derive_activity(screen_text, info.agent_type, attention)
            subagents, subagent_names = derive_subagents(screen_text)
            # Erro de API transitório com o agente parado → manda "continue".
            await self._maybe_auto_continue(info, screen_text, screen_sig)

        set_fields = {
            "tmux_name": info.name,
            "agent_type": info.agent_type.value,
            "status": status_value,
            "activity": activity,
            "tmux_session_id": info.id,
            "agent_pid": info.pane_pid,
            "last_seen_at": now,
            "updated_at": now,
            # Sub-agents rodando (heurística sobre a tela) — contador + nomes p/ a Home.
            "subagents": subagents,
            "subagent_names": subagent_names,
        }
        # work_dir: só grava quando o tmux expõe um cwd não-vazio. Nunca
        # sobrescreve um work_dir já conhecido (ex. de sessão criada pelo
        # SessionFlow) com vazio.
        if info.work_dir:
            set_fields["work_dir"] = info.work_dir

        # Marca atividade só quando a tela mudou de verdade (senão preserva o
        # last_activity_at anterior — não incluir no $set já mantém o valor).
        if screen_sig is not None:
            set_fields["screen_sig"] = screen_sig
            if screen_changed:
                set_fields["last_activity_at"] = now

        # Métricas REAIS da janela de contexto (só sessões Claude com work_dir).
        # É leitura de 1 arquivo JSONL por ciclo — barato; computamos sempre.
        # Best-effort: falhar aqui NÃO pode derrubar a reconciliação. Quando
        # não há dado (não-claude / sem work_dir / JSONL ausente) deixamos o
        # campo ``metrics`` como ``None`` (o front mostra "—").
        metrics = self._claude_metrics(info)
        # Anexa os limites REAIS (% sessão/semana) às métricas das sessões
        # claude, quando há snapshot recente em ``host_usage``. Se não houver
        # ``metrics`` (não-claude / sem JSONL), ``limits`` fica ausente.
        if metrics is not None and limits is not None:
            metrics["limits"] = limits
        set_fields["metrics"] = metrics

        update = {
            "$set": set_fields,
            "$setOnInsert": {
                "display_name": info.name,
                "origin": ORIGIN_EXTERNAL,
                "created_at": now,
                # Multi-host (AD-011): sessão descoberta pertence a ESTE host
                # (o tmux que a expõe é local). Sessões pré-existentes (sem
                # host_id) são migradas 1x no boot pelo heartbeat_loop.
                "host_id": self._host_id,
            },
        }

        result = await coll.update_one(
            {"tmux_name": info.name},
            update,
            upsert=True,
        )
        was_new = result.upserted_id is not None

        if was_new:
            await self._emit(
                type="created",
                kind="info",
                session_id=info.name,
                title=f"Sessão {info.name} descoberta",
                desc=f"Nova sessão tmux {info.name} ({info.agent_type.value}).",
            )
        elif (
            state is SessionState.DETACHED
            and prev_status != SessionState.DETACHED.value
        ):
            await self._emit(
                type="detached",
                kind="warning",
                session_id=info.name,
                title=f"Sessão {info.name} detached",
                desc=f"Sessão {info.name} ficou sem cliente anexado.",
            )

        await self._maybe_notify_attention(info, attention)
        return was_new

    async def _screen_text(self, name: str) -> str:
        """Texto da TELA atual da sessão (doc upsertado pelo capture loop)."""
        doc = await self._db[DEFAULT_SCREEN_COLLECTION].find_one(
            {"tmux_name": name}, projection={"text": 1}
        )
        return (doc or {}).get("text", "") or ""

    def _screen_idle(self, name: str, text: str) -> bool:
        """True se a tela está PARADA há ``IDLE_SECONDS`` após ter mudado antes.

        Rastreia assinatura da tela e o instante da última mudança (monotonic).
        Só considera "ocioso/terminou" se a sessão já mostrou atividade (a tela
        mudou ao menos uma vez) — evita falso-positivo logo na descoberta.
        """
        sig = hash(text)
        now = time.monotonic()
        if self._scr_sig.get(name) != sig:
            self._scr_sig[name] = sig
            self._scr_since[name] = now
            self._scr_active.add(name)
            return False
        if name not in self._scr_active:
            return False
        return (now - self._scr_since.get(name, now)) >= IDLE_SECONDS

    async def _maybe_auto_continue(
        self, info: SessionInfo, screen_text: str, screen_sig: str
    ) -> None:
        """Se a tela mostra um erro de API e o agente PAROU no prompt, manda
        "continue" automaticamente pra ele retomar — com trava anti-loop.

        Só age quando o frame COM erro fica estável por {@link _AUTOCONT_STABLE_S}
        (agente parado, não mid-stream). Limita a {@link _MAX_AUTOCONTINUE}
        continues seguidos; ao esgotar, avisa o usuário e para. O contador zera
        quando a sessão volta a progredir sem erro.
        """
        if not _AUTOCONTINUE_ON:
            return
        name = info.name
        st = self._autocont.setdefault(
            name, {"sig": None, "since": 0.0, "count": 0, "done_sig": None}
        )
        now = time.monotonic()

        if not _screen_has_api_error(screen_text):
            # Sem erro: se a tela mudou (progresso real), zera o contador.
            if st["sig"] != screen_sig:
                st["count"] = 0
            st["sig"] = screen_sig
            st["since"] = now
            return

        # Há erro na tela. Rastreia estabilidade do frame com erro.
        if st["sig"] != screen_sig:
            st["sig"] = screen_sig
            st["since"] = now
            return  # frame ainda mudando → espera estabilizar
        if now - st["since"] < _AUTOCONT_STABLE_S:
            return  # estável há pouco tempo → aguarda
        if st["done_sig"] == screen_sig:
            return  # este frame parado já foi tratado

        st["done_sig"] = screen_sig
        if st["count"] >= _MAX_AUTOCONTINUE:
            # Esgotou: avisa o usuário (1x por frame) e para de tentar.
            jv = await jarvis.is_enabled(self._db, name)
            await self._emit(
                type="attention", kind="attention", session_id=name,
                title=f"{name}: erro de API persistente",
                desc="Tentei continuar automaticamente algumas vezes e o erro "
                     "voltou — precisa de você.",
                jarvis=jv,
            )
            return

        st["count"] += 1
        await self._send_continue(name)
        logger.info("auto-continue #%d enviado p/ %r (erro de API)", st["count"], name)

    async def _send_continue(self, name: str) -> None:
        """Digita 'continue' e submete com Enter SEPARADO (bracketed-paste-safe)."""
        try:
            session = self._tmux.server.sessions.get(session_name=name, default=None)
            if session is None:
                return
            window = session.active_window
            pane = window.active_pane if window else None
            if pane is None:
                return
            pane.send_keys("continue", enter=False, literal=True)
            await asyncio.sleep(0.15)  # deixa o paste fechar antes do Enter
            pane.send_keys("Enter", enter=False, literal=False)
        except Exception:  # noqa: BLE001 - best-effort; nunca derruba o ciclo
            logger.debug("auto-continue: falha ao enviar p/ %r", name, exc_info=True)

    async def _maybe_notify_attention(
        self, info: SessionInfo, attention: str | None
    ) -> None:
        """Emite evento de notificação só na TRANSIÇÃO de estado de atenção.

        Evita spam: enquanto ``attention`` não muda, nada é emitido. Quando a
        tela volta a mudar (saiu de idle/waiting), reseta para re-notificar
        numa próxima parada.
        """
        name = info.name
        # Sessões internas de scraping (modelos/usage) não geram notificação.
        if name.startswith("sfusage-") or name.startswith("sfmodel-"):
            return
        if self._attn.get(name) == attention:
            return
        self._attn[name] = attention
        # Anti-flap: se a MESMA atenção foi notificada há pouco (a tela piscou
        # idle↔ocupado, ex.: reflow após resize), não re-notifica/re-fala.
        if attention is not None:
            prev = self._last_notified.get(name)
            if prev and prev[0] == attention and (time.monotonic() - prev[1]) < _NOTIFY_COOLDOWN_S:
                return
            self._last_notified[name] = (attention, time.monotonic())
        # Alto-falante da sessão (JARVIS global OU por-sessão): vai no evento p/
        # o cliente NÃO tocar o chime quando o usuário mutou a sessão.
        jv = await jarvis.is_enabled(self._db, name)
        title = desc = None
        if attention == "waiting":
            title = f"{name} aguarda você"
            desc = "A sessão está esperando sua resposta ou uma escolha."
            await self._emit(
                type="attention", kind="attention",
                session_id=name, title=title, desc=desc, jarvis=jv,
            )
        elif attention == "idle":
            title = f"{name} concluiu"
            desc = "O agente terminou o bloco e está ocioso."
            await self._emit(
                type="attention", kind="success",
                session_id=name, title=title, desc=desc, jarvis=jv,
            )
        # Web Push (app fechado): mesmo título/desc, link p/ a sessão.
        if title and desc:
            try:
                await send_to_all(self._db, title, desc, url=f"/sessao/{name}")
            except Exception:  # noqa: BLE001 - push nunca derruba o ciclo
                logger.debug("web push falhou para %r", name, exc_info=True)
            # JARVIS: resumo falado no celular (best-effort, em background p/ não
            # bloquear o discovery com o round-trip de resumo+voz).
            try:
                screen = await self._screen_text(name)
                asyncio.create_task(
                    jarvis.maybe_speak(
                        self._db, self._channel, name, title, desc, screen
                    )
                )
            except Exception:  # noqa: BLE001 - jarvis nunca derruba o ciclo
                logger.debug("jarvis: agendamento falhou para %r", name, exc_info=True)

    def _claude_metrics(self, info: SessionInfo) -> dict | None:
        """Métricas REAIS de contexto para sessões Claude (best-effort).

        Retorna ``None`` para sessões não-Claude, sem ``work_dir``, ou se a
        leitura do JSONL falhar — nunca propaga exceção.
        """
        if info.agent_type is not AgentType.CLAUDE or not info.work_dir:
            return None
        try:
            return claude_metrics_for(info.work_dir)
        except Exception:  # noqa: BLE001 - métricas nunca derrubam o ciclo
            logger.debug(
                "métricas Claude falharam para %r", info.name, exc_info=True
            )
            return None

    async def _mark_missing_stopped(
        self,
        coll,
        present_names: set[str],
    ) -> int:
        """Marca como ``stopped`` sessões ativas ausentes do tmux.

        Emite um evento ``stopped`` / ``warning`` por sessão que transiciona.

        **Multi-host (AD-011):** ``present_names`` só reflete o tmux DESTE
        host — sem o filtro ``host_id`` abaixo, um segundo worker (outra
        máquina) marcaria como "stopped" TODAS as sessões ativas de QUALQUER
        outro host (elas nunca aparecem no seu tmux local). Sessões legadas
        sem ``host_id`` (pré-migração) são cobertas pelo backfill do
        ``heartbeat_loop`` no boot, então esse filtro é seguro desde o 1º ciclo.
        """
        now = _now()
        query = {
            "status": {"$in": ACTIVE_STATUSES},
            "tmux_name": {"$nin": list(present_names)},
            "host_id": self._host_id,
        }

        # Coleta os nomes que vão transicionar ANTES do update p/ emitir eventos.
        to_stop: list[str] = [
            doc["tmux_name"]
            async for doc in coll.find(query, projection={"tmux_name": 1})
        ]

        result = await coll.update_many(
            query,
            {
                "$set": {
                    "status": SessionState.STOPPED.value,
                    "agent_pid": None,
                    "updated_at": now,
                }
            },
        )

        for name in to_stop:
            await self._emit(
                type="stopped",
                kind="warning",
                session_id=name,
                title=f"Sessão {name} parada",
                desc=f"Sessão {name} sumiu do tmux e foi marcada como stopped.",
            )

        return result.modified_count

    async def _emit(
        self,
        type: str,
        kind: str,
        session_id: str,
        title: str,
        desc: str,
        jarvis: bool | None = None,
    ) -> None:
        """Emite um evento (Mongo + Rabbit se houver channel). Best-effort.

        ``jarvis`` (quando informado) viaja no evento p/ o cliente decidir se
        toca o chime de notificação — sessão com o alto-falante OFF não soa.
        """
        await emit_event(
            self._db,
            type=type,
            kind=kind,
            session_id=session_id,
            title=title,
            desc=desc,
            channel=self._channel,
            collection=self._events_collection,
            extra=None if jarvis is None else {"jarvis": jarvis},
        )

    # -- loop -------------------------------------------------------------

    async def run_forever(self, interval: float = 5) -> None:
        """Loop infinito de reconciliação a cada ``interval`` segundos.

        Cada ciclo passa por ``reconcile_once`` (logo, pelo lock), de modo que
        nunca há dois ciclos concorrentes.
        """
        while True:
            try:
                await self.reconcile_once()
            except LibTmuxException:
                # Erro transitório de tmux (sessão sumindo, server reiniciando):
                # loga e segue no próximo ciclo. NÃO propaga — propagar mataria
                # a task de discovery e dispararia a reconexão COMPLETA do
                # worker (Mongo/Rabbit), que é o que queremos evitar.
                logger.warning(
                    "run_forever: erro transitório de tmux na reconciliação; "
                    "seguindo no próximo ciclo",
                    exc_info=True,
                )
            await asyncio.sleep(interval)
