# Fila de Demandas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar o usuário digitar demandas numa fila por projeto, e qualquer
sessão de agente ativa processá-las uma a uma — cada uma numa branch+worktree
isolada, contexto zerado, sinal de conclusão via milestones, erro registrado
sem travar a fila.

**Architecture:** Nova coleção Mongo `demands` + 3 rotas REST (CRUD simples,
mesmo padrão de `scheduled_commands`/`ScheduleOut`). Rota de clone existente
ganha campo opcional `name_hint` pra nomear a branch de forma legível. Nova
aba "Fila" no painel de detalhe da sessão (mesmo padrão visual/estrutural do
painel "Comandos programados" já existente). A orquestração em si é uma
instrução documentada (protocolo, como `docs/milestones-protocol.md`) que
qualquer sessão de agente segue — não é código novo rodando sozinho.

**Tech Stack:** FastAPI + Motor (Mongo) no backend; Angular standalone
components + signals no frontend; pytest (`@pytest.mark.integration`, stack
Mongo+RabbitMQ real via `.env` do host) no backend; Vitest no frontend.

**Spec:** `docs/superpowers/specs/2026-08-29-fila-de-demandas-design.md`

## Global Constraints

- Fila escopada a UM `base_session_id` por vez — sem fila multi-repo.
- Nenhuma fila nova no RabbitMQ — CRUD Mongo simples via HTTP.
- Reaproveitar o comando `clone` existente (não criar mecanismo de
  isolamento novo) e o mecanismo de milestones existente (não criar sinal de
  conclusão novo).
- Erro numa demanda (`status: error`) NUNCA trava a fila — o loop sempre
  segue pra próxima `pending`.
- `PATCH /demands/{id}` marca `in_progress` **antes** de criar a
  sessão-filha (evita picking duplicado entre execuções paralelas do loop).
- Todo commit termina com `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Correção de local (achado durante o planejamento)

O spec (seção C) diz "painel da sessão (session-panel...)". Mas
`session-panel.component.ts` é o painel LEVE do modo split, cujo próprio
docblock (linhas 30-36) diz explicitamente que ele **não repete** meta-info
da sessão (tarefas/métricas/share/rename/JARVIS) — isso é exclusivo da tela
cheia. O painel "Comandos programados" (mesma natureza de feature: lista +
criar) já vive em `detalhe.component.ts` (tela cheia), não em
`session-panel.component.ts`. A aba "Fila" segue esse precedente e entra em
`detalhe.component.ts`, ao lado do botão "Comandos programados" — mesma
convenção, sem inventar um padrão novo.

---

## File Structure

- `api/app/config.py` — **modificar**: novo campo `demands_collection: str = "demands"`.
- `api/app/repositories/demands_repo.py` — **criar**: CRUD da coleção `demands` (mesmo padrão de `schedules_repo.py`).
- `api/app/routers/demands.py` — **criar**: rotas `POST/GET /sessions/{base_id}/demands`, `PATCH /demands/{id}`.
- `api/app/main.py` — **modificar**: registra o novo router.
- `api/app/routers/sessions.py` — **modificar**: rota `clone` aceita corpo opcional `{name_hint}`.
- `api/app/tests/test_demands.py` — **criar**.
- `api/app/tests/test_sessions_clone.py` — **modificar**: novo teste pro `name_hint`.
- `frontend/src/app/core/models.ts` — **modificar**: `DemandStatus`, `Demand`.
- `frontend/src/app/core/api.service.ts` — **modificar**: `listDemands`/`createDemand`/`patchDemand`.
- `frontend/src/app/features/detalhe/detalhe.component.ts` — **modificar**: botão + painel "Fila".
- `docs/fila-de-demandas-protocolo.md` — **criar**: o protocolo/loop que a sessão orquestradora segue.

---

### Task 1: Repositório `demands` + config

**Files:**
- Modify: `api/app/config.py` (perto da linha 63, junto de `scheduled_commands_collection`)
- Create: `api/app/repositories/demands_repo.py`
- Test: `api/app/tests/test_demands_repo.py`

**Interfaces:**
- Produz: `DemandsRepository(db, collection_name="demands")` com
  `create(base_session_id: str, text: str) -> dict`,
  `list_for_base(base_session_id: str, status: str | None = None) -> list[dict]`,
  `get(demand_id: str) -> dict | None`,
  `update(demand_id: str, **fields: Any) -> dict | None` (só seta campos
  passados; ignora chaves com valor `None`... EXCETO que `None` é um valor
  válido pra `target_session_id`/`branch`/etc no primeiro PATCH — então o
  contrato é: campo ausente do dict de entrada = não mexe; campo presente
  (mesmo com valor `None`) = seta. O Task 2 monta esse dict via
  `model_dump(exclude_unset=True)` do Pydantic, que já distingue "ausente"
  de "None explícito").

- [ ] **Step 1: Adicionar `demands_collection` ao `Settings`**

Em `api/app/config.py`, logo após o bloco de `scheduled_commands_collection`
(linha ~63), adicionar:

```python
    # Collection holding demand-queue items (fila de demandas) — uma sessão
    # de agente processa, uma por vez, cada demanda numa branch+worktree
    # isolada (reaproveita o comando `clone`). Configurável para tests
    # isolarem (ex.: ``demands_test_<uuid>``).
    demands_collection: str = "demands"
