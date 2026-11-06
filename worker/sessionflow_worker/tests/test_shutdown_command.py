"""Testes do comando ``shutdown`` — o "Desligar" de host sandbox (Kaggle).

Por que este comando existe
---------------------------
``POST /workers/{host_id}/stop`` sempre marcou o host como offline só no
Mongo. Num Colab/Deepin/Duck isso engana mas se corrige: o ``sf stop``
também mata o worker por SSH. No **Kaggle não há SSH** — não existe
``ssh kaggle`` no ``~/.ssh/config`` nem túnel bore no selfboot. Sem um
comando de verdade, o worker continuaria vivo, mandaria heartbeat no ciclo
seguinte (30s) e o host voltaria a 🟢 sozinho: o botão Desligar seria
decorativo.

O contrato: o worker recebe ``shutdown`` e pede o encerramento do próprio
runner (``stop`` event), que já sabe cancelar tasks e fechar conexões.
"""

from __future__ import annotations

import asyncio

import pytest

from sessionflow_worker.command_consumer import CommandConsumer


class _StopFake:
    """Substitui o ``asyncio.Event`` do runner: registra se foi setado."""

    def __init__(self) -> None:
        self.setado = False

    def set(self) -> None:
        self.setado = True


class _RuntimeFalso:
    """Runtime mínimo: o handler de shutdown não deve tocar em tmux."""

    server = None

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    def __getattr__(self, nome):  # qualquer uso é um bug no handler
        raise AssertionError(f"shutdown não deveria chamar runtime.{nome}")


def _consumer(stop) -> CommandConsumer:
    c = CommandConsumer.__new__(CommandConsumer)
    c._host_id = "h_kaggle"
    c._shutdown_event = stop
    return c


@pytest.mark.asyncio
async def test_shutdown_seta_o_evento_do_runner() -> None:
    stop = _StopFake()
    out = await _consumer(stop)._handle_shutdown({})
    assert stop.setado is True, "sem isso o worker continua vivo e volta a 🟢"
    assert out.get("status") == "shutting_down"


@pytest.mark.asyncio
async def test_shutdown_sem_evento_nao_explode() -> None:
    """Worker sem evento injetado (testes/uso antigo) não pode quebrar."""
    c = _consumer(None)
    out = await c._handle_shutdown({})
    assert out.get("status") == "shutting_down"


@pytest.mark.asyncio
async def test_shutdown_esta_no_dispatch() -> None:
    """Registrado no dispatch — senão vira 'tipo de comando desconhecido'."""
    stop = _StopFake()
    c = _consumer(stop)
    out = await c._dispatch("shutdown", {})
    assert stop.setado is True
    assert out.get("status") == "shutting_down"


def test_runner_liga_o_evento_no_consumer() -> None:
    """O runner precisa injetar o evento — sem isso o handler é inerte.

    Regressão específica: um ``shutdown`` que responde ``ok`` mas não seta
    nada é pior que não existir, porque o botão parece funcionar e o host
    volta a 🟢 90s depois. Verificado na fonte (o teste de integração do
    runner exigiria Rabbit+mongo, desproporcional pra uma linha de fiação).
    """
    from pathlib import Path

    fonte = (
        Path(__file__).resolve().parents[1] / "runner.py"
    ).read_text(encoding="utf-8")
    assert "_shutdown_event" in fonte, "runner não conecta o evento de shutdown"
    # Tem que ser o MESMO evento que o `run(stop=...)` usa pra encerrar.
    assert "consumer._shutdown_event = stop" in fonte, (
        "o evento injetado precisa ser o `stop` do runner"
    )
