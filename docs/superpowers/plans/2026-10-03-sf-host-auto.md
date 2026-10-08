# `sf delegate --host auto` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sf delegate --host auto` escolhe sozinho o host online mais livre (CPU+RAM) que tem o provider instalado e a pasta do projeto, e informa ID da sessão + host escolhido.

**Architecture:** Worker publica `metrics` (carga) e `agents` (CLIs instaladas) no heartbeat → API repassa em `GET /workers` → CLI `sf` filtra/ranqueia via função pura `pick_host` e reaproveita os fluxos de delegação local/remoto existentes.

**Tech Stack:** Python 3.12, psutil, motor/Mongo (worker), FastAPI/pydantic (api), stdlib + unittest (`tools/sf`).

**Spec:** `docs/superpowers/specs/2026-10-03-sf-host-auto-design.md`

## Global Constraints

- Sem `--host` → local; `--host <alias>` → host indicado: comportamento e saída **inalterados**.
- Piso: `cpu_pct <= 85` e `mem_avail_gb >= 2`.
- Score: `mem_avail_gb * (1 - cpu_pct/100)`; empate (diferença ≤ 5% do maior) → host local.
- Host sem `metrics` ou sem `agents` (worker antigo) → descartado.
- Sem candidato → roda local (se `--worktree`/`--push` → erro, pois exigem remoto).
- **Desvio do spec:** sem EMA. `psutil.cpu_percent(interval=None)` já é a média desde a chamada anterior (= intervalo do heartbeat, 30s).
- Comentários/strings em PT-BR, mesmo estilo dos arquivos.

## Review Focus

1. Pasta com nome ambíguo no host remoto (2+ matches exatos por nome) → host descartado com motivo "pasta ambígua", não escolhe um arbitrário. (teste na Task 3)
2. `metrics` com `cpu_pct`/`mem_avail_gb` = `None` (probe falhou) → descartado "sem métricas", sem `TypeError`. (Task 3)
3. Host offline com métricas velhas no doc → descartado por estar offline. (Task 3)
4. `--host AUTO` (maiúsculas) → também ativa o modo auto. (Task 4)
5. `fetch_dirs` falha (HTTP≠200 chama `die`) num host remoto → não derruba o auto; o host sai como "erro ao listar pastas". (Task 4: `dir_ok` remoto captura `SystemExit`)

---

### Task 1: Worker publica `metrics` + `agents` no heartbeat

**Files:**
- Modify: `worker/sessionflow_worker/host_metrics.py` (adicionar `sample_load`, `installed_agents`)
- Modify: `worker/sessionflow_worker/runner.py:57` (import) e `:501-517` (`$set`)
- Test: `worker/sessionflow_worker/tests/test_host_metrics.py`

**Interfaces:**
- Produces: `sample_load() -> dict` com chaves `cpu_pct, mem_pct, mem_total_gb, mem_avail_gb` (float|None); `installed_agents() -> dict[str, bool]` com chaves `claude, codex, gemini, opencode, agy`. Doc `worker_status` ganha campos `metrics` e `agents`.

- [ ] **Step 1: Testes falhando** — append em `test_host_metrics.py`:

```python
def test_sample_load_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 12.5)
    vm = SimpleNamespace(percent=40.0, total=16 * 1024**3, available=6 * 1024**3)
    monkeypatch.setattr(host_metrics.psutil, "virtual_memory", lambda: vm)
    assert host_metrics.sample_load() == {
        "cpu_pct": 12.5, "mem_pct": 40.0, "mem_total_gb": 16.0, "mem_avail_gb": 6.0,
    }


def test_sample_load_probe_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("x")
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", boom)
    monkeypatch.setattr(host_metrics.psutil, "virtual_memory", boom)
    assert host_metrics.sample_load() == {
        "cpu_pct": None, "mem_pct": None, "mem_total_gb": None, "mem_avail_gb": None,
    }


def test_installed_agents_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    present = {"claude", "gemini"}
    monkeypatch.setattr(host_metrics.shutil, "which", lambda b: f"/bin/{b}" if b in present else None)
    assert host_metrics.installed_agents() == {
        "claude": True, "codex": False, "gemini": True, "opencode": False, "agy": False,
    }
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd worker && uv run pytest sessionflow_worker/tests/test_host_metrics.py -v`
Expected: FAIL `AttributeError: ... has no attribute 'sample_load'`

