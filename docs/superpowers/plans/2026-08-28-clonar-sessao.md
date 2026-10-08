# Clonar sessão — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um botão "Clonar" (3 pontos de entrada: Home, aba Sessões,
dentro da sessão) que cria uma sessão nova, com histórico zerado, rodando num
**git worktree isolado** (branch nova `clone/<slug>`), e abre essa sessão nova
numa **janela própria do navegador** assim que ela estiver pronta.

**Architecture:** Reaproveita o pipeline de comando existente (API publica no
RabbitMQ → worker do host da sessão original consome → efeito no
filesystem/tmux → grava Mongo → emite evento SSE de resultado). Endpoint novo
`POST /sessions/{id}/clone` (202, fire-and-forget); comando novo `type="clone"`
no worker, que cria o worktree via git, delega a `_handle_create` (reaproveita
toda a lógica de lançar a sessão) e grava `worktree_path`/`base_session_id` no
doc. Um gap pré-existente é corrigido como parte desta feature: hoje nenhum
comando devolve o `_id` do Mongo da sessão criada; o frontend precisa disso
pra saber que a sessão está pronta e abrir a janela. `SseService` ganha um
signal `commandResult(commandId)` pra isso.

**Tech Stack:** FastAPI (Python 3.12+) + motor (Mongo async) + aio-pika
(RabbitMQ) na API; Python worker com `libtmux` + `subprocess` (git) +
`pytest`/`pytest-asyncio` (marcado `integration`, roda contra Mongo+RabbitMQ+
tmux reais); Angular 22 standalone + signals + Vitest (`@angular/build:unit-test`,
comando `npm test`) no frontend.

**Spec:** `docs/superpowers/specs/2026-08-28-clonar-sessao-design.md`

## Global Constraints

- Clone **nunca** reaproveita a pasta do repo original nem um worktree
  existente — sempre cria um worktree novo, próprio da sessão clonada.
- Branch da sessão clonada é **sempre nova**: `clone/<slug-da-sessao-nova>`, a
  partir do HEAD atual da sessão original. Nunca reaproveita a branch
  original.
- Elegível pra clonar **só** quando `work_dir` é um único repo git na raiz
  (`git_repos == [{"name": "."}]`). Sem checagem antecipada pra esconder o
  botão — a API responde 400 se não elegível.
- Sessão clonada começa **zerada** (sem `--resume`); herda só config
  (`agent_type`, `model`, `effort`, `host_id`) + o worktree/branch.
- Path do worktree: irmão do repo original —
  `<pai-do-repo>/<nome-repo>-clones/<slug-da-sessao-nova>`.
- Ação do botão: 1 clique, sem diálogo. Nome gerado automaticamente
  (`<nome-original>-clone`, sufixo numérico em colisão).
- Nomes de branch passam pela regex anti-flag-injection já existente em
  `git_info.py`: `_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")`.
- Fora de escopo: clonar `work_dir` "guarda-chuva" (múltiplos subrepos);
  editar config no momento do clone; continuar histórico/conversa; corrigir o
  fluxo de confirmação de `criar.component.ts` além do fix mínimo de
  `session_id` (o signal fica disponível, ninguém é obrigado a consumi-lo).

---

## Task 1: `git_info.create_worktree` / `git_info.remove_worktree`

**Files:**
- Modify: `worker/sessionflow_worker/git_info.py`
- Test: `worker/sessionflow_worker/tests/test_git_info.py`

**Interfaces:**
- Produces: `create_worktree(repo_path: str, dest_path: str, branch_name: str) -> tuple[bool, str]`
  — sucesso: `(True, dest_path)`. Falha: `(False, "<mensagem curta>")`.
- Produces: `remove_worktree(repo_path: str, worktree_path: str) -> tuple[bool, str]`
  — sucesso: `(True, "removido")`. Falha: `(False, "<mensagem curta>")`.
- Consumes: `is_git_repo`, `_BRANCH_NAME_RE` (já existentes em `git_info.py`).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `worker/sessionflow_worker/tests/test_git_info.py`:

```python
def test_create_worktree_creates_dir_and_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo_at(repo)
    dest = tmp_path / "repo-clones" / "minha-sessao-clone"

    ok, msg = git_info.create_worktree(str(repo), str(dest), "clone/minha-sessao-clone")

    assert ok is True
    assert msg == str(dest)
    assert dest.is_dir()
    assert (dest / ".git").exists()  # worktree tem .git (arquivo, aponta pro repo principal)
    branches = git_info.list_branches(str(dest))
    assert "clone/minha-sessao-clone" in branches
    current = git_info._run_git(str(dest), ["rev-parse", "--abbrev-ref", "HEAD"])
    assert current == "clone/minha-sessao-clone"


def test_create_worktree_rejects_non_repo(tmp_path):
    dest = tmp_path / "dest"
    ok, msg = git_info.create_worktree(str(tmp_path), str(dest), "clone/x")
    assert ok is False
    assert "repositório" in msg
    assert not dest.exists()


@pytest.mark.parametrize(
    "branch_name",
    ["--upload-pack=evil", "-x", "; rm -rf /", "branch with spaces"],
)
def test_create_worktree_rejects_flag_like_branch_names(tmp_path, branch_name):
    repo = tmp_path / "repo"
    _init_repo_at(repo)
    dest = tmp_path / "clones" / "x"

    ok, msg = git_info.create_worktree(str(repo), str(dest), branch_name)

    assert ok is False
    assert "inválido" in msg
    assert not dest.exists()


def test_remove_worktree_removes_dir_and_registration(tmp_path):
    repo = tmp_path / "repo"
    _init_repo_at(repo)
    dest = tmp_path / "repo-clones" / "x"
    ok, _ = git_info.create_worktree(str(repo), str(dest), "clone/x")
    assert ok is True

    ok, msg = git_info.remove_worktree(str(repo), str(dest))

    assert ok is True
    assert msg == "removido"
    assert not dest.exists()
    remaining = git_info._run_git(str(repo), ["worktree", "list", "--porcelain"]) or ""
    assert str(dest) not in remaining


def test_remove_worktree_reports_failure_for_missing_path(tmp_path):
    repo = tmp_path / "repo"
    _init_repo_at(repo)
    ok, msg = git_info.remove_worktree(str(repo), str(tmp_path / "never-existed"))
    assert ok is False
    assert msg
```

