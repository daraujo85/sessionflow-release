"""Consumer de comandos do SessionFlow (TMUX-05/06/07/09/10/11).

Consome a fila ``sessionflow.commands`` e despacha comandos de ciclo de vida
de sessões (``create`` / ``kill`` / ``rename`` / ``resume``), aplicando os
efeitos no tmux (via :class:`~sessionflow_worker.tmux_runtime.TmuxRuntime`) e
persistindo o estado no MongoDB (coleção ``sessions``). Cada comando processado
publica um evento de resultado em ``sessionflow.events``.

Formato da mensagem em ``sessionflow.commands``::

    {
        "command_id": "<uuid>",
        "type": "create" | "kill" | "rename" | "resume" | "input" | "audio",
        "payload": { ... },
        "requested_at": "<iso8601>"
    }

Decisões de design
------------------
- **Envio do launch ao pane**: o ``TmuxRuntime`` não expõe send-keys, então
  usamos ``libtmux`` diretamente — pegamos a sessão criada no ``server`` e
  chamamos ``session.active_window.active_pane.send_keys(cmd, enter=True)``.
  Isso mantém o ``TmuxRuntime`` intacto e usa a mesma lib já adotada no projeto.
  O comando enviado é o resultado de ``build_launch_cmd`` (injetável/monkeypatch
  nos testes para não disparar a CLI real).
- **resume sem TTY**: o worker roda headless (sem terminal interativo), então
  não há como fazer um ``attach`` real ao pane. ``resume`` portanto apenas
  *reconcilia o estado*: se a sessão ainda existe no tmux (sessões detached
  continuam vivas e rodando o agente), marcamos ``running`` no Mongo; o attach
  real é responsabilidade do cliente (API/UI) que tem o TTY. Se a sessão não
  existe mais no tmux, ``resume`` falha (não há o que retomar).
- **Idempotência**: o upsert no Mongo é idempotente por ``tmux_name`` (não
  duplica documento). Além disso mantemos um *dedupe* em memória por
  ``command_id``: comandos já processados nesta instância são ignorados (no-op
  com evento de resultado deduplicado), evitando reprocessar efeitos colaterais
  em reentregas do RabbitMQ. O ``ack`` é manual e só ocorre após o
  processamento (sucesso OU falha tratada) — uma falha tratada NÃO derruba o
  consumer nem requeue infinito.
- **Erros**: qualquer falha (nome duplicado, dir inexistente, sessão
  inexistente, etc.) é capturada e publicada como evento ``{ok: false, ...}``;
  o consumer segue vivo.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aio_pika
import libtmux
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import milestones, transcriber
from sessionflow_worker.agent_launcher import AgentType, build_launch_cmd
from sessionflow_worker.rabbit import COMMANDS_QUEUE, EVENTS_QUEUE, publish
from sessionflow_worker.state import SessionState
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

SESSIONS_COLLECTION = "sessions"
SESSION_ORIGIN = "sessionflow"

# A API roda no Docker e grava uploads em ``/data/uploads/<sid>/<file>`` (path
# do CONTAINER), publicado no comando ``audio``. O Worker roda no HOST, onde
# esse path não existe — o volume mapeia para ``<repo>/data/uploads``. Aqui
# guardamos a raiz NO HOST para re-rotear o path recebido.
HOST_UPLOADS_DIR = Path(
    os.environ.get(
        "SESSIONFLOW_UPLOADS_DIR_HOST",
        str(Path(__file__).resolve().parents[2] / "data" / "uploads"),
    )
)

_VALID_TYPES = frozenset(
    {
        "create",
        "kill",
        "delete",
        "delete_task",
        "rename",
        "resume",
        "input",
        "key",
        "audio",
        "file",
        "open_terminal",
    }
)

# Teclas especiais permitidas (input do app) → nome da tecla no tmux send-keys.
# Cobre navegação de prompts TUI: setas, confirmar, marcar, cancelar, tab e
# Ctrl-C (interromper). Conjunto fechado = nada arbitrário chega ao pane.
_KEY_MAP: dict[str, str] = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "enter": "Enter",
    "space": "Space",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BSpace",
    "ctrl-c": "C-c",
}


class CommandError(Exception):
    """Erro de processamento de comando (vira evento ``{ok: false}``)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(s: str) -> str:
    """Converte um nome amigável num slug seguro p/ tmux.

    Regras (idênticas ao frontend): lowercase; NFD-normaliza e remove
    diacríticos/acentos; troca cada run de chars fora de ``[a-z0-9]`` por um
    único ``-``; tira ``-`` das pontas. Ex.: ``"Café da Manhã!"`` →
    ``"cafe-da-manha"``; ``"3 2 1 BANK"`` → ``"3-2-1-bank"``.
    """
    normalized = unicodedata.normalize("NFD", s or "")
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", stripped.lower())
    return slug.strip("-")