- [ ] **Step 3: Implementar** — em `host_metrics.py`, `import shutil` junto dos imports e, após `sample_host_metrics`:

```python
# Binários das CLIs de agente que o `sf delegate --host auto` exige no host
# (espelha AgentType de agent_launcher; "agy" = Antigravity).
_AGENT_BINARIES = ("claude", "codex", "gemini", "opencode", "agy")


def sample_load() -> dict[str, Any]:
    """Carga atual p/ o heartbeat (`sf delegate --host auto` ranqueia por isso).

    ``cpu_percent(interval=None)`` = média desde a chamada anterior, ou seja,
    do último intervalo de heartbeat — já suavizado, sem EMA. ``mem_avail_gb``
    usa ``available`` (inclui cache liberável), não ``free``. Best-effort:
    probe que falhar vira ``None``.
    """
    load: dict[str, Any] = {
        "cpu_pct": None, "mem_pct": None, "mem_total_gb": None, "mem_avail_gb": None,
    }
    try:
        load["cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_load: cpu_percent falhou", exc_info=True)
    try:
        vm = psutil.virtual_memory()
        load["mem_pct"] = round(vm.percent, 1)
        load["mem_total_gb"] = round(vm.total / (1024**3), 1)
        load["mem_avail_gb"] = round(vm.available / (1024**3), 1)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_load: virtual_memory falhou", exc_info=True)
    return load


def installed_agents() -> dict[str, bool]:
    """Quais CLIs de agente existem no PATH deste worker."""
    return {b: shutil.which(b) is not None for b in _AGENT_BINARIES}
```

Em `runner.py:57`: `from sessionflow_worker.host_metrics import fetch_ollama_models, installed_agents, sample_load`.
No `heartbeat_loop`, antes do `update_one` e no `$set`:

```python
        ollama_models = await asyncio.to_thread(fetch_ollama_models)
        # Carga + CLIs instaladas: base do `sf delegate --host auto`.
        metrics = sample_load()
        agents = installed_agents()
```
```python
                    "ollama_models": ollama_models,
                    "metrics": metrics,
                    "agents": agents,
```

- [ ] **Step 4: Rodar e passar**

Run: `cd worker && uv run pytest sessionflow_worker/tests/test_host_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/sessionflow_worker/host_metrics.py worker/sessionflow_worker/runner.py worker/sessionflow_worker/tests/test_host_metrics.py
git commit -m "feat(worker): heartbeat publica carga (metrics) e CLIs instaladas (agents)"
```

---

### Task 2: API repassa `metrics`/`agents` em `GET /workers`

**Files:**
- Modify: `api/app/routers/worker.py` (`WorkerOut` + `_to_worker_out`)
- Create: `api/app/tests/test_worker_out.py`

**Interfaces:**
- Consumes: campos `metrics`, `agents` do doc (Task 1).
- Produces: JSON de `/workers` com `metrics: dict|null`, `agents: dict|null`.

- [ ] **Step 1: Teste falhando** — `api/app/tests/test_worker_out.py`:

```python
"""Unit puro de _to_worker_out (sem Mongo)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.routers.worker import _to_worker_out


def test_passes_metrics_and_agents() -> None:
    doc = {
        "_id": "h1", "updated_at": datetime.now(UTC),
        "metrics": {"cpu_pct": 10.0, "mem_avail_gb": 4.0},
        "agents": {"claude": True},
    }
    out = _to_worker_out(doc)
    assert out.metrics == {"cpu_pct": 10.0, "mem_avail_gb": 4.0}
    assert out.agents == {"claude": True}


def test_old_doc_has_none() -> None:
    out = _to_worker_out({"_id": "h1", "updated_at": datetime.now(UTC)})
    assert out.metrics is None
    assert out.agents is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd api && uv run pytest app/tests/test_worker_out.py -v`
Expected: FAIL `AttributeError: 'WorkerOut' object has no attribute 'metrics'`

