"""Boot-time stale-session heal (mock, sem Mongo/tmux reais).

Cobre ``reconcile_stale_sessions_on_startup``: sessões ativas deste host cujo
tmux sumiu no boot são marcadas ``stopped``; sessões vivas ou de outros
status/host permanecem intactas. Falhas individuais não abortam o heal.
"""

from __future__ import annotations


from sessionflow_worker.runner import reconcile_stale_sessions_on_startup


class _FakeRuntime:
    def __init__(self, alive: set[str]) -> None:
        self._alive = set(alive)

    def has_session(self, name: str) -> bool:
        return name in self._alive


class _FakeCursor:
    def __init__(self, data: list[dict]) -> None:
        self._data = data

    async def to_list(self, length: int | None = None) -> list[dict]:
        return list(self._data)


class _FakeColl:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.updates: list[tuple[dict, dict]] = []

    def find(self, filter: dict, projection: dict | None = None) -> _FakeCursor:  # noqa: ANN001
        status_in = set(filter.get("status", {}).get("$in", []))
        host_id = filter.get("host_id")
        matched = [
            d
            for d in self.docs
            if d.get("status") in status_in and d.get("host_id") == host_id
        ]
        return _FakeCursor(matched)

    async def update_one(self, filter: dict, update: dict) -> object:  # noqa: ANN001
        self.updates.append((filter, update))
        return type("R", (), {"matched_count": 1})()


class _FakeDB:
    def __init__(self, coll: _FakeColl) -> None:
        self._coll = coll

    def __getitem__(self, _name: str) -> _FakeColl:
        return self._coll


def _make_docs() -> list[dict]:
    return [
        {"_id": "a", "tmux_name": "alive", "status": "running", "host_id": "h1"},
        {"_id": "b", "tmux_name": "dead", "status": "running", "host_id": "h1"},
        {
            "_id": "c",
            "tmux_name": "waiting",
            "status": "waiting_input",
            "host_id": "h1",
        },
        {
            "_id": "d",
            "tmux_name": "external",
            "status": "waiting_external",
            "host_id": "h1",
        },
        {"_id": "e", "tmux_name": "detached", "status": "detached", "host_id": "h1"},
        {"_id": "f", "tmux_name": "stopped", "status": "stopped", "host_id": "h1"},
        {"_id": "g", "tmux_name": "other-host", "status": "running", "host_id": "h2"},
    ]


async def test_running_dead_becomes_stopped() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    runtime = _FakeRuntime({"alive"})

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    updated = {str(u[0]["_id"]) for u in coll.updates}
    assert "b" in updated
    dead_update = next(u for u in coll.updates if u[0]["_id"] == "b")[1]
    assert dead_update["$set"]["status"] == "stopped"
    assert dead_update["$set"]["agent_pid"] is None
    assert "tmux_id" in dead_update["$unset"]
    assert dead_update["$set"]["last_activity_at"] is not None


async def test_running_alive_left_untouched() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    runtime = _FakeRuntime({"alive"})

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    updated = {str(u[0]["_id"]) for u in coll.updates}
    assert "a" not in updated


async def test_waiting_input_is_reconciled() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    runtime = _FakeRuntime({"alive"})

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    updated = {str(u[0]["_id"]) for u in coll.updates}
    assert {"b", "c", "d", "e"} <= updated


async def test_stopped_left_untouched() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    runtime = _FakeRuntime(set())

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    updated = {str(u[0]["_id"]) for u in coll.updates}
    assert "f" not in updated


async def test_other_host_left_untouched() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    runtime = _FakeRuntime(set())

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    updated = {str(u[0]["_id"]) for u in coll.updates}
    assert "g" not in updated


async def test_one_failure_does_not_stop_others() -> None:
    docs = _make_docs()
    coll = _FakeColl(docs)
    coll.updates = []  # será preenchido abaixo

    class _FailingColl(_FakeColl):
        async def update_one(self, filter: dict, update: dict) -> object:  # noqa: ANN001
            if filter["_id"] == "b":
                raise RuntimeError("boom")
            return await super().update_one(filter, update)

    failing_coll = _FailingColl(docs)
    runtime = _FakeRuntime(set())

    # Não deve propagar exceção; as demais updates devem ocorrer.
    await reconcile_stale_sessions_on_startup(
        _FakeDB(failing_coll), runtime, "h1"
    )

    updated = {str(u[0]["_id"]) for u in failing_coll.updates}
    assert "b" not in updated
    assert {"c", "d", "e"} <= updated


async def test_no_docs_is_noop() -> None:
    coll = _FakeColl([])
    runtime = _FakeRuntime(set())

    await reconcile_stale_sessions_on_startup(_FakeDB(coll), runtime, "h1")

    assert coll.updates == []
