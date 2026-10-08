"""Teste de reuso de conexão AMQP no publisher (perf: evita handshake por comando)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.publishers import command_publisher
from app.config import Settings


class _FakeExchange:
    def __init__(self) -> None:
        self.publish = AsyncMock()


class _FakeChannel:
    def __init__(self) -> None:
        self._exchange = _FakeExchange()
        self.close = AsyncMock()

    async def declare_exchange(self, *args, **kwargs):
        return self._exchange

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.is_closed = False
        self.channel = AsyncMock(side_effect=lambda: _FakeChannel())
        self.close = AsyncMock()


@pytest.fixture(autouse=True)
def _reset_cached_connection():
    command_publisher._connection = None
    yield
    command_publisher._connection = None


@pytest.mark.asyncio
async def test_publish_command_reuses_connection_across_calls(monkeypatch):
    fake_connection = _FakeConnection()
    connect_calls = []

    async def fake_connect_robust(uri):
        connect_calls.append(uri)
        return fake_connection

    monkeypatch.setattr(command_publisher.aio_pika, "connect_robust", fake_connect_robust)

    settings = Settings(rabbitmq_uri="amqp://fake")

    await command_publisher.publish_command(settings, type="noop", payload={})
    await command_publisher.publish_command(settings, type="noop", payload={})

    assert len(connect_calls) == 1  # conexão aberta uma vez só, reusada na 2ª chamada
    assert fake_connection.close.await_count == 0  # não fecha entre chamadas


@pytest.mark.asyncio
async def test_publish_command_reconnects_if_connection_closed(monkeypatch):
    fake_connection = _FakeConnection()
    connect_calls = []

    async def fake_connect_robust(uri):
        connect_calls.append(uri)
        return fake_connection

    monkeypatch.setattr(command_publisher.aio_pika, "connect_robust", fake_connect_robust)

    settings = Settings(rabbitmq_uri="amqp://fake")

    await command_publisher.publish_command(settings, type="noop", payload={})
    fake_connection.is_closed = True
    await command_publisher.publish_command(settings, type="noop", payload={})

    assert len(connect_calls) == 2  # conexão morta → reconecta


@pytest.mark.asyncio
async def test_publish_command_closes_channel_per_call(monkeypatch):
    fake_connection = _FakeConnection()
    created_channels: list[_FakeChannel] = []

    def _make_channel():
        channel = _FakeChannel()
        created_channels.append(channel)
        return channel

    fake_connection.channel = AsyncMock(side_effect=_make_channel)

    async def fake_connect_robust(uri):
        return fake_connection

    monkeypatch.setattr(command_publisher.aio_pika, "connect_robust", fake_connect_robust)

    settings = Settings(rabbitmq_uri="amqp://fake")

    await command_publisher.publish_command(settings, type="noop", payload={})
    await command_publisher.publish_command(settings, type="noop", payload={})

    assert len(created_channels) == 2  # canal novo por chamada
    for channel in created_channels:
        channel.close.assert_awaited_once()  # canal fechado ao fim de cada chamada
