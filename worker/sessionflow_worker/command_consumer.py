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

import asyncio
import glob
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aio_pika
import libtmux
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import jarvis, milestones, transcriber
from sessionflow_worker.agent_launcher import AgentType, build_launch_cmd
from sessionflow_worker.rabbit import EVENTS_QUEUE, commands_queue_name, publish
from sessionflow_worker.state import SessionState
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

SESSIONS_COLLECTION = "sessions"
SESSION_ORIGIN = "sessionflow"

logger = logging.getLogger("sessionflow_worker.command_consumer")

# TTL de frescor do comando. Comandos representam INTENÇÃO IMEDIATA do usuário;
# se ficaram na fila além disso (ex.: worker caiu e voltou horas depois), a
# intenção provavelmente já não vale — reprocessá-los "do nada" RESSUSCITA
# sessões encerradas e duplica estado. Restart normal do worker (segundos) passa
# folgado. 0/negativo desativa o guard. Configurável via env.
COMMAND_STALE_TTL_S = float(os.environ.get("SESSIONFLOW_COMMAND_TTL_S", "300"))

# Só comandos de CICLO DE VIDA (criam/ressuscitam sessão) são descartados quando
# velhos — é o que causou a duplicação. Os demais (kill/delete/rename) são
# idempotentes ou destrutivos-desejáveis e seguem mesmo atrasados.
_STALE_GUARDED_TYPES = frozenset(
    {"create", "resume", "input", "key", "audio", "file", "switch_agent"}
)

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
        "resize",
        "switch_agent",
        "jarvis_test",
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


# --- Guard de recursos (não iniciar sessão sem CPU/memória) ------------------

# Limiares (env-configuráveis). Defaults conservadores p/ o host do worker.
_MIN_FREE_MB = float(os.environ.get("SESSIONFLOW_MIN_FREE_MB", "1024"))
_MAX_LOAD_PER_CORE = float(os.environ.get("SESSIONFLOW_MAX_LOAD_PER_CORE", "6"))


def _available_mem_mb() -> float | None:
    """RAM disponível em MB (macOS via ``vm_stat``). ``None`` se indisponível."""
    try:
        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=3
        ).stdout
        page = 4096
        m = re.search(r"page size of (\d+) bytes", out)
        if m:
            page = int(m.group(1))
        free_pages = 0
        for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
            mm = re.search(rf"{key}:\s+(\d+)\.", out)
            if mm:
                free_pages += int(mm.group(1))
        return free_pages * page / (1024 * 1024)
    except Exception:  # noqa: BLE001 - best-effort; sem métrica, não bloqueia
        return None


def _load_per_core() -> float | None:
    """Load average de 1min por núcleo (``None`` se indisponível)."""
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1)
    except Exception:  # noqa: BLE001
        return None


def _ensure_codex_trust(work_dir: str) -> None:
    """Marca ``work_dir`` como 'trusted' no ``~/.codex/config.toml`` (idempotente).

    Sem isso, o codex trava no prompt 'Do you trust the contents of this
    directory?' ao subir num diretório novo — o SessionFlow não conseguia
    lançá-lo de forma autônoma. Cobre o path dado e o realpath (macOS: /tmp ->
    /private/tmp). Best-effort: nunca derruba o launch.
    """
    try:
        cfg = os.path.expanduser("~/.codex/config.toml")
        paths = {os.path.abspath(os.path.expanduser(work_dir))}
        try:
            paths.add(os.path.realpath(os.path.expanduser(work_dir)))
        except OSError:
            pass
        existing = ""
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as f:
                existing = f.read()
        add = "".join(
            f'\n[projects."{p}"]\ntrust_level = "trusted"\n'
            for p in paths
            if f'[projects."{p}"]' not in existing
        )
        if add:
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with open(cfg, "a", encoding="utf-8") as f:
                f.write(add)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("codex trust: falha ao marcar %r", work_dir, exc_info=True)


def _resource_block_reason() -> str | None:
    """Motivo p/ RECUSAR iniciar uma sessão agora, ou ``None`` se há folga.

    Fail-open: métrica indisponível não bloqueia. Checa memória livre e carga
    de CPU contra os limiares configuráveis.
    """
    mem = _available_mem_mb()
    if mem is not None and mem < _MIN_FREE_MB:
        return (
            f"memória insuficiente pra iniciar ({int(mem)} MB livres < "
            f"{int(_MIN_FREE_MB)} MB). Pare/elimine sessões e tente de novo."
        )
    load = _load_per_core()
    if load is not None and load > _MAX_LOAD_PER_CORE:
        return (
            f"CPU sobrecarregada pra iniciar (carga {load:.1f}/núcleo > "
            f"{_MAX_LOAD_PER_CORE:.0f}). Aguarde baixar e tente de novo."
        )
    return None


