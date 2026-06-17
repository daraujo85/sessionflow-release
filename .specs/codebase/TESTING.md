# Testing Infrastructure

*Forward-looking (greenfield). Estratégia decidida em 2026-06-16: **unit + integration**, **pytest + ruff** (Python). Frontend Angular a confirmar.*

## Test Frameworks

**Unit/Integration (Python):** pytest
**Lint/format (Python):** ruff
**Unit (Angular):** default Angular (Karma/Jasmine) — *a confirmar; possível Jest/Vitest*
**E2E (futuro):** Playwright (skill disponível)
**Coverage:** pytest-cov (opcional)

## Test Organization

**Location:** co-locado por componente — `worker/sessionflow_worker/tests/`, `api/app/tests/`.
**Naming:** `test_<modulo>.py`. Integration marcada com `@pytest.mark.integration`.
**Structure:** unit usam mocks (tmux/Mongo/Rabbit fakes); integration usam tmux real do host + Mongo/Rabbit da stack local.

## Testing Patterns

### Unit Tests
**Approach:** mockar `libtmux`/subprocess, motor e aio-pika. Testar lógica pura: derivação de estado, montagem de comando de launch (tabela de flags), inferência de tipo de agente, filtro de diretórios.
**Location:** mesmo pacote, `tests/`, sem marker.

### Integration Tests
**Approach:** criar sessões tmux reais (nomes namespaced `sftest-<uuid>` p/ não colidir), Mongo DB de teste (`sessionflow_test`), fila temporária. Limpar (`kill-session`, drop DB) no teardown.
**Location:** `tests/`, marcadas `@pytest.mark.integration`.
**Pré-requisito:** tmux instalado (host) + stack `docker compose up -d` no ar.

## Test Execution

**Commands:**
- Unit (rápido): `uv run pytest -m "not integration"`
- Integration: `uv run pytest -m integration`
- Tudo: `uv run pytest`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`

**Configuration:** markers em `pyproject.toml` (`[tool.pytest.ini_options] markers = ["integration: usa tmux/mongo/rabbit reais"]`).

## Coverage Targets

**Goals:** lógica pura do Worker (state, agent_launcher, dir filter) ~alta cobertura unit; caminhos de comando cobertos por ≥1 integration cada.
**Enforcement:** não automatizado no MVP.

## Test Coverage Matrix

| Code Layer | Required Test Type | Location Pattern | Run Command |
| --- | --- | --- | --- |
| `worker/.../state.py` (lógica pura) | unit | `worker/**/tests/test_state.py` | `uv run pytest -m "not integration"` |
| `worker/.../agent_launcher.py` (montagem de flags/inferência) | unit | `worker/**/tests/test_agent_launcher.py` | `uv run pytest -m "not integration"` |
| `worker/.../dir_scanner.py` (filtro/raízes) | unit | `worker/**/tests/test_dir_scanner.py` | `uv run pytest -m "not integration"` |
| `worker/.../tmux_runtime.py` (ops tmux reais) | integration | `worker/**/tests/test_tmux_runtime.py` | `uv run pytest -m integration` |
| `worker/.../discovery.py` (reconciliação) | integration | `worker/**/tests/test_discovery.py` | `uv run pytest -m integration` |
| `worker/.../command_consumer.py` (fila→tmux→mongo) | integration | `worker/**/tests/test_command_consumer.py` | `uv run pytest -m integration` |
| `api/app/routers/*.py` (REST + publish) | integration | `api/app/tests/test_*.py` | `uv run pytest -m integration` |
| Modelos/schemas (sem lógica) | none | — | — |

## Frontend (Angular) — adicionado na feature Dashboard+SSE

| Code Layer | Required Test Type | Location | Run Command |
| --- | --- | --- | --- |
| `core/*.service.ts` (ApiService, SseService) | unit | `frontend/src/**/*.spec.ts` | `cd frontend && npm test -- --watch=false` |
| Componentes de tela (UI) | none (verificados por e2e + build) | — | — |
| Fluxos-chave (criar→ver→responder) | e2e | `frontend/e2e/*` (Playwright, skill `playwright-skill`) | via skill Playwright |
| Tela principal | qualidade (Lighthouse) | — | skill `lighthouse-ci` |

**Gates frontend** (Angular 22 — test runner é **Vitest + jsdom**, sem Karma; ESLint não configurado por padrão):
- Quick: `cd frontend && npm test -- --watch=false`
- Build: `cd frontend && npm run build` (+ `docker compose build frontend`)
- Qualidade: `lighthouse-ci` na tela principal (meta: nota alta)

## Parallelism Assessment

| Test Type | Parallel-Safe? | Isolation Model | Evidence |
| --- | --- | --- | --- |
| unit | **Yes** | tudo mockado, sem estado compartilhado | mocks de tmux/motor/aio-pika |
| integration | **No** | servidor tmux compartilhado no host + Mongo/Rabbit únicos da stack | `kill-session`/drop DB no teardown = estado mutável compartilhado |

> Integration roda **sequencial** (mesmo que o código não tenha dependência) — o tmux/Mongo compartilhados são o gargalo. Tasks com testes de integração **não** levam `[P]`.

## Gate Check Commands

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick | Após tasks só com unit | `uv run pytest -m "not integration" && uv run ruff check .` |
| Full | Após tasks com integration | `uv run pytest && uv run ruff check .` |
| Build | Fim de fase | `docker compose build && uv run pytest && uv run ruff check .` |

> Pré-condição p/ gates Full/Build: `docker compose up -d` (Mongo+Rabbit no ar) e tmux disponível.
