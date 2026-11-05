"""Testes do handler ``fs_op`` — operações de arquivo endereçadas por SESSÃO.

Diferente do ``fs_fetch`` (host-to-host, read-only), aqui o chamador aponta uma
SESSÃO e opera no host/pasta dela: listar, ler, escrever, copiar, criar pasta e
remover. ``work_dir`` da sessão é a raiz preferida (path relativo resolve contra
ela); absolutos seguem o guard de ``$HOME``/``/tmp``.

Sem Mongo/RabbitMQ: ``command_results`` em memória, ``HOME`` no ``tmp_path``.
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


@pytest.fixture
def work(home: Path) -> Path:
    w = home / "proj"
    w.mkdir()
    return w


def _consumer(db: _FakeDb) -> CommandConsumer:
    return CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        host_id=HOST,
        runtime=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
async def test_list_mostra_arquivos_e_dir_com_tamanho(home: Path, work: Path) -> None:
    (work / "a.txt").write_text("abc")
    (work / "sub").mkdir()
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "list", "path": ".", "work_dir": str(work)}, "cmd-1"
    )
    names = {e["name"]: e for e in out["entries"]}
    assert names["a.txt"]["type"] == "file"
    assert names["a.txt"]["size"] == 3
    assert names["sub"]["type"] == "dir"
    assert db.results.docs["cmd-1"]["type"] == "fs_op"


async def test_list_ordena_dirs_primeiro_e_respeita_limit(home: Path, work: Path) -> None:
    for i in range(10):
        (work / f"f{i}.txt").write_text("x")
    (work / "zzz_dir").mkdir()
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "list", "path": ".", "work_dir": str(work), "limit": 3}, "cmd-1"
    )
    assert len(out["entries"]) == 3
    assert out["entries"][0]["type"] == "dir"
    assert out["truncated"] is True


async def test_list_nao_desce_a_arvore(home: Path, work: Path) -> None:
    deep = work / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("x")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "list", "path": ".", "work_dir": str(work)}, "cmd-1"
    )
    assert [e["name"] for e in out["entries"]] == ["a"]


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
async def test_read_texto(home: Path, work: Path) -> None:
    (work / "n.txt").write_text("conteudo\n")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "read", "path": "n.txt", "work_dir": str(work)}, "cmd-1"
    )
    assert out["text"] == "conteudo\n"
    assert out["binary"] is False
    assert out["returned_bytes"] == 9


async def test_read_binario_volta_base64(home: Path, work: Path) -> None:
    (work / "b.bin").write_bytes(b"\x00\x01\x02")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "read", "path": "b.bin", "work_dir": str(work)}, "cmd-1"
    )
    assert out["binary"] is True
    assert base64.b64decode(out["content_b64"]) == b"\x00\x01\x02"


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
async def test_write_cria_arquivo(home: Path, work: Path) -> None:
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "write", "path": "novo.txt", "work_dir": str(work),
         "content": "oi"}, "cmd-1"
    )
    assert (work / "novo.txt").read_text() == "oi"
    assert out["bytes_written"] == 2
    assert out["created"] is True


async def test_write_cria_dirs_intermediarios(home: Path, work: Path) -> None:
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "write", "path": "a/b/c.txt", "work_dir": str(work),
         "content": "x"}, "cmd-1"
    )
    assert (work / "a" / "b" / "c.txt").read_text() == "x"


async def test_write_base64_para_binario(home: Path, work: Path) -> None:
    payload = base64.b64encode(b"\x00\xff").decode()
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "write", "path": "x.bin", "work_dir": str(work),
         "content_b64": payload}, "cmd-1"
    )
    assert (work / "x.bin").read_bytes() == b"\x00\xff"


async def test_write_append_preserva_conteudo(home: Path, work: Path) -> None:
    (work / "log.txt").write_text("linha1\n")
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "write", "path": "log.txt", "work_dir": str(work),
         "content": "linha2\n", "append": True}, "cmd-1"
    )
    assert (work / "log.txt").read_text() == "linha1\nlinha2\n"


async def test_write_reporta_overwrite_false_quando_ja_existia(home: Path, work: Path) -> None:
    (work / "existe.txt").write_text("velho")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "write", "path": "existe.txt", "work_dir": str(work),
         "content": "novo"}, "cmd-1"
    )
    assert out["created"] is False


# --------------------------------------------------------------------------- #
# copy
# --------------------------------------------------------------------------- #
async def test_copy_arquivo(home: Path, work: Path) -> None:
    (work / "orig.txt").write_text("dados")
    db = _FakeDb()
    out = await _consumer(db)._handle_fs_op(
        {"op": "copy", "path": "orig.txt", "dest": "copia.txt",
         "work_dir": str(work)}, "cmd-1"
    )
    assert (work / "copia.txt").read_text() == "dados"
    assert out["bytes_copied"] == 5


async def test_copy_dir_recursivo(home: Path, work: Path) -> None:
    (work / "src").mkdir()
    (work / "src" / "a.txt").write_text("a")
    (work / "src" / "sub").mkdir()
    (work / "src" / "sub" / "b.txt").write_text("b")
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "copy", "path": "src", "dest": "dst", "work_dir": str(work)}, "cmd-1"
    )
    assert (work / "dst" / "a.txt").read_text() == "a"
    assert (work / "dst" / "sub" / "b.txt").read_text() == "b"


async def test_copy_recusa_destino_ja_existente(home: Path, work: Path) -> None:
    (work / "a.txt").write_text("x")
    (work / "b.txt").write_text("y")
    db = _FakeDb()
    with pytest.raises(CommandError, match="destino já existe"):
        await _consumer(db)._handle_fs_op(
            {"op": "copy", "path": "a.txt", "dest": "b.txt",
             "work_dir": str(work)}, "cmd-1"
        )
    assert db.results.docs["cmd-1"]["ok"] is False


# --------------------------------------------------------------------------- #
# mkdir / delete
# --------------------------------------------------------------------------- #
async def test_mkdir(home: Path, work: Path) -> None:
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "mkdir", "path": "nova/pasta", "work_dir": str(work)}, "cmd-1"
    )
    assert (work / "nova" / "pasta").is_dir()


async def test_delete_arquivo(home: Path, work: Path) -> None:
    (work / "lixo.txt").write_text("x")
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "delete", "path": "lixo.txt", "work_dir": str(work)}, "cmd-1"
    )
    assert not (work / "lixo.txt").exists()


async def test_delete_dir_nao_vazio_exige_recursive(home: Path, work: Path) -> None:
    (work / "d").mkdir()
    (work / "d" / "f.txt").write_text("x")
    db = _FakeDb()
    with pytest.raises(CommandError, match="não vazio"):
        await _consumer(db)._handle_fs_op(
            {"op": "delete", "path": "d", "work_dir": str(work)}, "cmd-1"
        )


async def test_delete_recursive_remove_arvore(home: Path, work: Path) -> None:
    (work / "d").mkdir()
    (work / "d" / "f.txt").write_text("x")
    db = _FakeDb()
    await _consumer(db)._handle_fs_op(
        {"op": "delete", "path": "d", "work_dir": str(work), "recursive": True}, "cmd-1"
    )
    assert not (work / "d").exists()


# --------------------------------------------------------------------------- #
# Segurança / validação
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("op", ["list", "read", "write", "copy", "mkdir", "delete"])
async def test_recusa_absoluto_fora_de_home(tmp_path: Path, home: Path, op: str) -> None:
    outside = tmp_path / "fora" / "x.txt"
    outside.parent.mkdir()
    outside.write_text("segredo")
    db = _FakeDb()
    payload: dict[str, Any] = {"op": op, "path": str(outside), "work_dir": str(home)}
    if op == "write":
        payload["content"] = "x"
    if op == "copy":
        payload["dest"] = str(home / "y")
    with pytest.raises(CommandError, match="fora de"):
        await _consumer(db)._handle_fs_op(payload, "cmd-1")
    # erro persiste → quem chamou não fica em polling eterno
    assert db.results.docs["cmd-1"]["ok"] is False


async def test_relativo_sem_work_dir_e_recusado(home: Path) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="work_dir"):
        await _consumer(db)._handle_fs_op({"op": "list", "path": "."}, "cmd-1")


async def test_op_desconhecida_e_erro(home: Path, work: Path) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="op desconhecida"):
        await _consumer(db)._handle_fs_op(
            {"op": "rm_rf", "path": ".", "work_dir": str(work)}, "cmd-1"
        )


async def test_work_dir_fora_de_home_e_recusado(tmp_path: Path, home: Path) -> None:
    outside = tmp_path / "proj"
    outside.mkdir()
    db = _FakeDb()
    with pytest.raises(CommandError, match="fora de"):
        await _consumer(db)._handle_fs_op(
            {"op": "list", "path": ".", "work_dir": str(outside)}, "cmd-1"
        )


async def test_escape_com_dotdot_e_recusado(home: Path, work: Path) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="fora de"):
        await _consumer(db)._handle_fs_op(
            {"op": "read", "path": str(work / ".." / ".." / "etc" / "hosts"),
             "work_dir": str(work)}, "cmd-1"
        )
