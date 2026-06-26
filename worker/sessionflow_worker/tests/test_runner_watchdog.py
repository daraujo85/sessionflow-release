"""Testes do watchdog do consumer de comandos (runner).

Unitários (sem infra real): a consulta ao broker é monkeypatchada. Validam a
LÓGICA do watchdog (quando levanta ``ConsumerStalled`` e quando não) e o parse
da URL/credenciais da mgmt API a partir da URI AMQP.
"""

from __future__ import annotations

import pytest

from sessionflow_worker import runner


async def test_watchdog_raises_after_consecutive_zeros(monkeypatch) -> None:
    """0 consumidores por ``grace`` checagens seguidas → ConsumerStalled."""
    counts = iter([0, 0])
    monkeypatch.setattr(runner, "_commands_consumer_count", lambda: next(counts))

    with pytest.raises(runner.ConsumerStalled):
        await runner.consumer_watchdog(interval=0, grace=2)


async def test_watchdog_positive_resets_streak(monkeypatch) -> None:
    """Um count > 0 zera o contador; só zeros SEGUIDOS disparam o rebuild."""
    # 0 (misses=1) -> 5 (reset) -> 0 (1) -> 0 (2 => raise). Consome os 4.
    counts = iter([0, 5, 0, 0])
    monkeypatch.setattr(runner, "_commands_consumer_count", lambda: next(counts))

    with pytest.raises(runner.ConsumerStalled):
        await runner.consumer_watchdog(interval=0, grace=2)


async def test_watchdog_none_does_not_count(monkeypatch) -> None:
    """``None`` (mgmt indisponível) não conta como zero nem reseta a sequência."""
    # 0 (misses=1) -> None (ignora, segue 1) -> 0 (misses=2 => raise).
    counts = iter([0, None, 0])
    monkeypatch.setattr(runner, "_commands_consumer_count", lambda: next(counts))

    with pytest.raises(runner.ConsumerStalled):
        await runner.consumer_watchdog(interval=0, grace=2)


def test_mgmt_url_and_auth_from_amqp_uri(monkeypatch) -> None:
    """Deriva host/credenciais da URI AMQP e usa a porta da mgmt + vhost ``/``."""
    monkeypatch.setenv(
        "RABBITMQ_URI_HOST", "amqp://user:p%40ss@127.0.0.1:5672/"
    )
    monkeypatch.setenv("RABBITMQ_MGMT_HOST_PORT", "15672")
    monkeypatch.setenv("RABBITMQ_VHOST", "/")
    monkeypatch.delenv("RABBITMQ_URI", raising=False)

    built = runner._mgmt_queue_url_and_auth()
    assert built is not None
    url, authorization = built
    # vhost "/" -> %2F, e o nome da fila preservado.
    assert url == (
        "http://127.0.0.1:15672/api/queues/%2F/sessionflow.commands"
    )
    # Senha URL-encoded (p%40ss) é decodificada antes do Basic.
    import base64

    assert authorization.startswith("Basic ")
    decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode()
    assert decoded == "user:p@ss"


def test_mgmt_url_and_auth_none_without_uri(monkeypatch) -> None:
    """Sem URI configurada, retorna None (watchdog vira no-op)."""
    monkeypatch.delenv("RABBITMQ_URI_HOST", raising=False)
    monkeypatch.delenv("RABBITMQ_URI", raising=False)
    assert runner._mgmt_queue_url_and_auth() is None
