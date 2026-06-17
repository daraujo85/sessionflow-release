"""E2E vertical slice do SessionFlow (TMUX-01/05/09 — slice MVP completo).

Prova o fluxo ponta-a-ponta REAL da feature tmux Runtime & Discovery:

    POST /sessions (API HTTP real)
        -> comando publicado na fila Rabbit real (sessionflow.commands)
        -> Worker (CommandConsumer in-process) consome 1 mensagem da fila real
        -> cria sessão tmux real (detached) e faz upsert no Mongo real
        -> Discovery.reconcile_once() reconcilia tmux <-> Mongo (real)
        -> GET /sessions (API HTTP real) mostra a sessão running
        -> DELETE /sessions/{id} (API HTTP real)
        -> kill da sessão tmux real + consumo do comando kill
        -> Discovery.reconcile_once() -> GET /sessions/{id} mostra stopped

Seams REAIS exercitados:
- HTTP real contra a API FastAPI subida como subprocess (uvicorn) — urllib (stdlib).
- Fila RabbitMQ real (a API publica o comando; o consumer in-process consome).
- tmux real no host (sessão detached criada/morta de verdade).
- Mongo real (coleção e2e isolada no DB ``sessionflow``).

Isolamento / segurança:
- Sessões tmux: SEMPRE prefixo ``sftest-`` (teardown mata SÓ ``sftest-*``).
- Mongo: coleção isolada ``sessions_e2e_<uuid>`` no DB ``sessionflow`` (o usuário
  de app só tem acesso a esse DB) — dropada no teardown. A MESMA coleção é
  passada à API (env ``SESSIONS_COLLECTION``) e injetada no consumer/discovery.

CRÍTICO: ``build_launch_cmd`` é monkeypatched para retornar ``"sleep 120"``, de
modo que o send-keys ao pane NUNCA dispara uma CLI de agente real
(claude/codex/...). A sessão tmux fica viva (sleep) o suficiente para o slice.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker import command_consumer as cc_mod
from sessionflow_worker import rabbit
from sessionflow_worker.command_consumer import CommandConsumer
from sessionflow_worker.discovery import Discovery
from sessionflow_worker.mongo import get_db
from sessionflow_worker.state import SessionState
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT_ENV = _REPO_ROOT / ".env"
_API_PROJECT = _REPO_ROOT / "api"
load_dotenv(_ROOT_ENV, override=False)

_PREFIX = "sftest-"
_ACTIVE_STATUSES = {SessionState.RUNNING.value, SessionState.DETACHED.value}


def _host_mongo_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


def _host_rabbit_uri() -> str | None:
    return os.getenv("RABBITMQ_URI_HOST") or os.getenv("RABBITMQ_URI")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- HTTP helpers (urllib, stdlib — não adicionamos httpx ao worker) --------


def _http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx ainda têm corpo útil
        payload = exc.read()
        status = exc.code
    parsed: dict = json.loads(payload) if payload else {}
    return status, parsed


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def session_name() -> Iterator[str]:
    """Nome namespaced ``sftest-e2e-*``; teardown mata SÓ ``sftest-*``."""
    name = f"{_PREFIX}e2e-{uuid.uuid4().hex[:8]}"
    rt = TmuxRuntime()
    try:
        yield name
    finally:
        assert name.startswith(_PREFIX)  # cinto de segurança
        try:
            if rt.has_session(name):
                rt.kill_session(name)
        except TmuxRuntimeError:
            pass


@pytest.fixture
def coll_name() -> str:
    return f"sessions_e2e_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name: str):
    uri = _host_mongo_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    database = get_db(uri=uri, db_name="sessionflow")
    client = database.client
    await database[coll_name].drop()
    try:
        yield database
    finally:
        await database[coll_name].drop()
        client.close()


@pytest.fixture
async def channel() -> AsyncIterator:
    conn = await rabbit.connect()
    try:
        ch = await conn.channel()
        await rabbit.declare_topology(ch)
        yield ch
    finally:
        await conn.close()


@pytest.fixture
def patch_launch(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Neutraliza o launch real: send-keys envia ``sleep 120`` ao pane.

    Garante que NENHUMA CLI de agente real (claude/codex/...) seja disparada;
    a sessão tmux fica viva (sleep) o suficiente para o slice.
    """
    calls: list[tuple] = []

    def _fake(agent_type, model, effort) -> str:
        calls.append((agent_type, model, effort))
        return "sleep 120"

    monkeypatch.setattr(cc_mod, "build_launch_cmd", _fake)
    return calls


