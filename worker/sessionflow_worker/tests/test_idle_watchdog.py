"""Testes do watchdog de filhos delegados ociosos/travados (``idle_watchdog``).

Reaproveita o fake de Mongo/publish de ``test_handoff_notify``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from sessionflow_worker import idle_watchdog as iw
from sessionflow_worker.tests.test_handoff_notify import NOW, _Db, _pub, _write_handoff

ECONNRESET = "\x1b[31m⎿  Connection dropped (ECONNRESET) · attempt 9/10\x1b[0m\n> "


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("SF_IDLE_WATCHDOG", "SF_IDLE_SECS", "SF_IDLE_MAX_NUDGES"):
        monkeypatch.delenv(var, raising=False)


def _doc(**over):
    doc = {
        "_id": 1, "tmux_name": "w1", "display_name": "w1", "parent": "chefe",
        "status": "running", "host_id": "duck", "activity": "Executando",
        "created_at": NOW - timedelta(hours=1), "parent_notified_at": None,
        "last_activity_at": NOW - timedelta(minutes=4),
    }
    doc.update(over)
    return doc


def _setup(tmp_path, screen: str = "", **child):
    db = _Db()
    doc = _doc(work_dir=str(tmp_path), **child)
    db["sessions"].docs += [doc, {"_id": 2, "tmux_name": "chefe", "host_id": "mac", "status": "running"}]
    db["session_screen"].docs.append({"tmux_name": "w1", "text": screen})
    return db, [], doc


# -- decide (pura) ------------------------------------------------------------


def test_connection_error_nudges():
    assert iw.decide(_doc(), iw.clean_tail(ECONNRESET), NOW) == "nudge"


def test_error_on_screen_still_changing_waits():
    doc = _doc(last_activity_at=NOW - timedelta(seconds=30))
    assert iw.decide(doc, "API Error: 529 overloaded", NOW) == "none"


def test_idle_concluido_without_handoff_nudges():
    assert iw.decide(_doc(activity="Concluído"), "> ", NOW) == "nudge"


def test_long_silence_nudges_and_short_does_not(monkeypatch):
    assert iw.decide(_doc(last_activity_at=NOW - timedelta(minutes=11)), "", NOW) == "nudge"
    assert iw.decide(_doc(), "", NOW) == "none"
    monkeypatch.setenv("SF_IDLE_SECS", "120")
    assert iw.decide(_doc(), "", NOW) == "nudge"


def test_backoff_respected():
    nudged = NOW - timedelta(minutes=4)
    doc = _doc(activity="Concluído", last_activity_at=nudged,
               watchdog={"nudges": 1, "last_nudge_at": nudged})
    assert iw.decide(doc, "", NOW) == "none"  # 1ª espera é 5 min
    assert iw.decide(doc, "", NOW + timedelta(minutes=1)) == "nudge"
    doc["watchdog"] = {"nudges": 2, "last_nudge_at": nudged}
    assert iw.decide(doc, "", NOW + timedelta(minutes=5)) == "none"  # 2ª espera é 10 min
    assert iw.decide(doc, "", NOW + timedelta(minutes=6)) == "nudge"


def test_real_activity_after_nudge_resets():
    nudged = NOW - timedelta(minutes=30)
    working = "● Update(src/app.py)\n✻ Pensando…"
    for activity in ("Pensando", "Codificando"):
        doc = _doc(activity=activity, last_activity_at=nudged + timedelta(minutes=3),
                   watchdog={"nudges": 3, "last_nudge_at": nudged})
        # Progrediu e depois silenciou > SF_IDLE_SECS: contador zerado, não escala.
        assert iw.decide(doc, working, NOW) == "nudge"
        # Atividade só logo após a cutucada (o próprio input) não zera.
        doc["last_activity_at"] = nudged + timedelta(seconds=10)
        assert iw.decide(doc, working, NOW) == "escalate"


def test_activity_that_ends_stuck_does_not_reset():
    nudged = NOW - timedelta(minutes=30)
    state = {"nudges": 3, "last_nudge_at": nudged}
    retried = nudged + timedelta(minutes=20)
    # Tela mexeu (retry de rede) mas ainda mostra o erro: não é progresso.
    doc = _doc(last_activity_at=retried, watchdog=dict(state))
    assert iw.decide(doc, iw.clean_tail(ECONNRESET), NOW) == "escalate"
    # Mexeu e voltou a ficar ocioso sem handoff: também não.
    doc = _doc(activity="Concluído", last_activity_at=retried, watchdog=dict(state))
    assert iw.decide(doc, "> ", NOW) == "escalate"


def test_max_nudges_escalates_once(monkeypatch):
    monkeypatch.setenv("SF_IDLE_MAX_NUDGES", "1")
    nudged = NOW - timedelta(minutes=6)
    doc = _doc(activity="Concluído", last_activity_at=nudged,
               watchdog={"nudges": 1, "last_nudge_at": nudged})
    assert iw.decide(doc, "", NOW) == "escalate"
    doc["watchdog"]["escalated_at"] = NOW
    assert iw.decide(doc, "", NOW + timedelta(hours=1)) == "none"


def test_with_handoff_none():
    assert iw.decide(_doc(activity="Concluído"), ECONNRESET, NOW, has_handoff=True) == "none"


def test_waiting_decision_escalates_after_5min_never_nudges():
    doc = _doc(activity="Aguardando sua decisão", last_activity_at=NOW - timedelta(minutes=4))
    assert iw.decide(doc, "", NOW) == "none"
    assert iw.decide(doc, "", NOW + timedelta(minutes=2)) == "escalate"


def test_without_parent_final_status_or_young_ignored():
    assert iw.decide(_doc(parent=None, activity="Concluído"), "", NOW) == "none"
    assert iw.decide(_doc(status="stopped", activity="Concluído"), "", NOW) == "none"
    assert iw.decide(_doc(parent_notified_at=NOW, activity="Concluído"), "", NOW) == "none"
    young = _doc(activity="Concluído", created_at=NOW - timedelta(minutes=2))
    assert iw.decide(young, "", NOW) == "none"


# -- watchdog_once (integração com fakes) -------------------------------------


async def test_nudge_goes_to_child_and_records_state(tmp_path):
    db, sent, child = _setup(tmp_path, screen=ECONNRESET)
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == [("w1", "nudge")]
    (rk, body), = sent
    assert rk == "sessionflow.commands.duck"
    assert body["type"] == "input"
    assert body["payload"]["name"] == "w1" and body["payload"]["enter"] is True
    assert ".sessionflow/handoff/w1.handoff.json" in body["payload"]["text"]
    assert child["watchdog"] == {"nudges": 1, "last_nudge_at": NOW, "escalated_at": None}
    # Mesma passada de novo: backoff segura.
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW + timedelta(minutes=1)) == []


async def test_escalation_goes_to_parent_once(tmp_path):
    nudged = NOW - timedelta(minutes=25)
    db, sent, child = _setup(
        tmp_path, activity="Concluído", last_activity_at=nudged,
        watchdog={"nudges": 3, "last_nudge_at": nudged},
    )
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == [("w1", "escalate")]
    (rk, body), = sent
    assert rk == "sessionflow.commands.mac"
    assert body["payload"]["name"] == "chefe"
    text = body["payload"]["text"]
    assert text.startswith("⚠️ TRAVADO w1 (host duck): 3 cutucadas sem progresso")
    assert "sf delegate --host auto-remote" in text
    assert child["watchdog"]["escalated_at"] == NOW
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW + timedelta(hours=1)) == []
    assert len(sent) == 1


async def test_persistent_network_error_with_retries_accumulates_and_escalates(tmp_path):
    """Rede caída: entre cutucadas o claude retenta (tela muda, last_activity_at
    avança) mas a tela continua com o erro — o contador tem que acumular."""
    db, sent, child = _setup(tmp_path, screen=ECONNRESET)
    t0 = NOW

    async def step(retry_at, at):
        child["last_activity_at"] = retry_at  # retry mexeu a tela
        return await iw.watchdog_once(db, _pub(sent), "duck", now=at)

    assert await iw.watchdog_once(db, _pub(sent), "duck", now=t0) == [("w1", "nudge")]
    m = lambda n: t0 + timedelta(minutes=n)  # noqa: E731
    assert await step(m(4), m(6)) == [("w1", "nudge")]
    assert child["watchdog"]["nudges"] == 2
    assert await step(m(14), m(17)) == [("w1", "nudge")]
    assert child["watchdog"]["nudges"] == 3
    assert await step(m(32), m(37)) == [("w1", "escalate")]
    assert await step(m(50), m(60)) == []

    *nudges, (rk, body) = sent
    assert len(nudges) == 3 and all(b["payload"]["name"] == "w1" for _, b in nudges)
    assert rk == "sessionflow.commands.mac"
    assert body["payload"]["text"].startswith("⚠️ TRAVADO w1 (host duck): 3 cutucadas sem progresso")
    assert "erro na tela (Connection dropped)" in body["payload"]["text"]


async def test_handoff_present_skips(tmp_path):
    db, sent, _ = _setup(tmp_path, screen=ECONNRESET, activity="Concluído")
    _write_handoff(tmp_path, "w1", "done", timedelta(minutes=5))
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == []


async def test_stale_handoff_from_previous_run_does_not_skip(tmp_path):
    db, sent, _ = _setup(tmp_path, activity="Concluído")
    _write_handoff(tmp_path, "w1", "done", timedelta(hours=3))
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == [("w1", "nudge")]


async def test_without_parent_or_other_host_ignored(tmp_path):
    db, sent, _ = _setup(tmp_path, screen=ECONNRESET, parent=None)
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == []
    db2, sent2, _ = _setup(tmp_path, screen=ECONNRESET)
    assert await iw.watchdog_once(db2, _pub(sent2), "mac", now=NOW) == []
    assert sent == sent2 == []


async def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_IDLE_WATCHDOG", "0")
    db, sent, child = _setup(tmp_path, screen=ECONNRESET, activity="Concluído")
    assert iw.decide(child, ECONNRESET, NOW) == "nudge"  # a pura não olha o env
    assert await iw.watchdog_once(db, _pub(sent), "duck", now=NOW) == []
    assert sent == []
