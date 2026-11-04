"""Read-only repository for session documents stored in Mongo (motor).

The collection name is taken from settings (``sessions_collection``) so tests
can inject an isolated collection within the ``sessionflow`` database.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase

_INTERNAL_PREFIXES_RE = re.compile(r"^(sfusage-|sfmodel-|sftest-)")


class SessionsRepository:
    """Reads session documents from a configurable Mongo collection."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "sessions") -> None:
        self._collection = db[collection_name]

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return all sessions, optionally filtered by exact ``status``."""
        query: dict[str, Any] = {"tmux_name": {"$not": _INTERNAL_PREFIXES_RE}}
        if status is not None:
            query["status"] = status

        cursor = self._collection.find(query).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session by id, or ``None`` if not found.

        Returns ``None`` for malformed ids so callers can map both the
        "invalid id" and "not found" cases to a coherent 404.
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return None

        return await self._collection.find_one({"_id": oid})

    async def delete_session(self, session_id: str) -> bool:
        """Remove o doc da sessão pelo id. Retorna True se removeu algo.

        Usado no purge (eliminar) para a sessão sumir da lista NA HORA, sem
        esperar o worker processar o comando assíncrono (evita o flicker de
        'apaguei e voltou'). O worker ainda mata o tmux + limpa dados ligados.
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.delete_one({"_id": oid})
        return res.deleted_count > 0

    async def mark_stopped(self, session_id: str) -> bool:
        """Marca a sessão como ``stopped`` diretamente (sem depender do worker).

        Usado quando o host da sessão está offline: não há worker vivo pra
        processar o comando ``kill``, então o usuário ficaria com a sessão
        presa em "running/detached" pra sempre. Best-effort — não mata
        processo real (o host já não está acessível de qualquer forma).
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$set": {"status": "stopped", "agent_pid": None}},
        )
        return res.matched_count > 0

    async def set_share(
        self, session_id: str, token: str, expires_at: Any
    ) -> bool:
        """Grava/rotaciona o token de link compartilhável + sua validade."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$set": {"share_token": token, "share_expires_at": expires_at}},
        )
        return res.matched_count > 0

    async def set_moving(self, session_id: str, moving: dict[str, Any]) -> bool:
        """Grava o estado ``moving`` (transferência entre hosts) no doc."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid}, {"$set": {"moving": moving}}
        )
        return res.matched_count > 0

    async def unset_moving(self, session_id: str) -> bool:
        """Remove ``moving`` (move travado/abandonado)."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid}, {"$unset": {"moving": ""}}
        )
        return res.modified_count > 0

    async def clear_share(self, session_id: str) -> bool:
        """Revoga o link: remove token + validade do doc da sessão."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$unset": {"share_token": "", "share_expires_at": ""}},
        )
        return res.modified_count > 0

    async def due_for_milestones_refresh(
        self, cutoff: Any, statuses: list[str]
    ) -> list[dict[str, Any]]:
        """Sessões ativas cuja última revisão de milestones já passou do prazo.

        ``cutoff`` é o instante-limite (agora - intervalo): pega quem nunca foi
        revisada (`milestones_refreshed_at` ausente) OU cuja última revisão é
        anterior a ``cutoff``.
        """
        query: dict[str, Any] = {
            "status": {"$in": statuses},
            # Só revisa quem já foi instruído a manter o arquivo de milestones
            # (ver `instruct_milestones`) — sessão nova ainda não tem o que
            # revisar, e isso evita competir por input com um scheduled_command
            # que possa estar disparando ao mesmo tempo pra essa mesma sessão.
            "milestones_instructed_at": {"$exists": True},
            # Sessão "Comando" é só shell (sem agente) — nunca revisa milestones.
            "agent_type": {"$ne": "command"},
            "$or": [
                {"milestones_refreshed_at": {"$exists": False}},
                {"milestones_refreshed_at": {"$lt": cutoff}},
            ],
        }
        return [doc async for doc in self._collection.find(query)]

    async def due_ephemeral(self, cutoff: Any) -> list[dict[str, Any]]:
        """Sessões efêmeras (UI "Rápida") sem atividade há mais de ``cutoff``.

        Critério: ``origin="ephemeral"`` + ``last_activity_at < cutoff`` (ou
        ausente — sessão nunca tocada desde a criação). Não filtra por status
        porque faz sentido apagar mesmo as que ficaram paradas em "waiting_input"
        ou "detached" — efêmeras não são "missões longas".
        """
        query: dict[str, Any] = {
            "origin": "ephemeral",
            "$or": [
                {"last_activity_at": {"$exists": False}},
                {"last_activity_at": {"$lt": cutoff}},
            ],
        }
        return [doc async for doc in self._collection.find(query)]

    async def mark_milestones_refreshed(self, session_id: str, when: Any) -> None:
        """Grava o timestamp da última revisão automática de milestones."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return
        await self._collection.update_one(
            {"_id": oid}, {"$set": {"milestones_refreshed_at": when}}
        )

    # ------------------------------------------------------------------ #
    # PONTE DE COMANDO (``agent_type="command"``)
    # ------------------------------------------------------------------ #

    async def set_bridge(
        self,
        session_id: str,
        owner: str | None = None,
        keep_alive: bool | None = None,
        alias: str | None = None,
        now: Any = None,
    ) -> bool:
        """Grava a DONA da ponte de comando + zera o relógio de atividade.

        ``owner`` = tmux_name da sessão que está usando a ponte (cascata:
        quando a dona morre, a ponte morre junto). É SEMPRE gravado — ``None``
        significa "ninguém é dona" e LIMPA uma dona antiga (a ponte pode ter
        sido recriada com o mesmo nome de uma vida velha).
        ``keep_alive`` marca serviço de longa duração (ex.: relay) — fora de
        TTL/cascata/dedupe; só muda quando informado (None = não mexe).
        ``alias`` = nome alternativo pelo qual a ponte pode ser endereçada
        (``sf send <alias>``).

        Grava também ``last_activity_at``: é o heartbeat que segura a ponte
        viva enquanto o comando está rodando.
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        fields: dict[str, Any] = {
            "last_activity_at": now or datetime.now(UTC),
            "owner": owner,
        }
        if keep_alive is not None:
            fields["keep_alive"] = keep_alive
        update: dict[str, Any] = {"$set": fields}
        if alias:
            update["$addToSet"] = {"aliases": alias}
        res = await self._collection.update_one({"_id": oid}, update)
        return res.matched_count > 0

    async def command_bridges(self) -> list[dict[str, Any]]:
        """Sessões "comando" — as pontes. Inclui as ``keep_alive`` (serviços).

        Ordena pela última atividade (mais recente primeiro): é a ordem em que
        o CLI escolhe qual ponte reusar e qual sobreviver ao dedupe.
        """
        cursor = self._collection.find({"agent_type": "command"}).sort(
            "last_activity_at", -1
        )
        return [doc async for doc in cursor]

    async def live_command_bridge(self, host_id: str) -> dict[str, Any] | None:
        """A ponte de comando VIVA de um host (a que ``sf command`` reusa).

        ``keep_alive`` (serviço de longa duração) NÃO ocupa o slot: o shell de
        um serviço não é o shell-ponte do host.
        """
        return await self._collection.find_one(
            {
                "agent_type": "command",
                "host_id": host_id,
                "status": {"$in": ["running", "waiting_input"]},
                "keep_alive": {"$ne": True},
            },
            sort=[("last_activity_at", -1)],
        )

    async def reset_bridge_state(self, tmux_name: str) -> bool:
        """Limpa dona/keep_alive de uma ponte que vai RECOMEÇAR (mesmo nome).

        O worker faz upsert pelo ``tmux_name``: uma ponte nova com o mesmo
        nome de uma vida velha HERDA o ``owner`` (dona já morta) e o
        ``keep_alive``. Sem esta limpeza a ponte nasce com uma dona que não
        existe mais e é morta em cascata no tick seguinte.
        """
        res = await self._collection.update_one(
            {"tmux_name": tmux_name, "agent_type": "command"},
            {"$set": {"owner": None, "keep_alive": False}},
        )
        return res.matched_count > 0

    async def due_command_bridges(self, cutoff: Any) -> list[dict[str, Any]]:
        """Pontes ociosas há mais de ``cutoff`` (sem uso, sem tela nova).

        ``keep_alive`` (serviço de longa duração) fica de fora — é a ponte que
        o usuário QUER que fique aberta.
        """
        query: dict[str, Any] = {
            "agent_type": "command",
            "keep_alive": {"$ne": True},
            "$or": [
                {"last_activity_at": {"$exists": False}},
                {"last_activity_at": {"$lt": cutoff}},
            ],
        }
        return [doc async for doc in self._collection.find(query)]

    async def owned_command_bridges(self) -> list[dict[str, Any]]:
        """Pontes COM dona (``owner`` setado) — candidatas à cascata."""
        query: dict[str, Any] = {
            "agent_type": "command",
            "keep_alive": {"$ne": True},
            "owner": {"$nin": [None, ""]},
        }
        return [doc async for doc in self._collection.find(query)]

    async def alive_session_names(self, statuses: list[str]) -> set[str]:
        """tmux_name das sessões vivas (nos status dados).

        Usado na cascata: se o nome da dona não está aqui, a dona acabou e a
        ponte dela morre junto.
        """
        cursor = self._collection.find(
            {"status": {"$in": statuses}}, {"tmux_name": 1}
        )
        return {doc["tmux_name"] async for doc in cursor if doc.get("tmux_name")}

    async def list_active(self) -> list[dict[str, Any]]:
        """Sessões atualmente ativas: ``running`` ou ``waiting_input``.

        Mesmo conjunto de status considerado "ativo" pelo front e pelo
        scheduler de milestones (ver ``ACTIVE_STATUSES`` em
        ``api/app/scheduler.py``) — quem está fora disso (``detached``,
        ``completed``, ``error``, ``stopped``, ``waiting_external``) não está
        de fato trabalhando agora.
        """
        cursor = self._collection.find(
            {"status": {"$in": ["running", "waiting_input"]}}
        ).sort("created_at", -1)
        return [
            {**doc, "name": doc.get("display_name") or doc.get("tmux_name")}
            async for doc in cursor
        ]

    async def active_with_name_exists(self, name: str) -> bool:
        """Return True if an ACTIVE session (status != stopped) uses ``name``.

        Matches against either ``tmux_name`` or ``display_name`` so an
        optimistic duplicate check can reject re-creating a live session.
        """
        query: dict[str, Any] = {
            "status": {"$ne": "stopped"},
            "$or": [{"tmux_name": name}, {"display_name": name}],
        }
        return await self._collection.find_one(query) is not None

    async def name_exists(self, name: str) -> bool:
        """Return True if ANY session (any status, including stopped) uses
        ``name`` — mais amplo que ``active_with_name_exists``: usado onde o
        nome vira branch/worktree em disco, que sobrevive à sessão parar (só
        some quando a sessão é apagada).
        """
        query: dict[str, Any] = {
            "$or": [{"tmux_name": name}, {"display_name": name}],
        }
        return await self._collection.find_one(query) is not None
