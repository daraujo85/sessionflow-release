"""Testes de ``POST /workers/{host_id}/stop`` — desligar de verdade.

Histórico do bug: o endpoint só gravava ``updated_at`` no epoch zero (marcar
offline no Mongo). Em host com SSH (Colab/Deepin/Duck) o ``sf stop`` também
matava o processo, então o efeito colateral escondia o buraco. No **Kaggle
não há SSH**: o worker continuava vivo, mandava heartbeat 30s depois e o
host voltava a 🟢 — o botão Desligar era decorativo.

Contrato novo: além de marcar offline, publica o comando ``shutdown`` (que o
worker consome e encerra pelo evento de parada do runner).

Marcar offline continua sendo correto: é o que faz o card mudar de estado na
hora, sem esperar o worker sumir no próximo ciclo de heartbeat.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.main import create_app
from app.routers import worker as worker_router

HOST = "558846ca-3973-40d1-ab7c-9dff20b97d5c"  # Kaggle GPU (host real em campo)


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

    monkeypatch.setattr(worker_router, "publish_command", fake_publish)
    return calls


async def _seed_host(app, host_id: str = HOST, age_s: float = 0.0) -> None:
    await app.state.mongo_db["worker_status"].insert_one(
        {
            "_id": host_id,
            "hostname": "fc1027582ecb",
            "display_name": "Kaggle GPU",
            "updated_at": datetime.now(UTC) - timedelta(seconds=age_s),
        }
    )


async def test_stop_publica_shutdown(app, client, published) -> None:
    """Sem isto o worker Kaggle sobrevive ao Desligar e volta a 🟢."""
    await _seed_host(app)
    resp = await client.post(f"/workers/{HOST}/stop")
    assert resp.status_code == 200
    assert [c["type"] for c in published] == ["shutdown"], (
        f"esperava publicar shutdown, veio {published!r}"
    )
    assert published[0]["host_id"] == HOST


async def test_stop_marca_offline_na_hora(app, client, published) -> None:
    """O card precisa virar offline imediatamente, sem esperar heartbeat."""
    await _seed_host(app)
    resp = await client.post(f"/workers/{HOST}/stop")
    assert resp.json()["online"] is False, "worker vivo no Mongo após stop"


async def test_stop_nao_publica_para_host_comum(app, client, published) -> None:
    """Mac/Duck/Deepin não são sandbox: nada de encerrar worker local por
    engano (lá o desligamento é decisão do dono da máquina, via SSH/serviço)."""
    host = "h-macbook"
    await app.state.mongo_db["worker_status"].insert_one(
        {
            "_id": host,
            "hostname": "Macbook-Air",
            "display_name": "Macbook Air",
            "updated_at": datetime.now(UTC),
        }
    )
    resp = await client.post(f"/workers/{host}/stop")
    assert resp.status_code == 200
    assert published == [], "stop num host comum não pode encerrar o worker"


async def test_stop_host_desconhecido_404(app, client, published) -> None:
    resp = await client.post("/workers/nao-existe/stop")
    assert resp.status_code == 404
    assert published == []
