"""Runtime tmux (TMUX-01, TMUX-02, TMUX-09, TMUX-10, TMUX-11).

Wrapper fino sobre ``libtmux`` para as operações de ciclo de vida de sessões
tmux que o worker precisa: listar (incluindo sessões externas criadas fora do
SessionFlow), criar, matar, renomear e inspecionar.

Decisões de design
------------------
- **Sanitização de nome**: o tmux usa ``:`` e ``.`` como separadores de
  *target* (``session:window.pane``). Nomes contendo esses caracteres quebram
  os comandos. Optamos por **rejeitar** explicitamente nomes com ``.`` ou ``:``
  (em vez de substituir silenciosamente), levantando ``TmuxNameError`` com
  mensagem clara — assim o chamador escolhe conscientemente um nome válido e
  não há colisão surpresa entre, p.ex., ``foo.bar`` e ``foo-bar``. Nomes vazios
  ou só com espaços também são rejeitados.
- **Erros tipados**: operações sobre sessões inexistentes levantam
  ``TmuxSessionNotFoundError``; falhas genéricas do tmux, ``TmuxRuntimeError``.

Inferência de tipo de agente é delegada a ``agent_launcher.infer_agent_type``
sobre o ``pane_command`` do pane ativo.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import libtmux
from libtmux.exc import LibTmuxException

from sessionflow_worker.agent_launcher import AgentType, infer_agent_type

logger = logging.getLogger("sessionflow_worker.tmux_runtime")

# Caracteres que o tmux interpreta como separadores de target.
_FORBIDDEN_NAME_CHARS = (".", ":")


class TmuxRuntimeError(RuntimeError):
    """Erro genérico de runtime tmux."""


class TmuxSessionNotFoundError(TmuxRuntimeError):
    """A sessão tmux solicitada não existe."""


class TmuxNameError(TmuxRuntimeError):
    """Nome de sessão inválido para o tmux."""


# Profundidade máxima de descendentes do pane que percorremos atrás da
# cmdline do agente. Ex.: pane(zsh) -> claude -> npm exec mcp. Olhar até 3
# níveis cobre o agente direto e wrappers (ex. ``node``) sem custo relevante.
_CMDLINE_MAX_DEPTH = 3


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Snapshot de uma sessão tmux relevante para o SessionFlow."""

    name: str
    id: str
    attached: bool
    created: int | None
    pane_command: str
    pane_pid: int | None
    # cmdline completa do processo do pane + descendentes (via ps). Em geral
    # o pane roda o shell e o agente real (claude/codex/...) é um FILHO, então
    # ``pane_command`` sozinho não basta para inferir o tipo. Default "" para
    # retrocompatibilidade de chamadores/testes que constroem SessionInfo.
    process_cmdline: str = field(default="")
    # cwd do pane ativo (``#{pane_current_path}``). Permite à UI mostrar o
    # diretório de trabalho mesmo de sessões externas. Default "" para
    # retrocompatibilidade de chamadores/testes que constroem SessionInfo.
    work_dir: str = field(default="")

    @property
    def agent_type(self) -> AgentType:
        """Tipo de agente inferido a partir do pane ativo.

        Prefere a cmdline completa do processo (que enxerga o agente mesmo
        quando é filho do shell do pane) e cai para ``pane_command`` quando a
        cmdline não está disponível.
        """
        agent = infer_agent_type(self.process_cmdline)
        if agent is not AgentType.UNKNOWN:
            return agent
        return infer_agent_type(self.pane_command)


def _validate_name(name: str) -> str:
    """Valida e normaliza um nome de sessão; levanta ``TmuxNameError``."""
    stripped = name.strip()
    if not stripped:
        raise TmuxNameError("nome de sessão vazio")
    for ch in _FORBIDDEN_NAME_CHARS:
        if ch in stripped:
            raise TmuxNameError(
                f"nome de sessão inválido {name!r}: caractere {ch!r} não é "
                "permitido (tmux usa '.' e ':' como separadores de target)"
            )
    return stripped