- [ ] **Step 3: Implementar** — em `WorkerOut`, após `ollama_models`:

```python
    # Carga atual (cpu_pct/mem_pct/mem_total_gb/mem_avail_gb) e CLIs de agente
    # instaladas ({claude: bool, ...}) — base do `sf delegate --host auto`
    # (ver worker/sessionflow_worker/host_metrics.sample_load/installed_agents).
    # None = worker antigo.
    metrics: dict | None = None
    agents: dict | None = None
```

Em `_to_worker_out`, após `ollama_models=doc.get("ollama_models"),`:

```python
        metrics=doc.get("metrics"),
        agents=doc.get("agents"),
```

- [ ] **Step 4: Rodar e passar**

Run: `cd api && uv run pytest app/tests/test_worker_out.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/worker.py api/app/tests/test_worker_out.py
git commit -m "feat(api): /workers expõe metrics e agents do heartbeat"
```

---

### Task 3: `pick_host` — função pura de escolha

**Files:**
- Modify: `tools/sf` (nova seção após `resolve_host`, ~linha 360)
- Test: `tools/test_sf.py` (nova classe `PickHostTests`)

**Interfaces:**
- Consumes: itens de `/workers` (Task 2): `host_id, online, metrics{cpu_pct, mem_avail_gb}, agents{}`.
- Produces:
  - constantes `AUTO_MAX_CPU = 85.0`, `AUTO_MIN_MEM_GB = 2.0`, `AUTO_TIE_PCT = 0.05`
  - `host_score(w) -> float`
  - `pick_host(workers, provider, local_id, dir_ok, need_remote=False) -> (chosen: dict|None, ranked: list[dict], rejected: list[tuple[dict, str]])`
    - `dir_ok(w) -> str | None`: `None` = pasta ok; string = motivo do descarte.
    - `ranked`: candidatos aprovados, ordenados (escolhido primeiro).

- [ ] **Step 1: Testes falhando** — append em `tools/test_sf.py`:

```python
def _w(hid, cpu=10.0, mem=8.0, online=True, agents=None, metrics=True):
    return {
        "host_id": hid, "display_name": hid, "hostname": hid, "online": online,
        "metrics": {"cpu_pct": cpu, "mem_avail_gb": mem} if metrics else None,
        "agents": {"claude": True} if agents is None else agents,
    }


OK = lambda w: None  # noqa: E731


class PickHostTests(unittest.TestCase):
    def test_highest_score_wins(self):
        chosen, ranked, _ = sf.pick_host([_w("mac", 50, 4), _w("duck", 1, 16)], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "duck")
        self.assertEqual([w["host_id"] for w in ranked], ["duck", "mac"])

    def test_tie_prefers_local(self):
        chosen, _, _ = sf.pick_host([_w("duck", 10, 8.2), _w("mac", 10, 8.0)], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "mac")

    def test_rejects_offline_missing_agent_and_old_worker(self):
        ws = [_w("off", online=False), _w("noagent", agents={"claude": False}),
              _w("old", metrics=False), _w("ok")]
        chosen, _, rejected = sf.pick_host(ws, "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "ok")
        reasons = {w["host_id"]: r for w, r in rejected}
        self.assertIn("offline", reasons["off"])
        self.assertIn("claude", reasons["noagent"])
        self.assertIn("métricas", reasons["old"])

    def test_none_metric_values_rejected_without_crash(self):
        w = _w("x"); w["metrics"] = {"cpu_pct": None, "mem_avail_gb": 4.0}
        chosen, _, rejected = sf.pick_host([w], "claude", "mac", OK)
        self.assertIsNone(chosen)
        self.assertIn("métricas", rejected[0][1])

    def test_floor_rejects_busy_and_low_memory(self):
        chosen, _, rejected = sf.pick_host([_w("busy", 90, 16), _w("tight", 5, 1.5)], "claude", "mac", OK)
        self.assertIsNone(chosen)
        reasons = {w["host_id"]: r for w, r in rejected}
        self.assertIn("cpu", reasons["busy"])
        self.assertIn("ram", reasons["tight"])

    def test_need_remote_excludes_local(self):
        chosen, _, _ = sf.pick_host([_w("mac", 1, 32), _w("duck", 50, 4)], "claude", "mac", OK, need_remote=True)
        self.assertEqual(chosen["host_id"], "duck")

    def test_dir_check_runs_last_and_reason_kept(self):
        seen = []
        def dir_ok(w):
            seen.append(w["host_id"])
            return "pasta ambígua" if w["host_id"] == "duck" else None
        chosen, _, rejected = sf.pick_host(
            [_w("duck", 1, 16), _w("mac", 10, 8), _w("busy", 99, 8)], "claude", "mac", dir_ok)
        self.assertEqual(chosen["host_id"], "mac")
        self.assertNotIn("busy", seen)  # barrado no piso antes de gastar rede
        self.assertIn(("duck", "pasta ambígua"), [(w["host_id"], r) for w, r in rejected])
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python3 -m unittest tools.test_sf.PickHostTests -v`
Expected: FAIL `AttributeError: module 'sf' has no attribute 'pick_host'`

