"""Testes da rota ``POST /hosts/{host_id}/gpu-services/{name}/{action}``.

Unitários: Mongo é o ``mongomock-motor`` (sem docker) e o publisher AMQP é
substituído por um fake (nada sai pra rede).

Contrato:
- 202 + ``command_id`` quando o host está online (mesma checagem de heartbeat
  do ``/fetch``);
- 400 quando a ``action`` está fora de ``{start, stop, status}``;
- 404 quando o host nunca fez heartbeat.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.main import create_app
from app.routers import host_commands

HOST = "host-gpu"


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
    # Sem lifespan (não conecta Mongo/Rabbit reais): injeta o mock.
    application.state.mongo_db = AsyncMongoMockClient()["sessionflow"]
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def published(monkeypatch):
    """Publisher AMQP falso — registra o que seria publicado."""
    calls: list[dict] = []

    async def fake_publish(settings, type, payload=None, host_id=None):
        calls.append({"type": type, "payload": payload, "host_id": host_id})
        return f"cmd-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr(host_commands, "publish_command", fake_publish)
    return calls


async def _seed_host(app, host_id: str = HOST, age_s: float = 0.0) -> None:
    """Heartbeat do worker (recente por default)."""
    await app.state.mongo_db["worker_status"].insert_one(
        {
            "_id": host_id,
            "hostname": "gpu-box",
            "updated_at": datetime.now(UTC) - timedelta(seconds=age_s),
        }
    )


async def test_post_gpu_services_start(app, client, published) -> None:
    await _seed_host(app)
    resp = await client.post(f"/hosts/{HOST}/gpu-services/xtts/start")
    assert resp.status_code == 202
    body = resp.json()
    assert body["command_id"]
    assert body["host_id"] == HOST
    assert published == [
        {
            "type": "gpu_service",
            "payload": {"action": "start", "name": "xtts"},
            "host_id": HOST,
        }
    ]


async def test_post_gpu_services_stop(app, client, published) -> None:
    await _seed_host(app)
    resp = await client.post(f"/hosts/{HOST}/gpu-services/xtts/stop")
    assert resp.status_code == 202
    assert published[0]["payload"] == {"action": "stop", "name": "xtts"}


async def test_post_gpu_services_valida_action(app, client, published) -> None:
    await _seed_host(app)
    resp = await client.post(f"/hosts/{HOST}/gpu-services/xtts/explode")
    assert resp.status_code == 400
    assert "explode" in resp.json()["detail"]
    # nada foi publicado no RabbitMQ
    assert published == []


async def test_post_gpu_services_nome_invalido(app, client, published) -> None:
    await _seed_host(app)
    resp = await client.post(f"/hosts/{HOST}/gpu-services/rm%20-rf/start")
    assert resp.status_code == 400
    assert published == []


async def test_post_gpu_services_offline_host(app, client, published) -> None:
    """Host que nunca fez heartbeat → 404 (não enfileira pra ninguém)."""
    resp = await client.post(f"/hosts/{HOST}/gpu-services/xtts/start")
    assert resp.status_code == 404
    assert published == []


async def test_post_gpu_services_worker_morto(app, client, published) -> None:
    """Heartbeat velho (> 5min) → 409 (ninguém vai consumir o comando)."""
    await _seed_host(app, age_s=600)
    resp = await client.post(f"/hosts/{HOST}/gpu-services/xtts/start")
    assert resp.status_code == 409
    assert published == []