@pytest.fixture
async def api_server(coll_name: str) -> AsyncIterator[str]:
    """API real (uvicorn subprocess) apontando para a coleção e2e isolada."""
    mongo_uri = _host_mongo_uri()
    rabbit_uri = _host_rabbit_uri()
    if not mongo_uri or not rabbit_uri:
        pytest.skip("URIs host (Mongo/Rabbit) não configuradas.")

    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "USE_HOST_URIS": "true",
            "MONGO_DB": "sessionflow",
            "SESSIONS_COLLECTION": coll_name,
            "MONGO_URI_HOST": mongo_uri,
            "RABBITMQ_URI_HOST": rabbit_uri,
        }
    )

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "--project",
            str(_API_PROJECT),
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(_API_PROJECT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        # Poll /health até 200 (ou processo morrer / timeout).
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                pytest.fail(f"API subprocess morreu na inicialização:\n{out}")
            try:
                status, body = _http("GET", f"{base}/health")
                if status == 200 and body.get("status") == "ok":
                    ready = True
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            time.sleep(0.4)
        if not ready:
            pytest.fail("API /health não respondeu 200 a tempo.")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
async def consumer(channel, db, runtime, coll_name) -> CommandConsumer:
    return CommandConsumer(
        channel=channel, db=db, runtime=runtime, collection=coll_name
    )


# -- helpers ----------------------------------------------------------------


async def _consume_one_command(consumer: CommandConsumer, channel, command_id: str,
                               timeout: float = 15.0) -> dict:
    """Busca da fila REAL ``sessionflow.commands`` a mensagem do ``command_id``.

    Faz ``handle()`` da mensagem do command_id alvo (ack), e re-publica
    qualquer outra mensagem que não seja a alvo (não deveria acontecer no slice
    isolado, mas mantém a fila íntegra). Levanta TimeoutError se não achar.
    """
    queue = await channel.get_queue(rabbit.COMMANDS_QUEUE)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        msg = await queue.get(timeout=2, fail=False)
        if msg is None:
            await asyncio.sleep(0.1)
            continue
        body = json.loads(msg.body)
        if body.get("command_id") == command_id:
            await msg.ack()
            return await consumer.handle(body)
        # Não é a nossa: devolve à fila p/ não perder.
        await msg.nack(requeue=True)
        await asyncio.sleep(0.1)
    raise TimeoutError(f"comando {command_id} não chegou na fila")


# -- teste E2E --------------------------------------------------------------


async def test_e2e_create_running_then_kill_stopped(
    api_server, consumer, channel, db, runtime, coll_name, session_name,
    patch_launch, tmp_path,
) -> None:
    base = api_server
    work_dir = str(tmp_path)

    # === a. POST /sessions (HTTP real) -> 202 + command_id ============
    status, body = _http(
        "POST",
        f"{base}/sessions",
        {
            "name": session_name,
            "agent_type": "claude",
            "work_dir": work_dir,
            "model": None,
            "effort": None,
        },
    )
    assert status == 202, body
    create_cmd_id = body["command_id"]
    assert create_cmd_id

    # === b. Worker consome 1 msg da fila Rabbit real e processa =======
    create_event = await _consume_one_command(consumer, channel, create_cmd_id)
    assert create_event["ok"] is True, create_event
    assert create_event["type"] == "create"

    # tmux real criado; launch neutralizado (sleep 120 enviado ao pane).
    assert runtime.has_session(session_name) is True
    assert len(patch_launch) == 1

    # === c. Discovery reconcilia tmux <-> Mongo (real) ================
    report = await Discovery(runtime, db, collection=coll_name).reconcile_once()
    assert report.discovered + report.updated >= 1

    # === d. GET /sessions (HTTP real) -> sessão aparece running =======
    status, listing = _http("GET", f"{base}/sessions")
    assert status == 200, listing
    match = [s for s in listing["items"] if s.get("tmux_name") == session_name]
    assert match, f"sessão {session_name} não apareceu no GET /sessions: {listing}"
    session_doc = match[0]
    assert session_doc["status"] in _ACTIVE_STATUSES, session_doc
    session_id = session_doc["id"]

    # GET /sessions/{id} (HTTP real) confirma o mesmo.
    status, one = _http("GET", f"{base}/sessions/{session_id}")
    assert status == 200, one
    assert one["tmux_name"] == session_name
    assert one["status"] in _ACTIVE_STATUSES

    # === e. DELETE /sessions/{id} (HTTP real) -> kill -> stopped ======
    status, killed = _http("DELETE", f"{base}/sessions/{session_id}")
    assert status == 202, killed
    kill_cmd_id = killed["command_id"]

    kill_event = await _consume_one_command(consumer, channel, kill_cmd_id)
    assert kill_event["ok"] is True, kill_event
    assert kill_event["type"] == "kill"

    # tmux real morto.
    assert runtime.has_session(session_name) is False

    # Discovery reconcilia: confirma stopped (idempotente com o kill handler).
    await Discovery(runtime, db, collection=coll_name).reconcile_once()

    # GET /sessions/{id} (HTTP real) -> stopped.
    status, final = _http("GET", f"{base}/sessions/{session_id}")
    assert status == 200, final
    assert final["status"] == SessionState.STOPPED.value, final
