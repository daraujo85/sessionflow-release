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
import logging
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Discovery:
    """Reconciliador entre o runtime tmux e a coleção Mongo de sessões."""

    def __init__(
        self,
        tmux: TmuxRuntime,
        db: AsyncIOMotorDatabase,
        collection: str = SESSIONS_COLLECTION,
        events_collection: str = EVENTS_COLLECTION,
        channel: aio_pika.abc.AbstractChannel | None = None,
    ) -> None:
        self._tmux = tmux
        self._db = db
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
            projection={"status": 1},
        )
        prev_status = prev.get("status") if prev else None

        # Sessão viva: tmux presente, agente vivo se há pane_pid, exit_code None.
        state = derive_state(
            tmux_present=True,
            attached=info.attached,
            agent_alive=info.pane_pid is not None,
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
        if state is SessionState.RUNNING:
            screen_text = await self._screen_text(info.name)
            if screen_wants_attention(screen_text, info.agent_type):
                status_value = SessionState.WAITING_INPUT.value
                attention = "waiting"
            elif self._screen_idle(info.name, screen_text):
                attention = "idle"
            activity = derive_activity(screen_text, info.agent_type, attention)

        set_fields = {
            "tmux_name": info.name,
            "agent_type": info.agent_type.value,
            "status": status_value,
            "activity": activity,
            "tmux_session_id": info.id,
            "agent_pid": info.pane_pid,
            "last_seen_at": now,
            "updated_at": now,
        }
        # work_dir: só grava quando o tmux expõe um cwd não-vazio. Nunca
        # sobrescreve um work_dir já conhecido (ex. de sessão criada pelo
        # SessionFlow) com vazio.
        if info.work_dir:
            set_fields["work_dir"] = info.work_dir

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
        title = desc = None
        if attention == "waiting":
            title = f"{name} aguarda você"
            desc = "A sessão está esperando sua resposta ou uma escolha."
            await self._emit(
                type="attention", kind="attention",
                session_id=name, title=title, desc=desc,
            )
        elif attention == "idle":
            title = f"{name} concluiu"
            desc = "O agente terminou o bloco e está ocioso."
            await self._emit(
                type="attention", kind="success",
                session_id=name, title=title, desc=desc,
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
        """
        now = _now()
        query = {
            "status": {"$in": ACTIVE_STATUSES},
            "tmux_name": {"$nin": list(present_names)},
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
    ) -> None:
        """Emite um evento (Mongo + Rabbit se houver channel). Best-effort."""
        await emit_event(
            self._db,
            type=type,
            kind=kind,
            session_id=session_id,
            title=title,
            desc=desc,
            channel=self._channel,
            collection=self._events_collection,
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
