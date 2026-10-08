"""Teste unitário do timing de `_type_and_submit` — sem tmux/mongo/rabbit reais.

Monkeypatch de `_send_keys`/`_send_key`/`_pane_tail`/`asyncio.sleep` pra medir
quantos re-Enters e quanto tempo de sleep o método usa, sem precisar de
infraestrutura de verdade (é lógica pura de retry/timing).
"""

from __future__ import annotations

import asyncio

import pytest

from sessionflow_worker.command_consumer import CommandConsumer


def _make_consumer() -> CommandConsumer:
    # Bypassa __init__ (pede channel/db reais) — _type_and_submit só usa
    # self._send_keys/_send_key/_pane_tail, que serão monkeypatched abaixo.
    return object.__new__(CommandConsumer)


@pytest.mark.asyncio
async def test_type_and_submit_retries_enter_at_most_once(monkeypatch):
    consumer = _make_consumer()
    sleeps: list[float] = []
    send_key_calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(consumer, "_send_keys", lambda name, text, enter=False: None)
    monkeypatch.setattr(
        consumer, "_send_key", lambda name, key: send_key_calls.append(key)
    )
    # Tela nunca muda → simula Enter "engolido" em todas as tentativas.
    monkeypatch.setattr(consumer, "_pane_tail", lambda name, lines=12: "tela-parada")

    await consumer._type_and_submit("sftest-x", "oi")

    enters = [k for k in send_key_calls if k == "Enter"]
    # 1 Enter inicial + no máximo 1 retry (era até 2 retries antes).
    assert len(enters) <= 2

    # Sleeps de retry (exclui o primeiro, que é o sleep pré-Enter proporcional
    # ao texto) devem ser <= 0.5s cada (era 1.3s antes).
    retry_sleeps = sleeps[1:]
    assert all(s <= 0.5 for s in retry_sleeps)


@pytest.mark.asyncio
async def test_type_and_submit_stops_retrying_once_screen_changes(monkeypatch):
    consumer = _make_consumer()
    send_key_calls: list[str] = []
    screens = iter(["tela-a", "tela-b", "tela-c"])  # muda logo na 1ª verificação

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(consumer, "_send_keys", lambda name, text, enter=False: None)
    monkeypatch.setattr(
        consumer, "_send_key", lambda name, key: send_key_calls.append(key)
    )
    monkeypatch.setattr(consumer, "_pane_tail", lambda name, lines=12: next(screens))

    await consumer._type_and_submit("sftest-x", "oi")

    enters = [k for k in send_key_calls if k == "Enter"]
    assert len(enters) == 1  # tela mudou na 1ª checagem → não reenvia
