#!/usr/bin/env python3
"""Servidor MCP (stdio) do SessionFlow.

Expõe os comandos do CLI ``sf`` como *tools* MCP, para que um agente Claude
(orquestrador) possa listar sessões, delegar trabalho a workers filhos, enviar
instruções a sessões irmãs, rodar comandos em outros hosts e colher handoffs —
sem precisar abrir um terminal.

Por que existe: a API REST tem 87 endpoints, mas expô-los 1:1 como tools
estouraria o system prompt (cada schema entra em TODO turno). Este servidor
espelha os **verbos do CLI** (~17 tools), que é a granularidade certa para
um agente decidir. ``api_call`` cobre o que não virou verbo.

Como funciona: importa ``tools/sf`` como módulo (mesmo padrão de
``test_sf.py``) e reusa ``load_env``/``login``/``_request``/``resolve_host``.
Nenhuma lógica HTTP é duplicada aqui — um fix no CLI propaga para o MCP.

Duas regras de higiene que o código abaixo respeita:

1. **stdout é o canal JSON-RPC.** Qualquer ``print()`` do CLI precisa ser
   capturado; um único byte solto no stdout corrompe o protocolo.
2. **``sf.die()`` chama ``sys.exit(1)``.** Como ``SystemExit`` herda de
   ``BaseException`` (não de ``Exception``), ``except Exception`` NÃO o pega —
   e ele mataria o servidor. As tools convertem isso em ``ToolError``
   (erro recuperável, que o cliente vê como resultado de falha).

Uso:
    python tools/mcp_server.py                 # stdio (registre no cliente)
    SESSIONFLOW_MCP_REMOTE=heverton python tools/mcp_server.py

Registro no Claude Code (``~/.claude.json`` → ``mcpServers``)::

    "sessionflow": {
      "command": "<repo>/tools/.venv-mcp/bin/python",
      "args": ["<repo>/tools/mcp_server.py"]
    }
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SF_PATH = HERE / "sf"


def _load_sf():
    """Carrega o CLI ``sf`` (sem extensão ``.py``) como módulo importável.

    Reusa o módulo já carregado, se houver: ``sf`` guarda estado de módulo
    (constantes, caches) e duas instâncias divergiriam. Importa para o teste
    também — quem carrega primeiro define a instância que todos enxergam.
    """
    existing = sys.modules.get("sf")
    if existing is not None and hasattr(existing, "_request"):
        return existing
    loader = importlib.machinery.SourceFileLoader("sf", str(SF_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sf"] = module
    loader.exec_module(module)
    return module


sf = _load_sf()

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class _ToolError(Exception):
    """Erro de negócio da tool (equivalente ao ``die`` do CLI).

    Subclasse própria para que os testes possam detectá-lo sem depender do
    SDK; ``_call`` traduz para ``ToolError`` do MCP na borda.
    """


#: Serializa a troca de ``sys.stdout``: o redirect é global ao processo e o
#: servidor pode atender tools concorrentes.
_STDOUT_LOCK = threading.RLock()


class _StdoutSink(io.StringIO):
    """Buffer que absorve tudo que o CLI imprime (nunca chega ao JSON-RPC)."""


def _run_capturing(func, *args, **kwargs) -> str:
    """Roda ``func`` com stdout/stderr desviados; devolve o texto capturado.

    ``SystemExit`` (do ``sf.die``) é convertido em ``_ToolError`` para não
    derrubar o processo — ver a nota no topo do módulo.
    """
    out = _StdoutSink()
    with _STDOUT_LOCK:
        saved = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = out
        try:
            func(*args, **kwargs)
        except SystemExit as exc:
            message = out.getvalue().strip().splitlines()
            detail = next(
                (ln.split("sf: erro:", 1)[1].strip() for ln in message if "sf: erro:" in ln),
                "\n".join(message) or "falha sem mensagem",
            )
            raise _ToolError(detail) from None
        except _ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - vira erro de tool, não crash
            raise _ToolError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            sys.stdout, sys.stderr = saved
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Helpers de acesso à API (reusam os do CLI)
# --------------------------------------------------------------------------- #
def _remote() -> str | None:
    """Instância-alvo: ``SESSIONFLOW_MCP_REMOTE`` (alias em sf-remotes.env)."""
    return os.environ.get("SESSIONFLOW_MCP_REMOTE") or None


def _session() -> tuple[dict, str, str]:
    """``(env, token, base)`` já autenticados — o trio usado por toda tool."""
    env = sf.resolve_env(type("A", (), {"remote": _remote()})())
    return env, sf.login(env), sf.api_base(env)


def _request(method: str, path: str, body: dict | None = None, timeout: int = 30):
    """GET/POST/... contra a instância alvo; devolve ``(status, body)``."""
    if not path.startswith("/"):
        raise _ToolError(f"path deve começar com '/': {path!r}")
    if urllib.parse.urlsplit(path).netloc or "://" in path:
        raise _ToolError("path relativo apenas — a instância alvo já está fixada")
    env, token, base = _session()
    return sf._request(method.upper(), f"{base}{path}", token=token, body=body, timeout=timeout)


# --------------------------------------------------------------------------- #
# Normalização: payload cru da API -> estrutura estável p/ o agente
# --------------------------------------------------------------------------- #
def _find_session(target: str) -> dict | None:
    """Localiza a sessão por id/nome EXATO dentro das ativas (sem baixar tudo)."""
    for state in ACTIVE_STATUSES:
        for s in _sessions_raw(state):
            if target in (s.get("id"), s.get("tmux_name"), s.get("display_name")):
                return s
    return None


def _session_view(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "name": s.get("tmux_name") or s.get("display_name") or s.get("id"),
        "display_name": s.get("display_name"),
        "status": s.get("status"),
        "activity": s.get("activity"),
        "agent": sf._AGENT_LABELS.get(s.get("agent_type") or "", s.get("agent_type")),
        "host_id": s.get("host_id"),
        "work_dir": s.get("work_dir"),
        "model": s.get("model"),
        "parent": s.get("parent"),
        "aliases": s.get("aliases") or [],
    }


def _host_view(w: dict) -> dict:
    agents = w.get("agents")
    if isinstance(agents, dict):
        agents = [k for k, v in agents.items() if v]
    metrics = w.get("metrics") or {}
    hardware = w.get("hardware") or {}
    return {
        "host_id": w.get("host_id"),
        "name": w.get("display_name") or w.get("hostname"),
        "hostname": w.get("hostname"),
        "emoji": w.get("emoji"),
        "online": bool(w.get("online")),
        "starting": bool(w.get("starting")),
        "agents": agents or [],
        "gpu": bool(hardware.get("gpu")),
        "cpu_pct": metrics.get("cpu_pct"),
        "mem_avail_gb": metrics.get("mem_avail_gb"),
        "platform": w.get("platform"),
    }


# --------------------------------------------------------------------------- #
# Servidor + tools
# --------------------------------------------------------------------------- #
server = MCPServer(
    name="sessionflow",
    title="SessionFlow",
    version="1.0.0",
    instructions=(
        "Orquestração de sessões de agentes em múltiplos hosts. Fluxo típico: "
        "list_sessions/list_hosts para descobrir alvos -> delegate para criar um "
        "worker filho em outro provedor -> check para colher o handoff. "
        "NÃO acompanhe o stream do worker: leia só o handoff. "
        "Use send para instruir sessões irmãs e run_command para trabalho "
        "mecânico sem gastar LLM. api_call é o escape para rotas sem verbo."
    ),
)


def _call(func, *args, **kwargs):
    """Roda um ``cmd_*`` do CLI convertendo falhas em ``ToolError`` do MCP."""
    try:
        return _run_capturing(func, *args, **kwargs)
    except _ToolError as exc:
        raise ToolError(str(exc)) from None


# --------------------------------------------------------------------------- #
# Arquivos por SESSÃO: o chamador aponta a sessão; a API resolve host/work_dir
# --------------------------------------------------------------------------- #
def _poll_command(command_id: str, *, timeout: float = 30.0) -> dict:
    """Polling de ``GET /commands/{id}`` até ``ok``/``error`` (mesmo do fetch)."""
    interval = sf._FETCH_POLL_INTERVAL
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, body = _request("GET", f"/commands/{command_id}", timeout=10)
        if status != 200:
            raise _ToolError(f"polling /commands falhou (HTTP {status}): {body}")
        state = body.get("status")
        if state == "ok":
            return body.get("result") or {}
        if state == "error":
            raise _ToolError(body.get("error") or "operação falhou no worker")
    raise _ToolError(f"timeout esperando o worker ({timeout:.0f}s)")


def _fs_session(session: str, payload: dict, *, timeout: float = 30.0) -> dict:
    """Publica ``fs_op`` na sessão alvo e devolve o resultado do worker."""
    try:
        session_doc = _find_session(session)
        if session_doc is None:
            raise _ToolError(f"sessão não encontrada: {session!r} (use list_sessions)")
        session_id = session_doc["id"]
        status, body = _request("POST", f"/sessions/{session_id}/fs", body=payload)
        if status != 202:
            raise _ToolError(
                f"POST /sessions/{session_id}/fs falhou (HTTP {status}): {body}"
            )
        return _poll_command(body["command_id"], timeout=timeout)
    except _ToolError as exc:
        raise ToolError(str(exc)) from None


@server.tool()
def copy_between_sessions(
    source_session: str,
    source_path: str,
    target_session: str,
    target_path: str,
) -> dict:
    """Copia um arquivo de UMA sessão para OUTRA, inclusive entre máquinas.

    Lê da origem e grava no destino via SessionFlow (sem SSH). O conteúdo
    passa por este processo — para arquivos grandes (>1 MiB) o read trunca;
    nesse caso use `run_command` com `scp`/`rsync`.
    """
    read = _fs_session(source_session, {"op": "read", "path": source_path})
    payload: dict = {"op": "write", "path": target_path}
    if read.get("binary"):
        payload["content_b64"] = read.get("content_b64")
    else:
        payload["content"] = read.get("text") or ""
    written = _fs_session(target_session, payload)
    return {
        "source_session": source_session,
        "source_path": read.get("path"),
        "target_session": target_session,
        "target_path": written.get("path"),
        "bytes": written.get("bytes_written"),
        "copied": True,
        "truncated_source": read.get("truncated", False),
    }


@server.tool()
def fs(
    session: str,
    op: str,
    path: str,
    dest: str | None = None,
    content: str | None = None,
    content_b64: str | None = None,
    append: bool = False,
    recursive: bool = False,
    limit: int = 200,
) -> dict:
    """Opera arquivos na máquina da sessão alvo — uma tool, várias operações.

    Endereça a SESSÃO (não o host): a API resolve o host e o diretório de
    trabalho dela. `path` relativo é resolvido contra esse diretório; absoluto
    precisa estar em `$HOME` ou `/tmp` daquela máquina.

    Args:
        session: nome/id da sessão alvo (use `list_sessions`).
        op: `list` (1 nível, dirs primeiro), `read` (texto ou base64 se
            binário), `write` (cria pastas), `copy` (dentro da sessão),
            `mkdir` ou `delete`.
        path: caminho alvo (relativo ao work_dir da sessão).
        dest: destino — obrigatório em `op=copy`. Recusa sobrescrever.
        content / content_b64: conteúdo em `op=write` (texto ou binário).
        append: em `write`, anexa em vez de sobrescrever.
        recursive: em `delete`, permite remover árvore não vazia.
        limit: máx. de entradas em `list` (1-500).

    Para copiar ENTRE sessões/máquinas diferentes, use `copy_between_sessions`.
    """
    allowed = {"list", "read", "write", "copy", "mkdir", "delete"}
    normalized = (op or "").strip().lower()
    if normalized not in allowed:
        raise ToolError(
            f"op inválida: {op!r} (aceitas: {', '.join(sorted(allowed))})"
        )

    payload: dict = {"op": normalized, "path": path}
    if normalized == "copy":
        if not dest:
            raise ToolError("op='copy' exige 'dest'")
        payload["dest"] = dest
    if normalized == "write":
        if content is None and content_b64 is None:
            raise ToolError("op='write' exige 'content' ou 'content_b64'")
        if content is not None:
            payload["content"] = content
        if content_b64 is not None:
            payload["content_b64"] = content_b64
        if append:
            payload["append"] = True
    if normalized == "delete" and recursive:
        payload["recursive"] = True
    if normalized == "list":
        payload["limit"] = limit

    result = _fs_session(session, payload)
    out: dict = {"session": session, "op": normalized, "path": result.get("path")}
    # Devolve só os campos que a operação produz — sem despejar o resto.
    for key in (
        "entries", "total", "truncated", "text", "content_b64", "binary",
        "size_bytes", "bytes_written", "created", "appended", "source",
        "dest", "bytes_copied", "is_dir", "deleted",
    ):
        if key in result:
            out[key] = result[key]
    return out


def _parse(argv: list[str]):
    """``build_parser().parse_args`` que não mata o servidor.

    O argparse faz ``sys.exit(2)`` em argumento inválido (``choices=``, faltando
    obrigatório). Fora do ``_run_capturing`` isso derrubaria o processo.
    """
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            return sf.build_parser().parse_args(argv)
    except SystemExit:
        message = err.getvalue().strip() or "argumentos inválidos"
        raise ToolError(message) from None


def _ns(**kwargs):
    """Namespace simples no lugar do ``argparse.Namespace``."""
    return type("Args", (), kwargs)()


#: Status que significam "sessão viva" (o repo da API usa exatamente estes).
ACTIVE_STATUSES = ("running", "waiting_input")


def _sessions_raw(status: str | None = None) -> list[dict]:
    """Sessões da instância alvo, com o filtro aplicado NA QUERY.

    O histórico passa de centenas de docs: filtrar depois de baixar não
    economiza nada e ainda entope o contexto do agente.
    """
    env, token, base = _session()
    if status:
        query = urllib.parse.urlencode({"status": status})
        status_code, body = sf._request("GET", f"{base}/sessions?{query}", token=token)
        if status_code != 200:
            raise _ToolError(f"GET /sessions falhou (HTTP {status_code}): {body}")
        return body.get("items", [])
    return sf.list_sessions(env, token)


def _workers_raw() -> list[dict]:
    _, token, base = _session()
    return sf.fetch_workers(base, token)


@server.tool()
def list_sessions(
    status: str | None = None,
    active_only: bool = True,
    limit: int = 25,
) -> dict:
    """Lista as sessões do SessionFlow (id, nome, agente, status, host, pasta).

    Use para descobrir o alvo de `send`, `check` e `run_command`.

    Args:
        status: filtra por um status exato (`running`, `waiting_input`,
            `completed`, `stopped`, `detached`). Ignorado se `active_only`.
        active_only: por padrão só as vivas (`running`/`waiting_input`). O
            histórico tem centenas de sessões — vire `False` só para investigar
            uma sessão encerrada.
        limit: máximo devolvido (default 25); `total`/`truncated` dizem o resto.
    """
    if active_only and not status:
        collected: list[dict] = []
        for state in ACTIVE_STATUSES:
            collected.extend(_sessions_raw(state))
    else:
        collected = _sessions_raw(status)

    views = [_session_view(s) for s in collected]
    total = len(views)
    limited = views[: max(1, limit)]
    return {
        "sessions": limited,
        "count": len(limited),
        "total": total,
        "truncated": total > len(limited),
    }


@server.tool()
def list_hosts(only_online: bool = False) -> dict:
    """Lista workers/hosts: online, agentes instalados, CPU/RAM e GPU.

    Use para escolher o `host` de `delegate` (aceita host_id, nome, hostname,
    emoji ou `auto`/`auto-remote`).
    """
    hosts = [_host_view(w) for w in _workers_raw()]
    if only_online:
        hosts = [h for h in hosts if h["online"]]
    return {"hosts": hosts, "count": len(hosts)}


@server.tool()
def delegate(
    provider: str,
    task: str,
    dir: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    name: str | None = None,
    role: str | None = None,
    host: str | None = None,
    gpu: bool = False,
    worktree: bool = False,
    push: bool = False,
    context_file: str | None = None,
) -> dict:
    """Cria um WORKER FILHO em outro provedor, manda a tarefa e ele grava um handoff.

    O filho roda autônomo. NÃO acompanhe o stream: use `check` de vez em quando
    e leia só o handoff (o contrato pede JSON com status/summary/diff/tests).

    Args:
        provider: `gemini`, `codex`, `opencode` ou `claude`.
        task: texto da tarefa. Seja específico sobre o critério de pronto.
        dir: work_dir do filho; em host remoto é caminho/nome NAQUELE host.
        model: omita para o default do provedor; prefira modelo intermediário.
        effort: `low`/`medium`/`high` (ignorado no gemini).
        name: nome do worker (default: slug da tarefa).
        role: papel (ex.: `dev-frontend`) — resolve persona global + overlay.
        host: host_id/nome/emoji, ou `auto` / `auto-remote`.
        gpu: exige host com GPU.
        worktree: roda num worktree novo na branch `sf/<nome>` (remoto).
        push: o filho faz push da branch — exige `worktree`.
        context_file: JSON de referências/fatos (nunca transcript).
    """
    argv = ["delegate", "--provider", provider, "--task", task]
    for flag, value in (
        ("--dir", dir), ("--model", model), ("--effort", effort),
        ("--name", name), ("--role", role), ("--host", host),
        ("--context-file", context_file),
    ):
        if value:
            argv += [flag, value]
    if gpu:
        argv.append("--gpu")
    if worktree:
        argv.append("--worktree")
    if push:
        argv.append("--push")
    if _remote():
        argv += ["--remote", _remote()]

    args = _parse(argv)
    output = _call(args.func, args)
    handle = args.name or args.task
    # O CLI imprime "handle:  <nome>  (id=...)"; extrai sem refazer a lógica.
    import re

    match = re.search(r"handle:\s+(\S+)\s+\(id=([^)]+)\)", output)
    handle = match.group(1) if match else None
    session_id = match.group(2) if match else None
    return {
        "handle": handle,
        "session_id": session_id,
        "provider": provider,
        "model": model or "(default)",
        "host": host or "local",
        "next": f"check(target='{handle}') para colher o handoff",
        "output": output,
    }


@server.tool()
def check(target: str, dir: str | None = None, summary: bool = True) -> dict:
    """Status/atividade de uma sessão + o handoff dela (se já existir).

    Esta é a forma correta de colher o resultado de um `delegate`. Não use
    `run_command` para ler o stream do worker.
    """
    argv = ["check", target]
    if dir:
        argv += ["--dir", dir]
    if summary:
        argv.append("--summary")
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)

    session = _find_session(target)
    return {
        "target": target,
        "status": (session or {}).get("status"),
        "found": session is not None,
        "output": output,
    }


@server.tool()
def send(target: str, text: str, enter: bool = True) -> dict:
    """Manda texto pro terminal de uma sessão JÁ EXISTENTE (irmã).

    O `target` aceita id, tmux_name, display_name ou substring ÚNICA.
    Com `enter=False` o texto é digitado sem submeter (outro revisa antes).
    """
    argv = ["send", target, text]
    if not enter:
        argv.append("--no-enter")
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    _call(args.func, args)
    session = _find_session(target)
    return {
        "session_id": (session or {}).get("id") or target,
        "delivered": True,
        "enter": enter,
    }


@server.tool()
def request_context(target: str, question: str) -> dict:
    """Pede um fato/path pontual ao orquestrador, sem puxar o transcript dele."""
    args = _ns(target=target, question=question, remote=_remote())
    _call(sf.cmd_request_context, args)
    return {"session": target, "asked": True, "question": question}


@server.tool()
def fetch_file(host: str, path: str, timeout: float | None = None) -> dict:
    """Lê 1 arquivo de OUTRO host via SessionFlow (sem SSH, read-only, ≤1 MiB).

    Só dentro de `$HOME` ou `/tmp` do alvo. Para binário, use `api_call` ou
    `share_file`; aqui o conteúdo volta como texto.
    """
    argv = ["fetch", host, path]
    if timeout:
        argv += ["--timeout", str(timeout)]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "path": path, "content": output}


@server.tool()
def share_file(path: str, to: str | None = None) -> dict:
    """Sobe um arquivo do disco pra ficar visível no app (inclusive celular).

    Args:
        path: caminho local (aceita `~` e relativo).
        to: sessão-alvo; por padrão a sessão de onde o comando roda.
    """
    argv = ["share", path]
    if to:
        argv += ["--to", to]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"path": path, "shared": True, "output": output}


@server.tool()
def run_command(
    host: str,
    command: str | None = None,
    name: str | None = None,
    dir: str | None = None,
    timeout: float | None = None,
    tail: bool = False,
    color: bool = False,
) -> dict:
    """Roda um comando num shell de OUTRO host, SEM agente no meio (zero LLM).

    Use para trabalho MECÂNICO (listar arquivos, checar log, screencapture,
    abrir um navegador remoto). Para algo que exige raciocínio, use `delegate`.
    A ponte é reusada por host; `name` cria um apelido dirigível por `send`.

    Com `tail=True` não executa nada: devolve a tela atual da sessão.
    """
    argv = ["command", host]
    if command:
        argv.append(command)
    if name:
        argv += ["--name", name]
    if dir:
        argv += ["--dir", dir]
    if timeout:
        argv += ["--timeout", str(timeout)]
    if tail:
        argv.append("--tail")
    if color:
        argv.append("--color")
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "command": command, "output": output}


@server.tool()
def move_session(target: str, host: str) -> dict:
    """Move uma sessão `claude` já iniciada pra outro host, retomando a conversa.

    Exige repo commitado e com push na origem. Não move codex/gemini/opencode.
    """
    argv = ["move", target, "--host", host]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"target": target, "host": host, "output": output}


@server.tool()
def start_host(host: str) -> dict:
    """Liga/inicia uma máquina Colab ou worker remoto (ex.: `colab1-cpu`)."""
    argv = ["start", host]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "output": output}


@server.tool()
def stop_host(host: str) -> dict:
    """Desliga/para uma máquina Colab ou worker remoto (mata worker + túnel)."""
    argv = ["stop", host]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "output": output}


@server.tool()
def gpu_service(host: str, action: str, name: str | None = None) -> dict:
    """Controla serviços de GPU de um host (com auto-stop quando ociosos).

    Args:
        host: host_id/nome/emoji.
        action: `status`, `start` ou `stop`.
        name: serviço (ex.: `xtts`). Obrigatório em start/stop; em status,
            sem nome devolve todos.
    """
    argv = ["gpu", host, action]
    if name:
        argv.append(name)
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "action": action, "service": name, "output": output}


@server.tool()
def list_dirs(host: str, term: str = "", limit: int = 20) -> dict:
    """Lista pastas indexadas de um host — necessário para escolher `dir` remoto.

    Em host remoto, `delegate(dir=...)` espera um caminho que exista LÁ; o nome
    da pasta listado aqui serve.
    """
    argv = ["dirs", "--host", host, "--limit", str(min(limit, 50))]
    if term:
        argv.append(term)
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    return {"host": host, "term": term, "output": output}


@server.tool()
def metrics(dir: str | None = None, strict: bool = False) -> dict:
    """Mede bytes de manifests/handoffs locais e valida o orçamento de contexto."""
    argv = ["metrics"]
    if dir:
        argv += ["--dir", dir]
    if strict:
        argv.append("--strict")
    args = _parse(argv)
    output = _call(args.func, args)
    return {"dir": dir or os.getcwd(), "strict": strict, "output": output}


@server.tool()
def notify(title: str, body: str, url: str | None = None) -> dict:
    """Manda um web push pro celular (todas as subscrições) — pede um humano."""
    argv = ["notify", title, body]
    if url:
        argv += ["--url", url]
    if _remote():
        argv += ["--remote", _remote()]
    args = _parse(argv)
    output = _call(args.func, args)
    # O CLI imprime "push ✅ -> N dispositivo(s)"; devolve o número no contrato.
    import re

    match = re.search(r"->\s*(\d+)\s+dispositivo", output)
    return {
        "title": title,
        "body": body,
        "url": url,
        "sent": int(match.group(1)) if match else None,
        "delivered": True,
    }


@server.tool()
def api_call(method: str, path: str, body: dict | None = None) -> dict:
    """Escape hatch: chama qualquer rota da API do SessionFlow (já autenticado).

    Use quando não houver verbo dedicado (skills, schedules, shared-files,
    worker). `path` é relativo à instância alvo (ex.: `/skills`,
    `/sessions/<id>/file`). URLs absolutas são recusadas de propósito: a
    instância alvo é fixada pelo servidor, não pelo agente.
    """
    status, payload = _request(method, path, body=body)
    return {"status": status, "ok": 200 <= status < 300, "body": payload}


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