- [ ] **Step 3: Implementar** — em `tools/sf`, logo após `resolve_host`:

```python
# --------------------------------------------------------------------------- #
# --host auto: escolhe o host mais livre
# --------------------------------------------------------------------------- #
AUTO_MAX_CPU = 85.0      # acima disso o host é considerado ocupado
AUTO_MIN_MEM_GB = 2.0    # RAM disponível mínima p/ subir um agent
AUTO_TIE_PCT = 0.05      # scores a ≤5% do maior empatam → prefere o local


def host_score(w):
    """RAM disponível ponderada pela fração de CPU livre (maior = mais livre)."""
    m = w["metrics"]
    return m["mem_avail_gb"] * (1 - m["cpu_pct"] / 100)


def pick_host(workers, provider, local_id, dir_ok, need_remote=False):
    """Escolhe o host p/ `--host auto`. Pura: rede só via `dir_ok` (injetado).

    Filtros baratos primeiro (online, métricas, agent, piso); `dir_ok(w)` —
    que pode consultar a API — só roda nos sobreviventes, em ordem de score.
    Retorna (escolhido|None, aprovados ordenados, [(host, motivo)]).
    """
    rejected, pool = [], []
    for w in workers:
        m = w.get("metrics") or {}
        cpu, mem = m.get("cpu_pct"), m.get("mem_avail_gb")
        if not w.get("online"):
            rejected.append((w, "offline"))
        elif need_remote and w.get("host_id") == local_id:
            rejected.append((w, "--worktree/--push exigem host remoto"))
        elif cpu is None or mem is None:
            rejected.append((w, "sem métricas (worker desatualizado?)"))
        elif not (w.get("agents") or {}).get(provider):
            rejected.append((w, f"{provider} não instalado"))
        elif cpu > AUTO_MAX_CPU:
            rejected.append((w, f"cpu {cpu:.0f}% > {AUTO_MAX_CPU:.0f}%"))
        elif mem < AUTO_MIN_MEM_GB:
            rejected.append((w, f"ram livre {mem:.1f}G < {AUTO_MIN_MEM_GB:.0f}G"))
        else:
            pool.append(w)
    ranked = []
    for w in sorted(pool, key=host_score, reverse=True):
        reason = dir_ok(w)
        if reason:
            rejected.append((w, reason))
        else:
            ranked.append(w)
    if not ranked:
        return None, [], rejected
    best = host_score(ranked[0])
    local = next((w for w in ranked if w.get("host_id") == local_id), None)
    if local and best - host_score(local) <= best * AUTO_TIE_PCT:
        ranked.remove(local)
        ranked.insert(0, local)
    return ranked[0], ranked, rejected
```

- [ ] **Step 4: Rodar e passar**