```

- [ ] **Step 2: Escrever o teste do repositório (falha primeiro)**

Criar `api/app/tests/test_demands_repo.py`:

```python
"""Unit tests for DemandsRepository (sem rede — usa mongomock via motor real
seria overkill aqui; testamos contra o Mongo real do host, mesma convenção
dos outros repositórios do projeto — ver test_schedules.py)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.repositories.demands_repo import DemandsRepository

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _env_value(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def _mongo_host_uri() -> str:
    return _env_value("MONGO_URI_HOST") or (
        "mongodb://sessionflow:<senha>"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow"
    )


@pytest_asyncio.fixture
async def repo():
    collection_name = f"demands_test_{uuid.uuid4().hex}"
    client = AsyncIOMotorClient(_mongo_host_uri())
    db = client["sessionflow"]
    try:
        yield DemandsRepository(db, collection_name)
    finally:
        await db[collection_name].drop()
        client.close()


@pytest.mark.integration
async def test_create_sets_pending_status(repo):
    doc = await repo.create("base-1", "fazer relatório mensal")
    assert doc["status"] == "pending"
    assert doc["base_session_id"] == "base-1"
    assert doc["text"] == "fazer relatório mensal"
    assert doc["target_session_id"] is None
    assert doc["branch"] is None
    assert doc["created_at"] is not None
    assert doc["started_at"] is None
    assert doc["completed_at"] is None
    assert doc["summary"] is None
    assert doc["error_note"] is None


@pytest.mark.integration
async def test_list_for_base_filters_by_session_and_status(repo):
    await repo.create("base-1", "a")
    d2 = await repo.create("base-1", "b")
    await repo.create("base-2", "c")
    await repo.update(str(d2["_id"]), status="in_progress")

    all_base1 = await repo.list_for_base("base-1")
    assert {d["text"] for d in all_base1} == {"a", "b"}

    pending_base1 = await repo.list_for_base("base-1", status="pending")
    assert [d["text"] for d in pending_base1] == ["a"]


@pytest.mark.integration
async def test_list_for_base_orders_most_recent_first(repo):
    await repo.create("base-1", "primeira")
    await repo.create("base-1", "segunda")

    items = await repo.list_for_base("base-1")
    assert [d["text"] for d in items] == ["segunda", "primeira"]


@pytest.mark.integration
async def test_update_only_sets_passed_fields(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    updated = await repo.update(demand_id, status="in_progress", started_at=None)
    assert updated["status"] == "in_progress"
    assert updated["text"] == "x"  # não mexeu

    updated2 = await repo.update(demand_id, target_session_id="sess-123", branch="clone/x")
    assert updated2["status"] == "in_progress"  # preservado
    assert updated2["target_session_id"] == "sess-123"
    assert updated2["branch"] == "clone/x"


@pytest.mark.integration
async def test_update_unknown_id_returns_none(repo):
    from bson import ObjectId

    assert await repo.update(str(ObjectId()), status="error") is None


@pytest.mark.integration
async def test_get_unknown_id_returns_none(repo):
    from bson import ObjectId

    assert await repo.get(str(ObjectId())) is None
```

- [ ] **Step 3: Rodar o teste — deve falhar (módulo não existe)**

Run: `cd api && .venv/bin/pytest app/tests/test_demands_repo.py -v -m integration`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.repositories.demands_repo'`

- [ ] **Step 4: Implementar `DemandsRepository`**

Criar `api/app/repositories/demands_repo.py`:

```python
"""Repository for the demand queue (fila de demandas).

