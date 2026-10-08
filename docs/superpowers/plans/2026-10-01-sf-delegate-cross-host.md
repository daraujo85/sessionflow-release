# `sf delegate` cross-host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sessão-chefe no Mac delega tarefa para worker em OUTRO host (ex.: Duck 🦆), num worktree/branch próprio no repo daquele host, e colhe o resultado via shared-files do SessionFlow.

**Architecture:** `tools/sf` resolve host (`GET /workers`) e pasta remota (`GET /directories`), envia `host_id` + `worktree_branch` no `POST /sessions`; API repassa `worktree_branch` no comando `create`; worker cria o worktree antes de abrir o tmux (mesma mecânica do clone). O filho faz commit, push ou patch, `sf share` dos artefatos e `sf send` pro pai; `sf check` no Mac baixa os shared-files para o `.sessionflow/handoff/` local.

**Tech Stack:** Python 3 stdlib (`tools/sf`), FastAPI + Pydantic (`api/`), worker asyncio + motor + libtmux (`worker/`), pytest (`asyncio_mode=auto`, marker `integration`), unittest (`tools/test_sf.py`).

**Spec:** `docs/superpowers/specs/2026-10-01-sf-delegate-cross-host-design.md`

**Correção ao spec:** `git_info.create_worktree(repo_path, dest_path, branch_name)` já aceita branch arbitrária, então NÃO ganha parâmetro novo. O worker só passa `worktree_branch` como `branch_name`.

## Global Constraints

- `tools/sf` continua **só stdlib** (sem `requests`). Mensagens de erro/saída em PT-BR, erros via `die()` (`sf: erro: ...`, exit 1).
- Sem `--host`, ou `--host` igual ao host local (`~/.claude/.sessionflow-host-id`), o `sf delegate` mantém o comportamento atual (manifest em disco, registry no work_dir).
- Branch do worktree: `sf/<nome>`; destino: `<repo_parent>/<repo>-clones/<nome>`.
- Manifest no modo remoto vai **inline** (JSON compacto) no texto da tarefa e não é gravado em disco.
- `--worktree` e `--push` só valem com `--host` remoto. Sem ele, `die`.
- `--role` e `--context-file` no modo remoto resolvem contra o **cwd local**. Os paths de persona apontam pro host do pai (limitação conhecida; fora do escopo).
- Working tree tem mudanças não relacionadas (voice/frontend). **Nunca** `git add -A`; sempre `git add <paths do task>`.
- Trabalhar numa branch `feat/sf-delegate-cross-host` (ou worktree), nunca direto na `main`.
- Commits terminam com `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Testes de integração (API/worker) exigem stack de pé (Mongo/Rabbit/tmux locais).

## Review Focus

1. **Nome de pasta com 2 matches exatos no host.** Ex.: `sessionflow` existe em `/mnt/c/repo` e em `~/Documents/projects` no Duck. Deve dar `die` listando os dois paths, nunca escolher sozinho. Teste no Task 5.
2. **Docs antigos de `host_directories` sem `is_git`** (`None`) + `--worktree`. Não pode bloquear; o worker valida. Teste no Task 5 (`is_git=None` passa) e no Task 6 (delegate segue).
3. **Host cai entre o `202` e o `running`.** O timeout deve citar o host, o `--dir` e a dica de repo git, não a mensagem genérica. Teste no Task 6.
4. **Reusar `--name` cujo `sf/<nome>` já existe no repo remoto** (worktree antigo não limpo). O worker deve falhar com `falha ao criar worktree` sem deixar sessão tmux. Teste no Task 3.
5. **Shared-files de rodada anterior com o mesmo nome.** `sf check` deve baixar a versão mais recente (a API devolve desc) e ignorar arquivos de outros nomes. Teste no Task 7.

---

### Task 1: `is_git` no indexador e no `DirectoryOut`

**Files:**
- Modify: `worker/sessionflow_worker/dir_scanner.py:98-113` (`to_suggestion`)
- Modify: `api/app/routers/directories.py:18-24` (`DirectoryOut`)
- Test: `worker/sessionflow_worker/tests/test_dir_scanner.py`
- Test: `api/app/tests/test_directories.py`

**Interfaces:**
- Produces: docs de `host_directories` e itens de `GET /directories` com `is_git: bool | None` (None = doc antigo). O sf (Task 4/5) lê `item.get("is_git")`.

- [ ] **Step 1: Write the failing worker test** (append em `test_dir_scanner.py`)

```python
def test_to_suggestion_marks_git_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /x/.git/worktrees/wt")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert to_suggestion(repo, "h")["is_git"] is True
    assert to_suggestion(worktree, "h")["is_git"] is True
    assert to_suggestion(plain, "h")["is_git"] is False
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_dir_scanner.py -v -k is_git`
Expected: FAIL with `KeyError: 'is_git'`

- [ ] **Step 3: Implement** (em `to_suggestion`, dict de retorno)

```python
    return {
        "path": _collapse_home(path),
        "parent": _collapse_home(path.parent),
        "name": path.name,
        "root": _collapse_home(path.parent),
        "host_id": host_id,
        # `.git` é dir em repo normal e ARQUIVO em worktree — exists() cobre ambos.
        # Usado pelo `sf delegate --worktree` p/ recusar pasta não-repo cedo.
        "is_git": (path / ".git").exists(),
    }
```

Atualizar a docstring: `{path, parent, name, root, host_id, is_git}`.

- [ ] **Step 4: Run, verify PASS**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_dir_scanner.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing API test** (em `api/app/tests/test_directories.py`)

Em `test_query_filters_by_substring`, trocar a asserção de chaves:

```python
    # Envelope shape: path/parent/name/root/is_git.
    for item in body["items"]:
        assert set(item.keys()) == {"path", "parent", "name", "root", "is_git"}
```

Append (imports `AsyncIOMotorClient`/`datetime, UTC` se ausentes; ajustar ao formato real que o fixture `seeded` devolve, ex.: settings direto em vez de dict):

```python
@pytest.mark.integration
async def test_is_git_exposed_and_none_for_old_docs(seeded):
    settings = seeded["settings"]
    mongo = AsyncIOMotorClient(settings.effective_mongo_uri)
    try:
        await mongo[settings.mongo_db][settings.host_directories_collection].insert_one(
            {**_doc("~/dev/repo-git", "repo-git", "~/dev", "~", datetime.now(UTC)), "is_git": True}
        )
    finally:
        mongo.close()

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            git_resp = await client.get("/directories", params={"q": "repo-git"})
            old_resp = await client.get("/directories", params={"q": "billing"})

    assert git_resp.json()["items"][0]["is_git"] is True
    assert old_resp.json()["items"][0]["is_git"] is None