Confira o topo do arquivo — `pytest` e `git_info` já são importados (mesmo
padrão dos testes existentes de `checkout_branch`); se `pytest` não estiver
importado, adicione `import pytest`.

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_git_info.py -v -k "worktree"`
Expected: FAIL com `AttributeError: module 'sessionflow_worker.git_info' has no attribute 'create_worktree'`

- [ ] **Step 3: Implementar `create_worktree` e `remove_worktree`**

Adicionar ao final de `worker/sessionflow_worker/git_info.py`:

```python
def create_worktree(repo_path: str, dest_path: str, branch_name: str) -> tuple[bool, str]:
    """git worktree add -b <branch_name> <dest_path>, a partir do HEAD atual
    de `repo_path`. Cria os diretórios pai de `dest_path` se necessário.

    Devolve (ok, mensagem) — mesmo padrão de `checkout_branch`: mensagem é o
    `dest_path` em caso de sucesso, ou um erro curto do git em caso de falha
    (branch já existe, disco cheio, etc.).
    """
    if not is_git_repo(repo_path):
        return False, "não é um repositório git"
    if not _BRANCH_NAME_RE.match(branch_name):
        return False, "nome de branch inválido"
    resolved = str(Path(repo_path).expanduser())
    dest = Path(dest_path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(
            ["git", "-C", resolved, "worktree", "add", "-b", branch_name, str(dest)],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "git worktree add expirou (timeout)"
    if out.returncode != 0:
        reason = (out.stderr or out.stdout or "falhou").strip().splitlines()
        return False, (reason[-1] if reason else "git worktree add falhou")[:200]
    return True, str(dest)


def remove_worktree(repo_path: str, worktree_path: str) -> tuple[bool, str]:
    """git worktree remove --force <worktree_path>, rodado a partir de
    `repo_path`. Best-effort — chamado na limpeza de uma sessão clonada;
    falha aqui nunca deve travar o delete da sessão.
    """
    if not is_git_repo(repo_path):
        return False, "repo original não é mais um repositório git"
    resolved = str(Path(repo_path).expanduser())
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                resolved,
                "worktree",
                "remove",
                "--force",
                str(Path(worktree_path).expanduser()),
            ],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "git worktree remove expirou (timeout)"
    if out.returncode != 0:
        reason = (out.stderr or out.stdout or "falhou").strip().splitlines()
        return False, (reason[-1] if reason else "git worktree remove falhou")[:200]
    return True, "removido"
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_git_info.py -v -k "worktree"`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/git_info.py worker/sessionflow_worker/tests/test_git_info.py
git commit -m "feat(worker): git_info.create_worktree/remove_worktree"
```

---

## Task 2: Worker `_handle_create` devolve `session_id`

**Files:**
- Modify: `worker/sessionflow_worker/command_consumer.py`
- Test: `worker/sessionflow_worker/tests/test_command_consumer.py`

**Interfaces:**
- Produces: `_handle_create` (via `_dispatch`/`handle`) agora inclui
  `"session_id": str | None` no dict retornado (além dos campos já
  existentes `name`, `launch_cmd`). `_emit` funde esse dict no evento SSE —
  então o evento emitido para `type="create"` passa a trazer `session_id`.
- Consumes: `ReturnDocument` (novo import de `pymongo`).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `worker/sessionflow_worker/tests/test_command_consumer.py` (perto
de `test_create_ok`, reaproveitando as fixtures `consumer`, `make_name`,
`patch_launch`, `channel`):

```python
@pytest.mark.integration
async def test_create_emits_session_id(consumer, make_name, patch_launch, channel, tmp_path):
    name = make_name()
    command = _cmd(
        "create",
        {"name": name, "work_dir": str(tmp_path), "agent_type": "claude"},
    )

    event = await consumer.handle(command)

    assert event["ok"] is True
    assert event.get("session_id"), "evento de create deveria trazer o _id do Mongo"
    doc = await consumer._sessions.find_one({"tmux_name": name})
    assert doc is not None
    assert event["session_id"] == str(doc["_id"])
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k test_create_emits_session_id`
Expected: FAIL em `assert event.get("session_id")` (chave ausente)

- [ ] **Step 3: Implementar o fix**

Em `worker/sessionflow_worker/command_consumer.py`, adicionar o import (perto
dos demais imports de terceiros, linha ~63):

```python
from pymongo import ReturnDocument
```

Trocar o `await self._sessions.update_one(...)` + `return {"name": name,
"launch_cmd": launch_cmd}` no final de `_handle_create` por:

```python
        doc = await self._sessions.find_one_and_update(
            {"tmux_name": name},
            {
                "$set": {
                    "tmux_name": name,
                    "display_name": display,
                    "origin": SESSION_ORIGIN,
                    "status": SessionState.RUNNING.value,
                    "agent_type": agent_type.value,
                    "model": model,
                    "effort": effort,
                    "work_dir": str(work_dir),
                    "tmux_id": info.id,
                    "claude_session_id": claude_session_id,
                    "parent": parent,
                    "host_id": self._host_id,
                    "updated_at": now,
                    "last_activity_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return {
            "name": name,
            "launch_cmd": launch_cmd,
            "session_id": str(doc["_id"]) if doc else None,
        }
```

(`now = _now()` continua igual, só a chamada de update muda de `update_one`
pra `find_one_and_update` com `return_document=ReturnDocument.AFTER`.)

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k "test_create_emits_session_id or test_create_ok"`
Expected: PASS (ambos — `test_create_ok` continua passando, o novo campo não
quebra nada que ele já verificava)

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/command_consumer.py worker/sessionflow_worker/tests/test_command_consumer.py
git commit -m "fix(worker): _handle_create devolve session_id (Mongo _id) no evento"
```

---

## Task 3: Worker `_handle_clone`

**Files:**
- Modify: `worker/sessionflow_worker/command_consumer.py`
- Test: `worker/sessionflow_worker/tests/test_command_consumer.py`

**Interfaces:**
- Consumes: `git_info.create_worktree` (Task 1), `_handle_create` com
  `session_id` no retorno (Task 2), `_slugify(s: str) -> str` (já existe,
  módulo-level).
- Produces: comando `type="clone"` aceito por `handle`/`_dispatch`; payload
  esperado: `{"name": str, "repo_path": str, "agent_type": str, "model":
  str|None, "effort": str|None, "source_session_id": str, "display_name":
  str|None}`. Evento emitido inclui todos os campos de `_handle_create`
  (inclusive `session_id`) mais `worktree_path` e `branch`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `worker/sessionflow_worker/tests/test_command_consumer.py`. Este
teste cria um repo git real em `tmp_path` (mesmo padrão de
`test_git_info.py`) e usa esse `tmp_path` como `repo_path`:

```python
def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, check=True, capture_output=True)


@pytest.mark.integration
async def test_clone_creates_worktree_and_session(consumer, make_name, patch_launch, channel, tmp_path):
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    name = make_name()

    command = _cmd(
        "clone",
        {
            "name": name,
            "repo_path": str(repo_path),
            "agent_type": "claude",
            "model": None,
            "effort": None,
            "source_session_id": "000000000000000000000000",
            "display_name": f"{name} (clone)",
        },
    )

    event = await consumer.handle(command)

    assert event["ok"] is True, event
    assert event.get("session_id")
    dest = repo_path.parent / "repo-clones" / name
    assert event["worktree_path"] == str(dest)
    assert event["branch"] == f"clone/{name}"
    assert dest.is_dir()

    doc = await consumer._sessions.find_one({"tmux_name": name})
    assert doc is not None
    assert doc["work_dir"] == str(dest)
    assert doc["worktree_path"] == str(dest)
    assert doc["worktree_repo"] == str(repo_path)
    assert doc["base_session_id"] == "000000000000000000000000"

    assert consumer._runtime.has_session(name)
    consumer._runtime.kill_session(name)


@pytest.mark.integration
async def test_clone_rejects_missing_repo_path(consumer, make_name, channel):
    command = _cmd("clone", {"name": make_name()})
    event = await consumer.handle(command)
    assert event["ok"] is False
    assert "repo_path" in event["error"]
```

Adicionar `import subprocess` no topo do arquivo de teste, se ainda não
estiver importado.

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k test_clone`
Expected: FAIL — `event["ok"] is False` com erro "tipo de comando desconhecido: 'clone'"

- [ ] **Step 3: Implementar `_handle_clone`**

Em `worker/sessionflow_worker/command_consumer.py`:

Adicionar `"clone"` em `_STALE_GUARDED_TYPES` (linha ~87-89):

```python
_STALE_GUARDED_TYPES = frozenset(
    {"create", "clone", "resume", "input", "key", "audio", "file", "switch_agent"}
)
```

Adicionar `"clone"` em `_VALID_TYPES` (linha ~102-124), na mesma lista que já
tem `"create"`.

Em `_dispatch` (linha ~600-641), adicionar o branch antes do
`raise CommandError(f"tipo de comando desconhecido: {ctype!r}")` final:

```python
        if ctype == "clone":
            return await self._handle_clone(payload)
```

Adicionar o novo handler logo após `_handle_create` (antes de
`_handle_delete`):

```python
    async def _handle_clone(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Clona uma sessão: cria um WORKTREE git isolado (branch
        `clone/<slug>`, a partir do HEAD do repo original) e lança uma sessão
        nova, zerada, dentro dele. Nunca reaproveita a pasta nem o worktree
        do repo original.
        """
        name = payload.get("name")
        if not name:
            raise CommandError("clone requer 'name'")
        repo_path = payload.get("repo_path")
        if not repo_path:
            raise CommandError("clone requer 'repo_path'")
        slug = _slugify(name)
        if not slug:
            raise CommandError("nome inválido (vazio após slug)")

        repo_dir = Path(repo_path).expanduser()
        dest_path = repo_dir.parent / f"{repo_dir.name}-clones" / slug
        branch_name = f"clone/{slug}"

        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(
            None, git_info.create_worktree, repo_path, str(dest_path), branch_name
        )
        if not ok:
            raise CommandError(f"falha ao criar worktree: {msg}")

        create_payload = {**payload, "work_dir": str(dest_path)}
        result = await self._handle_create(create_payload)

        await self._sessions.update_one(
            {"tmux_name": result["name"]},
            {
                "$set": {
                    "worktree_path": str(dest_path),
                    "worktree_repo": repo_path,
                    "base_session_id": payload.get("source_session_id"),
                }
            },
        )
        return {**result, "worktree_path": str(dest_path), "branch": branch_name}
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k test_clone`
Expected: PASS (2 testes)

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/command_consumer.py worker/sessionflow_worker/tests/test_command_consumer.py
git commit -m "feat(worker): comando type=clone cria worktree isolado + sessão zerada"
```

---

## Task 4: Worker `_handle_delete` limpa o worktree

**Files:**
- Modify: `worker/sessionflow_worker/command_consumer.py`
- Test: `worker/sessionflow_worker/tests/test_command_consumer.py`

**Interfaces:**
- Consumes: `git_info.remove_worktree` (Task 1).
- Produces: nenhuma mudança de assinatura pública — `_handle_delete`
  continua devolvendo `{"name": name, "deleted": True}`.

- [ ] **Step 1: Escrever o teste que falha**

```python
@pytest.mark.integration
async def test_delete_removes_worktree_when_cloned(consumer, make_name, channel, tmp_path):
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    dest = tmp_path / "repo-clones" / "x"
    ok, _ = git_info.create_worktree(str(repo_path), str(dest), "clone/x")
    assert ok is True

    name = make_name()
    await consumer._sessions.insert_one(
        {
            "tmux_name": name,
            "work_dir": str(dest),
            "worktree_path": str(dest),
            "worktree_repo": str(repo_path),
            "status": "running",
        }
    )

    event = await consumer.handle(_cmd("delete", {"name": name}))

    assert event["ok"] is True
    assert not dest.exists()
    assert await consumer._sessions.find_one({"tmux_name": name}) is None
```

Adicionar `from sessionflow_worker import git_info` no topo do arquivo de
teste, se ainda não importado (já é usado por `test_clone_creates_worktree_and_session`
indiretamente via `consumer`, mas o teste acima chama `git_info.create_worktree`
diretamente).

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k test_delete_removes_worktree`
Expected: FAIL — `assert not dest.exists()` (worktree continua no disco)

- [ ] **Step 3: Implementar o fix**

Em `_handle_delete`, trocar o início do método:

```python
    async def _handle_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not name:
            raise CommandError("delete requer 'name'")

        doc = await self._sessions.find_one(
            {"tmux_name": name},
            projection={"worktree_path": 1, "worktree_repo": 1},
        )

        try:
            if self._runtime.has_session(name):
                self._runtime.kill_session(name)
        except TmuxRuntimeError:
            pass

        # Sessão clonada (worktree próprio): remove o worktree do disco
        # também — best-effort, falha aqui NUNCA bloqueia o delete da sessão.
        if doc and doc.get("worktree_path") and doc.get("worktree_repo"):
            loop = asyncio.get_running_loop()
            ok, msg = await loop.run_in_executor(
                None,
                git_info.remove_worktree,
                doc["worktree_repo"],
                doc["worktree_path"],
            )
            if not ok:
                logger.warning(
                    "delete %r: falha ao remover worktree %r: %s",
                    name,
                    doc["worktree_path"],
                    msg,
                )

        db = self._sessions.database
        await self._sessions.delete_one({"tmux_name": name})
```

(o restante do método — loop de `delete_many` em `tasks`/`session_output`/
`events` e `session_screen`, e o `return {"name": name, "deleted": True}` —
continua igual.)

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k "test_delete_removes_worktree or test_delete"`
Expected: PASS (inclui os testes de delete pré-existentes, que não têm
`worktree_path` no doc — `doc.get("worktree_path")` é falsy pra eles, então o
bloco novo não roda e o comportamento deles não muda)

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/command_consumer.py worker/sessionflow_worker/tests/test_command_consumer.py
git commit -m "fix(worker): delete de sessão clonada remove o worktree (best-effort)"
```

---

## Task 5: API `POST /sessions/{id}/clone`

**Files:**
- Modify: `api/app/routers/sessions.py`
- Test: `api/app/tests/test_sessions_clone.py` (novo arquivo)

**Interfaces:**
- Produces: `POST /sessions/{session_id}/clone` — sem body. 202
  `SessionCreateAccepted{command_id, status:"accepted"}`. 400 se a sessão de
  origem não for elegível (`git_repos` não é exatamente
  `[{"name": "."}]`). 404 se `session_id` não existir.
- Consumes: `repo.get_session(session_id)` (`SessionsRepository`, já
  existente), `repo.active_with_name_exists(name)` (já existente),
  `publish_command` (já existente).

- [ ] **Step 1: Escrever os testes que falham**

Criar `api/app/tests/test_sessions_clone.py`:

```python
"""Integration tests for POST /sessions/{id}/clone.

Segue o mesmo padrão de `test_sessions_create.py`: coleção Mongo isolada por
teste, comandos publicados na fila do host de teste (drenados/removidos no
teardown pra nunca chegar no worker real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.main import create_app
from app.tests.test_sessions_create import (
    _TEST_HOST_ID,
    _client,
    drain_commands,
    settings,
)


def _seed_session(
    *,
    name: str,
    git_repos: list[dict] | None,
    host_id: str = _TEST_HOST_ID,
) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "model": "opus",
        "effort": "high",
        "status": "running",
        "work_dir": "/tmp/repo",
        "git_repos": git_repos,
        "host_id": host_id,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.integration
async def test_clone_eligible_publishes_command(settings, drain_commands):
    name = f"clone-src-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(name=name, git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}])

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    command_id = body["command_id"]

    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["type"] == "clone"
    assert msg["payload"]["name"] == f"{name}-clone"
    assert msg["payload"]["repo_path"] == "/tmp/repo"
    assert msg["payload"]["source_session_id"] == str(doc["_id"])
    assert msg["payload"]["agent_type"] == "claude"
    assert msg["payload"]["model"] == "opus"
    assert msg["payload"]["effort"] == "high"


@pytest.mark.integration
async def test_clone_dedupes_name_on_collision(settings, drain_commands):
    name = f"clone-dup-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(name=name, git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}])
    existing_clone = _seed_session(name=f"{name}-clone", git_repos=None)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    coll = client[settings.mongo_db][settings.sessions_collection]
    await coll.insert_one(doc)
    await coll.insert_one(existing_clone)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == f"{name}-clone-2"


@pytest.mark.integration
async def test_clone_rejects_umbrella_work_dir(settings):
    name = f"clone-umb-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[
            {"name": "backend", "path": "/tmp/repo/backend", "branch": "main"},
            {"name": "frontend", "path": "/tmp/repo/frontend", "branch": "main"},
        ],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 400


@pytest.mark.integration
async def test_clone_not_found_404(settings):
    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{ObjectId()}/clone")
    assert resp.status_code == 404
```

Nota: `settings` e `drain_commands` são fixtures pytest — importá-las de
`test_sessions_create` funciona porque pytest reconhece fixtures importadas
por nome no módulo de teste (mesmo padrão não é usado hoje no repo, então
confirme rodando; se o pytest não resolver, copie as duas fixtures + `_client`
para o novo arquivo em vez de importar — ambas têm ~15 linhas cada, ver
`test_sessions_create.py:64-144`).

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd api && python -m pytest app/tests/test_sessions_clone.py -v`
Expected: FAIL com 404 em vez de 202 (rota `/clone` não existe ainda → vira
404 do FastAPI por rota não encontrada, ou erro de import se a fixture não
resolver — nesse caso, copie as fixtures conforme a nota acima)

- [ ] **Step 3: Implementar o endpoint**

Em `api/app/routers/sessions.py`, adicionar logo após `create_session`
(depois da linha `return SessionCreateAccepted(command_id=command_id,
status="accepted")` do `create_session`, antes de `@router.get("/{session_id}"...)`):

```python
async def _dedupe_name(repo: SessionsRepository, base: str) -> str:
    """Nome tmux disponível derivado de `base`: `base` se livre, senão
    `base-2`, `base-3`... (mesma checagem de duplicidade de `create_session`).
    """
    if not await repo.active_with_name_exists(base):
        return base
    n = 2
    while await repo.active_with_name_exists(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


@router.post("/{session_id}/clone", response_model=SessionCreateAccepted, status_code=202)
async def clone_session(session_id: str, request: Request) -> SessionCreateAccepted:
    """Clona uma sessão: sessão nova, histórico zerado, herdando só config
    (agent_type/model/effort/host_id), rodando num worktree git isolado numa
    branch nova. Só elegível quando `work_dir` é um único repo git na raiz.
    """
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
    new_name = await _dedupe_name(repo, f"{base_name}-clone")
    base_display = doc.get("display_name") or base_name
    payload = {
        "name": new_name,
        "display_name": f"{base_display} (clone)",
        "agent_type": doc.get("agent_type"),
        "model": doc.get("model"),
        "effort": doc.get("effort"),
        "repo_path": doc.get("work_dir"),
        "source_session_id": session_id,
    }
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="clone", payload=payload, host_id=doc.get("host_id")
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd api && python -m pytest app/tests/test_sessions_clone.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/sessions.py api/app/tests/test_sessions_clone.py
git commit -m "feat(api): POST /sessions/{id}/clone"
```

---

## Task 6: Frontend `SseService.commandResult`

**Files:**
- Modify: `frontend/src/app/core/sse.service.ts`
- Test: `frontend/src/app/core/sse.service.spec.ts`

**Interfaces:**
- Produces: `interface CommandResultFrame { command_id: string; type?: string;
  ok: boolean; session_id?: string; error?: string; [key: string]: unknown }`
  e `commandResult(commandId: string): CommandResultFrame | undefined`
  (método público em `SseService`).
- Consumes: nenhuma interface nova de fora — só lê frames já trafegados pelo
  `EventSource` existente.

- [ ] **Step 1: Escrever o teste que falha**

Em `frontend/src/app/core/sse.service.spec.ts`, adicionar (mesmo padrão dos
testes existentes — `TestBed`, `FakeEventSource`):

```ts
it('captures command results by command_id without disrupting the normal event flow', () => {
  const service = TestBed.inject(SseService);
  service.connect();
  const frame = { command_id: 'c1', type: 'clone', ok: true, session_id: 'sid1', name: 'x-clone' };
  FakeEventSource.last!.emitMessage(frame);

  expect(service.commandResult('c1')).toEqual(frame);
  expect(service.commandResult('missing')).toBeUndefined();
  // Continua caindo no pipeline genérico de eventos (comportamento preservado).
  expect(service.events()).toHaveLength(1);
});
```

Confirme o nome exato do helper de emissão de mensagem no `FakeEventSource`
(`emitMessage` ou similar) e o padrão de acesso à última instância
(`FakeEventSource.last`) lendo os testes já existentes no topo do arquivo —
use o MESMO padrão dos testes vizinhos, sem inventar API nova no fake.

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd frontend && npx ng test --watch=false --include='**/sse.service.spec.ts'`
Expected: FAIL — `service.commandResult is not a function`

- [ ] **Step 3: Implementar**

Em `frontend/src/app/core/sse.service.ts`, adicionar a interface perto das
outras (`GitBranchesFrame` etc.):

```ts
export interface CommandResultFrame {
  command_id: string;
  type?: string;
  ok: boolean;
  session_id?: string;
  error?: string;
  [key: string]: unknown;
}
```

Na classe `SseService`, adicionar o signal privado e o método público (perto
dos outros signals, ex. junto de `gitBranches`):

```ts
  /** Resultados de comandos assíncronos (create/clone/...), por command_id —
   * usado por fluxos que precisam saber quando um comando específico
   * terminou (ex.: abrir a janela da sessão assim que ela existir). */
  private readonly commandResults = signal<Record<string, CommandResultFrame>>({});

  /** Resultado do comando `commandId`, ou `undefined` enquanto não chegou. */
  commandResult(commandId: string): CommandResultFrame | undefined {
    return this.commandResults()[commandId];
  }
```

Em `handleMessage()`, logo após o `JSON.parse` bem-sucedido (antes das
checagens de `kind === 'screen'` etc. — não precisa ser exatamente no topo,
só precisa rodar pra QUALQUER frame com `command_id`+`ok`), adicionar:

```ts
    // Resultado de um comando assíncrono (create/clone/...): guarda por
    // command_id ALÉM do fluxo normal abaixo — não retorna cedo, então o
    // pipeline existente (events/notifications/etc.) continua intacto.
    const cr = parsed as { command_id?: string; ok?: unknown };
    if (typeof cr.command_id === 'string' && typeof cr.ok === 'boolean') {
      const frame = parsed as CommandResultFrame;
      this.commandResults.update((m) => ({ ...m, [frame.command_id]: frame }));
    }
```

Importante: este bloco **não** tem `return` — o objetivo é só cachear o
frame, sem interferir no processamento existente (evita regressão em
qualquer fluxo que já dependa de frames com `command_id` chegarem em
`events`/`notifications`).

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd frontend && npx ng test --watch=false --include='**/sse.service.spec.ts'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/core/sse.service.ts frontend/src/app/core/sse.service.spec.ts
git commit -m "feat(frontend): SseService.commandResult(commandId)"
```

---

## Task 7: Frontend `ApiService.cloneSession`

**Files:**
- Modify: `frontend/src/app/core/api.service.ts`

**Interfaces:**
- Produces: `cloneSession(id: string): Observable<{ command_id: string }>`.

- [ ] **Step 1: Implementar**

Em `frontend/src/app/core/api.service.ts`, adicionar logo após
`purgeSession` (linha ~99):

```ts
  /**
   * Clona a sessão: cria uma sessão NOVA, zerada, num git worktree isolado
   * (branch própria). 202 `{command_id}` — o worker confirma via SSE
   * (`SseService.commandResult(commandId)`), com `session_id` quando pronta.
   */
  cloneSession(id: string): Observable<{ command_id: string }> {
    return this.http.post<{ command_id: string }>(this.url(`/sessions/${id}/clone`), {});
  }
```

Este método não tem teste unitário dedicado (é um `http.post` de uma linha
que espelha `purgeSession`/`resumeSession` já existentes, sem lógica
própria) — a cobertura vem do teste de `SessionCloneService` (Task 8), que
usa `HttpTestingController` e verifica a chamada real.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/app/core/api.service.ts
git commit -m "feat(frontend): ApiService.cloneSession"
```

---

## Task 8: Frontend `SessionCloneService` + toast de erro

**Files:**
- Create: `frontend/src/app/core/session-clone.service.ts`
- Test: `frontend/src/app/core/session-clone.service.spec.ts`
- Modify: `frontend/src/app/app.html`
- Modify: `frontend/src/app/app.ts`

**Interfaces:**
- Consumes: `ApiService.cloneSession(id)` (Task 7),
  `SseService.commandResult(commandId)` (Task 6).
- Produces: `SessionCloneService.clone(sessionId: string): void` (dispara o
  fluxo completo: chama a API, observa o resultado, abre janela nova ou
  mostra erro). `SessionCloneService.errorToast: Signal<string | null>`
  (mensagem de erro atual, ou `null`).

- [ ] **Step 1: Escrever o teste que falha**

Criar `frontend/src/app/core/session-clone.service.spec.ts`:

```ts
import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { API_BASE_URL } from './api.service';
import { SessionCloneService } from './session-clone.service';
import { SseService } from './sse.service';

describe('SessionCloneService', () => {
  let service: SessionCloneService;
  let httpMock: HttpTestingController;
  let sse: SseService;
  let openSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: 'http://test.local' },
      ],
    });
    service = TestBed.inject(SessionCloneService);
    httpMock = TestBed.inject(HttpTestingController);
    sse = TestBed.inject(SseService);
    openSpy = vi.fn();
    vi.stubGlobal('open', openSpy);
  });

  afterEach(() => {
    httpMock.verify();
    vi.unstubAllGlobals();
  });

  it('opens a new window when the clone command resolves ok', () => {
    service.clone('src-id');

    const req = httpMock.expectOne('http://test.local/sessions/src-id/clone');
    expect(req.request.method).toBe('POST');
    req.flush({ command_id: 'cmd-1' });

    expect(openSpy).not.toHaveBeenCalled();
    vi.spyOn(sse, 'commandResult').mockReturnValue({
      command_id: 'cmd-1',
      ok: true,
      session_id: 'new-sess-id',
    });
    service['pollOnce']('cmd-1');

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy.mock.calls[0][0]).toContain('/sessao/new-sess-id');
    expect(service.errorToast()).toBeNull();
  });

  it('shows an error toast when the clone command fails', () => {
    service.clone('src-id');
    const req = httpMock.expectOne('http://test.local/sessions/src-id/clone');
    req.flush({ command_id: 'cmd-2' });

    vi.spyOn(sse, 'commandResult').mockReturnValue({
      command_id: 'cmd-2',
      ok: false,
      error: 'falha ao criar worktree: disco cheio',
    });
    service['pollOnce']('cmd-2');

    expect(openSpy).not.toHaveBeenCalled();
    expect(service.errorToast()).toBe('falha ao criar worktree: disco cheio');
  });

  it('shows a neutral toast on HTTP failure calling clone', () => {
    service.clone('src-id');
    const req = httpMock.expectOne('http://test.local/sessions/src-id/clone');
    req.flush({ detail: 'sessão não pode ser clonada' }, { status: 400, statusText: 'Bad Request' });

    expect(service.errorToast()).toBe('sessão não pode ser clonada');
  });
});
```

Este teste chama `service['pollOnce'](...)` diretamente (método privado) em
vez de esperar o `setInterval` real — evita teste baseado em tempo (frágil).
`clone()` internamente agenda chamadas a `pollOnce` via `setInterval`; o
teste só invoca `pollOnce` manualmente pra validar a lógica de decisão
(abrir janela vs. mostrar erro), sem depender do agendamento.

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd frontend && npx ng test --watch=false --include='**/session-clone.service.spec.ts'`
Expected: FAIL — módulo `./session-clone.service` não existe

