# Session Status Endpoint (Connect Android prep) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compact `GET /sessions/{id}/status` endpoint (with `freshness=cached|auto|force`) that a future voice assistant client can poll to know "where is this session at", backed by an additive `snapshot` block in `milestones.json`.

**Architecture:** Extend the existing `milestones.json` per-session file (already parsed by the worker) with an additive `snapshot` block (`current_focus`, `next_step`, `waiting_for`), written by the coding agent itself via an extended instruction — same mechanism already used for `description`/`milestones`. The worker mirrors these fields onto the session's Mongo doc, exactly like `ai_description` today. The API exposes them through a new read-only endpoint that assembles a compact DTO from data that's already persisted. `force` freshness fires the existing fire-and-forget command pattern (no new synchronous-wait machinery — nothing in this codebase blocks on the agent).

**Tech Stack:** FastAPI (API), Motor/MongoDB, RabbitMQ command publishing (`app.publishers.command_publisher.publish_command`), Python worker polling loop (`sync_session`).

**Spec:** `docs/adr/2026-08-13-connect-android-status-endpoint-design.md`

## Global Constraints

- Additive only: existing `milestones.json` files without a `snapshot` block must keep working (parser returns `None` for missing fields, never raises).
- No `schema_version` field — parser is tolerant, no code branches on a version number (documented simplification in the spec).
- `waiting_for` is a free string or `null`, not an enum.
- No synchronous wait on the agent anywhere — `force` publishes a command and returns immediately, same as every other mutating endpoint in this codebase.
- No milestone edit endpoint — milestones stay writable only by the agent/file; the API only reads (this new endpoint) and deletes (already existing `DELETE /tasks/{id}`).
- Tests run against the real Mongo host in an isolated collection/document (project convention — no mongomock), skip if `MONGO_URI_HOST`/`MONGO_URI` isn't set where that pattern is used.

---

### Task 1: Worker — parse and mirror the `snapshot` block

**Files:**
- Modify: `worker/sessionflow_worker/milestones.py`
- Test: `worker/sessionflow_worker/tests/test_milestones.py` (new file)

**Interfaces:**
- Produces: `read_snapshot(work_dir: str, session_name: str, allow_shared: bool = True) -> dict[str, str | None] | None` — same namespacing/fallback rules as `read_description`. Returned dict always has exactly the keys `current_focus`, `next_step`, `waiting_for` (each `str | None`) when the file has a `snapshot` object; `None` when the file is missing/invalid or has no `snapshot` object at all.
- Modifies: `sync_session(...)` (signature unchanged) to also `$set` `current_focus`/`next_step`/`waiting_for`/`snapshot_updated_at` on the session doc in the (hardcoded, pre-existing) `"sessions"` collection, mirroring the existing `ai_description` block's idempotent-write pattern.

- [ ] **Step 1: Write the failing tests for `_parse_snapshot`/`read_snapshot`**

Create `worker/sessionflow_worker/tests/test_milestones.py`:

