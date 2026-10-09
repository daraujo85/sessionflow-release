"""Testes do handler ``fs_fetch`` (P2P host-to-host, sem sessão).

Sem Mongo/RabbitMQ reais: ``command_results`` em memória. ``HOME`` aponta pro
``tmp_path`` → o guard "só dentro de $HOME ou /tmp" é exercitado de verdade.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from sessionflow_worker.command_consumer import CommandConsumer, CommandError

HOST = "host-a"


class _FakeResults:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        self.docs.setdefault(flt["_id"], {}).update(update["$set"])


class _FakeDb:
    def __init__(self) -> None:
        self.results = _FakeResults()

    def __getitem__(self, name: str) -> _FakeResults:
        assert name == "command_results"
        return self.results


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _consumer(db: _FakeDb) -> CommandConsumer:
    return CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        host_id=HOST,
        runtime=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )


async def test_le_texto_e_grava_resultado(home: Path) -> None:
    f = home / "log.txt"
    f.write_text("olá mundo\n")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_fetch({"path": str(f)}, "cmd-1")
    assert out["truncated"] is False
    doc = db.results.docs["cmd-1"]
    assert doc["ok"] is True and doc["host_id"] == HOST and doc["type"] == "fs_fetch"
    assert doc["result"]["text"] == "olá mundo\n"
    assert doc["result"]["binary"] is False


async def test_til_expande_para_home(home: Path) -> None:
    (home / "a.txt").write_text("x")
    db = _FakeDb()
    await _consumer(db)._handle_fs_fetch({"path": "~/a.txt"}, "cmd-1")
    assert db.results.docs["cmd-1"]["result"]["text"] == "x"


@pytest.mark.parametrize("path", ["/etc/passwd", "/", "~/../../etc/hosts"])
async def test_recusa_fora_de_home_e_tmp(home: Path, path: str) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="fora de"):
        await _consumer(db)._handle_fs_fetch({"path": path}, "cmd-1")
    # erro também persiste → requester não fica em polling eterno
    assert db.results.docs["cmd-1"]["ok"] is False


async def test_recusa_symlink_que_aponta_para_fora(home: Path) -> None:
    (home / "atalho").symlink_to("/etc/hosts")
    db = _FakeDb()
    with pytest.raises(CommandError, match="fora de"):
        await _consumer(db)._handle_fs_fetch({"path": str(home / "atalho")}, "cmd-1")


async def test_binario_volta_base64(home: Path) -> None:
    raw = b"\x89PNG\x00\x01\x02"
    (home / "img.png").write_bytes(raw)
    db = _FakeDb()
    await _consumer(db)._handle_fs_fetch({"path": str(home / "img.png")}, "cmd-1")
    res = db.results.docs["cmd-1"]["result"]
    assert res["binary"] is True
    assert base64.b64decode(res["content_b64"]) == raw


async def test_trunca_acima_do_limite(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CommandConsumer, "_FS_FETCH_MAX_BYTES", 10)
    (home / "big.txt").write_text("a" * 50)
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_fetch({"path": str(home / "big.txt")}, "cmd-1")
    res = db.results.docs["cmd-1"]["result"]
    assert out["truncated"] is True and res["truncated"] is True
    assert res["returned_bytes"] == 10 and res["size_bytes"] == 50
    assert res["text"] == "a" * 10


@pytest.mark.parametrize(
    ("payload", "msg"),
    [({}, "requer 'path'"), ({"path": "~/nao-existe"}, "não encontrado")],
)
async def test_erros_de_entrada(home: Path, payload: dict, msg: str) -> None:
    with pytest.raises(CommandError, match=msg):
        await _consumer(_FakeDb())._handle_fs_fetch(payload, "cmd-1")


async def test_diretorio_e_recusado(home: Path) -> None:
    (home / "pasta").mkdir()
    with pytest.raises(CommandError, match="diretório"):
        await _consumer(_FakeDb())._handle_fs_fetch({"path": str(home / "pasta")}, "cmd-1")