class TmuxRuntime:
    """Wrapper das operações de sessão tmux sobre um ``libtmux.Server``."""

    def __init__(self, server: libtmux.Server | None = None) -> None:
        self._server = server if server is not None else libtmux.Server()

    @property
    def server(self) -> libtmux.Server:
        return self._server

    # -- inspeção ---------------------------------------------------------

    def has_session(self, name: str) -> bool:
        """True se existe uma sessão tmux com este nome (inclui externas)."""
        try:
            return self._server.has_session(name, exact=True)
        except Exception:  # noqa: BLE001 - tmux retorna erro p/ nome ausente
            return False

    def _get_session(self, name: str) -> libtmux.Session:
        try:
            session = self._server.sessions.get(session_name=name, default=None)
        except LibTmuxException as exc:
            # A sessão pode ter sumido entre a listagem e o lookup (sessões
            # efêmeras mortas, terminal fechado). Tratamos como ausente.
            raise TmuxSessionNotFoundError(
                f"sessão tmux {name!r} não existe"
            ) from exc
        if session is None:
            raise TmuxSessionNotFoundError(f"sessão tmux {name!r} não existe")
        return session

    def list_sessions(self) -> list[SessionInfo]:
        """Lista TODAS as sessões tmux do servidor (TMUX-01, TMUX-02).

        Inclui sessões criadas fora do SessionFlow (externas), pois o tmux
        server é compartilhado.

        **Resiliência**: uma sessão pode desaparecer ENTRE o ``list-sessions``
        e a inspeção de suas janelas/panes (``list-windows``) — ex. sessões
        efêmeras (``sfmodel-``/``sftest-``) que morrem, ou o usuário fechando um
        terminal. Nesse caso o libtmux levanta ``LibTmuxException`` (p.ex.
        ``list-windows: can't find session: $370``). Em vez de deixar essa
        exceção propagar (e derrubar o ciclo / disparar reconexão do worker),
        **pulamos** a sessão sumida e retornamos as demais válidas.
        """
        try:
            raw_sessions = list(self._server.sessions)
        except LibTmuxException:
            # Até o próprio ``list-sessions`` pode falhar se o server sumir no
            # meio; tratamos como "nenhuma sessão visível neste ciclo".
            logger.warning(
                "list_sessions: falha ao listar sessões tmux; pulando ciclo",
                exc_info=True,
            )
            return []

        infos: list[SessionInfo] = []
        for s in raw_sessions:
            info = self._to_info(s)
            if info is not None:
                infos.append(info)
        return infos

    def _to_info(self, session: libtmux.Session) -> SessionInfo | None:
        """Monta o ``SessionInfo`` de uma sessão; ``None`` se ela sumiu.

        Acessar ``active_window``/``active_pane`` dispara ``list-windows`` no
        tmux, que falha (``LibTmuxException``) se a sessão deixou de existir
        durante a iteração. Tratamos isso pulando a sessão (retornando ``None``)
        em vez de propagar e derrubar o chamador.
        """
        try:
            pane = None
            window = session.active_window
            if window is not None:
                pane = window.active_pane

            pane_command = ""
            pane_pid: int | None = None
            work_dir = ""
            if pane is not None:
                pane_command = pane.pane_current_command or ""
                pane_pid = _to_int(pane.pane_pid)
                work_dir = _collapse_home(pane.pane_current_path or "")

            return SessionInfo(
                name=session.session_name or "",
                id=session.session_id or "",
                attached=_attached_flag(session.session_attached),
                created=_to_int(session.session_created),
                pane_command=pane_command,
                pane_pid=pane_pid,
                process_cmdline=_process_cmdline(pane_pid),
                work_dir=work_dir,
            )
        except LibTmuxException:
            logger.debug(
                "_to_info: sessão %r sumiu durante a inspeção; pulando",
                getattr(session, "session_name", "?"),
                exc_info=True,
            )
            return None

    def is_attached(self, name: str) -> bool:
        """True se há ao menos um cliente acoplado à sessão (TMUX-11).

        Levanta ``TmuxSessionNotFoundError`` se a sessão não existe — é uma
        consulta por nome explícito (diferente da varredura de ``list_sessions``,
        que tolera sessões sumindo no meio).
        """
        session = self._get_session(name)
        return _attached_flag(session.session_attached)

    def pane_command(self, name: str) -> str:
        """Comando do pane ativo da sessão (para inferir o agente).

        Retorna "" (em vez de propagar) se a sessão sumiu.
        """
        try:
            session = self._get_session(name)
            window = session.active_window
            if window is None or window.active_pane is None:
                return ""
            return window.active_pane.pane_current_command or ""
        except (TmuxSessionNotFoundError, LibTmuxException):
            return ""

    def pane_pid(self, name: str) -> int | None:
        """PID do processo do pane ativo, ou None se indisponível/sumida."""
        try:
            session = self._get_session(name)
            window = session.active_window
            if window is None or window.active_pane is None:
                return None
            return _to_int(window.active_pane.pane_pid)
        except (TmuxSessionNotFoundError, LibTmuxException):
            return None

    def pane_current_path(self, name: str) -> str:
        """cwd do pane ativo da sessão (``#{pane_current_path}``).

        Home colapsado para ``~``. Retorna "" se indisponível ou se a sessão
        sumiu.
        """
        try:
            session = self._get_session(name)
            window = session.active_window
            if window is None or window.active_pane is None:
                return ""
            return _collapse_home(window.active_pane.pane_current_path or "")
        except (TmuxSessionNotFoundError, LibTmuxException):
            return ""

    def pane_process_cmdline(self, name: str) -> str:
        """cmdline completa do processo do pane + descendentes (via ps).

        O pane normalmente roda o shell; o agente real (``claude`` etc.) é um
        processo FILHO. Percorremos a árvore de processos a partir do
        ``pane_pid`` (até ``_CMDLINE_MAX_DEPTH`` níveis) e concatenamos as
        linhas de comando, de modo que ``infer_agent_type`` enxergue o token
        do agente onde quer que ele esteja. Retorna "" se indisponível.
        """
        return _process_cmdline(self.pane_pid(name))

    def agent_type(self, name: str) -> AgentType:
        """Tipo de agente inferido a partir do pane ativo da sessão.

        Usa a cmdline completa do processo (pane + filhos), com fallback para
        o ``pane_command`` quando nada é reconhecido.
        """
        agent = infer_agent_type(self.pane_process_cmdline(name))
        if agent is not AgentType.UNKNOWN:
            return agent
        return infer_agent_type(self.pane_command(name))

    # -- ciclo de vida ----------------------------------------------------

    def new_session(self, name: str, work_dir: str | Path) -> SessionInfo:
        """Cria sessão detached (``new-session -d``) em ``work_dir``.

        Valida nome (sanitização) e existência do diretório.
        """
        valid_name = _validate_name(name)
        path = Path(work_dir).expanduser()
        if not path.is_dir():
            raise TmuxRuntimeError(
                f"work_dir inexistente ou não é diretório: {work_dir!r}"
            )
        if self.has_session(valid_name):
            raise TmuxRuntimeError(f"sessão tmux {valid_name!r} já existe")
        try:
            session = self._server.new_session(
                session_name=valid_name,
                start_directory=str(path),
                detach=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise TmuxRuntimeError(
                f"falha ao criar sessão {valid_name!r}: {exc}"
            ) from exc
        info = self._to_info(session)
        if info is None:
            # A sessão recém-criada deveria estar viva; se sumiu já, é erro.
            raise TmuxRuntimeError(
                f"sessão {valid_name!r} criada mas inacessível (sumiu?)"
            )
        return info

    def kill_session(self, name: str) -> None:
        """Mata a sessão (TMUX-09). Erro tipado se não existir."""
        session = self._get_session(name)
        try:
            session.kill()
        except Exception as exc:  # noqa: BLE001
            raise TmuxRuntimeError(
                f"falha ao matar sessão {name!r}: {exc}"
            ) from exc

    def rename_session(self, old: str, new: str) -> None:
        """Renomeia sessão (TMUX-10). Valida novo nome; erro se ``old`` ausente."""
        valid_new = _validate_name(new)
        session = self._get_session(old)
        if valid_new != old and self.has_session(valid_new):
            raise TmuxRuntimeError(f"sessão tmux {valid_new!r} já existe")
        try:
            session.rename_session(valid_new)
        except Exception as exc:  # noqa: BLE001
            raise TmuxRuntimeError(
                f"falha ao renomear {old!r} -> {valid_new!r}: {exc}"
            ) from exc


def _ps_field(pids: list[int], field_name: str) -> dict[int, str]:
    """Roda ``ps -o pid=,<field>=`` para os PIDs dados; mapeia pid -> valor.

    Best-effort: qualquer falha do ``ps`` resulta em mapa vazio.
    """
    if not pids:
        return {}
    try:
        proc = subprocess.run(
            ["ps", "-o", f"pid=,{field_name}=", "-p", ",".join(map(str, pids))],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    out: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, value = line.partition(" ")
        pid = _to_int(pid_str)
        if pid is not None:
            out[pid] = value.strip()
    return out


def _child_pids(pids: list[int]) -> list[int]:
    """PIDs filhos diretos dos ``pids`` dados.

    Lista todos os processos (``ps -axo pid=,ppid=``) e filtra por ``ppid``
    contido em ``pids``. Best-effort: retorna [] se ``ps`` falhar.
    """
    if not pids:
        return []
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    wanted = set(pids)
    children: list[int] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pid = _to_int(parts[0])
        ppid = _to_int(parts[1])
        if pid is not None and ppid in wanted:
            children.append(pid)
    return children


def _process_cmdline(pane_pid: int | None) -> str:
    """Concatena a cmdline do ``pane_pid`` e seus descendentes (via ps).

    Percorre a árvore de processos em largura até ``_CMDLINE_MAX_DEPTH``
    níveis, juntando as linhas de comando com ``\\n``. Best-effort: retorna
    "" se ``pane_pid`` for None ou se o ``ps`` falhar.
    """
    if pane_pid is None:
        return ""

    seen: set[int] = set()
    frontier = [pane_pid]
    cmdlines: list[str] = []

    for _ in range(_CMDLINE_MAX_DEPTH + 1):
        frontier = [p for p in frontier if p not in seen]
        if not frontier:
            break
        seen.update(frontier)

        commands = _ps_field(frontier, "command")
        for pid in frontier:
            cmd = commands.get(pid, "").strip()
            if cmd:
                cmdlines.append(cmd)

        frontier = _child_pids(frontier)

    return "\n".join(cmdlines)


def _collapse_home(path: str) -> str:
    """Colapsa o prefixo do home do usuário para ``~`` (best-effort).

    Ex.: ``/Users/diego/proj`` -> ``~/proj``. Retorna o path inalterado se
    não estiver sob o home ou se o home não puder ser resolvido.
    """
    if not path:
        return ""
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return path
    if not home:
        return path
    if path == home:
        return "~"
    prefix = home.rstrip("/") + "/"
    if path.startswith(prefix):
        return "~/" + path[len(prefix):]
    return path


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attached_flag(value: str | int | None) -> bool:
    """tmux expõe ``session_attached`` como contagem de clientes (string)."""
    return _to_int(value) not in (None, 0)