```python
"""Testes de `snapshot` em milestones.json — parsing puro, sem Mongo.

Cobre só o bloco novo (`snapshot`); o resto do arquivo (description/
milestones) já roda em produção sem testes dedicados — fora de escopo aqui.
"""

from __future__ import annotations

import json
from pathlib import Path

from sessionflow_worker.milestones import read_snapshot


def _write(base: Path, session_name: str, data: dict) -> None:
    sf_dir = base / ".sessionflow"
    sf_dir.mkdir(parents=True, exist_ok=True)
    (sf_dir / f"milestones.{session_name}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_snapshot(str(tmp_path), "sess") is None


def test_missing_snapshot_block_returns_none(tmp_path: Path) -> None:
    _write(tmp_path, "sess", {"description": "x", "milestones": []})
    assert read_snapshot(str(tmp_path), "sess") is None


def test_full_snapshot_parsed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "sess",
        {
            "snapshot": {
                "current_focus": "Implementar AppFunctions",
                "next_step": "Expor getSessionStatus",
                "waiting_for": "decisão sobre o mecanismo principal",
            }
        },
    )
    assert read_snapshot(str(tmp_path), "sess") == {
        "current_focus": "Implementar AppFunctions",
        "next_step": "Expor getSessionStatus",
        "waiting_for": "decisão sobre o mecanismo principal",
    }


def test_partial_snapshot_fills_missing_with_none(tmp_path: Path) -> None:
    _write(tmp_path, "sess", {"snapshot": {"current_focus": "Só isso"}})
    assert read_snapshot(str(tmp_path), "sess") == {
        "current_focus": "Só isso",
        "next_step": None,
        "waiting_for": None,
    }


def test_non_dict_snapshot_returns_none(tmp_path: Path) -> None:
    _write(tmp_path, "sess", {"snapshot": "not a dict"})
    assert read_snapshot(str(tmp_path), "sess") is None


def test_truncates_long_fields(tmp_path: Path) -> None:
    long_text = "a" * 500
    _write(tmp_path, "sess", {"snapshot": {"current_focus": long_text}})
    result = read_snapshot(str(tmp_path), "sess")
    assert result is not None
    assert len(result["current_focus"]) == 240


def test_namespaced_file_wins_over_shared(tmp_path: Path) -> None:
    _write(tmp_path, "sess", {"snapshot": {"current_focus": "namespaced"}})
    sf_dir = tmp_path / ".sessionflow"
    (sf_dir / "milestones.json").write_text(
        json.dumps({"snapshot": {"current_focus": "shared"}}), encoding="utf-8"
    )
    result = read_snapshot(str(tmp_path), "sess")
    assert result is not None
    assert result["current_focus"] == "namespaced"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_milestones.py -v`
Expected: FAIL with `ImportError: cannot import name 'read_snapshot'`

- [ ] **Step 3: Implement `_parse_snapshot`/`read_snapshot` in `milestones.py`**

Add right after `_parse_description` (`worker/sessionflow_worker/milestones.py`, after line 99):

```python
def _parse_snapshot(path: Path) -> dict[str, str | None] | None:
    """Bloco top-level ``snapshot`` do arquivo de marcos (ou ``None``).

    Campos livres, escritos pela própria IA da sessão: ``current_focus``
    (no que está trabalhando agora), ``next_step`` (o que vem em seguida) e
    ``waiting_for`` (o que precisa do usuário agora, texto livre ou
    ``None``). ``None`` quando o arquivo não existe, é inválido, ou não tem
    bloco ``snapshot`` — nunca levanta.
    """
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None

    def _field(key: str) -> str | None:
        val = snap.get(key)
        if not isinstance(val, str):
            return None
        val = val.strip()
        return val[:240] if val else None

    return {
        "current_focus": _field("current_focus"),
        "next_step": _field("next_step"),
        "waiting_for": _field("waiting_for"),
    }


def read_snapshot(
    work_dir: str, session_name: str, allow_shared: bool = True
) -> dict[str, str | None] | None:
    """Lê o bloco ``snapshot`` da sessão (mesma lógica de path/namespacing de
    :func:`read_description`)."""
    if not work_dir:
        return None
    base = Path(work_dir).expanduser() / ".sessionflow"
    snap = _parse_snapshot(base / f"milestones.{session_name}.json")
    if snap is not None:
        return snap
    if allow_shared:
        return _parse_snapshot(base / "milestones.json")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_milestones.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write the failing test for `sync_session` mirroring**

Append to `worker/sessionflow_worker/tests/test_milestones.py` (needs real Mongo — same convention as `test_mongo.py`: isolated doc in the real `sessions` collection, dropped on teardown, since `sync_session` hardcodes `db["sessions"]`):

```python
import os
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv

from sessionflow_worker.milestones import sync_session
from sessionflow_worker.mongo import get_db

_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)


def _host_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
async def session_doc():
    uri = _host_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    db = get_db(uri=uri, db_name="sessionflow")
    tmux_name = f"snaptest-{uuid.uuid4().hex}"
    await db["sessions"].insert_one(
        {
            "tmux_name": tmux_name,
            "status": "running",
            "created_at": datetime.now(timezone.utc),
        }
    )
    try:
        yield db, tmux_name
    finally:
        await db["sessions"].delete_one({"tmux_name": tmux_name})
        await db["tasks"].delete_many({"session_id": tmux_name})