Cada doc é uma demanda digitada pelo usuário numa sessão/projeto
(`base_session_id`). Uma sessão de agente ativa processa a fila, uma
demanda por vez — o loop em si é um protocolo documentado (ver
``docs/fila-de-demandas-protocolo.md``), não um serviço em background.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class DemandsRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "demands") -> None:
        self._collection = db[collection_name]

    async def create(self, base_session_id: str, text: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc: dict[str, Any] = {
            "base_session_id": base_session_id,
            "text": text,
            "status": "pending",
            "target_session_id": None,
            "branch": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "summary": None,
            "error_note": None,
        }
        res = await self._collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def list_for_base(
        self, base_session_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"base_session_id": base_session_id}
        if status is not None:
            query["status"] = status
        cursor = self._collection.find(query).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def get(self, demand_id: str) -> dict[str, Any] | None:
        try:
            oid = ObjectId(demand_id)
        except (InvalidId, TypeError):
            return None
        return await self._collection.find_one({"_id": oid})

    async def update(self, demand_id: str, **fields: Any) -> dict[str, Any] | None:
        """Seta só as chaves passadas em ``fields`` (mesmo que o valor seja
        ``None`` — o caller decide o que "passar" via ``exclude_unset``).
        """
        try:
            oid = ObjectId(demand_id)
        except (InvalidId, TypeError):
            return None
        if fields:
            res = await self._collection.update_one({"_id": oid}, {"$set": fields})
            if res.matched_count == 0:
                return None
        return await self._collection.find_one({"_id": oid})
```

- [ ] **Step 5: Rodar o teste — deve passar**

Run: `cd api && .venv/bin/pytest app/tests/test_demands_repo.py -v -m integration`
Expected: PASS (6 tests)

- [ ] **Step 6: Índice por `(base_session_id, status, created_at)`**

Adicionar em `api/app/main.py`, na função de bootstrap de índices (procurar
por `create_index` existente, mesmo bloco onde outras coleções registram
seus índices no startup/lifespan) o índice:

```python
    await db[settings.demands_collection].create_index(
        [("base_session_id", 1), ("status", 1), ("created_at", 1)]
    )
```

Se não existir bloco de índices no `main.py` (ex.: índices são criados só
via script/migração separada), pular este passo e anotar no PR que o índice
fica pendente de infraestrutura — não bloqueia o restante do plano.

- [ ] **Step 7: Commit**

```bash
git add api/app/config.py api/app/repositories/demands_repo.py api/app/tests/test_demands_repo.py api/app/main.py
git commit -m "feat(api): repositório da fila de demandas"
```

---

### Task 2: Rotas da API (`POST`/`GET`/`PATCH`)

**Files:**
- Create: `api/app/routers/demands.py`
- Modify: `api/app/main.py` (import + `include_router`)
- Test: `api/app/tests/test_demands.py`

**Interfaces:**
- Consome: `DemandsRepository` (Task 1), `SessionsRepository.get_session`
  (`api/app/repositories/sessions_repo.py:26-32`, já existe).
- Produz: `DemandOut` (Pydantic, serializa doc → JSON com `id` string),
  rotas `POST /sessions/{base_id}/demands`, `GET /sessions/{base_id}/demands`,
  `PATCH /demands/{demand_id}`.

- [ ] **Step 1: Escrever os testes de rota (falha primeiro)**

Criar `api/app/tests/test_demands.py`:

```python
"""Integration tests for a fila de demandas (POST/GET/PATCH).

Segue o mesmo padrão de `test_schedules.py`: coleções Mongo isoladas por
teste, sem publicar nada no RabbitMQ (fila de demandas é CRUD puro).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _env_value(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def _mongo_host_uri() -> str:
    return _env_value("MONGO_URI_HOST") or (
        "mongodb://sessionflow:<senha>"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow"
    )


def _rabbit_host_uri() -> str:
    return _env_value("RABBITMQ_URI_HOST") or "amqp://guest:guest@127.0.0.1:5672/"


def _host_settings(sessions_collection: str, demands_collection: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_collection,
        demands_collection=demands_collection,
        auth_email="",
        auth_password="",
    )


@pytest_asyncio.fixture
async def settings():
    sessions_collection = f"sessions_test_{uuid.uuid4().hex}"
    demands_collection = f"demands_test_{uuid.uuid4().hex}"
    s = _host_settings(sessions_collection, demands_collection)

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][sessions_collection].drop()
        await client[s.mongo_db][demands_collection].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_session_doc(name: str) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "status": "running",
        "work_dir": "/tmp/work",
        "created_at": now,
        "updated_at": now,
    }


async def _seed_session(settings, name: str) -> str:
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    doc = _seed_session_doc(name)
    await collection.insert_one(doc)
    client.close()
    return str(doc["_id"])


@pytest.mark.integration
async def test_create_list_demand(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            create_resp = await client.post(
                f"/sessions/{base_id}/demands", json={"text": "fazer relatório mensal"}
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["text"] == "fazer relatório mensal"
            assert created["status"] == "pending"
            assert created["base_session_id"] == base_id
            assert created["target_session_id"] is None

            list_resp = await client.get(f"/sessions/{base_id}/demands")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


@pytest.mark.integration
async def test_create_unknown_base_session_404(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/demands", json={"text": "x"}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_create_rejects_empty_text(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(f"/sessions/{base_id}/demands", json={"text": ""})
    assert resp.status_code == 422


@pytest.mark.integration
async def test_list_filters_by_status(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            await client.post(f"/sessions/{base_id}/demands", json={"text": "a"})
            created_b = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "b"})
            ).json()
            await client.patch(
                f"/demands/{created_b['id']}", json={"status": "in_progress"}
            )

            resp = await client.get(f"/sessions/{base_id}/demands", params={"status": "pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["text"] == "a"


@pytest.mark.integration
async def test_patch_updates_progress_fields(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            started = (
                await client.patch(
                    f"/demands/{demand_id}",
                    json={
                        "status": "in_progress",
                        "target_session_id": "sess-abc",
                        "branch": "clone/x",
                    },
                )
            ).json()
            assert started["status"] == "in_progress"
            assert started["target_session_id"] == "sess-abc"
            assert started["branch"] == "clone/x"
            assert started["text"] == "x"  # não mexeu

            done = (
                await client.patch(
                    f"/demands/{demand_id}",
                    json={"status": "completed", "summary": "fez X e Y"},
                )
            ).json()
            assert done["status"] == "completed"
            assert done["summary"] == "fez X e Y"
            assert done["target_session_id"] == "sess-abc"  # preservado


@pytest.mark.integration
async def test_patch_unknown_id_404(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.patch(
                f"/demands/{ObjectId()}", json={"status": "error"}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_patch_rejects_invalid_status(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            resp = await client.patch(
                f"/demands/{created['id']}", json={"status": "nao-existe"}
            )
    assert resp.status_code == 422
```

- [ ] **Step 2: Rodar os testes — devem falhar**

Run: `cd api && .venv/bin/pytest app/tests/test_demands.py -v -m integration`
Expected: FAIL (rotas `/demands`/`/sessions/{id}/demands` não existem, 404 genérico do FastAPI ao invés dos códigos esperados)

- [ ] **Step 3: Implementar o router**

Criar `api/app/routers/demands.py`:

```python
"""Fila de demandas (orquestrador síncrono por sessão/projeto).

CRUD simples sobre a coleção `demands` — sem fila nova no RabbitMQ. A
sessão de agente que processa a fila (qualquer sessão ativa, instruída pelo
usuário) segue o protocolo em `docs/fila-de-demandas-protocolo.md`: pega a
`pending` mais antiga, marca `in_progress`, clona (`POST
/sessions/{id}/clone`) e manda o texto como instrução, aguarda milestones via
`GET /tasks?session=`, e finaliza via `PATCH /demands/{id}`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.repositories.demands_repo import DemandsRepository
from app.repositories.sessions_repo import SessionsRepository
from app.timeutil import utc_aware_fields

router = APIRouter(tags=["demands"])

DemandStatus = Literal["pending", "in_progress", "completed", "error"]


class DemandOut(BaseModel):
    """Serialized demand document."""

    id: str
    base_session_id: str
    text: str
    status: DemandStatus
    target_session_id: str | None = None
    branch: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None
    error_note: str | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> DemandOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        data = utc_aware_fields(data, "created_at", "started_at", "completed_at")
        return cls.model_validate(data)


class DemandListOut(BaseModel):
    items: list[DemandOut] = Field(default_factory=list)
    total: int = 0


class DemandCreate(BaseModel):
    text: str = Field(min_length=1)


class DemandPatch(BaseModel):
    """Todos os campos opcionais — o orquestrador manda só o que muda."""

    status: DemandStatus | None = None
    target_session_id: str | None = None
    branch: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None
    error_note: str | None = None


def _demands_repo(request: Request) -> DemandsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return DemandsRepository(db, settings.demands_collection)


def _sessions_repo(request: Request) -> SessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SessionsRepository(db, settings.sessions_collection)


@router.post(
    "/sessions/{base_id}/demands", response_model=DemandOut, status_code=201
)
async def create_demand(request: Request, base_id: str, body: DemandCreate) -> DemandOut:
    if await _sessions_repo(request).get_session(base_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    doc = await _demands_repo(request).create(base_id, body.text.strip())
    return DemandOut.from_doc(doc)


@router.get("/sessions/{base_id}/demands", response_model=DemandListOut)
async def list_demands(
    request: Request,
    base_id: str,
    status: DemandStatus | None = Query(default=None),
) -> DemandListOut:
    docs = await _demands_repo(request).list_for_base(base_id, status=status)
    items = [DemandOut.from_doc(d) for d in docs]
    return DemandListOut(items=items, total=len(items))


@router.patch("/demands/{demand_id}", response_model=DemandOut)
async def patch_demand(request: Request, demand_id: str, body: DemandPatch) -> DemandOut:
    fields = body.model_dump(exclude_unset=True)
    doc = await _demands_repo(request).update(demand_id, **fields)
    if doc is None:
        raise HTTPException(status_code=404, detail="Demand not found")
    return DemandOut.from_doc(doc)
```

- [ ] **Step 4: Registrar o router em `main.py`**

Em `api/app/main.py`, adicionar o import junto dos demais (ordem alfabética,
perto de `from app.routers import directories as directories_router`):

```python
from app.routers import demands as demands_router
```

E o registro junto dos demais `include_router` (linha ~160-179):

```python
    app.include_router(demands_router.router)
```

- [ ] **Step 5: Rodar os testes — devem passar**

Run: `cd api && .venv/bin/pytest app/tests/test_demands.py -v -m integration`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add api/app/routers/demands.py api/app/main.py api/app/tests/test_demands.py
git commit -m "feat(api): rotas CRUD da fila de demandas"
```

---

### Task 3: Clone aceita `name_hint` opcional

**Files:**
- Modify: `api/app/routers/sessions.py` (rota `clone_session`, linhas 331-372)
- Test: `api/app/tests/test_sessions_clone.py` (adicionar um teste)

**Interfaces:**
- Consome: `_dedupe_name` (já existe, `api/app/routers/sessions.py:331-339`).
- Produz: `ClonePayload` (novo, corpo opcional do `POST /{id}/clone`) — usado
  pelo protocolo da fila de demandas (Task 4/orquestrador) pra nomear a
  branch de forma legível em vez de `-clone-N`.

Nenhuma mudança é necessária no worker (`_handle_clone`,
`worker/sessionflow_worker/command_consumer.py:736-786`): ele já deriva slug
e branch (`clone/{slug}`) a partir do campo `name` do payload — a API só
precisa escolher um `name` melhor quando `name_hint` vier preenchido.

- [ ] **Step 1: Escrever o teste (falha primeiro)**

Em `api/app/tests/test_sessions_clone.py`, adicionar ao final do arquivo:

```python
@pytest.mark.integration
async def test_clone_uses_name_hint_when_given(settings, drain_commands):
    name = f"clone-hint-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(
                f"/sessions/{doc['_id']}/clone",
                json={"name_hint": "Relatório Mensal!"},
            )

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == "relatorio-mensal"


@pytest.mark.integration
async def test_clone_without_body_still_works(settings, drain_commands):
    """Retrocompat: front hoje manda POST com corpo `{}` (sem name_hint)."""
    name = f"clone-nobody-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone", json={})

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == f"{name}-clone"
```

- [ ] **Step 2: Rodar os testes — o de `name_hint` deve falhar**

Run: `cd api && .venv/bin/pytest app/tests/test_sessions_clone.py -v -m integration`
Expected: `test_clone_uses_name_hint_when_given` FAIL (nome gerado ainda é
`{name}-clone`, não `relatorio-mensal`); os demais (incluindo o novo
`test_clone_without_body_still_works`) PASS já hoje.

- [ ] **Step 3: Implementar**

Em `api/app/routers/sessions.py`, adicionar `unicodedata` aos imports
(linha 10, junto de `import re`):

```python
import re
import unicodedata
import uuid
```

Adicionar a função de slug (mesma lógica de
`worker/sessionflow_worker/command_consumer.py:285-296`, duplicada
deliberadamente — API e worker são pacotes deployáveis separados, sem lib
compartilhada hoje) logo antes de `_dedupe_name` (linha ~331):

```python
def _slugify(s: str) -> str:
    """Mesma regra do worker (`command_consumer._slugify`): lowercase,
    remove acentos, non-alnum vira `-`, tira `-` das pontas."""
    normalized = unicodedata.normalize("NFD", s or "")
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", stripped.lower())
    return slug.strip("-")


class ClonePayload(BaseModel):
    """Corpo opcional do clone. `name_hint` nomeia a branch/worktree de
    forma legível (usado pela fila de demandas) em vez do padrão
    `<sessão>-clone[-N]`."""

    name_hint: str | None = None
```

Substituir a assinatura e o trecho de derivação do nome em `clone_session`
(linhas 344-361):

```python
@router.post("/{session_id}/clone", response_model=SessionCreateAccepted, status_code=202)
async def clone_session(
    session_id: str, request: Request, body: ClonePayload = ClonePayload()
) -> SessionCreateAccepted:
    """Clona uma sessão em worktree git isolado numa branch nova."""
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")

    git_repos = doc.get("git_repos") or []
    if len(git_repos) != 1 or git_repos[0].get("name") != ".":
        raise HTTPException(
            status_code=400,
            detail="sessão não pode ser clonada: work_dir precisa ser um único repositório git na raiz",
        )

    base_name = doc.get("tmux_name") or ""
    hint_slug = _slugify(body.name_hint or "")
    desired_base = hint_slug or f"{base_name}-clone"
    new_name = await _dedupe_name(repo, desired_base)
    base_display = doc.get("display_name") or base_name
```

(o restante da função — `payload`, `publish_command`, `return` — não muda.)

- [ ] **Step 4: Rodar os testes — devem passar**

Run: `cd api && .venv/bin/pytest app/tests/test_sessions_clone.py -v -m integration`
Expected: PASS (7 tests: os 4 originais + os 2 novos deste passo, mais o
`test_clone_rejects_umbrella_work_dir` que já existia)

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/sessions.py api/app/tests/test_sessions_clone.py
git commit -m "feat(api): clone aceita name_hint opcional pra nomear a branch"
```

---

### Task 4: Frontend — modelos + `ApiService`

**Files:**
- Modify: `frontend/src/app/core/models.ts`
- Modify: `frontend/src/app/core/api.service.ts`
- Test: `frontend/src/app/core/api.service.spec.ts` (criar se não existir;
  se já existir um arquivo de spec pro `ApiService`, adicionar os testes lá)

**Interfaces:**
- Produz: `DemandStatus`, `Demand` (tipos), e em `ApiService`:
  `listDemands(baseSessionId: string, status?: DemandStatus): Observable<Demand[]>`,
  `createDemand(baseSessionId: string, text: string): Observable<Demand>`,
  `patchDemand(id: string, patch: Partial<Pick<Demand, 'status' | 'target_session_id' | 'branch' | 'started_at' | 'completed_at' | 'summary' | 'error_note'>>): Observable<Demand>`.
  Task 5 (painel "Fila") consome exatamente essas três funções.

- [ ] **Step 1: Checar se existe spec do `ApiService`**

Run: `ls frontend/src/app/core/api.service.spec.ts 2>/dev/null && echo EXISTS || echo MISSING`

Se `MISSING`, criar o arquivo do zero no Step 2 abaixo (mínimo: só os
testes da fila de demandas, usando `HttpClientTestingModule`, mesmo padrão
usado em qualquer spec Angular do projeto que testa um serviço com
`HttpClient`). Se `EXISTS`, adicionar os `describe`/`it` do Step 2 ao
arquivo existente, reaproveitando o `TestBed` já configurado nele.

- [ ] **Step 2: Escrever os testes (falha primeiro)**

Se o arquivo não existir, criar `frontend/src/app/core/api.service.spec.ts`:

```typescript
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ApiService } from './api.service';
import { Demand } from './models';

describe('ApiService — fila de demandas', () => {
  let service: ApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [ApiService],
    });
    service = TestBed.inject(ApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  const demand: Demand = {
    id: 'd1',
    base_session_id: 'base-1',
    text: 'fazer relatório mensal',
    status: 'pending',
    target_session_id: null,
    branch: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    summary: null,
    error_note: null,
  };

  it('listDemands sem status busca a lista da sessão-base', () => {
    service.listDemands('base-1').subscribe((items) => {
      expect(items).toEqual([demand]);
    });
    const req = httpMock.expectOne((r) => r.url.endsWith('/sessions/base-1/demands'));
    expect(req.request.params.has('status')).toBe(false);
    req.flush({ items: [demand], total: 1 });
  });

  it('listDemands com status inclui o filtro na query', () => {
    service.listDemands('base-1', 'pending').subscribe();
    const req = httpMock.expectOne((r) => r.url.endsWith('/sessions/base-1/demands'));
    expect(req.request.params.get('status')).toBe('pending');
    req.flush({ items: [], total: 0 });
  });

  it('createDemand faz POST com o texto', () => {
    service.createDemand('base-1', 'fazer relatório mensal').subscribe((d) => {
      expect(d).toEqual(demand);
    });
    const req = httpMock.expectOne((r) => r.url.endsWith('/sessions/base-1/demands'));
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ text: 'fazer relatório mensal' });
    req.flush(demand);
  });

  it('patchDemand faz PATCH só com os campos passados', () => {
    service.patchDemand('d1', { status: 'in_progress' }).subscribe();
    const req = httpMock.expectOne((r) => r.url.endsWith('/demands/d1'));
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ status: 'in_progress' });
    req.flush({ ...demand, status: 'in_progress' });
  });
});
```

- [ ] **Step 3: Rodar o teste — deve falhar**

Run: `cd frontend && npx vitest run src/app/core/api.service.spec.ts`
Expected: FAIL (`Demand`/`listDemands`/`createDemand`/`patchDemand` não existem)

- [ ] **Step 4: Adicionar os tipos em `models.ts`**

Em `frontend/src/app/core/models.ts`, logo após a interface `Schedule`
(linha 211, antes de `OutputLine`):

```typescript
/** Status de uma demanda na fila (fila de demandas / orquestrador). */
export type DemandStatus = 'pending' | 'in_progress' | 'completed' | 'error';