- [ ] **Step 3: Implementar `SessionCloneService`**

Criar `frontend/src/app/core/session-clone.service.ts`:

```ts
import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { SseService } from './sse.service';

/** Abre a sessão `sessionId` numa janela própria do navegador — mesmo
 * padrão/tamanho usado em `session-panel.component.ts#openInNewWindow`. */
export function openSessionWindow(sessionId: string): void {
  const url = `${location.origin}/sessao/${sessionId}`;
  const w = 760;
  const h = 860;
  const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
  const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
  window.open(
    url,
    `sf-janela-${sessionId}`,
    `popup=yes,width=${w},height=${h},left=${left},top=${top},noopener`,
  );
}

const POLL_INTERVAL_MS = 400;
const TIMEOUT_MS = 15000;

/** Orquestra o fluxo de clonar sessão a partir de qualquer um dos 3 pontos
 * de entrada da UI: chama a API, aguarda a confirmação assíncrona via SSE
 * (`SseService.commandResult`) e abre a janela nova quando a sessão clonada
 * estiver pronta. Erros (400 síncrono ou `ok:false` assíncrono) viram uma
 * mensagem em `errorToast`. Timeout (~15s) é fail-soft: mensagem neutra,
 * não é tratado como erro real (a sessão pode ter sido criada mesmo assim).
 */