def _command_age_s(command: dict[str, Any]) -> float | None:
    """Idade do comando em segundos a partir de ``requested_at`` (ISO-8601).

    ``None`` se ausente/inválido (sem timestamp não há como julgar frescor;
    o guard então deixa passar — fail-open). Tolera o sufixo ``Z``.
    """
    raw = command.get("requested_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now() - ts).total_seconds()


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
    host_id:
        Identidade deste host (multi-host) — usado pra (a) consumir só a
        fila de comandos DESTE host e (b) estampar ``host_id`` nas sessões
        que este worker CRIA (``_handle_create``).
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
        host_id: str,
        runtime: TmuxRuntime | None = None,
        collection: str = SESSIONS_COLLECTION,
        server: libtmux.Server | None = None,
    ) -> None:
        self._channel = channel
        self._db = db
        self._host_id = host_id
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

    async def _language_code(self) -> str | None:
        """Código ISO-639-1 do idioma da app p/ FORÇAR na transcrição de áudio.

        Lê ``app_settings.language`` (default ``pt-BR`` → ``"pt"``). Devolve
        ``"pt"``/``"es"``/``"en"``… ou ``None`` (auto-detect) p/ idiomas não
        mapeados. Best-effort: falha de leitura cai em PT (default do app).
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
            return "pt"
        if key in ("es", "es-es", "español", "espanhol"):
            return "es"
        if key in ("en", "en-us", "en-gb", "english", "inglês", "ingles"):
            return "en"
        return None  # idioma desconhecido: deixa o Whisper auto-detectar.

    def _remote_ssh_config(self) -> tuple[str, str, str, str] | None:
        """Config do SSH p/ abrir terminal de sessão de OUTRO host (via túnel).

        ``None`` se este worker não tem o acesso remoto configurado — nesse
        caso ``_handle_open_terminal`` recusa com erro claro em vez de tentar
        e falhar silenciosamente. Só o worker do Mac declara isso hoje (é o
        único com Terminal.app/osascript); os envs apontam pro proxy local
        ``cloudflared access tcp`` do túnel do host remoto.
        """
        host = os.environ.get("SESSIONFLOW_REMOTE_SSH_HOST")
        port = os.environ.get("SESSIONFLOW_REMOTE_SSH_PORT")
        user = os.environ.get("SESSIONFLOW_REMOTE_SSH_USER")
        if not (host and port and user):
            return None
        distro = os.environ.get("SESSIONFLOW_REMOTE_WSL_DISTRO", "Ubuntu")
        return host, port, user, distro

    def _local_attach_cmd(self, name: str) -> str:
        """Monta ``tmux [-L socket] attach -t <name>`` da sessão NESTE host."""
        if not self._runtime.has_session(name):
            raise CommandError(f"sessão tmux {name!r} não existe")

        # Monta ``tmux [-L socket] attach -t <name>`` com o MESMO socket do server.
        socket_name = getattr(self._server, "socket_name", None)
        base = ["tmux"]
        if socket_name and socket_name != "default":
            base += ["-L", socket_name]
        # O app fixa a janela em ``window-size manual`` (tamanho do celular). Ao
        # anexar no Terminal grande do Mac isso deixa o conteúdo num quadradinho
        # com o resto vazio. Voltamos p/ ``largest`` → a janela passa a seguir o
        # MAIOR cliente anexado (o Terminal do Mac) e preenche a tela. Quando o
        # celular reabre a sessão, o app reassume o ``manual`` no tamanho mobile.
        try:
            subprocess.run(
                base + ["set-option", "-t", name, "-w", "window-size", "largest"],
                check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001 - best-effort
            pass
        parts = list(base)
        parts += ["attach", "-t", name]
        # shlex.quote usa aspas SIMPLES p/ nomes com espaço (ex. "3 2 1 BANK"),
        # então a string não tem aspas duplas → segura dentro do AppleScript.
        return " ".join(shlex.quote(p) for p in parts)

    def _remote_attach_cmd(self, name: str) -> str:
        """Monta o SSH (via túnel Cloudflare) que anexa numa sessão de OUTRO host.

        Passa pelo proxy local ``cloudflared access tcp`` (não pela LAN direta
        — ver ``_remote_ssh_config``), então funciona de qualquer rede. O SSH
        do Windows cai no ``cmd.exe`` por padrão; ``wsl.exe -d <distro> --
        tmux attach`` roda o tmux de dentro do WSL2 a partir daí.
        """
        config = self._remote_ssh_config()
        if config is None:
            raise CommandError(
                "abrir terminal de outro host requer SESSIONFLOW_REMOTE_SSH_"
                "HOST/PORT/USER configurados neste worker"
            )
        host, port, user, distro = config
        parts = [
            "ssh", "-o", "StrictHostKeyChecking=no", "-p", port,
            f"{user}@{host}", "-t",
            "wsl.exe", "-d", distro, "--", "tmux", "attach", "-t", name,
        ]
        return " ".join(shlex.quote(p) for p in parts)

    async def _handle_open_terminal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Abre a sessão num Terminal do Mac (``tmux attach``) p/ uso lado a lado.

        O worker roda no Mac, então usamos ``osascript`` p/ abrir o Terminal.app
        (ou o app em ``SESSIONFLOW_TERMINAL_APP``) numa nova janela já anexada à
        sessão tmux. Vários clientes podem anexar a mesma sessão (espelhada), de
        modo que o que aparece no app e no terminal é o MESMO. Best-effort.

        ``session_host_id`` no payload (multi-host, AD-011): a API SEMPRE roteia
        ``open_terminal`` pro worker do Mac (só ele tem Terminal.app/osascript),
        mesmo quando a sessão é de OUTRO host — esse campo diz qual é o host
        real da sessão. Diferente do ``self._host_id`` (o Mac) → a janela abre
        aqui, mas o comando dentro dela é um SSH (via túnel) pro host certo,
        não um ``tmux attach`` local (que atacaria o tmux ERRADO).
        """
        name = payload.get("name")
        if not name:
            raise CommandError("open_terminal requer 'name'")

        session_host_id = payload.get("session_host_id")
        if session_host_id and session_host_id != self._host_id:
            attach_cmd = self._remote_attach_cmd(name)
        else:
            attach_cmd = self._local_attach_cmd(name)

        # Título amigável da aba (nome de exibição) p/ achar entre várias abas.
        # Sanitiza: sem aspas duplas/quebras (vai embutido em string AppleScript).
        title = str(payload.get("title") or name)
        title = re.sub(r'[\n\r"]', " ", title).strip()[:60] or name

        term_app = os.environ.get("SESSIONFLOW_TERMINAL_APP", "Terminal")
        # JANELA nova, limpa e titulada, já anexada à sessão.
        #
        # Por que NÃO aba: no Terminal.app, criar aba exige ``Cmd+T`` via System
        # Events, e foi medido que nesse setup o Cmd+T abre uma JANELA, não uma
        # aba (windows 6→7) — resultado: sobrava "aba/janela vazia" + a sessão em
        # outra janela. ``do script`` (sem alvo) abre UMA janela de forma
        # determinística, sem depender de Acessibilidade. (Para abas de verdade,
        # o iTerm2 expõe ``create tab`` no AppleScript — trocar via
        # SESSIONFLOW_TERMINAL_APP se um dia for instalado.)
        #
        # ORDEM IMPORTA: ``do script`` ANTES de ``activate``. Com o Terminal.app
        # fechado, ``activate`` sozinho já lança o app, que abre sua janela
        # padrão (shell em branco); o ``do script`` seguinte então abre uma
        # SEGUNDA janela (a anexada) — resultado: duas janelas por clique.
        # Chamando ``do script`` primeiro, ele lança o Terminal (se preciso) E
        # roda o comando na mesma janela criada pro lançamento — uma só.
        # ``activate`` depois só traz a janela existente pra frente.
        script = (
            f'tell application "{term_app}"\n'
            f'  set _t to do script "{attach_cmd}"\n'
            f"  activate\n"
            f"  try\n"
            f'    set custom title of _t to "{title}"\n'
            f"  end try\n"
            f"end tell\n"
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"falha ao abrir o terminal: {exc}") from exc
        return {"name": name, "note": f"terminal aberto ({term_app})"}

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

        # Guard de comando EXPIRADO: comandos de ciclo de vida que ficaram presos
        # na fila além do TTL (worker caiu/voltou) NÃO são reprocessados — era o
        # que ressuscitava sessões encerradas e duplicava. No-op idempotente.
        if COMMAND_STALE_TTL_S > 0 and ctype in _STALE_GUARDED_TYPES:
            age = _command_age_s(command)
            if age is not None and age > COMMAND_STALE_TTL_S:
                logger.warning(
                    "comando %r (%s) descartado: expirado (%.0fs > TTL %.0fs)",
                    ctype, command_id, age, COMMAND_STALE_TTL_S,
                )
                if command_id:
                    self._processed.add(command_id)
                return await self._emit(
                    command_id, ctype, ok=True, skipped="stale", age_s=round(age),
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
            # o mesmo erro em loop e segue vivo. Logada (antes era silenciosa
            # fora do evento publicado) pra não depender só do cliente ver o
            # erro — facilita diagnosticar falhas de resume/create em campo.
            logger.warning(
                "comando %r (%s) falhou: %s", ctype, command_id, exc, exc_info=True
            )
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
        if ctype == "resize":
            return await self._handle_resize(payload)
        if ctype == "switch_agent":
            return await self._handle_switch_agent(payload)
        if ctype == "jarvis_test":
            return await self._handle_jarvis_test(payload)
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
        # tmux_name da sessão PAI (chefe que delegou via `sf delegate`), ou None.
        # Gravado no doc p/ linkar pai→filho; imutável após a criação (o
        # discovery NÃO mexe neste campo no reconcile).
        parent = payload.get("parent") or None

        # Dedupe explícito de nome duplicado (o new_session também valida, mas
        # damos uma mensagem de erro clara e específica do consumer).
        if self._runtime.has_session(name):
            raise CommandError(f"sessão tmux {name!r} já existe")

        # Não inicia se o host não tem CPU/memória sobrando (evita derrubar tudo).
        reason = _resource_block_reason()
        if reason:
            raise CommandError(reason)

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
            name=display,
        )
        if agent_type is AgentType.CODEX:
            _ensure_codex_trust(work_dir)  # evita o prompt de "confiar no diretório"
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
                    "parent": parent,
                    "host_id": self._host_id,
                    "updated_at": now,
                    "last_activity_at": now,
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
        # NÃO mexe em display_name: o nome "falado/exibição" é editado à parte
        # (endpoint próprio) e pode diferir do nome técnico do tmux.
        await self._sessions.update_one(
            {"tmux_name": old},
            {"$set": {"tmux_name": new, "updated_at": _now()}},
        )
        return {"old": old, "new": new}

    async def _handle_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not name:
            raise CommandError("resume requer 'name'")

        # Sessão AINDA VIVA (agente de fato rodando no pane): sem TTY no worker
        # não há attach real; só reconciliamos o estado (o attach é do
        # cliente/UI). Checar só ``has_session`` não basta — o pane pode
        # existir com um shell morto dentro (ex.: o agente falhou ao subir na
        # 1ª tentativa, binário ausente etc.), daí "existir" não é "estar
        # rodando" e precisa recriar/relançar mesmo assim.
        if self._runtime.has_session(name) and self._runtime.agent_type(
            name
        ) is not AgentType.UNKNOWN:
            await self._sessions.update_one(
                {"tmux_name": name},
                {"$set": {"status": SessionState.RUNNING.value, "updated_at": _now()}},
                upsert=True,
            )
            return {"name": name, "note": "resumed (already alive)"}

        # Sessão MORTA (stopped) ou pane vivo sem agente: recria/relança.
        return await self._recreate_and_relaunch(name)

    async def _recreate_and_relaunch(self, name: str) -> dict[str, Any]:
        """RELANÇA o agente, reusando os parâmetros salvos no doc (work_dir /
        agent / model / effort). É o que "Retomar" faz.

        Se o pane tmux já existe (mas sem agente vivo dentro), reaproveita o
        pane e só reenvia o comando de lançamento — ``new_session`` recusa
        criar em cima de um nome já existente.
        """
        doc = await self._sessions.find_one({"tmux_name": name})
        if not doc:
            raise CommandError(f"não é possível retomar: sessão {name!r} desconhecida")
        work_dir = doc.get("work_dir")
        if not work_dir:
            raise CommandError(f"não é possível retomar {name!r}: sem work_dir salvo")
        # Mesmo guard do create: não ressuscita agente sem CPU/memória sobrando.
        reason = _resource_block_reason()
        if reason:
            raise CommandError(reason)

        agent_type = _coerce_agent_type(doc.get("agent_type"))
        model = doc.get("model")
        effort = doc.get("effort")

        # new_session expande ``~`` e valida o diretório (erro tipado). Só cria
        # um pane novo se não sobrou um (morto) do lançamento anterior.
        tmux_id = None
        if not self._runtime.has_session(name):
            info = self._runtime.new_session(name, work_dir)
            tmux_id = info.id

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
            name=doc.get("display_name") or name,
        )
        if agent_type is AgentType.CODEX:
            _ensure_codex_trust(work_dir)  # evita o prompt de "confiar no diretório"
        self._send_keys(name, launch_cmd)

        now = _now()
        set_fields: dict[str, Any] = {
            "status": SessionState.RUNNING.value,
            "updated_at": now,
        }
        if tmux_id is not None:
            set_fields["tmux_id"] = tmux_id
        await self._sessions.update_one(
            {"tmux_name": name},
            {"$set": set_fields},
            upsert=True,
        )
        return {
            "name": name,
            "note": "recreated" if tmux_id else "relaunched-in-pane",
            "launch_cmd": launch_cmd,
        }

    async def _await_agent_ready(self, name: str, timeout: float = 20.0) -> None:
        """Espera o agente (ex.: Claude Code) terminar de subir após um relaunch.

        Poll de ``pane_current_command``: enquanto for um SHELL (zsh/bash/…), o
        agente ainda não iniciou. Quando vira outro processo (ex.: ``node``),
        dá um respiro extra p/ o TUI renderizar a caixa de input antes de a
        gente injetar texto. Best-effort (não levanta; segue mesmo se estourar).
        """
        shells = {"zsh", "-zsh", "bash", "-bash", "sh", "-sh", "fish", "login", "tmux"}
        socket_name = getattr(self._server, "socket_name", None)
        base = ["tmux"]
        if socket_name and socket_name != "default":
            base += ["-L", socket_name]
        cmd = base + ["display-message", "-p", "-t", name, "#{pane_current_command}"]
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                out = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5,
                ).stdout.strip().lower()
            except Exception:  # noqa: BLE001
                out = ""
            if out and out not in shells:
                await asyncio.sleep(2.5)  # respiro p/ o TUI ficar pronto
                return
            await asyncio.sleep(0.5)

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
        # Sessão PARADA? Mandar msg deve INICIAR e entregar a mensagem. O relaunch
        # + espera do agente ficar pronto leva alguns segundos, então roda em
        # BACKGROUND (não bloqueia o consumer) e injeta quando pronto. (Só p/
        # submissão real; digitação ao vivo não ressuscita nada.)
        if enter and not self._runtime.has_session(name):
            await self._mark_working(name)  # UI já vira "rodando"
            asyncio.create_task(self._resume_and_send(name, text))
            return {"name": name, "note": "resuming"}
        if enter:
            await self._type_and_submit(name, text)
        else:
            self._send_keys(name, text, enter=False)
        # Submeter (enter) é INTERAÇÃO do usuário → marca atividade na hora (a
        # tela também mudaria em ~6s, mas isso deixa o "última atividade" imediato).
        # Digitação ao vivo (enter=False) é transitória, não conta.
        if enter:
            await self._sessions.update_one(
                {"tmux_name": name}, {"$set": {"last_activity_at": _now()}}
            )
            await self._mark_working(name)  # respondeu → agente trabalha
        return {"name": name}

    async def _resume_and_send(self, name: str, text: str) -> None:
        """Retoma a sessão parada, espera o agente subir e injeta a ``text``.

        Roda em background (não bloqueia o consumer). Best-effort: nunca levanta.
        """
        try:
            await self._recreate_and_relaunch(name)
            await self._await_agent_ready(name)
            await self._type_and_submit(name, text)
            await self._sessions.update_one(
                {"tmux_name": name}, {"$set": {"last_activity_at": _now()}}
            )
        except Exception:  # noqa: BLE001 - best-effort
            logger.warning("resume_and_send falhou para %r", name, exc_info=True)

    async def _handle_resize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Redimensiona a janela do tmux (colunas×linhas) p/ caber na área do
        cliente — o agente reflui e usa a largura toda (monitor grande etc.).

        ``window-size manual`` (por-janela) faz o tamanho forçado valer mesmo sem
        cliente anexado, sem afetar outras sessões. Best-effort.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("resize requer 'name'")
        try:
            cols = int(payload.get("cols", 0))
            rows = int(payload.get("rows", 0))
        except (TypeError, ValueError) as exc:
            raise CommandError("resize requer cols/rows inteiros") from exc
        cols = max(40, min(400, cols))
        rows = max(10, min(200, rows))
        socket_name = getattr(self._server, "socket_name", None)
        base = ["tmux"]
        if socket_name and socket_name != "default":
            base += ["-L", socket_name]
        try:
            subprocess.run(
                base + ["set-option", "-t", name, "-w", "window-size", "manual"],
                check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                base + ["resize-window", "-t", name, "-x", str(cols), "-y", str(rows)],
                check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            raise CommandError(f"falha ao redimensionar: {exc}") from exc
        return {"name": name, "cols": cols, "rows": rows}

    # -- troca de provedor (switch_agent) -----------------------------------

    _SWITCH_ACTIVITY = "Trocando provedor…"
    # Shells reconhecidos no pane (agente NÃO está rodando).
    _SHELLS = frozenset(
        {"zsh", "-zsh", "bash", "-bash", "sh", "-sh", "fish", "login", "tmux"}
    )

    async def _handle_switch_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Troca o PROVEDOR da sessão (mesmo tmux/registro) com handoff de contexto.

        Valida tudo ANTES de mexer no agente atual (inclusive o guard de
        recursos — se o host não tem folga pro novo agente, falha aqui e a
        sessão continua com o antigo). O fluxo pesado (pedir handoff, derrubar
        o agente, subir o novo e injetar o contexto) roda em BACKGROUND
        (:meth:`_switch_agent_flow`), como o ``_resume_and_send``.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("switch_agent requer 'name'")
        agent_type = _coerce_agent_type(payload.get("agent_type"))
        model = payload.get("model")
        effort = payload.get("effort")
        if agent_type is AgentType.GEMINI:
            effort = None  # gemini não tem dimensão de esforço

        if not self._runtime.has_session(name):
            raise CommandError(
                f"sessão tmux {name!r} não está rodando (retome antes de trocar)"
            )
        doc = await self._sessions.find_one({"tmux_name": name})
        if not doc:
            raise CommandError(f"sessão {name!r} desconhecida")
        work_dir = doc.get("work_dir")
        if not work_dir:
            raise CommandError(f"sessão {name!r} sem work_dir salvo")
        if doc.get("switching"):
            raise CommandError(f"sessão {name!r} já está trocando de provedor")

        # Guard de recursos ANTES de derrubar o agente atual: se bloquear,
        # falha aqui e a sessão fica intacta (nunca sem agente).
        reason = _resource_block_reason()
        if reason:
            raise CommandError(reason)

        now = _now()
        await self._sessions.update_one(
            {"tmux_name": name},
            {"$set": {
                "switching": True,
                "activity": self._SWITCH_ACTIVITY,
                "updated_at": now,
            }},
        )
        asyncio.create_task(
            self._switch_agent_flow(name, agent_type, model, effort, doc)
        )
        return {"name": name, "note": "switching", "to": agent_type.value}

    async def _switch_agent_flow(
        self,
        name: str,
        agent_type: AgentType,
        model: str | None,
        effort: str | None,
        doc: dict[str, Any],
    ) -> None:
        """Fluxo completo da troca de provedor (roda em background).

        1. pede ao agente ATUAL um handoff-resumo em
           ``<work_dir>/.sessionflow/handoff/switch-<name>.md`` (fallback:
           captura da tela+scrollback);
        2. exporta a CONVERSA da sessão anterior (transcript-<name>.md) — do
           JSONL nativo do Claude quando a origem é claude, ou do scrollback
           (bruto) nos demais;
        3. gera o índice de skills da máquina (skills-index-<name>.md) e, se o
           destino é codex, sincroniza ~/.claude/skills → ~/.codex/skills;
        4. encerra o agente atual SEM matar o tmux (Ctrl-C até voltar ao shell);
        5. lança o novo provedor no MESMO pane (claude→claude retoma a conversa
           nativa exata via --resume; indo pra claude gera claude_session_id);
        6. injeta a mensagem de contexto (referencia transcript + skills e
           embute o handoff) e atualiza o doc. Best-effort: nunca levanta.
        """
        old_agent = str(doc.get("agent_type") or "desconhecido")
        work_dir = str(doc.get("work_dir"))
        try:
            handoff = await self._collect_handoff(name, work_dir)
            transcript_path = self._write_transcript(name, work_dir, doc)
            skills_path = self._write_skills_index(name, work_dir)
            if agent_type is AgentType.CODEX:
                self._sync_skills_to_codex()

            if not await self._shutdown_agent(name):
                # Não derrubou o agente atual: ABORTA sem lançar o novo — a
                # sessão continua utilizável com o provedor antigo (nunca
                # digitamos o launch dentro do agente vivo).
                raise CommandError(
                    "não consegui encerrar o agente atual; a sessão segue no "
                    f"provedor {old_agent}"
                )

            lang_instruction = await self._language_instruction()
            claude_session_id = doc.get("claude_session_id")
            resume = False
            if agent_type is AgentType.CLAUDE:
                if claude_session_id:
                    # Voltando pro claude: retoma a conversa nativa EXATA.
                    resume = True
                else:
                    # Indo pro claude pela 1ª vez: fixa o id da conversa nova.
                    claude_session_id = str(uuid.uuid4())
            launch_cmd = build_launch_cmd(
                agent_type,
                model,
                effort,
                resume=resume,
                lang_instruction=lang_instruction,
                session_id=(
                    claude_session_id if agent_type is AgentType.CLAUDE else None
                ),
                name=doc.get("display_name") or name,
            )
            if agent_type is AgentType.CODEX:
                _ensure_codex_trust(work_dir)
            self._send_keys(name, launch_cmd)

            now = _now()
            update: dict[str, Any] = {
                "agent_type": agent_type.value,
                "model": model,
                "effort": effort,
                "status": SessionState.RUNNING.value,
                "updated_at": now,
                "last_activity_at": now,
            }
            if agent_type is AgentType.CLAUDE and claude_session_id:
                update["claude_session_id"] = claude_session_id
            await self._sessions.update_one({"tmux_name": name}, {"$set": update})

            await self._await_agent_ready(name)
            # Respiro extra pro TUI assentar (o boot do codex é mais lento).
            await asyncio.sleep(20.0 if agent_type is AgentType.CODEX else 10.0)

            parts = [
                f"Você está ASSUMINDO esta sessão, que antes rodava no "
                f"provedor {old_agent}.",
            ]
            if transcript_path:
                parts.append(
                    f"A conversa COMPLETA da sessão anterior está em "
                    f"{transcript_path} — leia esse arquivo antes de continuar."
                )
            if skills_path:
                parts.append(
                    f"As ferramentas/skills desta máquina estão indexadas em "
                    f"{skills_path} (scripts executáveis via shell; leia o "
                    f"SKILL.md da que precisar)."
                )
            parts.append(f"Resumo/handoff do trabalho até aqui:\n{handoff}")
            parts.append(
                "O diretório do projeto é o mesmo (memória/arquivos "
                "preservados). Continue de onde parou. Responda em português "
                "do Brasil."
            )
            await self._type_and_submit(name, "\n".join(parts))
            await self._emit(
                None, "switch_agent", ok=True, name=name, phase="done",
                from_agent=old_agent, to_agent=agent_type.value,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("switch_agent falhou para %r", name, exc_info=True)
            try:
                await self._emit(
                    None, "switch_agent", ok=False, name=name,
                    phase="done", error=str(exc),
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                await self._sessions.update_one(
                    {"tmux_name": name},
                    {"$set": {"updated_at": _now()},
                     "$unset": {"switching": ""}},
                )
                # Limpa o rótulo transitório só se ainda for o nosso (o
                # discovery pode já ter escrito a atividade real do novo agente).
                await self._sessions.update_one(
                    {"tmux_name": name, "activity": self._SWITCH_ACTIVITY},
                    {"$set": {"activity": None}},
                )
            except Exception:  # noqa: BLE001
                pass

    def _handoff_dir(self, work_dir: str) -> Path:
        return Path(os.path.expanduser(work_dir)) / ".sessionflow" / "handoff"

    async def _collect_handoff(self, name: str, work_dir: str) -> str:
        """Pede o handoff ao agente ATUAL e espera o arquivo aparecer/estabilizar.

        Poll de ~2s até 90s; "estável" = tamanho > 0 igual em 2 leituras
        seguidas. Se não vier no prazo, cai na captura bruta da tela+scrollback
        (últimos ~6000 chars). Devolve o texto do contexto (corta em ~8000).
        """
        handoff_dir = self._handoff_dir(work_dir)
        handoff_path = handoff_dir / f"switch-{name}.md"
        try:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            if handoff_path.exists():
                handoff_path.unlink()  # espera um handoff FRESCO desta troca
        except OSError:
            pass

        instruction = (
            "Vamos trocar o provedor desta sessão. Escreva AGORA um handoff "
            f"completo do contexto em {handoff_path}: o que está sendo feito, "
            "decisões tomadas, arquivos tocados, estado atual e próximos "
            "passos. Seja objetivo e completo. Depois de gravar, não faça "
            "mais nada."
        )
        await self._type_and_submit(name, instruction)

        deadline = asyncio.get_event_loop().time() + 90.0
        last_size = -1
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2.0)
            try:
                size = handoff_path.stat().st_size
            except OSError:
                continue
            if size > 0 and size == last_size:
                try:
                    text = handoff_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                except OSError:
                    text = ""
                if text:
                    return text[:8000]
            last_size = size

        raw = self._capture_pane_text(name)
        return (
            "(o agente anterior não gravou o handoff a tempo; segue a captura "
            "bruta do terminal)\n" + raw[-6000:]
        )

    def _capture_pane_text(self, name: str) -> str:
        """Tela visível + scrollback do pane via ``tmux capture-pane`` (host).

        Usa o MESMO socket do server (como o :meth:`_send_wheel`). Best-effort:
        devolve "" se o tmux reclamar.
        """
        socket_name = getattr(self._server, "socket_name", None)
        base = ["tmux"]
        if socket_name and socket_name != "default":
            base += ["-L", socket_name]
        for args in (
            ["capture-pane", "-p", "-S", "-2000", "-t", name],
            ["capture-pane", "-p", "-t", name],
        ):
            try:
                res = subprocess.run(
                    base + args, capture_output=True, text=True, timeout=10
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout
            except Exception:  # noqa: BLE001 - best-effort
                continue
        return ""

    async def _shutdown_agent(self, name: str) -> bool:
        """Encerra o agente do pane SEM matar o tmux (inverso do await_ready).

        Rajadas de 3x Ctrl-C (0.35s entre elas): nos TUIs (claude/gemini/codex)
        o 1º Ctrl-C pode só LIMPAR o input pendente e o "press ctrl-c again to
        exit" tem janela curta — 2 Ctrl-C espaçados não bastavam (visto no
        teste E2E). Se as rajadas não derrubarem, ESCALA matando o processo
        FILHO do shell do pane (o agente) com TERM→KILL — o tmux e o shell
        sobrevivem. Devolve True se o pane voltou a ser um shell.
        """
        loop = asyncio.get_event_loop()

        def is_shell() -> bool:
            cmd = (self._runtime.pane_command(name) or "").strip().lower()
            return cmd in self._SHELLS

        async def wait_shell(seconds: float) -> bool:
            deadline = loop.time() + seconds
            while loop.time() < deadline:
                if is_shell():
                    await asyncio.sleep(1.0)  # respiro pro shell assentar
                    return True
                await asyncio.sleep(0.5)
            return False

        for _ in range(2):
            for _ in range(3):
                try:
                    self._send_key(name, "C-c")
                except CommandError:
                    pass
                await asyncio.sleep(0.35)
            if await wait_shell(6.0):
                return True
        for sig in ("TERM", "KILL"):
            self._kill_pane_children(name, sig)
            if await wait_shell(5.0):
                return True
        return is_shell()

    def _kill_pane_children(self, name: str, sig: str) -> None:
        """Mata os processos FILHOS do shell do pane (o agente), não o tmux.

        ``pkill -<sig> -P <pid do shell>`` derruba o agente e devolve o pane ao
        shell. Best-effort: sem PID ou sem filho, no-op.
        """
        pid = self._runtime.pane_pid(name)
        if not pid:
            return
        try:
            subprocess.run(
                ["pkill", f"-{sig}", "-P", str(pid)],
                check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001 - best-effort
            pass

    def _write_transcript(
        self, name: str, work_dir: str, doc: dict[str, Any]
    ) -> str | None:
        """Exporta a CONVERSA da sessão anterior para um .md legível.

        Origem claude: localiza o JSONL nativo por ``claude_session_id`` em
        ``~/.claude/projects/*/<sid>.jsonl`` (o glob pelo uuid dispensa acertar
        o slug do diretório) e converte os turnos user/assistant. Origem
        não-claude (codex/gemini/opencode): não há formato nativo estável
        mapeado — usa o scrollback capturado como "transcript bruto".
        Devolve o path gravado ou ``None``. Best-effort: nunca levanta.
        """
        try:
            handoff_dir = self._handoff_dir(work_dir)
            handoff_dir.mkdir(parents=True, exist_ok=True)
            out_path = handoff_dir / f"transcript-{name}.md"
            old_agent = str(doc.get("agent_type") or "")
            sid = doc.get("claude_session_id")

            body: str | None = None
            if old_agent == AgentType.CLAUDE.value and sid:
                pattern = os.path.expanduser(
                    f"~/.claude/projects/*/{sid}.jsonl"
                )
                matches = glob.glob(pattern)
                if matches:
                    body = _claude_jsonl_to_markdown(matches[0])
            if body is None:
                raw = self._capture_pane_text(name).strip()
                if not raw:
                    return None
                body = (
                    f"> Transcript BRUTO (captura do terminal) — o provedor "
                    f"anterior ({old_agent or 'desconhecido'}) não tem export "
                    f"de conversa mapeado.\n\n```\n{raw[-20000:]}\n```\n"
                )
            header = (
                f"# Conversa da sessão {name} (provedor anterior: "
                f"{old_agent or 'desconhecido'})\n\n"
            )
            out_path.write_text(header + body, encoding="utf-8")
            return str(out_path)
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("transcript: falha ao exportar %r", name, exc_info=True)
            return None

    def _write_skills_index(self, name: str, work_dir: str) -> str | None:
        """Gera o índice das skills da máquina (~/.claude/skills/*/SKILL.md).

        Cada entrada: nome, 1ª linha da description e caminho base. O novo
        agente (qualquer CLI) pode ler o SKILL.md e executar os scripts via
        shell. Devolve o path do índice ou ``None`` (sem skills / falha).
        """
        try:
            skills_root = Path(os.path.expanduser("~/.claude/skills"))
            entries: list[tuple[str, str, str]] = []
            if skills_root.is_dir():
                for skill_md in sorted(skills_root.glob("*/SKILL.md")):
                    meta = _skill_frontmatter(skill_md)
                    if meta:
                        entries.append((meta[0], meta[1], str(skill_md.parent)))
            if not entries:
                return None
            lines = [
                f"# Skills disponíveis nesta máquina ({len(entries)})",
                "",
                "Ferramentas/skills disponíveis nesta máquina — são scripts",
                "executáveis; você pode usá-los via shell (leia o SKILL.md da",
                "que precisar para ver uso/parâmetros).",
                "",
            ]
            for nm, desc, base in entries:
                lines.append(f"- **{nm}** — {desc or '(sem descrição)'}")
                lines.append(f"  - base: `{base}` (doc: `{base}/SKILL.md`)")
            handoff_dir = self._handoff_dir(work_dir)
            handoff_dir.mkdir(parents=True, exist_ok=True)
            out_path = handoff_dir / f"skills-index-{name}.md"
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(out_path)
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("skills-index: falha", exc_info=True)
            return None

    def _sync_skills_to_codex(self) -> int:
        """Copia skills compatíveis ~/.claude/skills/<n> → ~/.codex/skills/<n>.

        Mesmo formato SKILL.md. NÃO sobrescreve as já existentes; pula skills
        sem frontmatter válido. Idempotente e best-effort. Devolve o nº copiado.
        """
        copied = 0
        try:
            src_root = Path(os.path.expanduser("~/.claude/skills"))
            dst_root = Path(os.path.expanduser("~/.codex/skills"))
            if not src_root.is_dir():
                return 0
            dst_root.mkdir(parents=True, exist_ok=True)
            for skill_md in sorted(src_root.glob("*/SKILL.md")):
                src = skill_md.parent
                dst = dst_root / src.name
                if dst.exists():
                    continue  # não sobrescreve
                if not _skill_frontmatter(skill_md):
                    continue  # frontmatter inválido → pula
                try:
                    shutil.copytree(src, dst, symlinks=True)
                    copied += 1
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("sync skills→codex: falha", exc_info=True)
        return copied

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
        k = str(key).lower()
        # scroll-up/scroll-down → evento de RODA DO MOUSE (SGR) pro agente, que
        # então redesenha o histórico (TUIs de tela alternada, ex.: Claude Code,
        # guardam o scrollback dentro de si — o tmux não tem o que rolar). É o
        # mesmo que o touchpad faz no Mac.
        if k in ("scroll-up", "scroll-down"):
            self._send_wheel(name, up=(k == "scroll-up"))
            return {"name": name, "key": k}
        # "scroll-bottom" = pular pro fim (mais recente): manda Ctrl+End, que os
        # TUIs (ex.: Claude Code) interpretam como "ir pro fim do histórico".
        if k == "scroll-bottom":
            self._send_ctrl_end(name)
            return {"name": name, "key": k}
        tmux_key = _KEY_MAP.get(k)
        if tmux_key is None:
            raise CommandError(f"tecla não suportada: {key!r}")
        self._send_key(name, tmux_key)
        await self._mark_working(name)  # tecla num prompt TUI é resposta → trabalha
        return {"name": name, "key": key}

    async def _handle_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Anexa arquivo(s): re-rooteia os paths p/ o host e injeta no pane.

        O agente (ex.: Claude Code) lê as imagens/arquivos pelos caminhos.
        Payload novo: ``{name, paths: [...], filenames?, caption?, upload_id?}``.
        Retrocompat com o formato antigo de 1 arquivo (``{name, path, ...}``) —
        comandos já enfileirados podem chegar no formato velho.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("file requer 'name'")
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            single = payload.get("path")
            raw_paths = [single] if single else []
        if not raw_paths:
            raise CommandError("file requer 'path' ou 'paths'")

        paths: list[str] = []
        for raw in raw_paths:
            if not raw:
                continue
            resolved = self._resolve_upload_path(str(raw))
            if not os.path.isfile(resolved):
                raise CommandError(f"arquivo não encontrado: {resolved!r}")
            paths.append(resolved)
        if not paths:
            raise CommandError("file requer 'path' ou 'paths'")

        filenames = payload.get("filenames")
        if not isinstance(filenames, list) or len(filenames) != len(paths):
            single_name = payload.get("filename")
            if len(paths) == 1 and single_name:
                filenames = [single_name]
            else:
                filenames = [os.path.basename(p) for p in paths]

        # Injeta os caminhos ABSOLUTOS no pane (o agente abre/lê os arquivos),
        # separados por ESPAÇO na MESMA linha — assim o Claude Code enxerga
        # todas as imagens de uma vez. Se veio uma legenda (texto do usuário),
        # vai TUDO numa mensagem só — imagens + texto chegam juntos, sem o
        # agente concluir só pelas imagens antes do texto.
        caption = (payload.get("caption") or "").strip()
        joined = " ".join(paths)
        plural = len(paths) > 1
        if caption:
            label = "arquivos anexados" if plural else "arquivo anexado"
            message = f"{caption} ({label}: {joined})"
        elif plural:
            message = f"Arquivos anexados ({len(paths)}): {joined}"
        else:
            message = f"Arquivo anexado ({filenames[0]}): {joined}"
        # Texto e Enter SEPARADOS (bracketed-paste-safe) → submissão confiável.
        await self._type_and_submit(name, message)
        await self._mark_working(name)  # anexo é resposta → agente trabalha
        result: dict[str, Any] = {
            "name": name,
            "paths": paths,
            "filenames": filenames,
            "path": paths[0],
            "filename": filenames[0],
        }
        upload_id = payload.get("upload_id")
        if upload_id is not None:
            result["upload_id"] = upload_id
        upload_ids = payload.get("upload_ids")
        if upload_ids:
            result["upload_ids"] = upload_ids
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

        # Força o idioma do app (default PT-BR) — o auto-detect do modelo às
        # vezes escorrega para inglês transcrevendo fala em português.
        language = await self._language_code()
        try:
            text = await transcriber.transcribe(path, language=language)
        except FileNotFoundError as exc:
            raise CommandError(f"áudio não transcrito: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - falha de modelo/transcrição
            raise CommandError(f"falha ao transcrever áudio: {exc}") from exc

        if not text:
            raise CommandError("transcrição vazia: nada a injetar")

        # Enter SEPARADO + verificação (bracketed-paste-safe): era o último
        # caminho ainda mandando texto+Enter grudados — transcrições ficavam
        # presas no input do agente sem submeter.
        await self._type_and_submit(name, text)
        await self._mark_working(name)  # áudio transcrito é resposta → trabalha
        result: dict[str, Any] = {"name": name, "text": text}
        upload_id = payload.get("upload_id")
        if upload_id is not None:
            result["upload_id"] = upload_id
        return result

    # -- infra ------------------------------------------------------------

    async def _mark_working(self, name: str) -> None:
        """Usuário respondeu → se a sessão AGUARDAVA por ele, vira ``running`` na
        hora (inverte o fluxo: agora o agente trabalha e o usuário é que espera).

        Só mexe quando o status atual é de ESPERA pelo usuário
        (``waiting_input``/``waiting_external``) — não atropela outros estados. O
        discovery reconcilia depois com a tela real; isto só elimina o atraso até
        lá. Best-effort: nunca levanta.
        """
        try:
            await self._sessions.update_one(
                {
                    "tmux_name": name,
                    "status": {
                        "$in": [
                            SessionState.WAITING_INPUT.value,
                            SessionState.WAITING_EXTERNAL.value,
                        ]
                    },
                },
                {"$set": {"status": SessionState.RUNNING.value, "updated_at": _now()}},
            )
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("mark_working falhou para %r", name, exc_info=True)

    def _send_keys(self, name: str, command: str, enter: bool = True) -> None:
        """Envia ``command`` (texto literal) ao pane ativo. ``enter`` anexa Enter.

        ``literal=True`` para o texto ir cru (sem o tmux interpretar nomes de
        tecla); ``enter=False`` no modo ao vivo (sem submeter).
        """
        self._active_pane(name).send_keys(command, enter=enter, literal=True)

    async def _type_and_submit(self, name: str, text: str) -> None:
        """Digita ``text`` e SUBMETE com um Enter SEPARADO (após uma pausa).

        Por que não mandar texto+Enter juntos: TUIs com *bracketed paste* (ex.:
        Claude Code) englobam o Enter grudado no paste como uma quebra de linha —
        o texto fica no input e NÃO envia (o usuário precisava dar Enter à mão).
        Enviando o texto, esperando o paste "fechar" e então mandando o Enter como
        evento próprio, a submissão acontece de forma confiável.
        """
        self._send_keys(name, text, enter=False)
        # Pausa proporcional ao tamanho (paste maior demora mais a assentar).
        # Teto maior (1,2s): transcrições de áudio são longas e o paste demora.
        await asyncio.sleep(min(1.2, 0.15 + len(text) / 3000))
        before = self._pane_tail(name)
        self._send_key(name, "Enter")
        # VERIFICAÇÃO: se a tela não mudou após o Enter, ele foi engolido (paste
        # ainda fechando / agente lento) e o texto ficou PRESO no input — manda
        # Enter de novo (até 2x). Se o agente já reagiu (tela mudou), para.
        for _ in range(2):
            await asyncio.sleep(1.3)
            after = self._pane_tail(name)
            if not before or not after or after != before:
                break
            self._send_key(name, "Enter")

    def _pane_tail(self, name: str, lines: int = 12) -> str:
        """Últimas linhas da tela do pane (p/ detectar se o Enter submeteu).

        Best-effort: erro → ``""`` (a verificação vira no-op, sem re-Enter).
        """
        socket_name = getattr(self._server, "socket_name", None)
        cmd = ["tmux"]
        if socket_name and socket_name != "default":
            cmd += ["-L", socket_name]
        cmd += ["capture-pane", "-p", "-t", name]
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            ).stdout
            return "\n".join(out.rstrip("\n").split("\n")[-lines:])
        except Exception:  # noqa: BLE001 - best-effort
            return ""

    def _send_key(self, name: str, tmux_key: str) -> None:
        """Envia uma tecla nomeada do tmux (ex.: ``Up``, ``Enter``) SEM Enter.

        ``literal=False`` faz o tmux interpretar o nome da tecla; ``enter=False``
        evita anexar um Enter (a própria tecla já é o evento desejado).
        """
        self._active_pane(name).send_keys(tmux_key, enter=False, literal=False)

    def _send_ctrl_end(self, name: str) -> None:
        """Manda Ctrl+End (CSI 1;5F) ao pane — "pular pro fim" nos TUIs.

        Sequência xterm padrão ``ESC [ 1 ; 5 F``. Enviada como bytes crus via
        ``tmux send-keys -H``. Best-effort.
        """
        # 1b=ESC 5b=[ 31=1 3b=; 35=5 46=F
        seq = ["1b", "5b", "31", "3b", "35", "46"]
        socket_name = getattr(self._server, "socket_name", None)
        cmd = ["tmux"]
        if socket_name and socket_name != "default":
            cmd += ["-L", socket_name]
        cmd += ["send-keys", "-t", name, "-H", *seq]
        try:
            subprocess.run(
                cmd, check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            raise CommandError(f"falha ao ir pro fim: {exc}") from exc

    def _send_wheel(self, name: str, up: bool, count: int = 2) -> None:
        """Injeta ``count`` eventos de RODA DO MOUSE (SGR 1006) no pane.

        Sequência: ``ESC [ < <btn> ; <col> ; <row> M`` com btn 64 (cima) / 65
        (baixo). Enviada como bytes crus via ``tmux send-keys -H`` (libtmux não
        manda hex cru). O agente com mouse habilitado interpreta como scroll e
        redesenha — replica o touchpad no Mac. Best-effort: não derruba o
        consumer se o tmux reclamar.
        """
        # 1b=ESC 5b=[ 3c=< | 36 34=64(up)/36 35=65(down) | 3b=; 31 30=10 ... 4d=M
        btn = ["36", "34"] if up else ["36", "35"]
        seq = ["1b", "5b", "3c", *btn, "3b", "31", "30", "3b", "31", "30", "4d"]
        socket_name = getattr(self._server, "socket_name", None)
        cmd = ["tmux"]
        if socket_name and socket_name != "default":
            cmd += ["-L", socket_name]
        cmd += ["send-keys", "-t", name, "-H", *(seq * max(1, count))]
        try:
            subprocess.run(
                cmd, check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            raise CommandError(f"falha ao rolar: {exc}") from exc

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

    # -- JARVIS: testar voz (Perfil > Áudio) -------------------------------

    async def _handle_jarvis_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sintetiza e publica uma frase de teste — mesmo pipeline REAL do
        resumo falado (resumo/voz/efeito), só que com texto fixo, pra o
        usuário ouvir como a voz DESTE host soa sem esperar um evento real."""
        owner = jarvis._owner_display_name()
        greet = f", {owner}" if owner else ""
        text = jarvis._clean_for_speech(
            f"Oi{greet}. Assim que a sua voz do JARVIS vai soar por aqui."
        )
        audio = await jarvis._synth(text, self._db, self._host_id)
        if audio is None:
            raise CommandError("não consegui sintetizar o áudio de teste")
        b64, mime = audio
        await jarvis._publish(
            self._channel,
            {
                "type": "jarvis_audio",
                "session_id": "jarvis-test",
                "title": "Teste de voz",
                "text": text,
                "audio_b64": b64,
                "mime": mime,
                "at": jarvis._now_iso(),
            },
        )
        return {"note": "teste de voz enviado"}

    # -- loop -------------------------------------------------------------

    async def run(self) -> None:
        """Consome ``sessionflow.commands`` com ack manual.

        Loop enxuto: cada mensagem é decodificada e despachada para
        :meth:`handle` (que nunca propaga falha de processamento), e só então
        recebe ``ack`` manual. Mensagens com JSON inválido são descartadas
        (``ack``) para não travar a fila.
        """
        queue = await self._channel.get_queue(commands_queue_name(self._host_id))
        async with queue.iterator() as it:
            async for message in it:
                async with message.process(ignore_processed=True):
                    try:
                        command = json.loads(message.body)
                    except (ValueError, TypeError):
                        # Mensagem corrompida: ack p/ não reentregar em loop.
                        continue
                    await self.handle(command)


def _claude_jsonl_to_markdown(jsonl_path: str) -> str | None:
    """Converte o JSONL nativo do Claude Code em markdown legível (turnos).

    Cada linha relevante tem ``type`` ``user``/``assistant`` e ``message`` com
    ``role``/``content``. ``content`` pode ser string ou lista de blocos:
    ``text`` vira texto; ``tool_use`` vira "[usou ferramenta X]"; blocos de
    ``tool_result``/meta são ignorados (ruído). Devolve ``None`` se nada
    aproveitável. Best-effort: linha malformada é pulada.
    """
    turns: list[str] = []
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if rec.get("type") not in ("user", "assistant"):
                    continue
                if rec.get("isSidechain"):
                    continue  # threads de sub-agentes poluem a linha do tempo
                msg = rec.get("message") or {}
                role = msg.get("role")
                content = msg.get("content")
                pieces: list[str] = []
                if isinstance(content, str):
                    if content.strip():
                        pieces.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            txt = str(block.get("text") or "").strip()
                            if txt:
                                pieces.append(txt)
                        elif btype == "tool_use":
                            tool = block.get("name") or "?"
                            pieces.append(f"[usou ferramenta {tool}]")
                if not pieces:
                    continue
                label = "## Usuário:" if role == "user" else "## Assistente:"
                turns.append(f"{label}\n\n" + "\n\n".join(pieces))
    except OSError:
        return None
    if not turns:
        return None
    return "\n\n".join(turns) + "\n"


def _skill_frontmatter(skill_md: Path) -> tuple[str, str] | None:
    """Extrai ``(name, 1ª linha da description)`` do frontmatter de um SKILL.md.

    ``None`` se o arquivo não tem frontmatter YAML válido (``---`` ... ``---``)
    ou não tem ``name``. Parse propositalmente simples (linha a linha), sem
    depender de lib YAML.
    """
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.lstrip().startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    name = ""
    desc = ""
    for line in parts[1].splitlines():
        stripped = line.strip()
        if stripped.startswith("name:") and not name:
            name = stripped[len("name:"):].strip().strip("\"'")
        elif stripped.startswith("description:") and not desc:
            first = stripped[len("description:"):].strip().strip("\"'")
            # description multiline (``description: >``/``|``): pega a 1ª linha
            # de conteúdo indentado que vier a seguir.
            desc = "" if first in (">", "|", ">-", "|-") else first
    if not name:
        return None
    return name, desc


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