Run: `python3 -m unittest tools.test_sf -v`
Expected: PASS (inclusive os testes existentes)

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): pick_host ranqueia hosts por RAM livre × CPU livre"
```

---

### Task 4: `sf delegate --host auto` (integração + saída)

**Files:**
- Modify: `tools/sf` — `cmd_delegate` (~677-705), `delegate_remote` (assinatura + bloco de resolução ~804-826 + saída final), saída local (~795-801), help do `--host` (~1229)
- Test: `tools/test_sf.py` (nova classe `AutoDelegateTests(SfApiTestCase)`)

**Interfaces:**
- Consumes: `pick_host`, `host_score`, `fetch_workers`, `fetch_dirs`, `_host_label`, `delegate_remote` (Task 3 + existentes).
- Produces: `auto_dir_checker(base, token, local_id, dir_arg) -> (dir_ok, targets: dict[host_id, {"path","is_git"}])`; `delegate_remote(..., target=None, auto_info=None)`; registry com `auto: true` e `auto_ranking`.

- [ ] **Step 1: Testes falhando** — append em `tools/test_sf.py`:

```python
class AutoDelegateTests(SfApiTestCase):
    ARGS = ("delegate", "--provider", "claude", "--host", "auto", "--dir", "sessionflow",
            "--name", "w1", "--task", "Do work")

    def setUp(self):
        super().setUp()
        Path("sessionflow").mkdir()
        self.workers = [
            {**WORKERS[0], "metrics": {"cpu_pct": 1.0, "mem_avail_gb": 16.0}, "agents": {"claude": True}},
            {**WORKERS[1], "metrics": {"cpu_pct": 20.0, "mem_avail_gb": 0.5}, "agents": {"claude": True}},
        ]

    def _post(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))

    def test_auto_picks_freest_remote_and_reports_host(self):
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "duck-id")
        self.assertEqual(self._post()["work_dir"], "~/Documents/projects/sessionflow")
        self.assertIn("host auto → 🦆 Duck Server", out)
        self.assertIn("Macbook Air", out)  # descartado listado
        self.assertIn("host:", out)
        reg = json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text(encoding="utf-8"))
        self.assertTrue(reg["auto"])
        self.assertEqual(reg["host_id"], "duck-id")

    def test_auto_uppercase_also_works(self):
        args = list(self.ARGS); args[args.index("auto")] = "AUTO"
        self.run_sf(*args)
        self.assertEqual(self._post()["host_id"], "duck-id")

    def test_auto_falls_back_to_local_when_no_candidate(self):
        self.workers[0]["agents"] = {"claude": False}
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")
        self.assertIn("nenhum host", out)

    def test_auto_picks_local_when_freest(self):
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 30.0}
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")
        self.assertIn("host auto → 🍎 Macbook Air", out)

    def test_auto_remote_dir_listing_error_does_not_abort(self):
        orig = sf._request
        def failing(method, url, token=None, body=None, timeout=30):
            if "/directories?" in url:
                return 500, {"detail": "boom"}
            return orig(method, url, token=token, body=body, timeout=timeout)
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 8.0}
        with mock.patch.object(sf, "_request", failing):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")

    def test_auto_worktree_without_remote_errors(self):
        self.workers[0]["online"] = False
        err = self.run_sf_error(*self.ARGS, "--worktree")
        self.assertIn("nenhum host", err)

    def test_explicit_host_unchanged(self):
        out = self.run_sf("delegate", "--provider", "claude", "--host", "duck", "--dir", "sessionflow",
                          "--name", "w1", "--task", "Do work")
        self.assertNotIn("host auto", out)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python3 -m unittest tools.test_sf.AutoDelegateTests -v`
Expected: FAIL (resolve_host não acha host "auto")

- [ ] **Step 3: Implementar**

3a. Após `pick_host`, adicionar:

```python
def auto_dir_checker(base, token, local_id, dir_arg):
    """`dir_ok` p/ pick_host + mapa host_id→alvo resolvido (path/is_git).

    Local: `--dir` (ou cwd) tem que existir aqui. Remoto: procura o NOME da
    pasta (basename) no índice do host; exige 1 match exato por nome.
    Falha de rede num host vira motivo de descarte, não aborta o auto.
    """
    local_path = os.path.abspath(dir_arg or os.getcwd())
    name = os.path.basename(local_path.rstrip("/"))
    targets = {}

    def dir_ok(w):
        hid = w["host_id"]
        if hid == local_id:
            if not os.path.isdir(local_path):
                return f"pasta {local_path} não existe aqui"
            targets[hid] = {"path": local_path, "is_git": None}
            return None
        try:
            items = fetch_dirs(base, token, hid, name, 50)
        except SystemExit:
            return "erro ao listar pastas do host"
        exact = [d for d in items if d.get("name") == name]
        if not exact:
            return f"pasta '{name}' não indexada"
        if len(exact) > 1:
            return f"pasta '{name}' ambígua ({len(exact)} matches)"
        targets[hid] = {"path": exact[0]["path"], "is_git": exact[0].get("is_git")}
        return None

    return dir_ok, targets