@pytest.mark.asyncio
async def test_sync_session_mirrors_snapshot(tmp_path: Path, session_doc) -> None:
    db, tmux_name = session_doc
    _write(
        tmp_path,
        tmux_name,
        {
            "snapshot": {
                "current_focus": "AppFunctions",
                "next_step": "Expor status",
                "waiting_for": None,
            }
        },
    )

    await sync_session(db, tmux_name, str(tmp_path), tmux_name)

    doc = await db["sessions"].find_one({"tmux_name": tmux_name})
    assert doc["current_focus"] == "AppFunctions"
    assert doc["next_step"] == "Expor status"
    assert doc.get("waiting_for") is None
    first_updated_at = doc["snapshot_updated_at"]

    # Rodar de novo SEM mudar nada não deve bumpar snapshot_updated_at.
    await sync_session(db, tmux_name, str(tmp_path), tmux_name)
    doc2 = await db["sessions"].find_one({"tmux_name": tmux_name})
    assert doc2["snapshot_updated_at"] == first_updated_at

    # Mudar um campo bumpa o timestamp.
    _write(
        tmp_path,
        tmux_name,
        {
            "snapshot": {
                "current_focus": "AppFunctions v2",
                "next_step": "Expor status",
                "waiting_for": None,
            }
        },
    )
    await sync_session(db, tmux_name, str(tmp_path), tmux_name)
    doc3 = await db["sessions"].find_one({"tmux_name": tmux_name})
    assert doc3["current_focus"] == "AppFunctions v2"
    assert doc3["snapshot_updated_at"] > first_updated_at
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_milestones.py -v -k sync_session`
Expected: FAIL (`doc["current_focus"]` -> `KeyError`, since `sync_session` doesn't mirror snapshot fields yet)

- [ ] **Step 7: Implement the mirror in `sync_session`**

In `worker/sessionflow_worker/milestones.py`, right after the existing `ai_description` mirror block (ends at line 228, before `coll = db[collection]`), add:

```python
    # Snapshot semântico (current_focus/next_step/waiting_for), escrito pela
    # IA da sessão no mesmo arquivo → espelha no doc da sessão, mesmo
    # idempotente do bloco de description acima: só grava (e só bumpa
    # snapshot_updated_at) quando algo de fato mudou.
    snap = read_snapshot(work_dir, session_name or session_id, allow_shared)
    if snap is not None:
        try:
            existing = await db["sessions"].find_one(
                {"tmux_name": session_name or session_id},
                projection={"current_focus": 1, "next_step": 1, "waiting_for": 1},
            )
            changed = {
                k: v
                for k, v in snap.items()
                if existing is None or existing.get(k) != v
            }
            if changed:
                changed["snapshot_updated_at"] = now
                await db["sessions"].update_one(
                    {"tmux_name": session_name or session_id},
                    {"$set": changed},
                )
        except Exception:  # noqa: BLE001 - best-effort, nunca trava o sync
            pass
```

- [ ] **Step 8: Run all milestones tests to verify they pass**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_milestones.py -v`
Expected: PASS (all tests, skipped only if Mongo env var missing)

- [ ] **Step 9: Commit**

```bash
git add worker/sessionflow_worker/milestones.py worker/sessionflow_worker/tests/test_milestones.py
git commit -m "feat(worker): parse and mirror snapshot block from milestones.json"
```

---

### Task 2: API — instruct the agent to maintain the `snapshot` block

**Files:**
- Modify: `api/app/routers/settings.py`
- Test: `api/app/tests/test_settings_instructions.py` (new file)

**Interfaces:**
- Consumes: nothing new (pure string functions, no signature change to `milestones_instruction(session: str) -> str` / `milestones_refresh_instruction(session: str) -> str`).
- Produces: same two functions, extended text — Task 4 does not call these directly but reuses `milestones_refresh_instruction` for the `force` freshness path.

- [ ] **Step 1: Write the failing tests**

Create `api/app/tests/test_settings_instructions.py`:

```python
"""Testes puros das instruções injetadas na sessão (sem Mongo/FastAPI)."""

from __future__ import annotations

from app.routers.settings import milestones_instruction, milestones_refresh_instruction


def test_initial_instruction_mentions_snapshot_block() -> None:
    text = milestones_instruction("my-session")
    assert '"snapshot"' in text
    assert "current_focus" in text
    assert "next_step" in text
    assert "waiting_for" in text


def test_refresh_instruction_mentions_snapshot_block() -> None:
    text = milestones_refresh_instruction("my-session")
    assert "snapshot" in text
    assert "current_focus" in text
    assert "next_step" in text
    assert "waiting_for" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && python -m pytest app/tests/test_settings_instructions.py -v`
Expected: FAIL (assertions on missing substrings)

- [ ] **Step 3: Extend `milestones_instruction()`**

In `api/app/routers/settings.py`, change the JSON format description (line 28-29) from:

```python
        'formato {"description":"<1 frase>","milestones":[{"id":"<kebab>",'
        '"title":"<curto>","status":"todo|doing|blocked|done"}]}, criando e '
```

to:

```python
        'formato {"description":"<1 frase>","milestones":[{"id":"<kebab>",'
        '"title":"<curto>","status":"todo|doing|blocked|done"}],"snapshot":'
        '{"current_focus":"<no que está trabalhando agora>","next_step":'
        '"<o que vem em seguida>","waiting_for":"<o que precisa do usuário '
        'agora, ou null se nada>"}}, criando e '
```

And after the existing `description` explanation (ends `"mudar de verdade. "` around line 34), add one more sentence before the filename instruction:

```python
        'O bloco "snapshot" é o estado semântico atual: atualize "current_focus" '
        'e "next_step" sempre que o foco mudar, e "waiting_for" com uma frase '
        'curta quando precisar de uma decisão/resposta do usuário (null quando '
        "não precisar). "
```

- [ ] **Step 4: Extend `milestones_refresh_instruction()`**

Replace the body of `milestones_refresh_instruction` (lines 80-86) with:

```python
    return (
        f"[SessionFlow] Revise AGORA o arquivo .sessionflow/milestones.{session}.json: "
        "confira o estado REAL do trabalho, atualize status desatualizados, remova "
        'itens obsoletos/duplicados e garanta no máximo uma tarefa "doing". '
        'Revise também o campo "description" (1 frase, o que se trata esta sessão '
        "na SUA observação) — atualize se o foco mudou; crie se não existir. "
        'Revise o bloco "snapshot" também: "current_focus" (no que está '
        'trabalhando agora), "next_step" (o que vem em seguida) e "waiting_for" '
        "(frase curta se precisar de algo do usuário agora, ou null)."
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd api && python -m pytest app/tests/test_settings_instructions.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add api/app/routers/settings.py api/app/tests/test_settings_instructions.py
git commit -m "feat(api): instruct sessions to maintain a snapshot block"
```

---

### Task 3: API — expose the snapshot fields on `SessionOut`

**Files:**
- Modify: `api/app/routers/sessions.py`
- Test: `api/app/tests/test_sessions_read.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SessionOut` gains `current_focus: str | None`, `next_step: str | None`, `waiting_for: str | None`, `snapshot_updated_at: datetime | None`. Task 4 reads these off the raw Mongo doc directly (not through `SessionOut`), but this task guarantees they round-trip correctly through the existing list/detail endpoints too — same doc, same fields, no duplication of logic.

- [ ] **Step 1: Write the failing test**