@Injectable({ providedIn: 'root' })
export class SessionCloneService {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);

  readonly errorToast = signal<string | null>(null);

  clone(sessionId: string): void {
    this.api.cloneSession(sessionId).subscribe({
      next: ({ command_id }) => this.watch(command_id),
      error: (err) => {
        const detail = err?.error?.detail || err?.error?.message || 'falha ao clonar sessão';
        this.errorToast.set(detail);
      },
    });
  }

  private watch(commandId: string): void {
    const startedAt = Date.now();
    const timer = setInterval(() => {
      if (this.pollOnce(commandId)) {
        clearInterval(timer);
        return;
      }
      if (Date.now() - startedAt > TIMEOUT_MS) {
        clearInterval(timer);
        this.errorToast.set('clonagem solicitada — confira na lista de sessões');
      }
    }, POLL_INTERVAL_MS);
  }

  /** Uma checagem do resultado do comando. Devolve `true` quando já resolveu
   * (ok ou erro) — sinal pro chamador parar de repetir. */
  private pollOnce(commandId: string): boolean {
    const result = this.sse.commandResult(commandId);
    if (!result) {
      return false;
    }
    if (result.ok && result.session_id) {
      openSessionWindow(result.session_id);
    } else if (!result.ok) {
      this.errorToast.set(result.error || 'falha ao clonar sessão');
    }
    return true;
  }
}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd frontend && npx ng test --watch=false --include='**/session-clone.service.spec.ts'`
Expected: PASS (3 testes)

- [ ] **Step 5: Adicionar o toast de erro em `app.html`/`app.ts`**

Em `frontend/src/app/app.ts`, injetar o serviço (perto de onde `sse` já é
injetado):

```ts
  protected readonly sessionClone = inject(SessionCloneService);