export interface Demand {
  id: string;
  base_session_id: string;
  text: string;
  status: DemandStatus;
  /** Sessão-filha criada pra essa demanda (preenchida quando entra em execução). */
  target_session_id: string | null;
  /** Branch git da sessão-filha. */
  branch: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  /** Títulos dos milestones concluídos, só quando `status === 'completed'`. */
  summary: string | null;
  /** Motivo do erro, só quando `status === 'error'`. */
  error_note: string | null;
}
```

- [ ] **Step 5: Adicionar os métodos em `ApiService`**

Em `frontend/src/app/core/api.service.ts`, adicionar `Demand` e
`DemandStatus` ao bloco de import de `./models` (linha ~4-20, ordem
alfabética):

```typescript
import {
  AgentModels,
  AgentType,
  AppSettings,
  CreateSessionPayload,
  Demand,
  DemandStatus,
  Directory,
  EventItem,
  Notification,
  OutputLine,
  RemoteSession,
  Schedule,
  Session,
  SessionStatus,
  ShareLink,
  SharedFile,
  Task,
  TerminalKey,
  UsageInfo,
  WorkerStatus,
} from './models';
```

E os métodos, logo após `deleteSchedule` (fim do bloco "Comandos
programados", perto da linha 240):

```typescript

  // --- Fila de demandas (orquestrador síncrono por sessão) ---

  listDemands(baseSessionId: string, status?: DemandStatus): Observable<Demand[]> {
    let params = new HttpParams();
    if (status) {
      params = params.set('status', status);
    }
    return this.http
      .get<{ items: Demand[] }>(this.url(`/sessions/${baseSessionId}/demands`), { params })
      .pipe(this.items<Demand>());
  }

  createDemand(baseSessionId: string, text: string): Observable<Demand> {
    return this.http.post<Demand>(this.url(`/sessions/${baseSessionId}/demands`), { text });
  }

  patchDemand(
    id: string,
    patch: Partial<
      Pick<
        Demand,
        | 'status'
        | 'target_session_id'
        | 'branch'
        | 'started_at'
        | 'completed_at'
        | 'summary'
        | 'error_note'
      >
    >,
  ): Observable<Demand> {
    return this.http.patch<Demand>(this.url(`/demands/${id}`), patch);
  }