Add to `api/app/tests/test_sessions_read.py` (after the existing tests, reusing the file's `_doc`/`seeded`/`_client` helpers already defined there):

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_detail_exposes_snapshot_fields(seeded):
    settings = seeded["settings"]
    docs = seeded["docs"]
    target = docs[0]

    client_wrapper = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client_wrapper[settings.mongo_db][settings.sessions_collection].update_one(
        {"_id": target["_id"]},
        {
            "$set": {
                "current_focus": "AppFunctions",
                "next_step": "Expor status",
                "waiting_for": "decisão sobre X",
                "snapshot_updated_at": datetime.now(UTC),
            }
        },
    )
    client_wrapper.close()

    app = create_app(settings)
    async with await _client(app) as client:
        resp = await client.get(f"/sessions/{target['_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_focus"] == "AppFunctions"
    assert body["next_step"] == "Expor status"
    assert body["waiting_for"] == "decisão sobre X"
    assert body["snapshot_updated_at"] is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_detail_snapshot_fields_default_none(seeded):
    """Sessão antiga, sem os campos novos no doc — não deve quebrar."""
    settings = seeded["settings"]
    docs = seeded["docs"]
    target = docs[1]

    app = create_app(settings)
    async with await _client(app) as client:
        resp = await client.get(f"/sessions/{target['_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_focus"] is None
    assert body["next_step"] is None
    assert body["waiting_for"] is None
    assert body["snapshot_updated_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && python -m pytest app/tests/test_sessions_read.py -v -k snapshot_fields`
Expected: FAIL with `KeyError: 'current_focus'` (field absent from response body)

- [ ] **Step 3: Add the fields to `SessionOut`**

In `api/app/routers/sessions.py`, in `SessionOut` (after `ai_description: str | None = None`, around line 87), add:

```python
    # Estado semântico atual da sessão (bloco "snapshot" do milestones.json,
    # escrito pela própria IA e espelhado pelo worker) — foco/próximo passo/
    # o que precisa do usuário agora. Ausente em sessões antigas/pré-migração.
    current_focus: str | None = None
    next_step: str | None = None
    waiting_for: str | None = None
    snapshot_updated_at: datetime | None = None
```

And in `SessionOut.from_doc`, add `"snapshot_updated_at"` to the `utc_aware_fields` call (around line 93-95):

```python
        data = utc_aware_fields(
            data,
            "last_seen_at",
            "last_activity_at",
            "created_at",
            "updated_at",
            "snapshot_updated_at",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && python -m pytest app/tests/test_sessions_read.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/sessions.py api/app/tests/test_sessions_read.py
git commit -m "feat(api): expose snapshot fields on SessionOut"
```

---

### Task 4: API — `GET /sessions/{id}/status` endpoint

**Files:**
- Modify: `api/app/routers/sessions.py`
- Test: `api/app/tests/test_session_status.py` (new file)

**Interfaces:**
- Consumes: `SessionsRepository.get_session` (existing), `TaskRepository.list_tasks` (existing, `api/app/repositories/task_repo.py`), `publish_command` (existing, `app.publishers.command_publisher`), `milestones_refresh_instruction` (Task 2), `utc_aware` (`app.timeutil`).
- Produces: `GET /sessions/{session_id}/status?freshness=cached|auto|force` → `SessionStatusOut` JSON body: `{id, title, status, snapshot: {summary, current_focus, next_step, waiting_for, updated_at}, workspace: {root_path, repository: {branch} | None, subrepositories: [{path, branch}]}, milestones: [{id, title, status}], freshness: {requested, refreshed, stale}}`.

- [ ] **Step 1: Write the failing tests**

Create `api/app/tests/test_session_status.py`:

```python
"""Integration tests for GET /sessions/{id}/status (Connect Android prep).

Same conventions as test_sessions_read.py: real Mongo host, isolated test
collections for sessions + tasks, dropped on teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app


def _mongo_host_uri() -> str:
    return os.environ.get(
        "MONGO_URI_HOST",
        "mongodb://sessionflow:<senha>"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow",
    )


def _settings(sessions_coll: str, tasks_coll: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_coll,
        tasks_collection=tasks_coll,
    )


@pytest_asyncio.fixture
async def seeded():
    sessions_coll = f"sessions_test_{uuid.uuid4().hex}"
    tasks_coll = f"tasks_test_{uuid.uuid4().hex}"
    settings = _settings(sessions_coll, tasks_coll)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = client[settings.mongo_db]

    now = datetime.now(UTC)
    session_id = ObjectId()
    session_doc = {
        "_id": session_id,
        "tmux_name": "tmux-connect-test",
        "display_name": "SessionFlow Connect",
        "status": "waiting_input",
        "work_dir": "/projects/session-flow",
        "git_repos": [
            {"name": ".", "path": "/projects/session-flow", "branch": "feature/connect"},
            {"name": "apps/web", "path": "/projects/session-flow/apps/web", "branch": "main"},
        ],
        "ai_description": "Integração Android em desenvolvimento.",
        "current_focus": "AppFunctions",
        "next_step": "Expor getSessionStatus",
        "waiting_for": "decisão sobre o mecanismo principal",
        "snapshot_updated_at": now,
        "created_at": now,
        "updated_at": now,
    }
    await db[sessions_coll].insert_one(session_doc)
    await db[tasks_coll].insert_many(
        [
            {
                "session_id": "tmux-connect-test",
                "milestone_id": "task-1",
                "source": "milestone",
                "title": "Implementar login",
                "state": "done",
                "updated_at": now,
            },
            {
                "session_id": "tmux-connect-test",
                "milestone_id": "task-2",
                "source": "milestone",
                "title": "Implementar AppFunctions",
                "state": "doing",
                "updated_at": now,
            },
        ]
    )

    try:
        yield {"settings": settings, "session_id": session_id, "now": now}
    finally:
        await db[sessions_coll].drop()
        await db[tasks_coll].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_cached_returns_full_dto(seeded):
    app = create_app(seeded["settings"])
    async with await _client(app) as client:
        resp = await client.get(
            f"/sessions/{seeded['session_id']}/status?freshness=cached"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "SessionFlow Connect"
    assert body["status"] == "waiting_input"
    assert body["snapshot"]["summary"] == "Integração Android em desenvolvimento."
    assert body["snapshot"]["current_focus"] == "AppFunctions"
    assert body["snapshot"]["next_step"] == "Expor getSessionStatus"
    assert body["snapshot"]["waiting_for"] == "decisão sobre o mecanismo principal"
    assert body["workspace"]["root_path"] == "/projects/session-flow"
    assert body["workspace"]["repository"] == {"branch": "feature/connect"}
    assert body["workspace"]["subrepositories"] == [
        {"path": "/projects/session-flow/apps/web", "branch": "main"}
    ]
    assert {"id": "task-1", "title": "Implementar login", "status": "done"} in body[
        "milestones"
    ]
    assert body["freshness"] == {"requested": "cached", "refreshed": False, "stale": False}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_defaults_to_auto(seeded):
    app = create_app(seeded["settings"])
    async with await _client(app) as client:
        resp = await client.get(f"/sessions/{seeded['session_id']}/status")
    assert resp.status_code == 200
    assert resp.json()["freshness"]["requested"] == "auto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_stale_when_snapshot_old(seeded):
    settings = seeded["settings"]
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    old = datetime.now(UTC) - timedelta(minutes=30)
    await client[settings.mongo_db][settings.sessions_collection].update_one(
        {"_id": seeded["session_id"]}, {"$set": {"snapshot_updated_at": old}}
    )
    client.close()

    app = create_app(settings)
    async with await _client(app) as c:
        resp = await c.get(f"/sessions/{seeded['session_id']}/status")
    assert resp.json()["freshness"]["stale"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_force_returns_immediately_unrefreshed(seeded):
    """force não bloqueia esperando o agente — mesmo padrão fire-and-forget
    de todo comando do SessionFlow."""
    app = create_app(seeded["settings"])
    async with await _client(app) as client:
        resp = await client.get(
            f"/sessions/{seeded['session_id']}/status?freshness=force"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["freshness"] == {"requested": "force", "refreshed": False, "stale": False}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_404_for_unknown_session(seeded):
    app = create_app(seeded["settings"])
    async with await _client(app) as client:
        resp = await client.get(f"/sessions/{ObjectId()}/status")
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_no_repository_when_no_git_repos(seeded):
    settings = seeded["settings"]
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].update_one(
        {"_id": seeded["session_id"]}, {"$unset": {"git_repos": ""}}
    )
    client.close()

    app = create_app(settings)
    async with await _client(app) as c:
        resp = await c.get(f"/sessions/{seeded['session_id']}/status")
    body = resp.json()
    assert body["workspace"]["repository"] is None
    assert body["workspace"]["subrepositories"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd api && python -m pytest app/tests/test_session_status.py -v`
Expected: FAIL with 404 on every request (route doesn't exist yet)

- [ ] **Step 3: Implement the endpoint**

In `api/app/routers/sessions.py`:

Add imports at the top (alongside the existing ones):

```python
from app.repositories.task_repo import TaskRepository
from app.timeutil import utc_aware, utc_aware_fields
```

Add a response model and helper near `SessionOut` (after the `SessionListOut` class, around line 108):

```python
class SessionStatusOut(BaseModel):
    """Compact status/snapshot DTO — for polling clients (Connect Android)."""

    id: str
    title: str | None = None
    status: str | None = None
    snapshot: dict[str, Any]
    workspace: dict[str, Any]
    milestones: list[dict[str, str]]
    freshness: dict[str, Any]


_STALE_AFTER = timedelta(minutes=10)


def _workspace_from(doc: dict[str, Any]) -> dict[str, Any]:
    git_repos = doc.get("git_repos") or []
    repository: dict[str, Any] | None = None
    subrepositories: list[dict[str, str]] = []
    for repo in git_repos:
        if repo.get("name") == ".":
            repository = {"branch": repo.get("branch")}
        else:
            subrepositories.append(
                {"path": repo.get("path", ""), "branch": repo.get("branch", "")}
            )
    return {
        "root_path": doc.get("work_dir"),
        "repository": repository,
        "subrepositories": subrepositories,
    }
```

Add the route near `get_session` (after line 337, right after the existing `GET /{session_id}` handler):

```python
@router.get("/{session_id}/status", response_model=SessionStatusOut)
async def get_session_status(
    request: Request,
    session_id: str,
    freshness: Literal["cached", "auto", "force"] = Query(default="auto"),
) -> SessionStatusOut:
    """Snapshot compacto da sessão (Connect Android e afins).

    ``freshness=force`` dispara (fire-and-forget, como todo comando do
    projeto) uma revisão do snapshot pro agente — não há espera síncrona;
    a atualização real aparece no próximo GET.
    """
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")

    settings = request.app.state.settings
    db = request.app.state.mongo_db
    task_repo = TaskRepository(db, settings.tasks_collection)
    tasks = await task_repo.list_tasks(session_id=doc["tmux_name"])
    milestones = [
        {
            "id": t.get("milestone_id", ""),
            "title": t.get("title", ""),
            "status": t.get("state", ""),
        }
        for t in tasks
    ]

    snapshot_updated_at = utc_aware(doc.get("snapshot_updated_at") or doc.get("updated_at"))
    stale = (
        snapshot_updated_at is None
        or (datetime.now(UTC) - snapshot_updated_at) > _STALE_AFTER
    )

    if freshness == "force":
        from app.routers.settings import milestones_refresh_instruction

        tmux_name = doc["tmux_name"]
        await publish_command(
            settings,
            type="input",
            payload={
                "name": tmux_name,
                "text": milestones_refresh_instruction(tmux_name),
                "enter": True,
            },
            host_id=doc.get("host_id"),
        )

    return SessionStatusOut(
        id=str(doc["_id"]),
        title=doc.get("display_name") or doc.get("tmux_name"),
        status=doc.get("status"),
        snapshot={
            "summary": doc.get("ai_description"),
            "current_focus": doc.get("current_focus"),
            "next_step": doc.get("next_step"),
            "waiting_for": doc.get("waiting_for"),
            "updated_at": snapshot_updated_at,
        },
        workspace=_workspace_from(doc),
        milestones=milestones,
        freshness={"requested": freshness, "refreshed": False, "stale": stale},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd api && python -m pytest app/tests/test_session_status.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Run the full API test suite to check for regressions**

Run: `cd api && python -m pytest app/tests/ -v`
Expected: PASS (no regressions in existing session/task tests)

- [ ] **Step 6: Commit**

```bash
git add api/app/routers/sessions.py api/app/tests/test_session_status.py
git commit -m "feat(api): add GET /sessions/{id}/status endpoint"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (milestones.json snapshot block) → Task 1. §2 (instruction update) → Task 2. §3 (SessionOut fields) → Task 3. §4 (endpoint + freshness) → Task 4. "Fora de escopo" items (app novo, FCM, edição de milestone via API) — intentionally not covered by any task.
- **Placeholder scan:** none found — every step has literal code/commands.
- **Type consistency:** `read_snapshot` return keys (`current_focus`/`next_step`/`waiting_for`) match the `SessionOut` field names (Task 3) and the `SessionStatusOut.snapshot` dict keys (Task 4) throughout.
