"""Unit tests puros para `_ensure_work_dir` + `_is_colab_host` no CommandConsumer.

Cobre:
- Colab + work_dir ausente → cria a pasta, retorna None.
- Colab + work_dir já existe → no-op, retorna None.
- Não-Colab + work_dir ausente → NÃO cria, retorna None.
- Colab + mkdir falha (PermissionError) → retorna string de erro legível.

Mock no Mongo (`_db["worker_status"].find_one`) + tmp_path real pra mkdir.
NÃO usa tmux/Rabbit —`_ensure_work_dir` é puro.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sessionflow_worker.command_consumer import CommandConsumer


class _FakeWorkerStatusColl:
    """Mock de ``db["worker_status"]`` para ``_is_colab_host``."""

    def __init__(self, doc: dict | None) -> None:
        self._doc = doc
        self.find_one = AsyncMock(return_value=doc)


class _FakeDb:
    """Mock de Mongo db: ``db["worker_status"]`` devolve nosso fake."""

    def __init__(self, worker_status_doc: dict | None) -> None:
        self._worker_status = _FakeWorkerStatusColl(worker_status_doc)

    def __getitem__(self, name: str) -> _FakeWorkerStatusColl:
        assert name == "worker_status"
        return self._worker_status


@pytest.fixture
def consumer() -> CommandConsumer:
    """Consumer mínimo — não inicializa Rabbit/tmux, só  para chamar métodos.

    O __init__ completo do CommandConsumer exige Rabbit channel; aqui só
    injetamos os atributos necessários para `_is_colab_host` e
    `_ensure_work_dir`.
    """
    c = CommandConsumer.__new__(CommandConsumer)
    c._host_id = "test-host"
    c._db = _FakeDb(None)  # default: sem doc, sem nome; cai no host_id
    return c


async def test_is_colab_host_by_display_name(consumer: CommandConsumer) -> None:
    consumer._db = _FakeDb({"display_name": "Colab Pro T4", "hostname": "host"})
    assert await consumer._is_colab_host("any-id") is True


async def test_is_colab_host_by_host_id(consumer: CommandConsumer) -> None:
    consumer._db = _FakeDb({"display_name": "Pro T4", "hostname": "host"})
    assert await consumer._is_colab_host("colab-worker-1") is True


async def test_is_colab_host_false(consumer: CommandConsumer) -> None:
    consumer._db = _FakeDb({"display_name":   "MacBook Air", "hostname": "mac"})
    assert await consumer._is_colab_host("h1") is False


async def test_is_colab_host_no_doc_falls_back_to_host_id(
    consumer: CommandConsumer,
) -> None:
    consumer._db = _FakeDb(None)
    assert await consumer._is_colab_host("colab-x") is True
    assert await consumer._is_colab_host("h1") is False


async def test_ensure_work_dir_creates_on_colab(
    consumer: CommandConsumer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer._db = _FakeDb({"display_name": "Colab 1 (CPU)"})
    target = tmp_path / "nova-pasta"
    assert not target.exists()

    err = await consumer._ensure_work_dir(str(target), "colab1")
    assert err is None
    assert target.exists()
    assert target.is_dir()


async def test_ensure_work_dir_existing_path_is_noop(
    consumer: CommandConsumer, tmp_path: Path
) -> None:
    consumer._db = _FakeDb({"display_name": "Colab 1 (CPU)"})
    target = tmp_path / "existente"
    target.mkdir()

    err = await consumer._ensure_work_dir(str(target), "colab1")
    assert err is None
    assert target.exists()


async def test_ensure_work_dir_skips_on_non_colab(
    consumer: CommandConsumer, tmp_path: Path
) -> None:
    consumer._db = _FakeDb({"display_name": "MacBook Air", "hostname": "mac"})
    target = tmp_path / "nao-vai-criar"

    err = await consumer._ensure_work_dir(str(target), "h-mac")
    assert err is None
    assert not target.exists()


async def test_ensure_work_dir_returns_error_on_failure(
    consumer: CommandConsumer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer._db = _FakeDb({"display_name": "Colab 1 (CPU)"})
    # Apontar pra um path impossível de criar (path inclui um arquivo
    # usado como pai — mkdir em pai que é arquivo falha com NotADirectoryError).
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    target = blocker / "filho"  # pai é arquivo regular

    err = await consumer._ensure_work_dir(str(target), "colab1")
    assert err is not None
    assert "NotADirectoryError" in err or "Error" in err