```

- [ ] **Step 6: Rodar o teste — deve passar**

Run: `cd frontend && npx vitest run src/app/core/api.service.spec.ts`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/core/models.ts frontend/src/app/core/api.service.ts frontend/src/app/core/api.service.spec.ts
git commit -m "feat(frontend): tipos e API client da fila de demandas"
```

---

### Task 5: Painel "Fila" em `detalhe.component.ts`

**Files:**
- Modify: `frontend/src/app/features/detalhe/detalhe.component.ts`
- Test: `frontend/src/app/features/detalhe/detalhe.component.spec.ts` (se
  não existir spec pro componente, criar um mínimo cobrindo só a fila; se
  existir, adicionar `describe` novo)

**Interfaces:**
- Consome: `ApiService.listDemands`/`createDemand` (Task 4), `Demand`,
  `DemandStatus` (Task 4), `Router` (já injetado, linha 3863).

- [ ] **Step 1: Checar spec existente**

Run: `ls frontend/src/app/features/detalhe/detalhe.component.spec.ts 2>/dev/null && echo EXISTS || echo MISSING`

- [ ] **Step 2: Escrever o teste (falha primeiro)**

Se `MISSING`, criar `frontend/src/app/features/detalhe/detalhe.component.spec.ts`
com o mínimo necessário pra montar o componente e testar só a fila (usar
`ActivatedRoute` fake com `paramMap` retornando o `id` de teste, e
`HttpClientTestingModule` pra interceptar as chamadas do `ApiService`). Se
`EXISTS`, adicionar este bloco reaproveitando o setup já presente no
arquivo:

```typescript
describe('DetalheComponent — fila de demandas', () => {
  // Reaproveita o TestBed/fixture já configurados no describe principal
  // deste arquivo (ver setup acima). `fixture`, `component` e `httpMock`
  // devem já existir no escopo — se este describe for adicionado a um
  // arquivo já existente, mover para dentro do describe principal em vez
  // de duplicar o beforeEach.

  it('abre o painel e carrega a lista via GET /sessions/{id}/demands', () => {
    component['toggleDemands']();
    fixture.detectChanges();
    expect(component['demandsOpen']()).toBe(true);

    const req = httpMock.expectOne((r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`));
    req.flush({
      items: [
        {
          id: 'd1',
          base_session_id: TEST_SESSION_ID,
          text: 'fazer relatório mensal',
          status: 'pending',
          target_session_id: null,
          branch: null,
          created_at: null,
          started_at: null,
          completed_at: null,
          summary: null,
          error_note: null,
        },
      ],
      total: 1,
    });
    fixture.detectChanges();

    expect(component['demands']().length).toBe(1);
    const text = fixture.nativeElement.querySelector('.demand-text')?.textContent;
    expect(text).toContain('fazer relatório mensal');
  });

  it('cria uma demanda nova via POST e prepende na lista', () => {
    component['toggleDemands']();
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`)).flush({
      items: [],
      total: 0,
    });

    component['newDemandText'].set('nova demanda');
    component['createDemand']();

    const req = httpMock.expectOne(
      (r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`) && r.request.method === 'POST',
    );
    expect(req.request.body).toEqual({ text: 'nova demanda' });
    req.flush({
      id: 'd2',
      base_session_id: TEST_SESSION_ID,
      text: 'nova demanda',
      status: 'pending',
      target_session_id: null,
      branch: null,
      created_at: null,
      started_at: null,
      completed_at: null,
      summary: null,
      error_note: null,
    });
    fixture.detectChanges();

    expect(component['demands']()[0].text).toBe('nova demanda');
    expect(component['newDemandText']()).toBe('');
  });
});
```

Adaptar `TEST_SESSION_ID`/`fixture`/`component`/`httpMock` pros nomes reais
usados no describe principal do arquivo (ler o arquivo antes de editar, caso
já exista, pra casar exatamente com o setup existente).

- [ ] **Step 3: Rodar o teste — deve falhar**

Run: `cd frontend && npx vitest run src/app/features/detalhe/detalhe.component.spec.ts`
Expected: FAIL (`toggleDemands`/`demandsOpen`/`demands`/`newDemandText`/`createDemand` não existem)

- [ ] **Step 4: Import dos tipos**

Em `frontend/src/app/features/detalhe/detalhe.component.ts`, no bloco de
import de `../../core/models` (linhas 28-37):

```typescript
import {
  AgentType,
  Demand,
  DemandStatus,
  Schedule,
  Session,
  SessionMetrics,
  ShareLink,
  SharedFile,
  Task,
  TerminalKey,
} from '../../core/models';
```

- [ ] **Step 5: Botão de abrir o painel (header de ações)**

Logo após o bloco do botão "Comandos programados" (linhas 339-355, fecha
com `}` na linha 356), adicionar:

```typescript
            @if (!guest()) {
              <button
                type="button"
                class="act act--ghost"
                [class.on]="demandsOpen()"
                (click)="toggleDemands()"
                [attr.aria-pressed]="demandsOpen()"
                aria-label="Fila de demandas"
                title="Fila de demandas: processa uma por vez, cada uma numa branch isolada"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
                </svg>
              </button>
            }
