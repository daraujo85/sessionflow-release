"""Integration test for GET /health.

Runs on the host against the docker stack, so it forces the host-facing
URIs (127.0.0.1) and a dedicated test database.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


def _host_settings() -> Settings:
    mongo_host = os.environ.get(
        "MONGO_URI_HOST",
        "mongodb://sessionflow:882938ade9f4298f59daccc2dc5add74"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow",
    )
    rabbit_host = os.environ.get(
        "RABBITMQ_URI_HOST",
        "amqp://sessionflow:538aff09246ba916d6aeeaeac9f932a1@127.0.0.1:5672/",
    )
    return Settings(
        mongo_uri_host=mongo_host,
        rabbitmq_uri_host=rabbit_host,
        use_host_uris=True,
        mongo_db="sessionflow_test",
    )


@pytest.mark.integration
async def test_health_ok():
    app = create_app(settings=_host_settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # trigger lifespan (startup/shutdown) around the request
        async with app.router.lifespan_context(app):
            resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mongo"] is True