class CommandConsumer:
    """Despacha comandos do SessionFlow para tmux + Mongo + eventos.

    Parameters
    ----------
    channel:
        Canal aio-pika já com a topologia declarada (usado para publicar
        eventos e, em :meth:`run`, para consumir a fila de comandos).
    db:
        Database ``motor`` onde persistir o estado das sessões.
    runtime:
        Runtime tmux (injetável; default cria um ``TmuxRuntime`` novo).
    collection:
        Nome da coleção de sessões (injetável p/ testes isolados).
    server:
        ``libtmux.Server`` usado para o send-keys ao pane. Default: reusa o
        server do ``runtime`` (mesmo servidor tmux), garantindo consistência.
    """

    def __init__(
        self,
        channel: aio_pika.abc.AbstractChannel,
        db: AsyncIOMotorDatabase,
        runtime: TmuxRuntime | None = None,
        collection: str = SESSIONS_COLLECTION,
        server: libtmux.Server | None = None,
    ) -> None:
        self._channel = channel
        self._db = db
        self._runtime = runtime if runtime is not None else TmuxRuntime()
        self._collection = collection
        self._server = server if server is not None else self._runtime.server
        # Dedupe simples em memória por command_id já processado.
        self._processed: set[str] = set()

    @property
    def _sessions(self):
        return self._db[self._collection]

    async def _language_instruction(self) -> str | None:
        """Instrução de idioma p/ sessões criadas pelo app.

        Lê ``app_settings.language`` (default ``pt-BR``). Devolve a frase a ser
        injetada no system prompt do agente, ou ``None`` (ex.: inglês = default
        do CLI, sem instrução). Best-effort: falha de leitura cai no default PT.
        """
        lang = "pt-BR"
        try:
            doc = await self._db["app_settings"].find_one(
                {"_id": "app"}, {"language": 1}
            )
            if doc and doc.get("language"):
                lang = str(doc["language"])
        except Exception:  # noqa: BLE001 - best-effort
            pass
        key = lang.strip().lower()
        if key in ("pt", "pt-br", "pt_br", "português", "portugues", ""):
            return (
                "Responda SEMPRE em português do Brasil, mesmo que arquivos, "
                "comandos ou prompts apareçam em outro idioma."
            )
        if key in ("es", "es-es", "español", "espanhol"):
            return "Responde SIEMPRE en español."
        return None

    async def _handle_open_terminal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Abre a sessão num Terminal do Mac (``tmux attach``) p/ uso lado a lado.

        O worker roda no Mac, então usamos ``osascript`` p/ abrir o Terminal.app
        (ou o app em ``SESSIONFLOW_TERMINAL_APP``) numa nova janela já anexada à
        sessão tmux. Vários clientes podem anexar a mesma sessão (espelhada), de
        modo que o que aparece no app e no terminal é o MESMO. Best-effort.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("open_terminal requer 'name'")
        if not self._runtime.has_session(name):
            raise CommandError(f"sessão tmux {name!r} não existe")

        # Monta ``tmux [-L socket] attach -t <name>`` com o MESMO socket do server.
        socket_name = getattr(self._server, "socket_name", None)
        parts = ["tmux"]
        if socket_name and socket_name != "default":
            parts += ["-L", socket_name]
        parts += ["attach", "-t", name]
        # shlex.quote usa aspas SIMPLES p/ nomes com espaço (ex. "3 2 1 BANK"),
        # então a string não tem aspas duplas → segura dentro do AppleScript.
        attach_cmd = " ".join(shlex.quote(p) for p in parts)

        # Título amigável da aba (nome de exibição) p/ achar entre várias abas.
        # Sanitiza: sem aspas duplas/quebras (vai embutido em string AppleScript).
        title = str(payload.get("title") or name)
        title = re.sub(r'[\n\r"]', " ", title).strip()[:60] or name

        term_app = os.environ.get("SESSIONFLOW_TERMINAL_APP", "Terminal")
        # Modo: "tab" (default) abre numa ABA da janela atual (lado a lado, via
        # Cmd+T do System Events — exige permissão de Acessibilidade 1x); "window"
        # abre uma janela nova (sem permissão extra). Env SESSIONFLOW_TERMINAL_MODE.
        mode = os.environ.get("SESSIONFLOW_TERMINAL_MODE", "tab").lower()
        if mode == "tab":
            # activate + espera o Terminal vir à frente ANTES do Cmd+T (senão a
            # tecla pode ir p/ outro app e a aba não nasce). Roda o attach na aba
            # recém-criada (selected tab) e fixa o título nela, com delay p/ a aba
            # assentar — set custom title sobrepõe e ignora escapes de título.
            ascript = [
                "-e",
                f'tell application "{term_app}" to activate',
                "-e",
                "delay 0.35",
                "-e",
                'tell application "System Events" to keystroke "t" using command down',
                "-e",
                "delay 0.45",
                "-e",
                f'tell application "{term_app}"',
                "-e",
                f'set _t to do script "{attach_cmd}" in selected tab of front window',
                "-e",
                "delay 0.2",
                "-e",
                f'set custom title of _t to "{title}"',
                "-e",
                f'set custom title of selected tab of front window to "{title}"',
                "-e",
                "end tell",
            ]
        else:
            ascript = [
                "-e",
                f'tell application "{term_app}"',
                "-e",
                "activate",
                "-e",
                f'set _t to do script "{attach_cmd}"',
                "-e",
                f'set custom title of _t to "{title}"',
                "-e",
                "end tell",
            ]
        try:
            subprocess.Popen(
                ["osascript", *ascript],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"falha ao abrir o terminal: {exc}") from exc
        return {"name": name, "note": f"terminal aberto ({term_app}, {mode})"}

    # -- despacho ---------------------------------------------------------

    async def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        """Processa um comando e publica o evento de resultado.

        Nunca propaga exceção de processamento: falhas viram evento
        ``{ok: false, error: ...}``. Retorna o evento publicado (útil em
        testes). Reprocessamento do mesmo ``command_id`` é tratado como no-op
        idempotente.
        """
        command_id = command.get("command_id")
        ctype = command.get("type")

        if command_id and command_id in self._processed:
            return await self._emit(
                command_id, ctype, ok=True, deduplicated=True
            )

        try:
            if ctype not in _VALID_TYPES:
                raise CommandError(f"tipo de comando desconhecido: {ctype!r}")
            payload = command.get("payload") or {}
            result = await self._dispatch(ctype, payload)
            if command_id:
                self._processed.add(command_id)
            return await self._emit(command_id, ctype, ok=True, **result)
        except (CommandError, TmuxRuntimeError, ValueError) as exc:
            # Falha esperada/tratada: marca como processado p/ não reentregar
            # o mesmo erro em loop e segue vivo.
            if command_id:
                self._processed.add(command_id)
            return await self._emit(
                command_id, ctype, ok=False, error=str(exc)
            )

    async def _dispatch(
        self, ctype: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if ctype == "create":
            return await self._handle_create(payload)
        if ctype == "kill":
            return await self._handle_kill(payload)
        if ctype == "delete":
            return await self._handle_delete(payload)
        if ctype == "delete_task":
            return await self._handle_delete_task(payload)
        if ctype == "rename":
            return await self._handle_rename(payload)
        if ctype == "resume":
            return await self._handle_resume(payload)
        if ctype == "input":
            return await self._handle_input(payload)
        if ctype == "key":
            return await self._handle_key(payload)
        if ctype == "audio":
            return await self._handle_audio(payload)
        if ctype == "file":
            return await self._handle_file(payload)
        if ctype == "open_terminal":
            return await self._handle_open_terminal(payload)
        raise CommandError(f"tipo de comando desconhecido: {ctype!r}")

    # -- handlers ---------------------------------------------------------

    async def _handle_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("name"):
            raise CommandError("create requer 'name'")
        # ``display`` é o nome amigável (mostrado no app); ``name`` é o SLUG
        # seguro p/ tmux (sem espaços/acentos). Slug vazio = nome inválido.
        display = payload.get("display_name") or payload.get("name")
        name = _slugify(payload.get("name") or "")
        if not name:
            raise CommandError("nome inválido (vazio após slug)")
        work_dir = payload.get("work_dir")
        if not work_dir:
            raise CommandError("create requer 'work_dir'")

        agent_type = _coerce_agent_type(payload.get("agent_type"))
        model = payload.get("model")
        effort = payload.get("effort")

        # Dedupe explícito de nome duplicado (o new_session também valida, mas
        # damos uma mensagem de erro clara e específica do consumer).
        if self._runtime.has_session(name):
            raise CommandError(f"sessão tmux {name!r} já existe")

        # new_session valida work_dir inexistente e nome inválido (erro tipado).
        info = self._runtime.new_session(name, work_dir)

        # Idioma da sessão criada pelo app (config global): injeta no system
        # prompt p/ o agente já responder no idioma certo (default pt-BR).
        lang_instruction = await self._language_instruction()
        # UUID fixo da conversa Claude (só claude suporta): permite o Retomar
        # depois resumir a conversa EXATA via --resume, sem agarrar a conversa
        # mais recente do diretório (bug do --continue quando há outra sessão
        # na mesma pasta). Guardado no doc como claude_session_id.
        claude_session_id = (
            str(uuid.uuid4()) if agent_type is AgentType.CLAUDE else None
        )
        launch_cmd = build_launch_cmd(
            agent_type,
            model,
            effort,
            lang_instruction=lang_instruction,
            session_id=claude_session_id,
        )
        self._send_keys(name, launch_cmd)

        now = _now()
        await self._sessions.update_one(
            {"tmux_name": name},
            {
                "$set": {
                    "tmux_name": name,
                    "display_name": display,
                    "origin": SESSION_ORIGIN,
                    "status": SessionState.RUNNING.value,
                    "agent_type": agent_type.value,
                    "model": model,
                    "effort": effort,
                    "work_dir": str(work_dir),
                    "tmux_id": info.id,
                    "claude_session_id": claude_session_id,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return {"name": name, "launch_cmd": launch_cmd}

    async def _handle_kill(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not name:
            raise CommandError("kill requer 'name'")

        # kill_session levanta TmuxSessionNotFoundError se não existir.
        self._runtime.kill_session(name)

        # Preserva o documento/histórico: apenas marca stopped.
        await self._sessions.update_one(
            {"tmux_name": name},
            {"$set": {"status": SessionState.STOPPED.value, "updated_at": _now()}},
        )
        return {"name": name}

    async def _handle_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """ELIMINA a sessão de vez: mata o tmux (se vivo) e REMOVE o documento
        + dados relacionados (tasks/output/screen/events). Diferente de ``kill``
        (que só para e mantém o histórico). Some do app e do host.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("delete requer 'name'")

        # Mata o tmux se ainda existir (idempotente; ignora se já morreu).
        try:
            if self._runtime.has_session(name):
                self._runtime.kill_session(name)
        except TmuxRuntimeError:
            pass

        db = self._sessions.database
        await self._sessions.delete_one({"tmux_name": name})
        # Limpa dados relacionados (best-effort; chaves variam por coleção).
        for coll in ("tasks", "session_output", "events"):
            try:
                await db[coll].delete_many({"session_id": name})
            except Exception:  # noqa: BLE001
                pass
        try:
            await db["session_screen"].delete_many({"tmux_name": name})
        except Exception:  # noqa: BLE001
            pass
        return {"name": name, "deleted": True}

    async def _handle_delete_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apaga uma TAREFA (marco) do arquivo no host e da coleção ``tasks``.

        Payload: ``{name, work_dir, task_id}`` (``name`` = sessão/tmux_name,
        ``task_id`` = id do marco no JSON). Remove a entrada do arquivo
        ``.sessionflow/milestones.<name>.json`` (best-effort, para o sync não
        re-adicionar) e o doc correspondente em ``tasks`` (match por
        ``session_id`` + ``milestone_id``). Nunca derruba o consumer.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("delete_task requer 'name'")
        task_id = payload.get("task_id")
        if not task_id:
            raise CommandError("delete_task requer 'task_id'")
        work_dir = payload.get("work_dir") or ""

        # Remove do arquivo de marcos no host (best-effort; nunca levanta).
        removed_file = False
        try:
            removed_file = milestones.remove_milestone(work_dir, name, task_id)
        except Exception:  # noqa: BLE001 - tolerante por contrato
            removed_file = False

        # Remove o doc da coleção tasks (match por sessão + id do marco).
        db = self._sessions.database
        removed_db = False
        try:
            res = await db["tasks"].delete_many(
                {"session_id": name, "milestone_id": task_id}
            )
            removed_db = res.deleted_count > 0
        except Exception:  # noqa: BLE001 - best-effort
            removed_db = False

        return {
            "name": name,
            "task_id": task_id,
            "removed_file": removed_file,
            "removed_db": removed_db,
        }

    async def _handle_rename(self, payload: dict[str, Any]) -> dict[str, Any]:
        old = payload.get("old") or payload.get("name")
        new = payload.get("new")
        if not old or not new:
            raise CommandError("rename requer 'old'/'name' e 'new'")

        # rename_session valida nome novo e existência da sessão antiga.
        self._runtime.rename_session(old, new)

        # Preserva o _id: update por tmux_name antigo, sem recriar documento.
        await self._sessions.update_one(
            {"tmux_name": old},
            {
                "$set": {
                    "tmux_name": new,
                    "display_name": new,
                    "updated_at": _now(),
                }
            },
        )
        return {"old": old, "new": new}

    async def _handle_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not name:
            raise CommandError("resume requer 'name'")

        # Sessão AINDA VIVA (detached): sem TTY no worker não há attach real; só
        # reconciliamos o estado (o attach é do cliente/UI).
        if self._runtime.has_session(name):
            await self._sessions.update_one(
                {"tmux_name": name},
                {"$set": {"status": SessionState.RUNNING.value, "updated_at": _now()}},
                upsert=True,
            )
            return {"name": name, "note": "resumed (already alive)"}

        # Sessão MORTA (stopped): o tmux dela não existe mais. "Retomar" então
        # RECRIA a sessão e relança o agente, reusando os parâmetros salvos no
        # doc (work_dir / agent / model / effort) — é o que o usuário espera.
        doc = await self._sessions.find_one({"tmux_name": name})
        if not doc:
            raise CommandError(f"não é possível retomar: sessão {name!r} desconhecida")
        work_dir = doc.get("work_dir")
        if not work_dir:
            raise CommandError(f"não é possível retomar {name!r}: sem work_dir salvo")

        agent_type = _coerce_agent_type(doc.get("agent_type"))
        model = doc.get("model")
        effort = doc.get("effort")

        # new_session expande ``~`` e valida o diretório (erro tipado).
        info = self._runtime.new_session(name, work_dir)
        # resume=True → retoma a conversa anterior. Com claude_session_id salvo,
        # usa --resume <uuid> (a conversa EXATA dessa sessão); senão cai no
        # --continue (sessões antigas, sujeitas a agarrar a conversa errada se
        # houver outra na mesma pasta). Reinjeta o idioma (default pt-BR) p/
        # sessões criadas antes desse fluxo passarem a responder em português.
        lang_instruction = await self._language_instruction()
        claude_session_id = doc.get("claude_session_id")
        launch_cmd = build_launch_cmd(
            agent_type,
            model,
            effort,
            resume=True,
            lang_instruction=lang_instruction,
            session_id=claude_session_id,
        )
        self._send_keys(name, launch_cmd)

        now = _now()
        await self._sessions.update_one(
            {"tmux_name": name},
            {"$set": {
                "status": SessionState.RUNNING.value,
                "tmux_id": info.id,
                "updated_at": now,
            }},
            upsert=True,
        )
        return {"name": name, "note": "recreated", "launch_cmd": launch_cmd}

    async def _handle_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Injeta texto remoto na sessão via send-keys (DASH-13).

        Não persiste estado (input é efêmero); apenas envia o texto ao pane
        ativo e emite evento de resultado. Sessão inexistente vira evento de
        erro (``_send_keys`` levanta ``CommandError``), sem derrubar o consumer.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("input requer 'name'")
        text = payload.get("text")
        if not text:
            raise CommandError("input requer 'text'")

        # ``enter`` (default True): quando False, injeta o texto SEM Enter — usado
        # pelo modo "ao vivo" (encaminha o que está sendo digitado p/ o CLI
        # mostrar o autocomplete, sem submeter).
        enter = payload.get("enter", True)
        self._send_keys(name, text, enter=bool(enter))
        return {"name": name}

    async def _handle_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Envia uma TECLA ESPECIAL (seta/enter/espaço/esc/tab…) ao pane.

        Diferente de ``input`` (texto literal + Enter), serve para navegar
        prompts TUI dos agentes (pickers de ``/model``, listas de seleção,
        confirmações). Payload: ``{name, key}`` com ``key`` num conjunto
        permitido; mapeado para o nome de tecla do tmux e enviado SEM Enter.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("key requer 'name'")
        key = payload.get("key")
        if not key:
            raise CommandError("key requer 'key'")
        tmux_key = _KEY_MAP.get(str(key).lower())
        if tmux_key is None:
            raise CommandError(f"tecla não suportada: {key!r}")
        self._send_key(name, tmux_key)
        return {"name": name, "key": key}

    async def _handle_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Anexa um arquivo: re-rooteia o path p/ o host e injeta no pane.

        O agente (ex.: Claude Code) lê a imagem/arquivo pelo caminho. Payload:
        ``{name, path, filename?, upload_id?}``.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("file requer 'name'")
        path = payload.get("path")
        if not path:
            raise CommandError("file requer 'path'")
        path = self._resolve_upload_path(path)
        if not os.path.isfile(path):
            raise CommandError(f"arquivo não encontrado: {path!r}")

        filename = payload.get("filename") or os.path.basename(path)
        # Injeta o caminho ABSOLUTO no pane (o agente abre/lê o arquivo).
        self._send_keys(name, f"Arquivo anexado ({filename}): {path}")
        result: dict[str, Any] = {"name": name, "path": path, "filename": filename}
        upload_id = payload.get("upload_id")
        if upload_id is not None:
            result["upload_id"] = upload_id
        return result

    async def _handle_audio(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Transcreve áudio (Whisper) e injeta o texto na sessão (DASH-15).

        Payload: ``{name, path, upload_id?}``. Transcreve ``path`` via
        :func:`transcriber.transcribe` (await de executor — não trava o
        consumer), injeta o texto no pane via :meth:`_send_keys` e retorna o
        texto transcrito (vira evento ``input``/info). Falha de arquivo/modelo
        é convertida em ``CommandError`` → evento de erro, sem derrubar o
        consumer.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("audio requer 'name'")
        path = payload.get("path")
        if not path:
            raise CommandError("audio requer 'path'")
        path = self._resolve_upload_path(path)

        try:
            text = await transcriber.transcribe(path)
        except FileNotFoundError as exc:
            raise CommandError(f"áudio não transcrito: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - falha de modelo/transcrição
            raise CommandError(f"falha ao transcrever áudio: {exc}") from exc

        if not text:
            raise CommandError("transcrição vazia: nada a injetar")

        self._send_keys(name, text)
        result: dict[str, Any] = {"name": name, "text": text}
        upload_id = payload.get("upload_id")
        if upload_id is not None:
            result["upload_id"] = upload_id
        return result

    # -- infra ------------------------------------------------------------

    def _send_keys(self, name: str, command: str, enter: bool = True) -> None:
        """Envia ``command`` (texto literal) ao pane ativo. ``enter`` anexa Enter.

        ``literal=True`` para o texto ir cru (sem o tmux interpretar nomes de
        tecla); ``enter=False`` no modo ao vivo (sem submeter).
        """
        self._active_pane(name).send_keys(command, enter=enter, literal=True)

    def _send_key(self, name: str, tmux_key: str) -> None:
        """Envia uma tecla nomeada do tmux (ex.: ``Up``, ``Enter``) SEM Enter.

        ``literal=False`` faz o tmux interpretar o nome da tecla; ``enter=False``
        evita anexar um Enter (a própria tecla já é o evento desejado).
        """
        self._active_pane(name).send_keys(tmux_key, enter=False, literal=False)

    def _resolve_upload_path(self, path: str) -> str:
        """Resolve o path do upload de áudio para o filesystem do HOST.

        A API (no container) publica ``/data/uploads/<sid>/<file>``. Se esse
        path não existir aqui (worker no host), re-rooteia os 2 últimos
        componentes (``<sid>/<file>``) em :data:`HOST_UPLOADS_DIR`. Mantém o
        path original quando já é acessível (ex.: dev tudo no host).
        """
        p = Path(path)
        if p.is_file():
            return path
        parts = p.parts
        if len(parts) >= 2:
            candidate = HOST_UPLOADS_DIR / parts[-2] / parts[-1]
            if candidate.is_file():
                return str(candidate)
        return path  # deixa o transcriber falhar com erro claro de arquivo

    def _active_pane(self, name: str) -> Any:
        """Resolve o pane ativo da sessão ``name`` ou levanta ``CommandError``."""
        session = self._server.sessions.get(session_name=name, default=None)
        if session is None:
            raise CommandError(f"sessão {name!r} desapareceu antes do send-keys")
        window = session.active_window
        if window is None or window.active_pane is None:
            raise CommandError(f"sessão {name!r} sem pane ativo para send-keys")
        return window.active_pane

    async def _emit(
        self,
        command_id: str | None,
        ctype: str | None,
        *,
        ok: bool,
        **extra: Any,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "command_id": command_id,
            "type": ctype,
            "ok": ok,
            "emitted_at": _now().isoformat(),
            **extra,
        }
        await publish(self._channel, EVENTS_QUEUE, event)
        return event

    # -- loop -------------------------------------------------------------

    async def run(self) -> None:
        """Consome ``sessionflow.commands`` com ack manual.

        Loop enxuto: cada mensagem é decodificada e despachada para
        :meth:`handle` (que nunca propaga falha de processamento), e só então
        recebe ``ack`` manual. Mensagens com JSON inválido são descartadas
        (``ack``) para não travar a fila.
        """
        queue = await self._channel.get_queue(COMMANDS_QUEUE)
        async with queue.iterator() as it:
            async for message in it:
                async with message.process(ignore_processed=True):
                    try:
                        command = json.loads(message.body)
                    except (ValueError, TypeError):
                        # Mensagem corrompida: ack p/ não reentregar em loop.
                        continue
                    await self.handle(command)


def _coerce_agent_type(value: Any) -> AgentType:
    """Converte string/AgentType em :class:`AgentType`; rejeita unknown."""
    if isinstance(value, AgentType):
        agent = value
    elif isinstance(value, str):
        try:
            agent = AgentType(value)
        except ValueError as exc:
            raise CommandError(f"agent_type inválido: {value!r}") from exc
    else:
        raise CommandError(f"agent_type ausente ou inválido: {value!r}")

    if agent is AgentType.UNKNOWN:
        raise CommandError("agent_type 'unknown' não é lançável")
    return agent