```

(adicionar `import { SessionCloneService } from './core/session-clone.service';`
no topo.)

Em `frontend/src/app/app.html`, adicionar um bloco de toast não-clicável,
próximo ao bloco existente de `sse.globalToast()` (reaproveitando a família
de classes CSS `sf-global-toast`, sem o `(click)` de navegação):

```html
@if (sessionClone.errorToast(); as cloneErr) {
  <div class="sf-global-toast sf-global-toast--error" role="alert">
    {{ cloneErr }}
  </div>
}
```

Sem CSS novo: `sf-global-toast` já existe; se quiser destacar erro visualmente,
adicionar só a variante `.sf-global-toast--error { border-color: #e05252; }`
no stylesheet do componente — opcional, avaliar junto do restante do visual
existente no momento da implementação real (não é crítico pro funcionamento).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/core/session-clone.service.ts frontend/src/app/core/session-clone.service.spec.ts frontend/src/app/app.html frontend/src/app/app.ts
git commit -m "feat(frontend): SessionCloneService orquestra clonar sessão + toast de erro"
```

---

## Task 9: Botão "Clonar" dentro da sessão (`session-panel.component.ts`)

**Files:**
- Modify: `frontend/src/app/features/detalhe/session-panel.component.ts`

**Interfaces:**
- Consumes: `SessionCloneService.clone(sessionId)` (Task 8).

- [ ] **Step 1: Implementar**

Em `frontend/src/app/features/detalhe/session-panel.component.ts`, injetar o
serviço junto dos outros `inject(...)` da classe:

```ts
  private readonly sessionClone = inject(SessionCloneService);
```

(adicionar o import correspondente no topo do arquivo.)

No template inline, dentro do `.panel-menu[role=menu]` (linhas ~75-101), ao
lado do item de `openInNewWindow()`, adicionar:

```html
<button type="button" class="pm-item" role="menuitem" (click)="cloneSession()">
  Clonar
</button>
```

Adicionar o método na classe, perto de `openInNewWindow`:

```ts
  protected cloneSession(): void {
    this.menuOpen.set(false);
    this.sessionClone.clone(this.sessionId());
  }
```

Sem teste dedicado neste componente: a lógica de decisão (abrir janela vs.
erro) já está coberta pelo teste de `SessionCloneService` (Task 8); este
método só delega uma chamada.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/app/features/detalhe/session-panel.component.ts
git commit -m "feat(frontend): botão Clonar dentro da sessão"
```

---

## Task 10: Botão "Clonar" no card da Home (`inicio.component.ts`)

**Files:**
- Modify: `frontend/src/app/features/inicio/inicio.component.ts`

**Interfaces:**
- Consumes: `SessionCloneService.clone(sessionId)` (Task 8).

- [ ] **Step 1: Implementar**

Injetar `SessionCloneService` na classe (mesmo padrão da Task 9).

No template, o card hoje é um `<button class="sf-card sf-enter" ...
(click)="openSession(s.id)">` inteiro (linhas ~222-362) — não dá pra aninhar
outro botão dentro. Envolver o `<button class="sf-card">` existente num
container `<div class="sf-card-item">`, com um novo botão irmão
posicionado por cima via CSS:

```html
<div class="sf-card-item">
  <button class="sf-card sf-enter" ... (click)="openSession(s.id)">
    <!-- conteúdo existente do card, sem alteração -->
  </button>
  <button
    type="button"
    class="sf-card-clone"
    aria-label="Clonar sessão"
    (click)="cloneSession($event, s.id)"
  >
    ⧉
  </button>
</div>
```

Adicionar o método na classe:

```ts
  protected cloneSession(event: Event, sessionId: string): void {
    event.stopPropagation();
    this.sessionClone.clone(sessionId);
  }
```

(`stopPropagation` evita que o clique no botão de clone também dispare
`openSession` do card por baixo, já que os dois ficam sobrepostos.)

CSS (no bloco de estilos do componente, perto de `.sf-card`):

```css
.sf-card-item {
  position: relative;
}
.sf-card-clone {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 1;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
  font-size: 16px;
  line-height: 1;
}
```

Sem teste dedicado — comportamento equivalente ao da Task 9 (delega pro
`SessionCloneService`, já coberto).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/app/features/inicio/inicio.component.ts
git commit -m "feat(frontend): botão Clonar no card da Home"
```

---

## Task 11: Botão "Clonar" no card da aba Sessões (`sessoes.component.ts`)

**Files:**
- Modify: `frontend/src/app/features/sessoes/sessoes.component.ts`

**Interfaces:**
- Consumes: `SessionCloneService.clone(sessionId)` (Task 8).

- [ ] **Step 1: Implementar**

Injetar `SessionCloneService` na classe (mesmo padrão das Tasks 9/10).

No template, `<li class="sf-card-wrap">` (linhas ~238-299) já contém os
botões irmãos `sf-delete`, `sf-fav` e `sf-card` — adicionar mais um irmão
`sf-clone` seguindo exatamente o mesmo padrão de offset/posicionamento:

```html
<button
  type="button"
  class="sf-clone"
  aria-label="Clonar sessão"
  (click)="cloneSession($event, s.id)"
>
  ⧉
</button>
```

Adicionar o método na classe:

```ts
  protected cloneSession(event: Event, sessionId: string): void {
    event.stopPropagation();
    this.sessionClone.clone(sessionId);
  }
```

CSS: seguir o MESMO padrão já usado por `.sf-fav`/`.sf-delete` neste arquivo
(mesma técnica de offset via `translateX`) — copiar as propriedades de
posicionamento de `.sf-fav` e ajustar `left`/`right` pra não sobrepor os
botões existentes.

Sem teste dedicado — mesma justificativa das Tasks 9/10.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/app/features/sessoes/sessoes.component.ts
git commit -m "feat(frontend): botão Clonar no card da aba Sessões"
```

---

## Self-Review

**1. Cobertura do spec:**
- Histórico zerado / herda config → Task 3 (`_handle_clone` chama
  `_handle_create` puro, sem `--resume`) + Task 5 (payload só com
  `agent_type`/`model`/`effort`, sem histórico). ✅
- Branch sempre nova `clone/<slug>` a partir do HEAD → Task 1
  (`create_worktree`) + Task 3 (`branch_name = f"clone/{slug}"`). ✅
- Elegibilidade (repo git único na raiz, API responde 400) → Task 5
  (`clone_session` checa `git_repos`). ✅
- Path do worktree irmão do repo → Task 3 (`dest_path = repo_dir.parent /
  f"{repo_dir.name}-clones" / slug`). ✅
- Ação de 1 clique, nome auto-gerado com dedupe → Task 5 (`_dedupe_name`). ✅
- Novo endpoint `POST /sessions/{id}/clone` → Task 5. ✅
- Novo comando `type="clone"` no worker → Task 3. ✅
- `create_worktree` em `git_info.py` → Task 1. ✅
- Confirmação de prontidão (`session_id` no evento, `commandResult` no
  frontend) → Tasks 2 e 6. ✅
- Limpeza no delete (`git worktree remove --force` best-effort) → Task 4. ✅
- UI nos 3 pontos de entrada → Tasks 9, 10, 11. ✅
- Testes (worker unit/integration, API, frontend) → presentes em todas as
  tasks relevantes. ✅
- Fora de escopo (guarda-chuva, editar config no clone, continuar
  histórico, fluxo de `criar.component.ts`) → nenhuma task mexe nisso. ✅

**2. Placeholder scan:** nenhum "TBD"/"TODO"/"implementar depois" — toda
step de código tem o código completo e real, com nomes de arquivo/linha
verificados contra o código atual do repo.

**3. Consistência de tipos entre tasks:**
- `create_worktree`/`remove_worktree` sempre `tuple[bool, str]` (Task 1),
  consumidos exatamente assim nas Tasks 3 e 4.
- `_handle_create` retorna `{"name", "launch_cmd", "session_id"}` (Task 2);
  Task 3 faz `result = await self._handle_create(...)` e usa `result["name"]`/
  spread `**result` — compatível.
- Payload do comando `clone` (`name`, `repo_path`, `agent_type`, `model`,
  `effort`, `source_session_id`, `display_name`) é o MESMO conjunto de chaves
  entre o que a Task 5 publica e o que a Task 3 consome.
- `CommandResultFrame` (Task 6) é o mesmo shape usado em `SessionCloneService`
  (Task 8): `{command_id, ok, session_id?, error?}`.
- `SessionCloneService.clone(sessionId: string): void` é o método usado
  identicamente nas Tasks 9, 10 e 11.

Nenhum gap encontrado — plano pronto para execução.
