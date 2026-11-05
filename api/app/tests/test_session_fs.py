"""Testes da rota ``POST /sessions/{session_id}/fs`` — arquivos por SESSÃO.

Diferencia-se de ``/hosts/{host_id}/fetch`` (host-to-host, read-only) por
endereçar a SESSÃO: a API resolve o ``host_id`` e o ``work_dir`` dela, e
publica o comando ``fs_op`` pro worker daquele host.

Contrato:
- 202 + ``command_id`` quando a sessão existe e o host dela está vivo;
- 404 quando a sessão não existe;
- 409 quando a sessão não tem ``host_id`` (legado) ou o worker está morto;
- 400 quando ``op`` está fora de {list, read, write, copy, mkdir, delete};
- ``work_dir`` da sessão vai no payload (o worker resolve relativo contra ele).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.main import create_app
from app.routers import sessions as sessions_router

HOST = "host-fs"
WORK_DIR = "/home/diego/proj"


def _settings(tmp_path) -> Settings:
    return Settings(
        auth_email="",
        auth_password="",
        uploads_dir=str(tmp_path / "uploads"),
        skills_local_dir=str(tmp_path / "skills"),
        google_client_id="",
        google_client_secret="",
        google_refresh_token="",
        skills_drive_folder_id="",
    )


@pytest.fixture
def app(tmp_path):
    application = create_app(settings=_settings(tmp_path))
    application.state.mongo_db = AsyncMongoMockClient()["sessionflow"]
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def published(monkeypatch):
    calls: list[dict] = []

    async def fake_publish(settings, type, payload=None, host_id=None):
        calls.append({"type": type, "payload": payload, "host_id": host_id})
        return f"cmd-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr(sessions_router, "publish_command", fake_publish)
    return calls


async def _seed_host(app, host_id: str = HOST, age_s: float = 0.0) -> None:
    await app.state.mongo_db["worker_status"].insert_one(
        {"_id": host_id, "hostname": "fs-box",
         "updated_at": datetime.now(UTC) - timedelta(seconds=age_s)}
    )


async def _seed_session(app, **overrides) -> str:
    """Insere a sessão e devolve o id (str) para usar na URL."""
    oid = ObjectId()
    doc = {
        "_id": oid,
        "tmux_name": "worker-1",
        "status": "running",
        "agent_type": "claude",
        "host_id": HOST,
        "work_dir": WORK_DIR,
    }
    doc.update(overrides)
    await app.state.mongo_db["sessions"].insert_one(doc)
    return str(oid)


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
async def test_list_publica_fs_op_com_work_dir_da_sessao(app, client, published) -> None:
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "list", "path": "."}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["command_id"]
    assert body["host_id"] == HOST
    assert body["work_dir"] == WORK_DIR
    assert published == [{
        "type": "fs_op",
        "payload": {"op": "list", "path": ".", "work_dir": WORK_DIR},
        "host_id": HOST,
    }]


async def test_write_leva_conteudo_no_payload(app, client, published) -> None:
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs",
        json={"op": "write", "path": "a.txt", "content": "oi"},
    )
    assert resp.status_code == 202
    assert published[0]["payload"]["content"] == "oi"


async def test_copy_leva_dest_no_payload(app, client, published) -> None:
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs",
        json={"op": "copy", "path": "a.txt", "dest": "b.txt"},
    )
    assert resp.status_code == 202
    assert published[0]["payload"]["dest"] == "b.txt"


async def test_path_absoluto_ignora_work_dir(app, client, published) -> None:
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "read", "path": "/tmp/x.log"}
    )
    assert resp.status_code == 202
    assert published[0]["payload"]["path"] == "/tmp/x.log"


# --------------------------------------------------------------------------- #
# sessão / host
# --------------------------------------------------------------------------- #
async def test_sessao_inexistente_404(app, client, published) -> None:
    await _seed_host(app)
    # Sessão que nunca existiu: id bem formado, mas sem doc no Mongo.
    resp = await client.post(
        f"/sessions/{ObjectId()}/fs", json={"op": "list", "path": "."}
    )
    assert resp.status_code == 404
    assert published == []


async def test_sessao_sem_host_id_409(app, client, published) -> None:
    """Sessão legada (pré multi-host) não tem host_id: não dá pra rotear."""
    await _seed_host(app)
    sid = await _seed_session(app, host_id=None)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "list", "path": "."}
    )
    assert resp.status_code == 409
    assert "host_id" in resp.json()["detail"]
    assert published == []


async def test_worker_offline_404(app, client, published) -> None:
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "list", "path": "."}
    )
    assert resp.status_code == 404
    assert published == []


async def test_worker_morto_409(app, client, published) -> None:
    await _seed_host(app, age_s=600)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "list", "path": "."}
    )
    assert resp.status_code == 409
    assert published == []


# --------------------------------------------------------------------------- #
# validação de entrada
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("op", ["rm_rf", "exec", "LIST_ALL"])
async def test_op_invalida_400(app, client, published, op) -> None:
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": op, "path": "."}
    )
    assert resp.status_code == 400
    assert published == []


async def test_op_vazia_422(app, client, published) -> None:
    """String vazia é rejeitada pelo Pydantic (min_length=1) antes do endpoint."""
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "", "path": "."}
    )
    assert resp.status_code == 422
    assert published == []


async def test_path_vazio_422(app, client, published) -> None:
    """Pydantic rejeita string vazia (min_length=1) antes de publicar."""
    await _seed_host(app)
    sid = await _seed_session(app)
    resp = await client.post(
        f"/sessions/{sid}/fs", json={"op": "list", "path": ""}
    )
    assert resp.status_code == 422
    assert published == []