```

- [ ] **Step 6: Painel "Fila" (template)**

Logo após o fechamento do painel "Comandos programados" (o `}` que fecha o
bloco `@if (schedulesOpen() && !guest())`, linha ~691), adicionar:

```typescript

      <!-- Painel "Fila de demandas": digitar demandas simples pra uma sessão
           de agente processar uma a uma (branch+worktree isolados, contexto
           zerado). Ver docs/fila-de-demandas-protocolo.md. -->
      @if (demandsOpen() && !guest()) {
        <section class="rename" aria-label="Fila de demandas">
          <div class="sched-list">
            @for (d of demands(); track d.id) {
              <div class="sched-item">
                <div class="sched-item-main">
                  @if (d.target_session_id) {
                    <a
                      class="demand-text demand-link"
                      [routerLink]="['/sessao', d.target_session_id]"
                    >{{ d.text }}</a>
                  } @else {
                    <span class="demand-text sched-text">{{ d.text }}</span>
                  }
                  <span class="sched-meta">
                    <span class="demand-status" [class]="'demand-status--' + d.status">
                      {{ demandStatusLabel(d.status) }}
                    </span>
                  </span>
                  @if (d.status === 'completed' && d.summary) {
                    <span class="demand-summary">{{ d.summary }}</span>
                  }
                  @if (d.status === 'error' && d.error_note) {
                    <span class="demand-error">{{ d.error_note }}</span>
                  }
                </div>
              </div>
            } @empty {
              <span class="rename-hint">Nenhuma demanda na fila ainda.</span>
            }
          </div>

          <label class="rename-field">
            <span class="rename-lbl">Nova demanda</span>
            <input
              class="rename-input mono"
              type="text"
              [ngModel]="newDemandText()"
              (ngModelChange)="newDemandText.set($event)"
              placeholder="ex: revisar PRs abertos e comentar achados"
              autocomplete="off"
            />
          </label>
          <div class="rename-acts">
            <button type="button" class="rename-btn" (click)="closeDemands()">Fechar</button>
            <button
              type="button"
              class="rename-btn rename-btn--primary"
              [disabled]="!newDemandText().trim() || demandsCreating()"
              (click)="createDemand()"
            >
              {{ demandsCreating() ? 'Adicionando…' : 'Adicionar' }}
            </button>
          </div>
        </section>
      }
