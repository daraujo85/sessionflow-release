"""Integration tests for authentication (password login, JWT guards, WebAuthn).

Runs on the host against the docker Mongo. The Mongo user can only access the
``sessionflow`` database, so we do NOT create a test DB; instead we inject an
isolated ``webauthn_credentials_test_<uuid>`` collection (dropped on teardown).

We do not simulate the browser's WebAuthn crypto here. We exercise the password
login, the JWT-protection middleware (guards + exemptions), the SSE-style
``?token=`` extraction, and the public webauthn endpoints (availability +
register-options JWT guard).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app

EMAIL = "diego.araujo@pratadigital.com.br"
PASSWORD = "test-password-123"


def _mongo_host_uri() -> str:
    return os.environ.get(
        "MONGO_URI_HOST",
        "mongodb://sessionflow:882938ade9f4298f59daccc2dc5add74"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow",
    )


def _host_settings(collection_name: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        webauthn_collection=collection_name,
        auth_email=EMAIL,
        auth_password=PASSWORD,
        jwt_secret="test-secret-do-not-use-0123456789abcdef",
        jwt_ttl_seconds=3600,
        rp_id="sessionflow.boletoazap.dev.br",
        rp_origin="https://sessionflow.boletoazap.dev.br",
    )


@pytest_asyncio.fixture
async def settings():
    collection_name = f"webauthn_credentials_test_{uuid.uuid4().hex}"
    s = _host_settings(collection_name)
    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][collection_name].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _login(client, app, email=EMAIL, password=PASSWORD):
    async with app.router.lifespan_context(app):
        return await client.post("/auth/login", json={"email": email, "password": password})


@pytest.mark.integration
async def test_login_success_returns_token(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        resp = await _login(client, app)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == EMAIL
    assert body["expires_in"] == 3600
    assert isinstance(body["token"], str) and body["token"]


@pytest.mark.integration
async def test_login_wrong_password_401(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        resp = await _login(client, app, password="nope")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_login_email_case_insensitive(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        resp = await _login(client, app, email=EMAIL.upper())
    assert resp.status_code == 200


@pytest.mark.integration
async def test_protected_route_without_token_401(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "unauthorized"}


@pytest.mark.integration
async def test_protected_route_with_token_200(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            login = await client.post(
                "/auth/login", json={"email": EMAIL, "password": PASSWORD}
            )
            token = login.json()["token"]
            resp = await client.get(
                "/directories", headers={"Authorization": f"Bearer {token}"}
            )
    assert resp.status_code == 200


@pytest.mark.integration
async def test_health_is_public(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_sse_style_query_token_accepted(settings):
    """The SSE/EventSource client cannot send headers; it passes ?token=."""
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            login = await client.post(
                "/auth/login", json={"email": EMAIL, "password": PASSWORD}
            )
            token = login.json()["token"]
            # Hit a protected route with the token only as a query param.
            resp = await client.get("/directories", params={"token": token})
    assert resp.status_code == 200


@pytest.mark.integration
async def test_extract_token_helper():
    """Unit-level check of the bearer/query extraction helper."""
    from starlette.requests import Request

    from app.auth import extract_token

    def make(scope_extra):
        scope = {"type": "http", "headers": [], "query_string": b"", **scope_extra}
        return Request(scope)

    # Header form.
    r = make({"headers": [(b"authorization", b"Bearer abc.def.ghi")]})
    assert extract_token(r) == "abc.def.ghi"
    # Query form (SSE).
    r = make({"query_string": b"token=xyz.123"})
    assert extract_token(r) == "xyz.123"
    # Neither.
    r = make({})
    assert extract_token(r) is None


@pytest.mark.integration
async def test_webauthn_available_false_when_no_credentials(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/auth/webauthn/available")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


@pytest.mark.integration
async def test_webauthn_login_options_404_when_no_credentials(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/auth/webauthn/login/options")
    assert resp.status_code == 404


@pytest.mark.integration
async def test_webauthn_register_options_requires_jwt(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            # No token -> 401 (handler-level guard, even though /auth/ is exempt).
            resp = await client.post("/auth/webauthn/register/options")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_webauthn_register_options_with_jwt_returns_challenge(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            login = await client.post(
                "/auth/login", json={"email": EMAIL, "password": PASSWORD}
            )
            token = login.json()["token"]
            resp = await client.post(
                "/auth/webauthn/register/options",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert "challenge" in body
    assert body["rp"]["id"] == "sessionflow.boletoazap.dev.br"
    assert body["user"]["name"] == EMAIL