def _auto_summary(chosen, rejected):
    """Linhas de saída do auto: escolhido (score/cpu/ram) + descartados."""
    lines = []
    if chosen:
        m = chosen["metrics"]
        shown = f"{chosen.get('emoji') or ''} {chosen.get('display_name') or chosen.get('hostname')}".strip()
        lines.append(f"host auto → {shown} ({chosen['host_id'][:8]}…) score {host_score(chosen):.1f}"
                     f" | cpu {m['cpu_pct']:.0f}% | ram livre {m['mem_avail_gb']:.1f}G")
    if rejected:
        lines.append("  descartados: " + "; ".join(
            f"{w.get('emoji') or ''} {w.get('display_name') or w.get('hostname')} ({r})".strip()
            for w, r in rejected))
    return lines
```

3b. Em `cmd_delegate`, substituir o bloco `host_id = None` / `if args.host:` (linhas ~687-703) por:

```python
    host_id = None
    auto_info = None
    if args.host and args.host.lower() == "auto":
        env = resolve_env(args)
        token = login(env)
        base = api_base(env)
        local_id = local_host_id()
        need_remote = bool(args.worktree or args.push)
        dir_ok, targets = auto_dir_checker(base, token, local_id, args.dir)
        chosen, ranked, rejected = pick_host(fetch_workers(base, token), provider,
                                             local_id, dir_ok, need_remote)
        summary = _auto_summary(chosen, rejected)
        if chosen is None:
            if need_remote:
                die("--host auto: nenhum host remoto serve. " + " ".join(summary))
            print("host auto → nenhum host atende; rodando local")
            print("\n".join(summary))
            host_id = local_id
        else:
            print("\n".join(summary))
            auto_info = {
                "auto": True,
                "auto_ranking": [{"host_id": w["host_id"], "score": round(host_score(w), 2)}
                                 for w in ranked],
            }
            host_id = chosen["host_id"]
            if host_id != local_id:
                return delegate_remote(args, env, token, base, chosen, slugify_remote(name),
                                       effort, target=targets[host_id], auto_info=auto_info)
            args.dir = targets[host_id]["path"]
    elif args.host:
        env = resolve_env(args)
        token = login(env)
        base = api_base(env)
        try:
            host = resolve_host(fetch_workers(base, token), args.host)
        except ValueError as exc:
            die(str(exc))
        host_id = host["host_id"]
        if host_id != local_host_id():
            return delegate_remote(args, env, token, base, host, slugify_remote(name), effort)
    else:
        # Sem --host = ESTE host. Sem isso a API escolhe o worker com heartbeat
        # mais recente, que pode ser outro (ex.: Duck) e a sessão nunca sobe aqui.
        host_id = local_host_id()
```

No bloco auto, ao escolher local, guardar `auto_host = chosen` (inicializar `auto_host = None` junto de `auto_info = None`). No fluxo local, o `reg` ganha `"host_id": host_id` e `**(auto_info or {})`; a saída final, após a linha `handle:`, ganha:

```python
    if auto_host:
        print(f"  host:    {_host_label(auto_host)}")
```

3c. `delegate_remote`: assinatura `def delegate_remote(args, env, token, base, host, name, effort, target=None, auto_info=None):`. Trocar o bloco de resolução (`try: role = ... target = resolve_remote_dir(...)`) por:

```python
    try:
        role = resolve_role(args.role, local_dir)
        context = load_context_file(args.context_file, local_dir)
        if target is None:
            literal = _is_literal_remote_path(args.dir)
            lookup = [] if literal else fetch_dirs(base, token, host_id, args.dir, 50)
            target = resolve_remote_dir(args.dir, lookup, str(Path.home()))
    except ValueError as exc:
        die(str(exc))