```

Se o template ainda não importar `RouterLink` (checar o array `imports` do
`@Component`, topo do arquivo), adicionar ao array de imports do componente
e ao import de `@angular/router` (linha 16):

```typescript
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
```

- [ ] **Step 7: Estado + métodos**

Logo após o bloco de signals de `schedules` (linhas 3948-3957, antes do
comentário `/** Pede pro agente revisar...`), adicionar:

```typescript

  // --- Fila de demandas (orquestrador síncrono por sessão/projeto) ---
  protected readonly demandsOpen = signal<boolean>(false);
  protected readonly demands = signal<Demand[]>([]);
  protected readonly demandsCreating = signal<boolean>(false);
  protected readonly newDemandText = signal<string>('');
```

E os métodos, logo após `deleteSchedule` (linhas ~4056-4067, antes do
comentário `/** "3600" → "1 hora"...`):

```typescript

  protected toggleDemands(): void {
    if (this.demandsOpen()) {
      this.closeDemands();
      return;
    }
    this.demandsOpen.set(true);
    this.loadDemands();
  }

  protected closeDemands(): void {
    this.demandsOpen.set(false);
  }

  private loadDemands(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .listDemands(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.demands.set(list),
        error: () => this.demands.set([]),
      });
  }

  protected createDemand(): void {
    const id = this.id();
    const text = this.newDemandText().trim();
    if (!id || !text) {
      return;
    }
    this.demandsCreating.set(true);
    this.api
      .createDemand(id, text)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (d) => {
          this.demands.update((list) => [d, ...list]);
          this.newDemandText.set('');
          this.demandsCreating.set(false);
        },
        error: () => this.demandsCreating.set(false),
      });
  }

  protected demandStatusLabel(status: DemandStatus): string {
    switch (status) {
      case 'pending':
        return 'pendente';
      case 'in_progress':
        return 'em execução';
      case 'completed':
        return 'concluída';
      case 'error':
        return 'erro';
    }
  }
```

- [ ] **Step 8: CSS do painel**

Logo após o bloco `.sched-interval-unit` (fim do CSS de "Comandos
programados", antes do comentário `/* Trocar provedor...`), adicionar:

```typescript
      .demand-link {
        color: #f4f5f7;
        text-decoration: none;
      }
      .demand-link:hover {
        text-decoration: underline;
      }
      .demand-status {
        display: inline-block;
        font-size: 10.5px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.02em;
        padding: 1px 6px;
        border-radius: 999px;
      }
      .demand-status--pending {
        color: #b8bec7;
        background: #2a2f34;
      }
      .demand-status--in_progress {
        color: #0d1f1a;
        background: #2cecc4;
      }
      .demand-status--completed {
        color: #eafff8;
        background: #00a482;
      }
      .demand-status--error {
        color: #fff;
        background: #b3312c;
      }
      .demand-summary {
        font-size: 11.5px;
        color: #7d8590;
      }
      .demand-error {
        font-size: 11.5px;
        color: #f87171;
        font-weight: 600;
      }
```

- [ ] **Step 9: Rodar o teste — deve passar**

Run: `cd frontend && npx vitest run src/app/features/detalhe/detalhe.component.spec.ts`
Expected: PASS

- [ ] **Step 10: Rodar o build do frontend (checagem de tipos)**

Run: `cd frontend && npx ng build`
Expected: build sem erros TS novos (o WIP pré-existente em
`frontend/src/app/features/meeting/` — se ainda presente e quebrado — não é
deste plano; se o build já falhava antes desta task por causa dele, ignorar
esses erros específicos e confirmar que nenhum erro novo aponta pra
`detalhe.component.ts`/`api.service.ts`/`models.ts`).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/app/features/detalhe/detalhe.component.ts frontend/src/app/features/detalhe/detalhe.component.spec.ts
git commit -m "feat(frontend): painel Fila de demandas na tela de detalhe"
```

---

### Task 6: Protocolo da orquestração (documento)

**Files:**
- Create: `docs/fila-de-demandas-protocolo.md`

**Interfaces:**
- Consome: rotas da Task 2/3 (`POST/GET /sessions/{base_id}/demands`,
  `PATCH /demands/{id}`, `POST /sessions/{id}/clone` com `name_hint`),
  `POST /{session_id}/input` (já existe,
  `api/app/routers/sessions.py:654-679`), `GET /tasks?session=` (já existe,
  `api/app/routers/history.py:151-159`).

Esta task não tem teste automatizado — é documentação que orienta o
comportamento de uma sessão de agente. A "verificação" é o self-review do
Step 2.

- [ ] **Step 1: Escrever o protocolo**

Criar `docs/fila-de-demandas-protocolo.md`:

```markdown
# Protocolo da Fila de Demandas (SessionFlow)

Faz uma sessão de agente ativa (qualquer uma — claude/gemini/codex/opencode)
processar, **uma de cada vez**, a fila de demandas de um projeto: cada
demanda roda isolada (branch+worktree+tmux novos, contexto zerado) e o
progresso fica registrado na própria fila (visível na aba "Fila" da tela de
detalhe da sessão).

## Quando seguir este protocolo

Quando o usuário pedir explicitamente pra "processar a fila" (ou
equivalente) na sessão atual. `base_id` é o id da sessão/projeto onde a fila
foi aberta — normalmente a própria sessão que recebeu o pedido.

## O loop

1. `GET /sessions/{base_id}/demands?status=pending` — dentre os itens
   retornados, pegue o de menor `created_at` (o endpoint devolve mais
   recente primeiro; escolha o último da lista, ou ordene por `created_at`
   ascendente antes de escolher).

2. Se não houver nenhum item `pending`: pare e informe o usuário que a fila
   está vazia (ou totalmente processada) — não é erro, é o fim normal do
   loop.

3. **Antes de criar a sessão-filha**, marque a demanda como em execução:

   `PATCH /demands/{id}` com corpo `{"status": "in_progress", "started_at": "<agora, ISO-8601>"}`.

   Isso evita picking duplicado se duas execuções do loop (em sessões
   diferentes) tentarem processar a mesma fila ao mesmo tempo.

4. Crie a sessão-filha isolada:

   `POST /sessions/{base_id}/clone` com corpo `{"name_hint": "<slug curto do texto da demanda>"}`
   (ex.: demanda "revisar PRs abertos e comentar achados" → `name_hint:
   "revisar-prs-abertos"`, algumas palavras já bastam). A resposta é
   `202 {command_id, status: "accepted"}` — aguarde o resultado do comando
   (mecanismo de SSE do próprio SessionFlow; se você não tiver acesso a
   SSE, faça polling em `GET /sessions?status=running` procurando uma
   sessão nova com o `branch`/nome esperado, com timeout curto, ex. 20s)
   até obter `session_id` e `branch` da sessão-filha.

5. Envie o texto da demanda como primeira instrução pra sessão-filha:

   `POST /{session_id}/input` com corpo `{"text": "<texto da demanda>", "enter": true}`.

6. Registre a sessão-filha na demanda:

   `PATCH /demands/{id}` com corpo `{"target_session_id": "<session_id>", "branch": "<branch>"}`.

7. Acompanhe periodicamente (ex. a cada 1-2 minutos) `GET /tasks?session=<tmux_name da filha>`:
   - Se existe pelo menos 1 item **e** todos têm `state: "done"` → sucesso, vá pro passo 8.
   - Se passarem 30 minutos desde o `started_at` sem nenhum item aparecer,
     **ou** a sessão-filha caiu pra `status: "error"` (`GET /sessions/{target_session_id}`)
     → falha, vá pro passo 8.

8. Finalize a demanda:
   - **Sucesso:** `PATCH /demands/{id}` com
     `{"status": "completed", "completed_at": "<agora>", "summary": "<títulos dos itens concluídos, concatenados por '; '>"}`.
   - **Falha:** `PATCH /demands/{id}` com
     `{"status": "error", "completed_at": "<agora>", "error_note": "<'timeout sem milestones' ou o erro reportado pela sessão-filha>"}`.

   Uma demanda em `error` **não trava a fila** — ela fica registrada pro
   usuário revisar depois (aba "Fila" mostra o `error_note` em destaque).

9. Volte ao passo 1 (próxima `pending`). Quando a fila esvaziar, pare e
   informe ao usuário quantas demandas concluíram e quantas deram erro
   nesta execução do loop.

## Fora de escopo (YAGNI)

Ver `docs/superpowers/specs/2026-08-29-fila-de-demandas-design.md`, seção
"Fora de escopo": sem fila multi-repo, sem cancelar/editar demanda
`in_progress`, sem paralelismo real do orquestrador, sem sinal de conclusão
novo (é sempre milestones).
```

- [ ] **Step 2: Self-review**

Reler o documento contra `docs/superpowers/specs/2026-08-29-fila-de-demandas-design.md`
seção D — confirmar que os 9 passos batem 1:1 com o spec (mesma ordem, mesma
decisão de "marca in_progress antes de clonar", mesmo timeout de 30min, mesmo
"não trava a fila em erro"). Nenhuma divergência esperada — ambos foram
escritos a partir do mesmo texto aprovado pelo usuário.

- [ ] **Step 3: Commit**

```bash
git add docs/fila-de-demandas-protocolo.md
git commit -m "docs: protocolo da fila de demandas (loop do orquestrador)"
```

---

## Self-Review (spec coverage)

- **Seção A (dados):** Task 1 — coleção `demands`, campos idênticos ao
  spec, índice `(base_session_id, status, created_at)`. ✅
- **Seção B (API):** Task 2 (`POST`/`GET`/`PATCH`) + Task 3 (`clone` com
  `name_hint`). ✅
- **Seção C (frontend):** Task 5 — painel com campo+lista, badges de
  status, link pra sessão-filha, summary/error_note. Local corrigido pra
  `detalhe.component.ts` (ver seção "Correção de local" acima) — mesma
  funcionalidade, local que já segue a convenção do projeto. ✅
- **Seção D (orquestração):** Task 6 — protocolo documentado, 9 passos
  idênticos ao spec. ✅
- **Fora de escopo:** nenhuma task implementa fila multi-repo,
  cancelar/editar `in_progress`, paralelismo do orquestrador, ou sinal de
  conclusão alternativo — confirmado por omissão deliberada. ✅
