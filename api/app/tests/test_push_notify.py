"""Testes de ``POST /push/notify`` (aviso avulso via Web Push).

Unitários: Mongo é o ``mongomock-motor`` e o ``webpush`` é mockado — nada sai
pra rede nem precisa de chave VAPID real.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app import push_sender
from app.auth import create_token
from app.config import Settings
from app.main import create_app

SECRET = "test-secret-do-not-use-0123456789abcdef"
# Placeholder (não é chave): só prova que o "\n" escapado do .env é desfeito.
FAKE_KEY = "linha1\\nlinha2"

def _settings(**overrides) -> Settings:
    base = dict(
        auth_email="diego@example.com",
        auth_password="x",
        jwt_secret=SECRET,
        vapid_private=FAKE_KEY,
        vapid_subject="mailto:diego@example.com",
    )
    return Settings(**{**base, **overrides})

def _app(**overrides):
    application = create_app(settings=_settings(**overrides))
    application.state.mongo_db = AsyncMongoMockClient()["sessionflow"]
    return application

def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

def _auth():
    return {"Authorization": f"Bearer {create_token('diego', secret=SECRET, ttl_seconds=60)}"}

async def _seed(app, *endpoints):
    col = app.state.mongo_db[app.state.settings.push_subscriptions_collection]
    for ep in endpoints:
        await col.insert_one({"endpoint": ep, "keys": {"p256dh": "p", "auth": "a"}})
    return col

@pytest.mark.asyncio
async def test_notify_without_auth_401():
    app = _app()
    async with _client(app) as c:
        resp = await c.post("/push/notify", json={"title": "t", "body": "b"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_notify_sends_to_all_and_prunes_expired():
    app = _app()
    col = await _seed(app, "https://push/ok", "https://push/gone")
    sent = []

    def fake_send(sub, payload, pem, subject):
        sent.append((sub["endpoint"], json.loads(payload), pem, subject))
        return 410 if sub["endpoint"].endswith("gone") else None

    with mock.patch.object(push_sender, "_send_one_sync", fake_send):
        async with _client(app) as c:
            resp = await c.post(
                "/push/notify",
                headers=_auth(),
                json={"title": "🤖 Colab precisa de você", "body": "colab1: captcha",
                      "url": "https://colab.research.google.com/drive/x"},
            )
    assert resp.status_code == 200
    assert resp.json() == {"sent": 1}
    assert {e for e, *_ in sent} == {"https://push/ok", "https://push/gone"}
    _, payload, pem, subject = sent[0]
    notif = payload["notification"]
    assert notif["title"] == "🤖 Colab precisa de você"
    assert notif["body"] == "colab1: captcha"
    assert notif["data"]["onActionClick"]["default"]["url"].startswith("https://colab")
    assert pem == "linha1\nlinha2"
    assert subject == "mailto:diego@example.com"
    assert await col.count_documents({}) == 1  # 410 podada

@pytest.mark.asyncio
async def test_notify_without_url_has_no_click_action():
    app = _app()
    await _seed(app, "https://push/ok")
    payloads = []

    def fake_send(sub, payload, pem, subject):
        payloads.append(json.loads(payload))

    with mock.patch.object(push_sender, "_send_one_sync", fake_send):
        async with _client(app) as c:
            resp = await c.post("/push/notify", headers=_auth(), json={"title": "t"})
    assert resp.json() == {"sent": 1}
    assert "data" not in payloads[0]["notification"]

@pytest.mark.asyncio
async def test_notify_without_private_key_503():
    app = _app(vapid_private="")
    async with _client(app) as c:
        resp = await c.post("/push/notify", headers=_auth(), json={"title": "t"})
    assert resp.status_code == 503

@pytest.mark.asyncio
async def test_notify_requires_title():
    app = _app()
    async with _client(app) as c:
        resp = await c.post("/push/notify", headers=_auth(), json={"title": "", "body": "b"})
    assert resp.status_code == 422
