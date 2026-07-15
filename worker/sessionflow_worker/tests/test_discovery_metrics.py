"""Discovery grava ``metrics`` no doc de sessões Claude (mock, sem reais).

Determinístico via mocks — sem o marker ``integration``. Captura o ``update``
passado ao ``update_one`` e confirma que ``metrics`` (vindo de
``claude_metrics_for``, aqui mockado) é gravado no ``$set``.
"""

from __future__ import annotations

import pytest

from sessionflow_worker import discovery as discovery_mod
from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.discovery import Discovery
from sessionflow_worker.tmux_runtime import SessionInfo


class _FakeColl:
    def __init__(self, prev: dict | None = None) -> None:
        self.last_update: dict | None = None
        self._prev = prev

    async def find_one(self, *_a, **_k):
        return self._prev

    async def update_one(self, _filter, update, upsert=False):
        self.last_update = update

        class _Res:
            upserted_id = "new-id"

        return _Res()


class _FakeDB:
    def __init__(self, coll: _FakeColl) -> None:
        self._coll = coll

    def __getitem__(self, _name: str) -> _FakeColl:
        return self._coll


def _claude_info(work_dir: str = "~/Documents/projects/pvax") -> SessionInfo:
    return SessionInfo(
        name="planner",
        id="$planner",
        attached=True,
        created=1700000000,
        pane_command="claude",
        pane_pid=4321,
        work_dir=work_dir,
    )


@pytest.mark.asyncio
async def test_metrics_written_for_claude_session(monkeypatch) -> None:
    coll = _FakeColl()
    disc = Discovery(tmux=object(), db=_FakeDB(coll), host_id="test-host")  # type: ignore[arg-type]

    fake_metrics = {
        "model": "Opus 4.8",
        "context_used": 6002,
        "context_max": 200000,
        "context_pct": 3,
        "tokens_in": 6002,
        "tokens_out": 47,
        "source": "claude_jsonl",
    }
    monkeypatch.setattr(
        discovery_mod, "claude_metrics_for", lambda _wd, **_kw: fake_metrics
    )
    # emit_event toca o DB falso; neutraliza para isolar o teste.
    monkeypatch.setattr(discovery_mod, "emit_event", _noop)

    info = _claude_info()
    assert info.agent_type is AgentType.CLAUDE  # sanity

    await disc._upsert_session(info)

    assert coll.last_update is not None
    assert coll.last_update["$set"]["metrics"] == fake_metrics


@pytest.mark.asyncio
async def test_limits_attached_to_claude_metrics(monkeypatch) -> None:
    coll = _FakeColl()
    disc = Discovery(tmux=object(), db=_FakeDB(coll), host_id="test-host")  # type: ignore[arg-type]

    monkeypatch.setattr(
        discovery_mod,
        "claude_metrics_for",
        lambda _wd, **_kw: {"model": "Opus 4.8", "source": "claude_jsonl"},
    )
    monkeypatch.setattr(discovery_mod, "emit_event", _noop)

    limits = {
        "session_pct": 32,
        "session_reset": "12:30pm",
        "week_pct": 2,
        "week_reset": "Jun 24 at 9am",
        "week_sonnet_pct": 0,
    }
    await disc._upsert_session(_claude_info(), limits=limits)

    assert coll.last_update is not None
    assert coll.last_update["$set"]["metrics"]["limits"] == limits


@pytest.mark.asyncio
async def test_limits_absent_when_no_host_usage(monkeypatch) -> None:
    coll = _FakeColl()
    disc = Discovery(tmux=object(), db=_FakeDB(coll), host_id="test-host")  # type: ignore[arg-type]

    monkeypatch.setattr(
        discovery_mod,
        "claude_metrics_for",
        lambda _wd, **_kw: {"model": "Opus 4.8", "source": "claude_jsonl"},
    )
    monkeypatch.setattr(discovery_mod, "emit_event", _noop)

    await disc._upsert_session(_claude_info(), limits=None)

    metrics = coll.last_update["$set"]["metrics"]
    assert "limits" not in metrics


@pytest.mark.asyncio
async def test_metrics_none_for_non_claude(monkeypatch) -> None:
    coll = _FakeColl()
    disc = Discovery(tmux=object(), db=_FakeDB(coll), host_id="test-host")  # type: ignore[arg-type]

    monkeypatch.setattr(discovery_mod, "emit_event", _noop)
    # claude_metrics_for NÃO deve ser chamado p/ não-claude; se for, falha.
    monkeypatch.setattr(
        discovery_mod,
        "claude_metrics_for",
        lambda _wd: pytest.fail("não deve chamar para não-claude"),
    )

    info = SessionInfo(
        name="codex-sess",
        id="$codex",
        attached=False,
        created=1700000000,
        pane_command="codex",
        pane_pid=99,
        work_dir="~/proj/x",
    )
    assert info.agent_type is AgentType.CODEX

    await disc._upsert_session(info)

    assert coll.last_update is not None
    assert coll.last_update["$set"]["metrics"] is None


@pytest.mark.asyncio
async def test_claude_session_id_passed_through_from_prev_doc(monkeypatch) -> None:
    """O UUID salvo no doc anterior é repassado ao extractor (evita a sessão

    'roubar' a métrica de outra que compartilha o mesmo work_dir — ver
    ``metrics.py::_jsonl_for_session_id``).
    """
    coll = _FakeColl(prev={"claude_session_id": "sid-abc-123"})
    disc = Discovery(tmux=object(), db=_FakeDB(coll), host_id="test-host")  # type: ignore[arg-type]

    seen: dict = {}

    def _fake(work_dir, **kw):
        seen["work_dir"] = work_dir
        seen["session_id"] = kw.get("session_id")
        return {"model": "Opus 4.8", "source": "claude_jsonl"}

    monkeypatch.setattr(discovery_mod, "claude_metrics_for", _fake)
    monkeypatch.setattr(discovery_mod, "emit_event", _noop)

    await disc._upsert_session(_claude_info())

    assert seen["session_id"] == "sid-abc-123"


async def _noop(*_a, **_k) -> None:
    return None
