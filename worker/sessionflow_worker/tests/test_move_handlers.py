"""Testes dos handlers ``move_out`` / ``move_in`` do CommandConsumer.

Sem tmux/Mongo/RabbitMQ reais: coleções em memória (``_FakeDb``), runtime
falso e ``publish`` capturado (eventos E comandos p/ outros hosts passam por
ele). ``HOME`` aponta pro ``tmp_path`` → ``~/.claude/projects`` isolado.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

from sessionflow_worker import command_consumer as cc_mod
from sessionflow_worker import session_move as sm
from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.command_consumer import CommandConsumer
from sessionflow_worker.rabbit import commands_queue_name

HOST_A = "host-a"
HOST_B = "host-b"
CLAUDE_SID = "11111111-2222-3333-4444-555555555555"


# -- fakes ------------------------------------------------------------------

def _get(doc: dict[str, Any], dotted: str) -> Any:
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class _FakeColl:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.updates: list[tuple[dict, dict]] = []

    def _match(self, doc: dict, flt: dict) -> bool:
        return all(_get(doc, k) == v for k, v in flt.items())

    async def find_one(self, flt: dict, projection: Any = None) -> dict | None:
        for d in self.docs:
            if self._match(d, flt):
                return d
        return None

    def find(self, flt: dict, projection: Any = None) -> "_FakeCursor":
        matched = [d for d in self.docs if self._match(d, flt)]
        if projection:
            keys = projection.keys() if hasattr(projection, "keys") else projection
            matched = [{k: d.get(k) for k in keys} for d in matched]
        return _FakeCursor(matched)

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(doc)

    async def delete_one(self, flt: dict) -> None:
        self.docs = [d for d in self.docs if not self._match(d, flt)]

    async def delete_many(self, flt: dict) -> None:
        self.docs = [d for d in self.docs if not self._match(d, flt)]

    async def update_one(self, flt: dict, update: dict, upsert: bool = False) -> Any:
        self.updates.append((flt, update))
        for d in self.docs:
            if self._match(d, flt):
                for k, v in update.get("$set", {}).items():
                    parts = k.split(".")
                    tgt = d
                    for p in parts[:-1]:
                        tgt = tgt.setdefault(p, {})
                    tgt[parts[-1]] = v
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                for k, v in update.get("$push", {}).items():
                    d.setdefault(k, []).append(v)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, _field: str, _direction: int) -> "_FakeCursor":
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeDb:
    def __init__(self) -> None:
        self.colls: dict[str, _FakeColl] = {}

    def __getitem__(self, name: str) -> _FakeColl:
        return self.colls.setdefault(name, _FakeColl())


class _FakeRuntime:
    def __init__(self, alive: set[str] | None = None) -> None:
        self.alive = set(alive or ())
        self.killed: list[str] = []
        self.created: list[tuple[str, str]] = []

    def has_session(self, name: str) -> bool:
        return name in self.alive

    def kill_session(self, name: str) -> None:
        self.alive.discard(name)
        self.killed.append(name)

    def new_session(self, name: str, work_dir: str) -> Any:
        self.created.append((name, work_dir))
        self.alive.add(name)
        return SimpleNamespace(id="$9")

    def agent_type(self, name: str) -> AgentType:
        return AgentType.CLAUDE


# -- fixtures ---------------------------------------------------------------

@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    sent: list[tuple[str, dict]] = []

    async def fake_publish(channel: Any, routing_key: str, payload: dict) -> None:
        sent.append((routing_key, payload))

    monkeypatch.setattr(cc_mod, "publish", fake_publish)
    return sent


@pytest.fixture(autouse=True)
def _isola(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(cc_mod, "_resource_block_reason", lambda: None)
    monkeypatch.setattr(cc_mod, "_ensure_claude_trust", lambda wd: None)


def _consumer(host_id: str, db: _FakeDb, runtime: _FakeRuntime) -> CommandConsumer:
    c = CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        host_id=host_id,
        runtime=runtime,  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )
    c.sent_keys: list[tuple[str, str]] = []  # type: ignore[misc]
    c._send_keys = lambda name, cmd, enter=True: c.sent_keys.append((name, cmd))  # type: ignore[method-assign]

    async def fake_shutdown(name: str) -> bool:
        return True

    c._shutdown_agent = fake_shutdown  # type: ignore[method-assign]
    c.agent_up = True  # type: ignore[attr-defined]

    async def fake_wait(name: str, timeout: float = 20.0) -> bool:
        return c.agent_up  # type: ignore[attr-defined]

    c._wait_agent_process = fake_wait  # type: ignore[method-assign]
    return c


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _session_doc(work_dir: str, **moving: Any) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "tmux_name": "proj",
        "host_id": HOST_A,
        "work_dir": work_dir,
        "agent_type": "claude",
        "claude_session_id": CLAUDE_SID,
        "status": "running",
        "moving": {
            "target_host_id": HOST_B,
            "target_work_dir": "/dest/proj",
            "phase": "exporting",
            "error": None,
            "started_at": datetime.now(timezone.utc),
            "transfer_id": None,
            **moving,
        },
    }


def _write_jsonl(home: Path, work_dir: str, text: str) -> Path:
    p = home / ".claude" / "projects" / "-src-proj" / f"{CLAUDE_SID}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _cmd(ctype: str, payload: dict) -> dict:
    return {
        "command_id": f"c-{ctype}-{ObjectId()}",
        "type": ctype,
        "payload": payload,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }


def _move_out_payload(doc: dict, *, git_origin: str | None = None) -> dict:
    return {
        "name": doc["tmux_name"],
        "session_id": str(doc["_id"]),
        "target_host_id": HOST_B,
        "target_work_dir": "/dest/proj",
        "git_origin": git_origin,
    }


# -- move_out ---------------------------------------------------------------

async def test_move_out_repo_sujo_nao_mata_agente(tmp_path: Path, published: list) -> None:
    repo = _repo(tmp_path / "src")
    (repo / "a.txt").write_text("sujo\n")
    db = _FakeDb()
    doc = _session_doc(str(repo))
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is False
    assert "mudanças não commitadas" in ev["error"]
    assert rt.killed == []  # agente segue rodando
    assert doc["status"] == "running"
    assert doc["moving"]["phase"] == "failed"
    assert "faça commit/push antes de mover" in doc["moving"]["error"]
    assert not any(rk == commands_queue_name(HOST_B) for rk, _ in published)


async def test_move_out_sucesso_grava_transfer_e_publica_move_in(
    tmp_path: Path, published: list
) -> None:
    repo = _repo(tmp_path / "src")
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    _git(repo, "remote", "add", "origin", origin)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    conteudo = '{"cwd":"%s","type":"user"}\n' % repo
    _write_jsonl(tmp_path / "home", str(repo), conteudo)
    db = _FakeDb()
    doc = _session_doc(str(repo))
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc, git_origin=origin)))

    assert ev["ok"] is True, ev
    assert rt.killed == ["proj"]
    assert doc["status"] == "stopped"
    assert doc["moving"]["phase"] == "importing"
    tid = doc["moving"]["transfer_id"]
    # Novo formato: 1 doc por chunk. _id = ``<tid>:NNNN``.
    chunk_docs = [d for d in db["session_transfers"].docs if d.get("transfer_id") == tid]
    assert chunk_docs, "nenhum doc de chunk gravado"
    assert chunk_docs[0]["session_id"] == str(doc["_id"])
    assert chunk_docs[0]["claude_session_id"] == CLAUDE_SID
    assert chunk_docs[0]["branch"] == "feat/x"
    assert chunk_docs[0]["source_host_id"] == HOST_A
    # Reader behavior (move_in): cursor ordenado por _id → concatena → unpack.
    raw = b"".join(bytes(d["data"]) for d in sorted(chunk_docs, key=lambda x: x["_id"]))
    assert sm.unpack([raw]).decode() == conteudo

    cmds = [p for rk, p in published if rk == commands_queue_name(HOST_B)]
    assert len(cmds) == 1
    assert cmds[0]["type"] == "move_in"
    assert cmds[0]["payload"] == {
        "name": "proj",
        "session_id": str(doc["_id"]),
        "transfer_id": tid,
        "target_work_dir": "/dest/proj",
        "branch": "feat/x",
        "git_origin": origin,
        "source_host_id": HOST_A,
    }


async def test_move_out_sem_origin_publica_none_no_payload(
    tmp_path: Path, published: list
) -> None:
    """Sem remote no repo A, o payload manda ``git_origin=None`` (resiliente:
    destino que não tem a pasta vai falhar com mensagem clara pedindo setup)."""
    repo = _repo(tmp_path / "src")
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _write_jsonl(tmp_path / "home", str(repo), "{}\n")
    db = _FakeDb()
    doc = _session_doc(str(repo))
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is True
    cmds = [p for rk, p in published if rk == commands_queue_name(HOST_B)]
    assert cmds[0]["payload"]["git_origin"] is None


async def test_move_out_jsonl_ausente_falha_sem_parar(tmp_path: Path, published: list) -> None:
    repo = _repo(tmp_path / "src")
    db = _FakeDb()
    doc = _session_doc(str(repo))
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is False
    assert "Conversa do claude não encontrada" in ev["error"]
    assert rt.killed == []
    assert doc["moving"]["phase"] == "failed"


async def test_move_out_falha_apos_parar_relanca_local(
    tmp_path: Path, published: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "src")
    _write_jsonl(tmp_path / "home", str(repo), "{}\n")
    db = _FakeDb()
    doc = _session_doc(str(repo))
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    def grande(data: bytes) -> list[bytes]:
        raise ValueError("conversa grande demais para mover (20 MB comprimida)")

    monkeypatch.setattr(cc_mod.session_move, "pack", grande)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is False
    assert "grande demais" in ev["error"]
    assert rt.killed == ["proj"]
    assert rt.created == [("proj", str(repo))]  # rollback: relançou em A
    assert c.sent_keys and "--resume" in c.sent_keys[-1][1]
    assert doc["status"] == "running"
    assert doc["moving"]["phase"] == "failed"
    assert db["session_transfers"].docs == []


async def test_move_out_sem_moving_e_ignorado(tmp_path: Path, published: list) -> None:
    db = _FakeDb()
    doc = _session_doc(str(tmp_path))
    del doc["moving"]
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is True
    assert ev["skipped"] == "not-moving"
    assert rt.killed == []


# -- move_in ----------------------------------------------------------------

def _seed_transfer_chunks(coll: _FakeColl, transfer_id: str, chunks: list[bytes], **meta) -> None:
    """Insere 1 doc por chunk (formato novo). Marca o índice 0000 com
    metadados (session_id/claude_session_id/branch/source_host_id) —
    o reader faz find_one por ``<tid>:0000`` pra descobri-los."""
    if not chunks:
        coll.docs.append({
            "_id": f"{transfer_id}:0000",
            "transfer_id": transfer_id,
            "index": 0,
            "data": b"",
            "session_id": meta.get("session_id"),
            "claude_session_id": meta.get("claude_session_id"),
            "branch": meta.get("branch"),
            "source_host_id": meta.get("source_host_id"),
            "created_at": datetime.now(timezone.utc),
        })
        return
    for idx, data in enumerate(chunks):
        d = {
            "_id": f"{transfer_id}:{idx:04d}",
            "transfer_id": transfer_id,
            "index": idx,
            "data": data,
            "created_at": datetime.now(timezone.utc),
        }
        if idx == 0:
            d.update(meta)
        coll.docs.append(d)


def _setup_move_in(tmp_path: Path, chunks: list[bytes], *, dest_dirty: bool = False):
    dest = _repo(tmp_path / "dest" / "proj")
    if dest_dirty:
        (dest / "a.txt").write_text("sujo\n")
    db = _FakeDb()
    doc = _session_doc("/src/proj", phase="importing", transfer_id="t-1",
                       target_work_dir=str(dest))
    doc["status"] = "stopped"
    db["sessions"].docs.append(doc)
    _seed_transfer_chunks(
        db["session_transfers"], "t-1", chunks,
        session_id=str(doc["_id"]),
        claude_session_id=CLAUDE_SID,
        branch=None,
        source_host_id=HOST_A,
    )
    payload = {
        "name": "proj",
        "session_id": str(doc["_id"]),
        "transfer_id": "t-1",
        "target_work_dir": str(dest),
        "branch": None,
        "source_host_id": HOST_A,
    }
    return dest, db, doc, payload


def _setup_move_in_clone(tmp_path: Path, chunks: list[bytes], origin: str):
    """Sessão chega num host novo: pasta do repo NÃO existe. Com ``git_origin``
    no payload, o worker deve clonar e prosseguir."""
    target = tmp_path / "host-novo" / "proj"  # não cria — dir inexistente
    db = _FakeDb()
    doc = _session_doc("/src/proj", phase="importing", transfer_id="t-2",
                       target_work_dir=str(target))
    doc["status"] = "stopped"
    db["sessions"].docs.append(doc)
    _seed_transfer_chunks(
        db["session_transfers"], "t-2", chunks,
        session_id=str(doc["_id"]),
        claude_session_id=CLAUDE_SID,
        branch="main",
        source_host_id=HOST_A,
    )
    payload = {
        "name": "proj",
        "session_id": str(doc["_id"]),
        "transfer_id": "t-2",
        "target_work_dir": str(target),
        "branch": "main",
        "git_origin": origin,
        "source_host_id": HOST_A,
    }
    return target, db, doc, payload


async def test_move_in_sucesso(tmp_path: Path, published: list) -> None:
    original = '{"cwd":"/src/proj","type":"user"}\nlixo {\n'
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(original.encode()))
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is True, ev
    assert ev["type"] == "move_in"
    assert ev["session_id"] == str(doc["_id"])
    assert ev["host_id"] == HOST_B
    assert doc["host_id"] == HOST_B
    assert doc["work_dir"] == str(dest)
    assert doc["status"] == "running"
    assert "moving" not in doc
    assert isinstance(doc["moved_at"], datetime)
    assert doc["move_history"][0]["from"] == HOST_A
    assert doc["move_history"][0]["to"] == HOST_B
    assert db["session_transfers"].docs == []
    # Uma única operação faz host/work_dir/status + $unset moving + $push.
    flt, upd = db["sessions"].updates[-1]
    assert upd["$set"]["host_id"] == HOST_B and "moving" in upd["$unset"]
    assert "move_history" in upd["$push"]

    jsonl = sm.jsonl_target_path(str(dest), CLAUDE_SID)
    assert jsonl.read_text() == '{"cwd":"%s","type":"user"}\nlixo {\n' % dest
    assert rt.created == [("proj", str(dest))]
    assert CLAUDE_SID in c.sent_keys[-1][1]
    assert not any(rk == commands_queue_name(HOST_A) for rk, _ in published)


async def test_move_in_falha_git_faz_rollback_para_a(tmp_path: Path, published: list) -> None:
    dest, db, doc, payload = _setup_move_in(
        tmp_path, sm.pack(b"{}\n"), dest_dirty=True
    )
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert "mudanças não commitadas" in ev["error"]
    assert doc["host_id"] == HOST_A
    assert doc["moving"]["phase"] == "failed"
    assert "mudanças não commitadas" in doc["moving"]["error"]
    assert db["session_transfers"].docs == []
    assert rt.created == []
    cmds = [p for rk, p in published if rk == commands_queue_name(HOST_A)]
    assert len(cmds) == 1
    assert cmds[0]["type"] == "resume"
    assert cmds[0]["payload"] == {"name": "proj"}


async def test_move_in_idempotente_transfer_diferente(tmp_path: Path, published: list) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    doc["moving"]["transfer_id"] = "outro"
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is True
    assert ev["skipped"] == "not-moving"
    assert rt.created == []
    assert doc["host_id"] == HOST_A
    assert len(db["session_transfers"].docs) == 1  # não é dele: não apaga
    assert not any(rk == commands_queue_name(HOST_A) for rk, _ in published)


async def test_move_in_tmux_homonimo_no_destino_faz_rollback(
    tmp_path: Path, published: list
) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert rt.killed == []  # não mexe na sessão alheia
    assert doc["moving"]["phase"] == "failed"
    assert any(
        rk == commands_queue_name(HOST_A) and p["type"] == "resume" for rk, p in published
    )


async def test_move_in_falha_apos_criar_tmux_mata_sessao(
    tmp_path: Path, published: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    def boom(name: str, cmd: str, enter: bool = True) -> None:
        raise cc_mod.CommandError("send-keys falhou")

    c._send_keys = boom  # type: ignore[method-assign]

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert rt.created == [("proj", str(dest))]
    assert rt.killed == ["proj"]
    assert "proj" not in rt.alive
    assert doc["moving"]["phase"] == "failed"
    assert any(
        rk == commands_queue_name(HOST_A) and p["type"] == "resume" for rk, p in published
    )

async def test_move_in_agente_nao_sobe_faz_rollback(tmp_path: Path, published: list) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)
    c.agent_up = False  # type: ignore[attr-defined]

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert "não subiu" in ev["error"]
    assert rt.killed == ["proj"]
    assert doc["host_id"] == HOST_A
    assert doc["work_dir"] == "/src/proj"
    assert doc["moving"]["phase"] == "failed"
    assert db["session_transfers"].docs == []
    cmds = [p for rk, p in published if rk == commands_queue_name(HOST_A)]
    assert [p["type"] for p in cmds] == ["resume"]

async def test_move_in_pasta_inexistente_com_origin_clona_e_prossegue(
    tmp_path: Path, published: list
) -> None:
    """Cenário cross-host resiliente: host novo sem a pasta do repo. O
    ``git_origin`` no payload permite auto-clonar antes de relançar o agente."""
    # Origem real (bare) — o clone do destino vai puxar daqui.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = _repo(tmp_path / "src")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "-u", "origin", "main")

    target, db, doc, payload = _setup_move_in_clone(
        tmp_path, sm.pack(b'{"cwd":"/src/proj","type":"user"}\n'),
        origin=str(origin),
    )
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is True, ev
    assert ev["type"] == "move_in"
    assert ev["host_id"] == HOST_B
    assert target.is_dir(), "clone não criou o diretório"
    assert (target / ".git").exists(), "clone não criou .git"
    assert doc["host_id"] == HOST_B
    assert doc["work_dir"] == str(target)
    assert doc["status"] == "running"
    assert rt.created == [("proj", str(target))]
    # Rollback pro A NÃO é publicado — sucesso limpa o transfer direto.
    assert not any(rk == commands_queue_name(HOST_A) for rk, _ in published)


async def test_move_in_pasta_inexistente_sem_origin_falha_com_mensagem_clara(
    tmp_path: Path, published: list
) -> None:
    """Sem pasta e sem ``git_origin`` no payload: erro pt-BR claro pedindo setup."""
    target = tmp_path / "host-novo" / "proj"  # não existe
    db = _FakeDb()
    doc = _session_doc("/src/proj", phase="importing", transfer_id="t-3",
                       target_work_dir=str(target))
    doc["status"] = "stopped"
    db["sessions"].docs.append(doc)
    _seed_transfer_chunks(
        db["session_transfers"], "t-3", sm.pack(b"{}\n"),
        session_id=str(doc["_id"]),
        claude_session_id=CLAUDE_SID,
        branch=None,
        source_host_id=HOST_A,
    )
    payload = {
        "name": "proj",
        "session_id": str(doc["_id"]),
        "transfer_id": "t-3",
        "target_work_dir": str(target),
        "branch": None,
        "git_origin": None,  # A não tem remote configurado
        "source_host_id": HOST_A,
    }
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert "não encontrada" in ev["error"]
    assert "Configure o repositório" in ev["error"]
    assert "auto-clone" in ev["error"]
    assert doc["moving"]["phase"] == "failed"
    assert doc["host_id"] == HOST_A  # rollback: sessão não migrou
    assert rt.created == []
    # Rollback pro A: publica ``resume``.
    assert any(
        rk == commands_queue_name(HOST_A) and p["type"] == "resume"
        for rk, p in published
    )


async def test_move_in_clone_falho_faz_rollback_para_a(
    tmp_path: Path, published: list
) -> None:
    """Origin aponta pra URL inválida → clone falha → rollback pro A."""
    target = tmp_path / "host-novo" / "proj"
    db = _FakeDb()
    doc = _session_doc("/src/proj", phase="importing", transfer_id="t-4",
                       target_work_dir=str(target))
    doc["status"] = "stopped"
    db["sessions"].docs.append(doc)
    _seed_transfer_chunks(
        db["session_transfers"], "t-4", sm.pack(b"{}\n"),
        session_id=str(doc["_id"]),
        claude_session_id=CLAUDE_SID,
        branch=None,
        source_host_id=HOST_A,
    )
    payload = {
        "name": "proj",
        "session_id": str(doc["_id"]),
        "transfer_id": "t-4",
        "target_work_dir": str(target),
        "branch": None,
        "git_origin": "https://example.com/nao-existe.git",
        "source_host_id": HOST_A,
    }
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert "git clone" in ev["error"]
    assert doc["moving"]["phase"] == "failed"
    assert doc["host_id"] == HOST_A
    assert not target.exists(), "clone falhou: dir não deveria ter sido criado"
    assert rt.created == []
    assert any(
        rk == commands_queue_name(HOST_A) and p["type"] == "resume"
        for rk, p in published
    )


async def test_move_in_moving_limpo_no_meio_mata_destino(
    tmp_path: Path, published: list
) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    async def wait_and_clear(name: str, timeout: float = 20.0) -> bool:
        doc.pop("moving", None)  # stale/Retomar na origem enquanto subia
        return True

    c._wait_agent_process = wait_and_clear  # type: ignore[method-assign]

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is True
    assert ev["skipped"] == "move-cancelled"
    assert rt.killed == ["proj"]
    assert doc["host_id"] == HOST_A
    assert "move_history" not in doc

async def test_move_in_claude_session_id_invalido_faz_rollback(
    tmp_path: Path, published: list
) -> None:
    dest, db, doc, payload = _setup_move_in(tmp_path, sm.pack(b"{}\n"))
    db["session_transfers"].docs[0]["claude_session_id"] = "../../x"
    rt = _FakeRuntime()
    c = _consumer(HOST_B, db, rt)

    ev = await c.handle(_cmd("move_in", payload))

    assert ev["ok"] is False
    assert "claude_session_id inválido" in ev["error"]
    assert rt.created == []
    assert doc["moving"]["phase"] == "failed"

async def test_move_out_claude_session_id_invalido_falha_sem_parar(
    tmp_path: Path, published: list
) -> None:
    repo = _repo(tmp_path / "src")
    db = _FakeDb()
    doc = _session_doc(str(repo))
    doc["claude_session_id"] = "*"
    db["sessions"].docs.append(doc)
    rt = _FakeRuntime(alive={"proj"})
    c = _consumer(HOST_A, db, rt)

    ev = await c.handle(_cmd("move_out", _move_out_payload(doc)))

    assert ev["ok"] is False
    assert "claude_session_id inválido" in ev["error"]
    assert rt.killed == []
    assert doc["moving"]["phase"] == "failed"

def test_tipos_registrados() -> None:
    assert {"move_out", "move_in"} <= cc_mod._VALID_TYPES
