"""Testes unitários do aviso filho→pai (``handoff_notify``).

Sem Mongo/Rabbit reais: um fake mínimo de coleção motor (só os operadores que o
módulo usa) e um ``publish`` que grava as mensagens.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sessionflow_worker import handoff_notify as hn

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _match(doc: dict, query: dict) -> bool:
    for key, cond in query.items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$nin" and val in arg:
                    return False
                if op == "$gte" and (val is None or val < arg):
                    return False
                if op == "$regex" and not re.search(arg, val or ""):
                    return False
        elif val != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)


class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query, projection=None):
        return _Cursor(d for d in self.docs if _match(d, query))

    async def find_one(self, query, projection=None):
        return next((d for d in self.docs if _match(d, query)), None)

    async def update_one(self, query, update):
        for d in self.docs:
            if _match(d, query):
                d.update(update["$set"])
                return


class _Db(dict):
    def __missing__(self, name):
        self[name] = _Coll()
        return self[name]


def _setup(tmp_path: Path, **child):
    db = _Db()
    sent: list[tuple[str, dict]] = []
    doc = {
        "_id": 1, "tmux_name": "w1", "display_name": "w1", "parent": "chefe",
        "work_dir": str(tmp_path), "status": "running", "host_id": "duck",
        "created_at": NOW - timedelta(hours=1), "parent_notified_at": None,
    }
    doc.update(child)
    db["sessions"].docs += [doc, {"_id": 2, "tmux_name": "chefe", "host_id": "mac", "status": "running"}]
    return db, sent, doc


def _write_handoff(tmp_path: Path, name: str, status: str, age: timedelta) -> Path:
    path = tmp_path / ".sessionflow" / "handoff" / f"{name}.handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status}), encoding="utf-8")
    ts = (NOW - age).timestamp()
    os.utime(path, (ts, ts))
    return path


async def test_handoff_written_notifies_parent_on_its_host(tmp_path):
    db, sent, child = _setup(tmp_path)
    _write_handoff(tmp_path, "w1", "done", timedelta(minutes=5))

    marked = await hn.notify_once(db, publish=_pub(sent), host_id="duck", now=NOW)

    assert marked == ["w1"]
    (rk, body), = sent
    assert rk == "sessionflow.commands.mac"
    assert body["type"] == "input"
    assert body["payload"] == {
        "name": "chefe", "text": "✅ HANDOFF w1 status=done (aviso do worker)", "enter": True,
    }
    assert child["parent_notified_at"] == NOW


async def test_second_pass_does_not_duplicate(tmp_path):
    db, sent, _ = _setup(tmp_path)
    _write_handoff(tmp_path, "w1", "done", timedelta(minutes=5))
    await hn.notify_once(db, _pub(sent), "duck", now=NOW)
    await hn.notify_once(db, _pub(sent), "duck", now=NOW + timedelta(minutes=1))
    assert len(sent) == 1


async def test_grace_period_waits_for_agent_own_send(tmp_path):
    db, sent, child = _setup(tmp_path)
    _write_handoff(tmp_path, "w1", "done", timedelta(seconds=10))
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == [] and child["parent_notified_at"] is None


async def test_agent_already_told_parent_only_marks(tmp_path):
    db, sent, child = _setup(tmp_path)
    _write_handoff(tmp_path, "w1", "done", timedelta(minutes=5))
    db["session_output"].docs.append(
        {"tmux_name": "chefe", "text": "✅ HANDOFF w1 status=done", "at": NOW - timedelta(minutes=4)}
    )
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == ["w1"]
    assert sent == []
    assert child["parent_notified_at"] == NOW


async def test_stale_handoff_from_previous_run_is_ignored(tmp_path):
    db, sent, _ = _setup(tmp_path)
    _write_handoff(tmp_path, "w1", "done", timedelta(hours=3))  # antes da criação
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == []


async def test_session_ended_without_handoff_notifies(tmp_path):
    db, sent, _ = _setup(tmp_path, status="detached")
    await hn.notify_once(db, _pub(sent), "duck", now=NOW)
    (_, body), = sent
    assert body["payload"]["text"] == "✅ HANDOFF w1 status=sem-handoff (sessão detached) (aviso do worker)"


async def test_running_without_handoff_waits(tmp_path):
    db, sent, _ = _setup(tmp_path)
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == []


async def test_other_host_and_old_children_are_skipped(tmp_path):
    db, sent, _ = _setup(tmp_path, status="stopped")
    assert await hn.notify_once(db, _pub(sent), "mac", now=NOW) == []
    db2, sent2, _ = _setup(tmp_path, status="stopped", created_at=NOW - timedelta(days=3))
    assert await hn.notify_once(db2, _pub(sent2), "duck", now=NOW) == []
    assert sent == sent2 == []


async def test_handoff_found_by_display_name_when_slug_differs(tmp_path):
    db, sent, _ = _setup(tmp_path, tmux_name="worker-x", display_name="Worker_X")
    _write_handoff(tmp_path, "Worker_X", "blocked", timedelta(minutes=5))
    await hn.notify_once(db, _pub(sent), "duck", now=NOW)
    (_, body), = sent
    assert body["payload"]["text"].startswith("✅ HANDOFF Worker_X status=blocked")


async def test_missing_parent_marks_without_publishing(tmp_path):
    db, sent, child = _setup(tmp_path, parent="sumiu", status="stopped")
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == ["w1"]
    assert sent == []
    assert child["parent_notified_at"] == NOW


async def test_stopped_parent_is_not_resurrected(tmp_path):
    db, sent, child = _setup(tmp_path, status="stopped")
    db["sessions"].docs[1]["status"] = "stopped"
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == ["w1"]
    assert sent == []
    assert child["parent_notified_at"] == NOW


async def test_young_session_ended_without_handoff_waits(tmp_path):
    db, sent, _ = _setup(tmp_path, status="stopped", created_at=NOW - timedelta(seconds=20))
    assert await hn.notify_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == []


async def test_stopped_doc_but_tmux_alive_waits(tmp_path):
    db, sent, _ = _setup(tmp_path, status="stopped")
    marked = await hn.notify_once(db, _pub(sent), "duck", now=NOW, has_session=lambda n: n == "w1")
    assert marked == [] and sent == []


async def test_handoff_wins_even_if_doc_says_stopped(tmp_path):
    db, sent, _ = _setup(tmp_path, status="stopped", created_at=NOW - timedelta(minutes=10))
    _write_handoff(tmp_path, "w1", "done", timedelta(minutes=5))
    await hn.notify_once(db, _pub(sent), "duck", now=NOW, has_session=lambda n: True)
    (_, body), = sent
    assert body["payload"]["text"].startswith("✅ HANDOFF w1 status=done")


def _pub(sent):
    async def publish(rk, body):
        sent.append((rk, body))
    return publish