```

- [ ] **Step 6: Run, verify FAIL**

Run: `cd api && python -m pytest app/tests/test_directories.py -v`
Expected: FAIL (`is_git` ausente nas chaves)

- [ ] **Step 7: Implement** (`DirectoryOut`)

```python
class DirectoryOut(BaseModel):
    """Serialized directory suggestion returned by the API."""

    path: str
    parent: str | None = None
    name: str | None = None
    root: str | None = None
    # None = doc indexado antes do campo existir (worker ainda não re-escaneou).
    is_git: bool | None = None
```

- [ ] **Step 8: Run, verify PASS**

Run: `cd api && python -m pytest app/tests/test_directories.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add worker/sessionflow_worker/dir_scanner.py worker/sessionflow_worker/tests/test_dir_scanner.py \
  api/app/routers/directories.py api/app/tests/test_directories.py
git commit -m "feat(dirs): indexa is_git por pasta e expõe em GET /directories

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: API repassa `worktree_branch` no comando `create`

**Files:**
- Modify: `api/app/routers/sessions.py:~130-145` (`SessionCreate`), `:~317-325` (payload em `create_session`)
- Test: `api/app/tests/test_sessions_create.py`

**Interfaces:**
- Produces: `POST /sessions` aceita `worktree_branch: str | None`; payload do comando `create` tem `worktree_branch` (strip; vazio → `None`). Consumido pelo worker (Task 3) e enviado pelo sf (Task 6).

- [ ] **Step 1: Write the failing tests** (append em `test_sessions_create.py`)

```python
@pytest.mark.integration
async def test_create_forwards_worktree_branch(settings, drain_commands):
    app = create_app(settings=settings)
    payload = {
        "name": f"sess-{uuid.uuid4().hex[:8]}",
        "agent_type": "claude",
        "work_dir": "~/repo",
        "host_id": _TEST_HOST_ID,
        "worktree_branch": "  sf/x  ",
    }
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    assert resp.status_code == 202
    msg = await drain_commands(resp.json()["command_id"])
    assert msg is not None
    assert msg["payload"]["worktree_branch"] == "sf/x"


@pytest.mark.integration
async def test_create_blank_worktree_branch_becomes_none(settings, drain_commands):
    app = create_app(settings=settings)
    payload = {
        "name": f"sess-{uuid.uuid4().hex[:8]}",
        "agent_type": "claude",
        "work_dir": "/tmp/work",
        "host_id": _TEST_HOST_ID,
        "worktree_branch": "   ",
    }
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    msg = await drain_commands(resp.json()["command_id"])
    assert msg is not None
    assert msg["payload"]["worktree_branch"] is None
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd api && python -m pytest app/tests/test_sessions_create.py -v -k worktree`
Expected: FAIL with `KeyError: 'worktree_branch'`

- [ ] **Step 3: Implement**

Em `SessionCreate`, logo após `host_id`:

```python
    # `sf delegate --worktree`: worker trata `work_dir` como repo e abre a sessão
    # num worktree novo nessa branch (`<repo>-clones/<name>`). None = pasta direta.
    worktree_branch: str | None = None
```

No dict `payload` de `create_session`, após `"parent"`:

```python
        "worktree_branch": (body.worktree_branch or "").strip() or None,
```

- [ ] **Step 4: Run, verify PASS**

