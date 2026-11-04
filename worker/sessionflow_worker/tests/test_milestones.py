"""Testes de ``milestones``: ``all_done`` e o fechamento da lista em ``sync_session``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sessionflow_worker.milestones import all_done, sync_session


def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$nin" in v:
            if doc.get(k) in v["$nin"]:
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if doc.get(k) == v["$ne"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _FakeColl:
    """Só o que o ``sync_session`` usa: find_one/update_one(upsert)/delete_many."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], projection: Any = None) -> Any:
        return next((dict(d) for d in self.docs if _match(d, query)), None)

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        doc = next((d for d in self.docs if _match(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = {}
            self.docs.append(doc)
        doc.update(update.get("$set", {}))

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.docs = [d for d in self.docs if not _match(d, query)]


class _FakeDB(dict):
    def __missing__(self, key: str) -> _FakeColl:
        self[key] = _FakeColl()
        return self[key]


def _write(work_dir: Path, states: list[str]) -> None:
    base = work_dir / ".sessionflow"
    base.mkdir(exist_ok=True)
    items = [{"id": f"m{i}", "title": f"Marco {i}", "status": s} for i, s in enumerate(states)]
    (base / "milestones.s1.json").write_text(json.dumps({"milestones": items}), "utf-8")


def test_all_done() -> None:
    assert all_done([{"state": "done"}, {"state": "done"}])
    assert not all_done([{"state": "done"}, {"state": "todo"}])
    assert not all_done([])  # lista vazia não é "concluída"


async def test_sync_session_fecha_lista_so_na_transicao(tmp_path: Path) -> None:
    db = _FakeDB()
    _write(tmp_path, ["done", "doing"])
    newly, closed = await sync_session(db, "s1", str(tmp_path), allow_shared=False)
    assert (newly, closed) == ([], False)  # 1ª passada: só importa

    _write(tmp_path, ["done", "done"])
    newly, closed = await sync_session(db, "s1", str(tmp_path), allow_shared=False)
    assert [m["mid"] for m in newly] == ["m1"]
    assert closed is True  # última aberta virou done → fanfarra

    newly, closed = await sync_session(db, "s1", str(tmp_path), allow_shared=False)
    assert (newly, closed) == ([], False)  # ciclo repetido não re-dispara


async def test_sync_session_importar_ja_concluida_nao_dispara(tmp_path: Path) -> None:
    db = _FakeDB()
    _write(tmp_path, ["done", "done"])
    assert await sync_session(db, "s1", str(tmp_path), allow_shared=False) == ([], False)


async def test_sync_session_done_parcial_nao_fecha(tmp_path: Path) -> None:
    db = _FakeDB()
    _write(tmp_path, ["doing", "todo"])
    await sync_session(db, "s1", str(tmp_path), allow_shared=False)
    _write(tmp_path, ["done", "todo"])
    newly, closed = await sync_session(db, "s1", str(tmp_path), allow_shared=False)
    assert [m["mid"] for m in newly] == ["m0"]
    assert closed is False