```

E a checagem `if not args.dir: die(...)` vira `if not args.dir and target is None:`. No `reg` remoto adicionar `**(auto_info or {})`. Na saída final do remoto, após a linha do handle, adicionar `print(f"  host:    {_host_label(host)}")` **somente se** `auto_info`.

3d. Help do `--host`:

```python
                   help="host de destino (host_id, nome, hostname, emoji ou trecho) "
                        "ou 'auto' (escolhe o mais livre com o provider e a pasta). "
                        "Remoto: --dir é caminho/nome NAQUELE host (veja 'sf dirs').")
```

- [ ] **Step 4: Rodar e passar**

Run: `python3 -m unittest tools.test_sf -v`
Expected: PASS — todos, inclusive `LocalDelegateTests`/`RemoteDelegateTests` (garantem modos antigos intactos).

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): delegate --host auto escolhe o host mais livre e informa id + host"
```

---

### Task 5: `sf hosts` mostra carga + agents

**Files:**
- Modify: `tools/sf` `cmd_hosts` (~1086-1108)
- Test: `tools/test_sf.py` (classe `HostsCommandTests(SfApiTestCase)`)

**Interfaces:**
- Consumes: `metrics`, `agents` de `/workers`.

- [ ] **Step 1: Teste falhando**

```python
class HostsCommandTests(SfApiTestCase):
    def test_hosts_shows_load_and_agents(self):
        self.workers = [{**WORKERS[0], "capabilities": {},
                         "metrics": {"cpu_pct": 18.0, "mem_avail_gb": 0.4, "mem_total_gb": 16.0},
                         "agents": {"claude": True, "codex": False, "gemini": True}}]
        out = self.run_sf("hosts")
        self.assertIn("cpu 18%", out)
        self.assertIn("ram livre 0.4/16.0G", out)
        self.assertIn("agents=claude,gemini", out)

    def test_hosts_old_worker_without_metrics(self):
        self.workers = [{**WORKERS[0], "capabilities": {}}]
        out = self.run_sf("hosts")
        self.assertIn("host_id=duck-id", out)
        self.assertNotIn("cpu", out)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python3 -m unittest tools.test_sf.HostsCommandTests -v`
Expected: FAIL (`cpu 18%` ausente)

- [ ] **Step 3: Implementar** — em `cmd_hosts`, substituir o `print` final por:

```python
        line = f"{online} {label}\thost_id={host_id}\tcaps={cap_txt or '-'}"
        m = w.get("metrics") or {}
        if m.get("cpu_pct") is not None and m.get("mem_avail_gb") is not None:
            line += (f"\tcpu {m['cpu_pct']:.0f}% | ram livre "
                     f"{m['mem_avail_gb']:.1f}/{m.get('mem_total_gb') or 0:.1f}G")
        agents = [k for k, v in (w.get("agents") or {}).items() if v]
        if agents:
            line += f"\tagents={','.join(agents)}"
        print(line)
```

- [ ] **Step 4: Rodar e passar**

Run: `python3 -m unittest tools.test_sf -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/sf tools/test_sf.py
git commit -m "feat(sf): hosts mostra cpu, ram livre e agents instalados"
```

---

### Task 6: Deploy + verificação real

- [ ] Reiniciar worker do Mac (launchd, ver memória `sessionflow-worker-infra`) e do Duck (deploy via bundle, memória `duck-worker-layout`); rebuild da API: `docker compose --profile app up -d --build api`.
- [ ] `sf hosts` → ambos online mostram `cpu`/`ram livre`/`agents`.
- [ ] `sf delegate --provider claude --host auto --dir sessionflow --task "rode pwd e escreva o handoff" --name w-auto-smoke` → saída tem `host auto →`, `id=` e `host:`; `sf check w-auto-smoke`.
- [ ] Docs: em `~/.claude/skills/sf-delegate/SKILL.md` (aponta para o repo) adicionar exemplo `--host auto`.