Run: `cd api && python -m pytest app/tests/test_sessions_create.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/sessions.py api/app/tests/test_sessions_create.py
git commit -m "feat(api): POST /sessions repassa worktree_branch ao worker

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: Worker cria sessão em worktree quando `worktree_branch` vem no `create`

**Files:**
- Modify: `worker/sessionflow_worker/command_consumer.py` (`_handle_create` ~650, `_handle_clone` ~736)
- Test: `worker/sessionflow_worker/tests/test_command_consumer.py`

**Interfaces:**
- Consumes: payload `create` com `worktree_branch` (Task 2).
- Produces: novo método `async def _create_in_worktree(self, payload, repo_path: str, slug: str, branch_name: str, extra: dict | None = None) -> dict` que retorna `{name, launch_cmd, session_id, worktree_path, branch}`. O doc da sessão fica com `work_dir = worktree_path = <repo_parent>/<repo>-clones/<slug>` e `worktree_repo = repo_path`. O sf (Task 6) lê `work_dir` da sessão.

- [ ] **Step 1: Write the failing tests** (append em `test_command_consumer.py`, após os testes de clone; garantir `import subprocess` no topo)

```python
async def test_create_with_worktree_branch_opens_session_in_worktree(
    consumer, make_name, patch_launch, channel, tmp_path
) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    name = make_name()

    event = await consumer.handle(
        _cmd(
            "create",
            {
                "name": name,
                "agent_type": "claude",
                "work_dir": str(repo_path),
                "worktree_branch": f"sf/{name}",
                "parent": "pai",
            },
        )
    )

    assert event["ok"] is True, event
    dest = repo_path.parent / "repo-clones" / name
    assert event["worktree_path"] == str(dest)
    assert event["branch"] == f"sf/{name}"
    current = subprocess.run(
        ["git", "-C", str(dest), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert current == f"sf/{name}"

    doc = await consumer._sessions.find_one({"tmux_name": name})
    assert doc["work_dir"] == str(dest)
    assert doc["worktree_path"] == str(dest)
    assert doc["worktree_repo"] == str(repo_path)
    assert doc["parent"] == "pai"
    assert "base_session_id" not in doc

    consumer._runtime.kill_session(name)


async def test_create_with_worktree_branch_rolls_back_when_session_fails(
    consumer, make_name, patch_launch, channel, tmp_path
) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    name = make_name()
    runtime = consumer._runtime
    runtime.new_session(name, tmp_path)  # força "já existe" na criação interna
    try:
        event = await consumer.handle(
            _cmd(
                "create",
                {
                    "name": name,
                    "agent_type": "claude",
                    "work_dir": str(repo_path),
                    "worktree_branch": f"sf/{name}",
                },
            )
        )
    finally:
        runtime.kill_session(name)

    assert event["ok"] is False  # msg exata = a do has_session em _handle_create
    assert not (repo_path.parent / "repo-clones" / name).exists()


async def test_create_with_existing_worktree_branch_fails_cleanly(
    consumer, make_name, patch_launch, channel, tmp_path
) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    name = make_name()
    subprocess.run(
        ["git", "-C", str(repo_path), "branch", f"sf/{name}"],
        check=True, capture_output=True,
    )

    event = await consumer.handle(
        _cmd(
            "create",
            {
                "name": name,
                "agent_type": "claude",
                "work_dir": str(repo_path),
                "worktree_branch": f"sf/{name}",
            },
        )
    )

    assert event["ok"] is False
    assert "falha ao criar worktree" in event["error"]
    assert not consumer._runtime.has_session(name)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k worktree_branch`
Expected: FAIL. O primeiro teste falha com `KeyError: 'worktree_path'` (a sessão abre no repo). Os outros falham porque nenhum worktree é criado e as mensagens de erro não batem.

- [ ] **Step 3: Implement**

Em `_handle_create`, logo após a validação de `work_dir` (antes de `agent_type = ...` e ANTES do `has_session`, para o rollback cobrir nome duplicado):

```python
        # `sf delegate --worktree`: `work_dir` é o REPO; a sessão abre num
        # worktree novo nessa branch (mesma mecânica do clone, mesmo host).
        worktree_branch = (payload.get("worktree_branch") or "").strip()
        if worktree_branch:
            return await self._create_in_worktree(
                payload, work_dir, name, worktree_branch
            )
```

Novo método (logo antes de `_handle_clone`):

```python
    async def _create_in_worktree(
        self,
        payload: dict[str, Any],
        repo_path: str,
        slug: str,
        branch_name: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cria worktree `<repo>-clones/<slug>` na branch nova e abre a sessão nele.

        Falha ao abrir a sessão → remove o worktree (não deixa lixo em disco).
        """
        repo_dir = Path(repo_path).expanduser()
        dest_path = repo_dir.parent / f"{repo_dir.name}-clones" / slug

        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(
            None, git_info.create_worktree, repo_path, str(dest_path), branch_name
        )
        if not ok:
            raise CommandError(f"falha ao criar worktree: {msg}")

        try:
            result = await self._handle_create(
                {**payload, "work_dir": str(dest_path), "worktree_branch": None}
            )
        except Exception:
            rollback_ok, rollback_msg = await loop.run_in_executor(
                None, git_info.remove_worktree, repo_path, str(dest_path)
            )
            if not rollback_ok:
                logger.warning(
                    "falha ao reverter worktree %s: %s", dest_path, rollback_msg
                )
            raise

        await self._sessions.update_one(
            {"tmux_name": result["name"]},
            {
                "$set": {
                    "worktree_path": str(dest_path),
                    "worktree_repo": repo_path,
                    **(extra or {}),
                }
            },
        )
        return {**result, "worktree_path": str(dest_path), "branch": branch_name}
```

`_handle_clone`: substituir tudo após a validação do `slug` por:

```python
        return await self._create_in_worktree(
            payload,
            repo_path,
            slug,
            f"clone/{slug}",
            extra={"base_session_id": payload.get("source_session_id")},
        )
```

(O teste `test_clone_removes_worktree_when_session_creation_fails` faz monkeypatch de `consumer._handle_create`. O helper chama `self._handle_create`, então o teste continua válido.)

- [ ] **Step 4: Run, verify PASS**

Run: `cd worker && python -m pytest sessionflow_worker/tests/test_command_consumer.py -v -k "worktree or clone or create"`
Expected: all PASS (inclui os clones existentes)

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/command_consumer.py worker/sessionflow_worker/tests/test_command_consumer.py
git commit -m "feat(worker): create com worktree_branch abre sessão em worktree próprio

Extrai _create_in_worktree (reusado pelo clone) com rollback.

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: `sf` resolve host + comando `sf dirs`

**Files:**
- Modify: `tools/sf` (imports, constantes, novas funções após `resolve_session`, novo `cmd_dirs`, `build_parser`)
- Test: `tools/test_sf.py`

**Interfaces:**
- Produces (usados nos Tasks 5-7):
  - `HOST_ID_PATH: Path`
  - `local_host_id(path=None) -> str | None`
  - `resolve_host(workers: list[dict], alias: str) -> dict`, que levanta `ValueError` (mensagem PT-BR) se o host for desconhecido, ambíguo ou estiver offline
  - `fetch_workers(base: str, token: str) -> list[dict]`
  - `fetch_dirs(base: str, token: str, host_id: str, q: str, limit: int) -> list[dict]`
  - `cmd_dirs(args)`
- Test harness `SfApiTestCase` (reusado nos Tasks 6-7): patcha `resolve_env/login/api_base/detect_parent/local_host_id/list_sessions/_request/time.sleep`, chdir num tmp e `run_sf(*argv) -> str` (stdout).

- [ ] **Step 1: Write the failing tests** (em `tools/test_sf.py`)

Imports no topo (junto dos existentes):

```python
import contextlib
import io
import os
from unittest import mock
```

Append:

```python
WORKERS = [
    {"host_id": "duck-id", "display_name": "Duck Server", "hostname": "DESKTOP-ASCBQRT", "emoji": "🦆", "online": True},
    {"host_id": "mac-id", "display_name": "Macbook Air", "hostname": "mac", "emoji": "🍎", "online": True},
    {"host_id": "old-id", "display_name": "LaptopAlvarenga", "hostname": "alv", "emoji": None, "online": False},
]


class ResolveHostTests(unittest.TestCase):
    def test_exact_host_id_wins(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck-id")["host_id"], "duck-id")

    def test_exact_name_hostname_or_emoji_case_insensitive(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck server")["host_id"], "duck-id")
        self.assertEqual(sf.resolve_host(WORKERS, "desktop-ascbqrt")["host_id"], "duck-id")
        self.assertEqual(sf.resolve_host(WORKERS, "🦆")["host_id"], "duck-id")

    def test_unique_substring(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck")["host_id"], "duck-id")

    def test_ambiguous_substring_lists_candidates(self):
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            sf.resolve_host(WORKERS, "a")

    def test_unknown_lists_known_hosts(self):
        with self.assertRaisesRegex(ValueError, "Duck Server"):
            sf.resolve_host(WORKERS, "nope")

    def test_offline_host_rejected(self):
        with self.assertRaisesRegex(ValueError, "offline"):
            sf.resolve_host(WORKERS, "alvarenga")

    def test_local_host_id_reads_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "hid")
            path.write_text("mac-id\n", encoding="utf-8")
            self.assertEqual(sf.local_host_id(path), "mac-id")
            self.assertIsNone(sf.local_host_id(Path(root, "missing")))


class SfApiTestCase(unittest.TestCase):
    """Base: API fake em memória; nada sai pra rede."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)
        self.calls = []
        self.workers = list(WORKERS)
        self.dirs = [{"path": "~/Documents/projects/sessionflow", "name": "sessionflow", "is_git": True}]
        self.shared = []
        self.session = {
            "id": "s1", "tmux_name": "w1", "status": "running",
            "work_dir": "~/Documents/projects/sessionflow-clones/w1",
        }

        def fake_request(method, url, token=None, body=None, timeout=30):
            self.calls.append((method, url, body))
            if url.endswith("/workers"):
                return 200, self.workers
            if "/directories?" in url:
                return 200, {"items": self.dirs}
            if url.endswith("/shared-files"):
                return 200, {"items": self.shared}
            if method == "POST" and url.endswith("/sessions"):
                return 202, {"command_id": "c1"}
            if url.endswith("/input"):
                return 202, {}
            raise AssertionError(f"rota inesperada: {method} {url}")

        patches = {
            "resolve_env": lambda args: {},
            "login": lambda env: "tok",
            "api_base": lambda env: "http://api",
            "detect_parent": lambda: "pai",
            "local_host_id": lambda path=None: "mac-id",
            "list_sessions": lambda env, token: [self.session],
            "_request": fake_request,
        }
        for attr, value in patches.items():
            patcher = mock.patch.object(sf, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sf.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_sf(self, *argv):
        args = sf.build_parser().parse_args(list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            args.func(args)
        return out.getvalue()

    def run_sf_error(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_sf(*argv)
        return err.getvalue()


class DirsCommandTests(SfApiTestCase):
    def test_dirs_lists_paths_with_git_marker(self):
        out = self.run_sf("dirs", "--host", "duck", "sessionflow")
        self.assertIn("~/Documents/projects/sessionflow  [git]", out)
        url = next(u for m, u, b in self.calls if "/directories?" in u)
        self.assertIn("host_id=duck-id", url)
        self.assertIn("q=sessionflow", url)
        self.assertIn("limit=20", url)

    def test_dirs_unknown_host_dies(self):
        self.assertIn("nope", self.run_sf_error("dirs", "--host", "nope"))
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: FAIL/ERROR (`AttributeError: module 'sf' has no attribute 'resolve_host'`, `invalid choice: 'dirs'`)

- [ ] **Step 3: Implement**

Imports: adicionar `import urllib.parse` junto de `urllib.request`.

Constantes (após `ROLE_PERSONAS_DIR`):

```python
# host_id deste host (gravado pelo worker local) — distingue delegação local de remota.
HOST_ID_PATH = Path(os.environ.get(
    "SESSIONFLOW_HOST_ID_PATH",
    str(Path.home() / ".claude" / ".sessionflow-host-id"),
))
```

Após `resolve_session`:

```python
def local_host_id(path=None):
    """host_id do worker DESTE host, ou None (arquivo ausente/vazio)."""
    try:
        return Path(path or HOST_ID_PATH).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _host_label(w):
    state = "online" if w.get("online") else "offline"
    shown = w.get("display_name") or w.get("hostname") or "?"
    return f"{w.get('emoji') or ''} {shown} ({w.get('hostname') or '-'}) {w.get('host_id')} {state}".strip()


def resolve_host(workers, alias):
    """Resolve `--host`: host_id exato > nome/hostname/emoji exato > substring única.

    Ambíguo, desconhecido ou offline → ValueError (o chamador faz `die`).
    """
    known = [w for w in workers if w.get("host_id")]
    match = next((w for w in known if w["host_id"] == alias), None)
    if match is None:
        needle = alias.casefold()
        fields = ("display_name", "hostname", "emoji")
        exact = [w for w in known if any((w.get(f) or "").casefold() == needle for f in fields)]
        candidates = exact or [
            w for w in known if any(needle in (w.get(f) or "").casefold() for f in fields)
        ]
        if len(candidates) > 1:
            raise ValueError(
                f"host '{alias}' ambíguo: " + "; ".join(_host_label(w) for w in candidates)
            )
        if not candidates:
            listing = "; ".join(_host_label(w) for w in known) or "(nenhum host)"
            raise ValueError(f"host '{alias}' não encontrado. Hosts: {listing}")
        match = candidates[0]
    if not match.get("online"):
        raise ValueError(f"host {_host_label(match)} está offline; suba o worker dele antes")
    return match


def fetch_workers(base, token):
    status, body = _request("GET", f"{base}/workers", token=token)
    if status != 200 or not isinstance(body, list):
        die(f"GET /workers falhou (HTTP {status}): {body}")
    return body


def fetch_dirs(base, token, host_id, q, limit):
    qs = urllib.parse.urlencode({"host_id": host_id, "q": q, "limit": min(limit, 50)})
    status, body = _request("GET", f"{base}/directories?{qs}", token=token)
    if status != 200:
        die(f"GET /directories falhou (HTTP {status}): {body}")
    return body.get("items", [])
```

Novo comando (antes de `cmd_list`):

```python
# --------------------------------------------------------------------------- #
# comando: dirs
# --------------------------------------------------------------------------- #
def cmd_dirs(args):
    """Lista pastas indexadas de um host (pra escolher o `--dir` remoto)."""
    env = resolve_env(args)
    token = login(env)
    base = api_base(env)
    try:
        host = resolve_host(fetch_workers(base, token), args.host)
    except ValueError as exc:
        die(str(exc))
    items = fetch_dirs(base, token, host["host_id"], args.term or "", args.limit)
    if not items:
        print("(nenhuma pasta indexada casando)")
        return
    for item in items:
        print(item["path"] + ("  [git]" if item.get("is_git") else ""))
```

`build_parser`, após o subparser `metrics`:

```python
    ds = sub.add_parser(
        "dirs",
        help="lista pastas indexadas de um host (use p/ escolher --dir remoto).",
    )
    ds.add_argument("--host", required=True,
                    help="host: host_id, nome, hostname, emoji ou trecho único.")
    ds.add_argument("term", nargs="?", default="", help="trecho do caminho/nome.")
    ds.add_argument("--limit", type=int, default=20, help="máx. de pastas (≤50).")
    ds.add_argument("--remote", default=None, help=_REMOTE_HELP)
    ds.set_defaults(func=cmd_dirs)
```

- [ ] **Step 4: Run, verify PASS**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): resolve --host via /workers e comando sf dirs

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: `sf` resolve `--dir` remoto e monta a tarefa remota

**Files:**
- Modify: `tools/sf` (`build_handoff_task` ~457, novas `resolve_remote_dir` e `build_remote_task`)
- Test: `tools/test_sf.py`

**Interfaces:**
- Consumes: itens de `/directories` com `name`, `path` e `is_git` (Task 1).
- Produces:
  - `resolve_remote_dir(dir_arg: str, dirs: list[dict], local_home: str) -> {"path": str, "is_git": bool | None}`. Levanta `ValueError`.
  - `build_handoff_task(task, work_dir, name, manifest=None, finish_steps=None)`. O novo kwarg substitui a frase final "PARE".
  - `build_remote_task(task, work_dir, name, manifest: dict, parent: str | None, push: bool) -> str`.

- [ ] **Step 1: Write the failing tests** (append)

```python
class RemoteDirTests(unittest.TestCase):
    HOME = "/Users/diegoaraujo"

    def test_absolute_and_tilde_paths_pass_through(self):
        self.assertEqual(sf.resolve_remote_dir("/mnt/c/repo/x", [], self.HOME), {"path": "/mnt/c/repo/x", "is_git": None})
        self.assertEqual(sf.resolve_remote_dir("~/x", [], self.HOME), {"path": "~/x", "is_git": None})

    def test_locally_expanded_home_rejected(self):
        with self.assertRaisesRegex(ValueError, "aspas"):
            sf.resolve_remote_dir("/Users/diegoaraujo/Documents/projects/x", [], self.HOME)

    def test_name_prefers_exact_match(self):
        dirs = [
            {"path": "~/p/sessionflow-clones/a", "name": "a", "is_git": True},
            {"path": "~/p/sessionflow", "name": "sessionflow", "is_git": True},
        ]
        self.assertEqual(sf.resolve_remote_dir("sessionflow", dirs, self.HOME), {"path": "~/p/sessionflow", "is_git": True})

    def test_two_exact_matches_are_ambiguous(self):
        dirs = [
            {"path": "/mnt/c/repo/sessionflow", "name": "sessionflow", "is_git": True},
            {"path": "~/Documents/projects/sessionflow", "name": "sessionflow", "is_git": True},
        ]
        with self.assertRaisesRegex(ValueError, "/mnt/c/repo/sessionflow.*~/Documents/projects/sessionflow"):
            sf.resolve_remote_dir("sessionflow", dirs, self.HOME)

    def test_old_doc_without_is_git_is_none(self):
        dirs = [{"path": "~/p/x", "name": "x"}]
        self.assertEqual(sf.resolve_remote_dir("x", dirs, self.HOME), {"path": "~/p/x", "is_git": None})

    def test_unknown_name_suggests_sf_dirs(self):
        with self.assertRaisesRegex(ValueError, "sf dirs"):
            sf.resolve_remote_dir("nada", [], self.HOME)


class RemoteTaskTests(unittest.TestCase):
    MANIFEST = {"schema_version": 1, "objective": "Do work", "context": {}}

    def test_patch_mode_instructions(self):
        text = sf.build_remote_task("Do work", "~/r-clones/w1", "w1", self.MANIFEST, "pai", push=False)
        self.assertTrue(text.startswith("PASSO 0"))
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn('"objective":"Do work"', text)
        self.assertIn("git format-patch <BASE>..HEAD --stdout > ~/r-clones/w1/.sessionflow/handoff/w1.patch", text)
        self.assertIn("sf share ~/r-clones/w1/.sessionflow/handoff/w1.handoff.json", text)
        self.assertIn("sf share ~/r-clones/w1/.sessionflow/handoff/w1.patch", text)
        self.assertIn('sf send pai "✅ HANDOFF w1 status=<status do JSON>"', text)
        self.assertNotIn("Depois de escrever ambos, PARE", text)

    def test_push_mode_without_parent(self):
        text = sf.build_remote_task("Do work", "~/r", "w1", self.MANIFEST, None, push=True)
        self.assertIn("git push -u origin HEAD", text)
        self.assertNotIn("format-patch", text)
        self.assertNotIn('"✅ HANDOFF', text)

    def test_local_task_keeps_original_finish(self):
        self.assertIn("Depois de escrever ambos, PARE", sf.build_handoff_task("t", "/r", "w"))
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: ERROR `AttributeError: ... 'resolve_remote_dir'` / `'build_remote_task'`

- [ ] **Step 3: Implement**

`build_handoff_task`: assinatura e frase final:

```python
def build_handoff_task(task, work_dir, name, manifest=None, finish_steps=None):
```

Trocar o literal `"Depois de escrever ambos, PARE — não faça mais nada.\n\n"` por:

```python
        f"{finish_steps or 'Depois de escrever ambos, PARE — não faça mais nada.'}\n\n"
```

Novas funções logo após `build_handoff_task`:

```python
def resolve_remote_dir(dir_arg, dirs, local_home):
    """`--dir` p/ host remoto: caminho literal (/ ou ~) ou NOME indexado no host.

    `dirs` = itens de GET /directories (só usado no modo nome). ValueError em
    caminho expandido pelo shell local, nome ambíguo ou ausente.
    """
    home = local_home.rstrip("/")
    if dir_arg == home or dir_arg.startswith(home + "/"):
        raise ValueError(
            f"'{dir_arg}' é caminho DESTE host (o shell expandiu `~` localmente); "
            "use aspas: --dir '~/...'"
        )
    if dir_arg.startswith("/") or dir_arg == "~" or dir_arg.startswith("~/"):
        return {"path": dir_arg, "is_git": None}
    exact = [d for d in dirs if d.get("name") == dir_arg]
    candidates = exact or dirs
    if len(candidates) == 1:
        return {"path": candidates[0]["path"], "is_git": candidates[0].get("is_git")}
    if not candidates:
        raise ValueError(
            f"nenhuma pasta '{dir_arg}' indexada no host; veja `sf dirs --host <alias> {dir_arg}`"
        )
    raise ValueError(
        f"'{dir_arg}' ambíguo no host: " + ", ".join(d["path"] for d in candidates)
        + " — passe o caminho completo entre aspas"
    )


def build_remote_task(task, work_dir, name, manifest, parent, push):
    """Tarefa p/ filho em OUTRO host: manifest inline + entrega git + share/send."""
    md_path = handoff_md_path(work_dir, name)
    json_path = handoff_json_path(work_dir, name)
    patch_path = os.path.join(handoff_dir(work_dir), f"{name}.patch")
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    if push:
        deliver = ("2. `git push -u origin HEAD`; ponha a branch (`git branch --show-current`) "
                   "e o SHA final no `diff` do JSON.\n")
    else:
        deliver = (f"2. `git format-patch <BASE>..HEAD --stdout > {patch_path}` "
                   "(BASE = SHA do PASSO 0); cite o arquivo no `diff` do JSON.\n")
    body = (
        f"{task}\n\n"
        "Manifest de contexto (JSON inline; leia referências seletivamente; nunca puxe transcript):\n"
        f"{manifest_json}\n\n"
        "----- ENTREGA GIT (ao concluir, ANTES do handoff) -----\n"
        "1. Commit de todas as mudanças, sem `.sessionflow/`: "
        "`git add -A -- . ':!.sessionflow' && git commit -m \"<mensagem descritiva>\"`.\n"
        + deliver
    )
    files = [md_path, json_path] + ([] if push else [patch_path])
    finish = (
        "Depois de escrever ambos:\n"
        + "".join(f"- `sf share {path}`\n" for path in files)
        + "(pule arquivo que não existir)\n"
        + (f"- `sf send {parent} \"✅ HANDOFF {name} status=<status do JSON>\"`\n" if parent else "")
        + "Então PARE — não faça mais nada."
    )
    return (
        "PASSO 0 (ANTES DE QUALQUER COISA): rode `git rev-parse HEAD` neste diretório e "
        "anote o SHA como BASE. Se não for repositório git, ignore todos os passos de git.\n\n"
        + build_handoff_task(body, work_dir, name, finish_steps=finish)
    )
```

- [ ] **Step 4: Run, verify PASS**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: all PASS (incluindo `test_prompt_references_manifest_not_contents` existente)

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): resolve --dir remoto e monta tarefa remota (git + share + send)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: `sf delegate --host/--worktree/--push`

**Files:**
- Modify: `tools/sf` (`cmd_delegate` ~503, novas `wait_running` e `delegate_remote`, parser `delegate`)
- Test: `tools/test_sf.py`

**Interfaces:**
- Consumes:
  - `resolve_host`, `fetch_workers`, `fetch_dirs`, `local_host_id` (Task 4)
  - `resolve_remote_dir`, `build_remote_task` (Task 5)
  - API `worktree_branch` (Task 2)
  - `work_dir` real da sessão (Task 3)
- Produces:
  - `CREATE_TIMEOUT_SECONDS = 40`
  - `wait_running(env, token, name) -> dict | None`
  - `delegate_remote(args, env, token, base, host, name, effort)`
  - Registry remoto em `<cwd>/.sessionflow/handoff/<nome>.registry.json` com as chaves: `id, name, host_id, remote: True, dir (cwd local), work_dir (real remoto), provider, model, effort, push, worktree, created_at`. O Task 7 lê `remote`, `id`, `name` e `dir`.

- [ ] **Step 1: Write the failing tests** (append)

```python
class RemoteDelegateTests(SfApiTestCase):
    ARGS = ("delegate", "--provider", "claude", "--host", "duck", "--dir", "sessionflow",
            "--name", "w1", "--task", "Do work")

    def _post_body(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))

    def test_remote_worktree_payload_task_and_registry(self):
        self.run_sf(*self.ARGS, "--worktree")
        post = self._post_body()
        self.assertEqual(post["host_id"], "duck-id")
        self.assertEqual(post["work_dir"], "~/Documents/projects/sessionflow")
        self.assertEqual(post["worktree_branch"], "sf/w1")
        self.assertEqual(post["parent"], "pai")
        text = next(b for m, u, b in self.calls if u.endswith("/input"))["text"]
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn('"objective":"Do work"', text)
        self.assertIn("sessionflow-clones/w1/.sessionflow/handoff/w1.handoff.json", text)
        self.assertIn("format-patch", text)
        reg = json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text(encoding="utf-8"))
        self.assertTrue(reg["remote"])
        self.assertEqual(reg["host_id"], "duck-id")
        self.assertEqual(reg["work_dir"], self.session["work_dir"])
        self.assertEqual(reg["dir"], os.getcwd())
        self.assertFalse(Path(".sessionflow/handoff/w1.manifest.json").exists())

    def test_push_flag_switches_to_push_instructions(self):
        self.run_sf(*self.ARGS, "--worktree", "--push")
        text = next(b for m, u, b in self.calls if u.endswith("/input"))["text"]
        self.assertIn("git push -u origin HEAD", text)
        self.assertTrue(json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text())["push"])

    def test_worktree_on_non_git_dir_dies_before_create(self):
        self.dirs = [{"path": "~/plain", "name": "sessionflow", "is_git": False}]
        self.assertIn("não é repositório git", self.run_sf_error(*self.ARGS, "--worktree"))
        self.assertFalse(any(m == "POST" for m, u, b in self.calls))

    def test_worktree_with_unknown_is_git_proceeds(self):
        self.dirs = [{"path": "~/old", "name": "sessionflow"}]
        self.run_sf(*self.ARGS, "--worktree")
        self.assertEqual(self._post_body()["work_dir"], "~/old")

    def test_remote_requires_dir(self):
        args = [a for a in self.ARGS if a not in ("--dir", "sessionflow")]
        self.assertIn("--dir", self.run_sf_error(*args))

    def test_timeout_message_names_host_and_dir(self):
        self.session["status"] = "starting"
        with mock.patch.object(sf, "CREATE_TIMEOUT_SECONDS", 0):
            err = self.run_sf_error(*self.ARGS, "--worktree")
        self.assertIn("Duck Server", err)
        self.assertIn("~/Documents/projects/sessionflow", err)
        self.assertIn("repo git", err)

    def test_local_host_alias_keeps_local_flow_with_host_id(self):
        self.session["work_dir"] = os.getcwd()
        self.run_sf("delegate", "--provider", "claude", "--host", "mac", "--name", "w1", "--task", "Do work")
        post = self._post_body()
        self.assertEqual(post["host_id"], "mac-id")
        self.assertEqual(post["work_dir"], os.path.abspath(os.getcwd()))
        self.assertNotIn("worktree_branch", post)
        self.assertTrue(Path(".sessionflow/handoff/w1.manifest.json").exists())

    def test_worktree_requires_remote_host(self):
        err = self.run_sf_error("delegate", "--provider", "claude", "--worktree", "--name", "w1", "--task", "t")
        self.assertIn("--host", err)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd tools && python3 -m unittest test_sf.RemoteDelegateTests -v`
Expected: FAIL (`unrecognized arguments: --host`)

- [ ] **Step 3: Implement**

Constante (junto de `PROVIDERS`):

```python
CREATE_TIMEOUT_SECONDS = 40
```

Antes de `cmd_delegate`, extrair o poll existente:

```python
def wait_running(env, token, name):
    """Poll até a sessão `name` aparecer `running` (criação é assíncrona) ou timeout."""
    deadline = time.time() + CREATE_TIMEOUT_SECONDS
    while time.time() < deadline:
        for s in list_sessions(env, token):
            if (s.get("tmux_name") == name or s.get("display_name") == name) \
                    and (s.get("status") or "").lower() == "running":
                return s
        time.sleep(2)
    return None
```

Início de `cmd_delegate`. Substituir o bloco desde `work_dir = os.path.abspath(...)` até `args.effort = effort` por:

```python
    name = sanitize_name(args.name) if args.name else slugify_name(args.task)
    # gemini não tem dimensão de esforço → força None.
    effort = None if provider == "gemini" else args.effort
    args.effort = effort

    host_id = None
    if args.host:
        env = resolve_env(args)
        token = login(env)
        base = api_base(env)
        try:
            host = resolve_host(fetch_workers(base, token), args.host)
        except ValueError as exc:
            die(str(exc))
        host_id = host["host_id"]
        if host_id != local_host_id():
            return delegate_remote(args, env, token, base, host, name, effort)
    if args.worktree or args.push:
        die("--worktree/--push exigem --host apontando pra OUTRO host (ex.: --host duck)")

    work_dir = os.path.abspath(args.dir or os.getcwd())
    try:
        role = resolve_role(args.role, work_dir)
        context = load_context_file(args.context_file, work_dir)
    except ValueError as exc:
        die(str(exc))
```

(o restante do fluxo local fica como está: `manifest = build_manifest(...)`, `env/token/base`, `parent`...)

No `payload` local, após o bloco do `parent`:

```python
    if host_id:
        payload["host_id"] = host_id
```

Trocar o loop de POLL local (de `session = None` / `deadline = ...` até o `die("a sessão não subiu em 40s ...")`) por:

```python
    session = wait_running(env, token, name)
    if session is None:
        die(f"a sessão não subiu em {CREATE_TIMEOUT_SECONDS}s (não apareceu como running). "
            f"Verifique 'sf check {name}' em instantes ou o worker do SessionFlow.")
```

Nova função após `cmd_delegate`:

```python
def delegate_remote(args, env, token, base, host, name, effort):
    """Delegação p/ OUTRO host: pasta/worktree lá, manifest inline, registry aqui."""
    host_id = host["host_id"]
    label = host.get("display_name") or host.get("hostname") or host_id
    local_dir = os.getcwd()
    if not args.dir:
        die(f"--dir é obrigatório com host remoto; veja `sf dirs --host {args.host}`")
    try:
        role = resolve_role(args.role, local_dir)
        context = load_context_file(args.context_file, local_dir)
        literal = args.dir.startswith(("/", "~"))
        lookup = [] if literal else fetch_dirs(base, token, host_id, args.dir, 50)
        target = resolve_remote_dir(args.dir, lookup, str(Path.home()))
    except ValueError as exc:
        die(str(exc))
    if args.worktree and target["is_git"] is False:
        die(f"{target['path']} não é repositório git no host {label}; --worktree exige repo")

    manifest = build_manifest(args, target["path"], name, role, context)
    parent = detect_parent()
    payload = {
        "name": name,
        "display_name": name,
        "agent_type": args.provider,
        "work_dir": target["path"],
        "model": args.model,
        "effort": effort,
        "host_id": host_id,
    }
    if parent:
        payload["parent"] = parent
    if args.worktree:
        payload["worktree_branch"] = f"sf/{name}"
    status, body = _request("POST", f"{base}/sessions", token=token, body=payload)
    if status == 409:
        die(f"já existe uma sessão ativa chamada '{name}'. Use --name outro.")
    if status != 202:
        die(f"criação falhou (HTTP {status}): {body}")

    session = wait_running(env, token, name)
    if session is None:
        die(f"a sessão não subiu em {CREATE_TIMEOUT_SECONDS}s no host {label}. Confira: "
            f"host online? --dir {target['path']} existe lá?"
            + (f" é repo git e a branch sf/{name} ainda não existe?" if args.worktree else "")
            + f" Depois: sf check {name}")

    session_id = session["id"]
    # Com --worktree o worker abre em <repo>-clones/<nome>: usa o caminho REAL.
    work_dir = session.get("work_dir") or target["path"]
    task_text = build_remote_task(args.task, work_dir, name, manifest, parent, args.push)
    status, body = _request(
        "POST", f"{base}/sessions/{session_id}/input",
        token=token, body={"text": task_text, "enter": True},
    )
    if status != 202:
        die(f"envio da tarefa falhou (HTTP {status}): {body}")

    reg = {
        "id": session_id,
        "name": name,
        "host_id": host_id,
        "remote": True,
        "dir": local_dir,
        "work_dir": work_dir,
        "provider": args.provider,
        "model": args.model,
        "effort": effort,
        "push": bool(args.push),
        "worktree": bool(args.worktree),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        os.makedirs(handoff_dir(local_dir), exist_ok=True)
        with open(registry_path(local_dir, name), "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"aviso: não consegui gravar o registro local: {exc}", file=sys.stderr)

    print("delegado ✅ (remoto)")
    print(f"  handle:  {name}  (id={session_id})")
    print(f"  host:    {label} ({host_id})")
    print(f"  pasta:   {work_dir}" + (f"  [branch sf/{name}]" if args.worktree else ""))
    print(f"  entrega: {'git push' if args.push else 'patch via shared-files'}")
    print(f"  acompanhe: sf check {name}")
```

Parser `delegate` (após `--name`):

```python
    d.add_argument("--host", default=None,
                   help="host de destino (host_id, nome, hostname, emoji ou trecho). "
                        "Remoto: --dir é caminho/nome NAQUELE host (veja 'sf dirs').")
    d.add_argument("--worktree", action="store_true",
                   help="(remoto) roda num worktree novo na branch sf/<nome>.")
    d.add_argument("--push", action="store_true",
                   help="(remoto) filho faz git push da branch; sem isso, devolve .patch.")
```

- [ ] **Step 4: Run, verify PASS**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): delegate --host/--worktree/--push p/ worker em outro host

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: `sf check` baixa o handoff remoto de shared-files

**Files:**
- Modify: `tools/sf` (novas `_download`, `pick_remote_files`, `pull_remote_handoff`; `cmd_check` ~614)
- Test: `tools/test_sf.py`

**Interfaces:**
- Consumes: registry remoto (Task 6). A API de shared-files devolve os itens mais novos primeiro, como `{id, filename, ...}`.
- Produces:
  - `pick_remote_files(items, name) -> list[dict]`
  - `_download(url, token, dest)`
  - `pull_remote_handoff(env, token, reg) -> list[str]` (paths locais salvos)

- [ ] **Step 1: Write the failing tests** (append)

```python
class RemoteCheckTests(SfApiTestCase):
    HANDOFF = {"schema_version": 1, "status": "done", "summary": "ok",
               "diff": ["w1.patch"], "tests": [], "blockers": [], "questions": []}

    def setUp(self):
        super().setUp()
        hdir = Path(".sessionflow/handoff")
        hdir.mkdir(parents=True)
        (hdir / "w1.registry.json").write_text(json.dumps(
            {"id": "s1", "name": "w1", "remote": True, "dir": os.getcwd(), "host_id": "duck-id"}
        ), encoding="utf-8")
        self.contents = {"f2": json.dumps(self.HANDOFF), "f1": "velho", "f3": "# md", "f4": "patch", "f5": "x"}
        self.shared = [  # API: mais recente primeiro
            {"id": "f2", "filename": "w1.handoff.json"},
            {"id": "f1", "filename": "w1.handoff.json"},
            {"id": "f3", "filename": "w1.md"},
            {"id": "f4", "filename": "w1.patch"},
            {"id": "f5", "filename": "other.md"},
        ]
        self.downloaded = []

        def fake_download(url, token, dest):
            fid = url.rstrip("/").split("/")[-2]
            self.downloaded.append(fid)
            Path(dest).write_text(self.contents[fid], encoding="utf-8")

        patcher = mock.patch.object(sf, "_download", fake_download)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_summary_uses_newest_files_only(self):
        out = self.run_sf("check", "w1", "--summary")
        self.assertIn("status=done", out)
        self.assertEqual(sorted(self.downloaded), ["f2", "f3", "f4"])
        self.assertFalse(Path(".sessionflow/handoff/other.md").exists())

    def test_full_check_prints_md_and_patch_hint(self):
        out = self.run_sf("check", "w1")
        self.assertIn("# md", out)
        self.assertIn("git am", out)

    def test_no_shared_files_yet_means_running(self):
        self.shared = []
        self.assertIn("ainda rodando", self.run_sf("check", "w1", "--summary"))

    def test_pick_remote_files_ignores_paths_and_other_names(self):
        items = [{"id": "a", "filename": "../w1.md"}, {"id": "b", "filename": "w1.md"}]
        self.assertEqual([i["id"] for i in sf.pick_remote_files(items, "w1")], ["b"])
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd tools && python3 -m unittest test_sf.RemoteCheckTests -v`
Expected: FAIL (`AttributeError: ... '_download'`)

- [ ] **Step 3: Implement**

Após `_upload_file`:

```python
def _download(url, token, dest):
    """GET binário autenticado → grava em `dest` (stdlib pura)."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": _BROWSER_UA}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        die(f"download falhou (HTTP {exc.code}): {url}")
    except urllib.error.URLError as exc:
        die(f"falha de conexão com a API ({url}): {exc.reason}")
    Path(dest).write_bytes(data)
```

Antes de `cmd_check`:

```python
def pick_remote_files(items, name):
    """Artefatos de handoff de `name`, 1 por arquivo, o mais recente (API: desc).

    Só nomes exatos do contrato → o filename nunca vira path traversal local.
    """
    wanted = {f"{name}.md", f"{name}.handoff.json", f"{name}.patch"}
    seen, picked = set(), []
    for item in items:
        filename = item.get("filename")
        if filename in wanted and filename not in seen:
            seen.add(filename)
            picked.append(item)
    return picked


def pull_remote_handoff(env, token, reg):
    """Baixa os artefatos do filho remoto p/ o handoff local; devolve paths salvos."""
    base = api_base(env)
    status, body = _request("GET", f"{base}/sessions/{reg['id']}/shared-files", token=token)
    if status != 200:
        print(f"aviso: não consegui listar shared-files (HTTP {status})", file=sys.stderr)
        return []
    dest_dir = handoff_dir(reg["dir"])
    os.makedirs(dest_dir, exist_ok=True)
    saved = []
    for item in pick_remote_files(body.get("items", []), reg["name"]):
        dest = os.path.join(dest_dir, item["filename"])
        _download(f"{base}/shared-files/{item['id']}/download", token, dest)
        saved.append(dest)
    return saved
```

Em `cmd_check`, logo após calcular `name`/`hdir` (antes de imprimir status):

```python
    patch_file = None
    if reg and reg.get("remote"):
        saved = pull_remote_handoff(env, token, reg)
        patch_file = next((p for p in saved if p.endswith(".patch")), None)
```

E logo antes do bloco `md_path = handoff_md_path(hdir, name)` (caminho não-summary):

```python
    if patch_file:
        print(f"patch: {patch_file}  (aplique com: git am {patch_file})")
```

(`hdir` já vem de `reg["dir"]`, que no registry remoto é o cwd local, então a validação e a impressão do fluxo atual leem os arquivos baixados.)

- [ ] **Step 4: Run, verify PASS**

Run: `cd tools && python3 -m unittest test_sf -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): check baixa handoff do filho remoto via shared-files

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: Documentação + verificação final

**Files:**
- Modify: `tools/README.md` (nova subseção após `### Uso` do `sf`)

- [ ] **Step 1: Write docs** (inserir antes de `### Controlar a instância de OUTRO amigo`)

````markdown
### Delegar para OUTRO host (`--host`, ex.: Duck Server 🦆)

Divide processamento: o filho roda na CPU/RAM do outro host, num worktree
próprio, e devolve o resultado pelo próprio SessionFlow.

```bash
./tools/sf dirs --host duck sessionflow          # pastas indexadas lá ([git] = repo)
./tools/sf delegate --host duck --dir sessionflow --worktree \
  --provider claude --task "..."                 # branch sf/<nome> em <repo>-clones/<nome>
# --push: filho faz git push da branch; sem ele, devolve <nome>.patch
./tools/sf check <nome> --summary                # baixa .md/.handoff.json/.patch p/ .sessionflow/handoff/
git am .sessionflow/handoff/<nome>.patch         # (modo patch) aplica no repo local
```

- `--host`: host_id, nome, hostname, emoji ou trecho único (`sf dirs` / `GET /workers`).
- `--dir` remoto: nome da pasta (resolvido no índice do host) ou caminho **entre aspas**
  (`--dir '~/x'`); sem aspas o zsh expande `~` pro home do Mac e o `sf` recusa.
- Ao terminar, o filho manda `✅ HANDOFF <nome> status=...` pro terminal do pai.

Pré-requisitos no host remoto: worker rodando; `sf` no PATH **dentro do WSL** com
`.env` válido; repo sob um `SESSIONFLOW_SCAN_ROOTS`; credencial git/gh se `--push`.
Repos em `/mnt/c` (9P) são lentos no WSL: prefira `~/`. Worktrees remotos não são
limpos automaticamente (`git worktree remove` lá quando terminar).
````

- [ ] **Step 2: Full verification**

Run:
```bash
cd tools && python3 -m unittest test_sf -v
cd ../worker && python -m pytest sessionflow_worker/tests/test_dir_scanner.py sessionflow_worker/tests/test_command_consumer.py -v
cd ../api && python -m pytest app/tests/test_directories.py app/tests/test_sessions_create.py -v
```
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add tools/README.md
git commit -m "docs(sf): delegate cross-host (--host/--worktree/--push, sf dirs)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 4: Smoke manual (após deploy do worker no Duck + rebuild da API)**

Run (no Mac, dentro de tmux de uma sessão SessionFlow):
```bash
./tools/sf dirs --host duck sessionflow
./tools/sf delegate --host duck --dir sessionflow --worktree --provider claude \
  --name smoke-cross --task "Crie SMOKE.md com 'ok' e finalize o handoff"
./tools/sf check smoke-cross --summary     # repetir até status=done
```
Expected: `✅ HANDOFF smoke-cross status=done` chega no terminal do pai. `check` mostra `status=done` e o `.patch` aparece em `.sessionflow/handoff/`.
````
