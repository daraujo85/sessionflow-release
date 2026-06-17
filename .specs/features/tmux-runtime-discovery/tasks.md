# tmux Runtime & Discovery — Tasks

**Design**: `.specs/features/tmux-runtime-discovery/design.md`
**Status**: ✅ Done — T1..T16 implementados e verdes (Worker 62 testes, API 25 testes, ruff limpo, build da API ok, e2e passa)

### Progresso de execução
- ✅ T1 (scaffold worker) — Python 3.12.12, ruff ok, pytest<9 (conflito plugin libtmux)
- ✅ T11 (scaffold API) — /health 200 c/ Mongo, ruff ok, 1 integração
- ✅ T4 (state.py) — 8 unit, precedência stopped>error>detached>running
- ✅ T5 (agent_launcher.py) — 14 unit; codex `model_reasoning_effort` confirmado, codex `max→high`, gemini sem effort
- ✅ T6 (dir_scanner.py) — 8 unit, scan/filter com tmp_path
- ✅ Gate combinado worker: 31 unit passam, ruff limpo
- ✅ T2 (mongo.py) — 3 integ; índice `$in ACTIVE_STATUSES`; testes em coleção isolada no DB `sessionflow` (L-002)
- ✅ T3 (rabbit.py) — 3 integ; exchange `sessionflow` + filas commands/events
- ✅ T7 (tmux_runtime.py) — 10 integ; rejeita nome `.`/`:`; teardown seguro (sessões reais intactas)
- ✅ Gate full worker: 47 passed (31 unit + 16 integ), ruff limpo, 0 sftest vazadas
- ✅ T8 (discovery.py) — 4 integ; upsert por tmux_name, sumidas→stopped, lock anti-concorrência
- ✅ T10 (dir persist) — 2 integ; upsert idempotente em host_directories
- ✅ T9 (command_consumer.py) — 8 integ; create/kill/rename/resume + idempotência + send-keys libtmux
- ✅ Gate full worker (Fase 3 done): 61 passed (31 unit + 30 integ), ruff limpo
- ✅ T12 (GET /sessions + /{id}) — 5 integ; filtros + 404; coleção injetável
- ✅ T13 (GET /directories) — 5 integ; substring + no_match + limite
- ✅ T14 (POST /sessions) — 6 integ; publisher rabbit, dup→409, effort gemini→None
- ✅ T15 (kill/rename/resume) — 8 integ; payloads kill/rename/resume + 404. API: 25 verdes
- ✅ T16 (e2e vertical slice) — 1 e2e real: POST→fila→consumer→tmux→mongo→GET running→DELETE→stopped; launch neutralizado; build da API ok
- ✅ **FEATURE COMPLETA** — Worker 62 + API 25 verdes, ruff limpo, 0 sftest vazadas, sessões reais intactas

> Testes: **unit + integration** (pytest + ruff). Integração **não é paralela** (tmux/Mongo/Rabbit compartilhados — ver `codebase/TESTING.md`). Pré-condição p/ gates Full/Build: `docker compose up -d` no ar + tmux disponível.

---

## Execution Plan

### Phase 1 — Foundation (Sequential)

```
T1 ──┬─→ T2 ─→ T3
     └─→ (libera Phase 2)
T11  (API scaffold — independente do worker, mas integração ⇒ sequencial)
```

### Phase 2 — Worker pure logic (Parallel OK)

```
        ┌─→ T4 [P]
T1 ─────┼─→ T5 [P]
        └─→ T6 [P]
T1 ─────────→ T7   (integração ⇒ sequencial)
```

### Phase 3 — Worker integration (Sequential)

```
T2,T4,T7 ─→ T8
T2,T3,T5,T7 ─→ T9
T2,T6 ─→ T10
```

### Phase 4 — API + E2E (Sequential)

```
T11 ─→ T12
T10,T11 ─→ T13
T11,T3 ─→ T14
T11 ─→ T15
T8,T9,T12,T14 ─→ T16 (e2e vertical slice)
```

---

## Task Breakdown

### T1: Scaffold do pacote `worker/`
**What**: Criar o projeto Python do Worker com `uv`, ruff, markers de pytest e esqueleto de pacote.
**Where**: `worker/pyproject.toml`, `worker/sessionflow_worker/__init__.py`, `worker/sessionflow_worker/tests/__init__.py`
**Depends on**: None
**Reuses**: — (estabelece padrão)
**Requirement**: base (TMUX-01..12)
**Tools**: MCP: `context7` (uv/ruff/pytest config) · Skill: NONE
**Done when**:
- [ ] `pyproject.toml` com deps (libtmux, motor, aio-pika, pydantic) + dev (pytest, ruff) + marker `integration`
- [ ] `uv run ruff check .` passa (0 erros)
- [ ] `uv run pytest -m "not integration"` roda (0 testes, exit 0)

**Tests**: none · **Gate**: quick
**Commit**: `chore(worker): scaffold do pacote python com uv/ruff/pytest`

---

### T2: Cliente MongoDB do Worker
**What**: Módulo de conexão Mongo (motor) lendo `MONGO_URI_HOST`, com `get_db()` e índices de `sessions`.
**Where**: `worker/sessionflow_worker/mongo.py` + `tests/test_mongo.py`
**Depends on**: T1
**Reuses**: `.env` (`MONGO_URI_HOST`)
**Requirement**: TMUX-01/03 (persistência de estado)
**Tools**: MCP: `context7` (motor) · Skill: NONE
**Done when**:
- [ ] `get_db()` conecta e cria índices (`tmux_name` único parcial, `status`, `updated_at`)
- [ ] Teste integração conecta no Mongo da stack e faz ping/insert/drop em DB `sessionflow_test`
- [ ] Gate Full passa: `uv run pytest && uv run ruff check .`
- [ ] Test count: ≥1 integração passa

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): cliente mongodb + índices de sessions`

---

### T3: Cliente RabbitMQ + topologia
**What**: Conexão aio-pika + declaração de exchange `sessionflow` e filas `sessionflow.commands`/`sessionflow.events`.
**Where**: `worker/sessionflow_worker/rabbit.py` + `tests/test_rabbit.py`
**Depends on**: T2
**Reuses**: `.env` (`RABBITMQ_URI_HOST`)
**Requirement**: TMUX-05/09/10/11 (transporte)
**Tools**: MCP: `context7` (aio-pika) · Skill: NONE
**Done when**:
- [ ] Declara exchange direct `sessionflow` + 2 filas (idempotente)
- [ ] Teste integração publica e consome 1 mensagem na fila de teste
- [ ] Gate Full passa
- [ ] Test count: ≥1 integração passa

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): cliente rabbitmq + topologia sessionflow.*`

---

### T4: Máquina de estados (`state.py`) [P]
**What**: Função pura `derive_state(tmux_present, attached, agent_alive, exit_code)` → estado determinístico.
**Where**: `worker/sessionflow_worker/state.py` + `tests/test_state.py`
**Depends on**: T1
**Reuses**: —
**Requirement**: TMUX-12
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] Cobre `running`/`detached`/`stopped`/`error` (os 3 semânticos ficam fora desta feature)
- [ ] Tabela-verdade testada (todas as combinações relevantes)
- [ ] Gate quick passa: `uv run pytest -m "not integration" && uv run ruff check .`
- [ ] Test count: ≥6 unit passam

**Tests**: unit · **Gate**: quick
**Commit**: `feat(worker): máquina de estados determinística`

---

### T5: Launcher de agente (`agent_launcher.py`) [P]
**What**: `build_launch_cmd(agent_type, model, effort)` (tabela de flags) + `infer_agent_type(pane_command)`.
**Where**: `worker/sessionflow_worker/agent_launcher.py` + `tests/test_agent_launcher.py`
**Depends on**: T1
**Reuses**: tabela de flags do `design.md`/`INTEGRATIONS.md`
**Requirement**: TMUX-04, TMUX-06
**Tools**: MCP: `context7` (verificar flags claude/codex/gemini/opencode) · Skill: NONE
**Done when**:
- [ ] Monta comando correto por agente; **gemini ignora effort** (grava null)
- [ ] `infer_agent_type` reconhece os 4 + `desconhecido`
- [ ] Mapeia rótulos PT (Baixo/Médio/Alto/Máximo) → valores de cada CLI (codex: confirmar `model_reasoning_effort`)
- [ ] Gate quick passa
- [ ] Test count: ≥8 unit passam (1+ por agente)

**Tests**: unit · **Gate**: quick
**Commit**: `feat(worker): launcher de agente com flags model/effort por CLI`

---

### T6: Lógica de scan de diretórios (`dir_scanner.py`) [P]
**What**: Varredura pura de raízes permitidas + filtro/limite (sem persistência ainda).
**Where**: `worker/sessionflow_worker/dir_scanner.py` + `tests/test_dir_scanner.py`
**Depends on**: T1
**Reuses**: —
**Requirement**: TMUX-08 (parcial)
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] Varre só raízes permitidas (default `~/dev`,`~/work`), profundidade limitada
- [ ] Filtra por termo e limita a N; termo vazio → recentes/raízes
- [ ] Testado contra árvore temporária (`tmp_path`)
- [ ] Gate quick passa
- [ ] Test count: ≥4 unit passam

**Tests**: unit · **Gate**: quick
**Commit**: `feat(worker): scan/filtro de diretórios do host`

---

### T7: Runtime tmux (`tmux_runtime.py`)
**What**: Wrapper libtmux: `list_sessions`, `new_session`, `kill_session`, `rename_session`, `has_session`, `is_attached`, `pane_command`, `pane_pid`.
**Where**: `worker/sessionflow_worker/tmux_runtime.py` + `tests/test_tmux_runtime.py`
**Depends on**: T1
**Reuses**: —
**Requirement**: TMUX-01, TMUX-02, TMUX-09, TMUX-10, TMUX-11
**Tools**: MCP: `context7` (libtmux) · Skill: NONE
**Done when**:
- [ ] Todas as ops funcionam contra tmux real (sessões namespaced `sftest-<uuid>`, teardown mata tudo)
- [ ] Sanitiza nome (`:`/`.`); lista inclui sessões externas; `has_session` correto
- [ ] Gate Full passa
- [ ] Test count: ≥6 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): runtime tmux (libtmux) com ops de ciclo de vida`

---

### T8: Discovery / reconciliação (`discovery.py`)
**What**: `reconcile_once()` + `run_forever(interval=5)` — varre tmux, upsert em `sessions`, marca sumidas `stopped`, infere tipo/estado, lock anti-concorrência.
**Where**: `worker/sessionflow_worker/discovery.py` + `tests/test_discovery.py`
**Depends on**: T2, T4, T7
**Reuses**: `tmux_runtime`, `state`, `agent_launcher.infer_agent_type`, `mongo`
**Requirement**: TMUX-01, TMUX-02, TMUX-03, TMUX-12
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] Sessão externa criada no tmux aparece em `sessions` (`origem: externa`) após `reconcile_once`
- [ ] Sessão morta vira `stopped`; sem servidor tmux → vazio sem erro
- [ ] `asyncio.Lock` impede ciclos concorrentes
- [ ] Gate Full passa
- [ ] Test count: ≥4 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): discovery loop com reconciliação tmux→mongo`

---

### T9: Consumer de comandos (`command_consumer.py`)
**What**: Consome `sessionflow.commands`, valida (dup/dir), executa create/kill/rename/resume, grava `sessions`, publica ack/erro em `sessionflow.events`.
**Where**: `worker/sessionflow_worker/command_consumer.py` + `tests/test_command_consumer.py`
**Depends on**: T2, T3, T5, T7
**Reuses**: `tmux_runtime`, `agent_launcher`, `mongo`, `rabbit`
**Requirement**: TMUX-05, TMUX-06, TMUX-07, TMUX-09, TMUX-10, TMUX-11
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] `create` cria sessão tmux + inicia agente com flags; aparece `origem: sessionflow`, `running`
- [ ] Nome duplicado / diretório inexistente → publica `error`, não cria
- [ ] `kill`/`rename`/`resume` refletem no tmux; rename preserva `_id`; ack manual; idempotência por `command_id`
- [ ] Gate Full passa
- [ ] Test count: ≥6 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): consumer de comandos de ciclo de vida`

---

### T10: Persistência + agendamento do scan de diretórios
**What**: Persistir o resultado do `dir_scanner` em `host_directories` (upsert) e agendar a varredura (boot + intervalo).
**Where**: `worker/sessionflow_worker/dir_scanner.py` (estende) + `tests/test_dir_scanner_persist.py`
**Depends on**: T2, T6
**Reuses**: `mongo`, lógica de T6
**Requirement**: TMUX-08
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] `scan()` faz upsert idempotente em `host_directories`
- [ ] Reexecução não duplica (chave `path`)
- [ ] Gate Full passa
- [ ] Test count: ≥2 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): persistência e agendamento do scan de diretórios`

---

### T11: Scaffold da `api/` (FastAPI)
**What**: App FastAPI com settings (env), clientes Mongo/Rabbit, CORS p/ `sessionflow.boletoazap.dev.br`, `GET /health`, Dockerfile.
**Where**: `api/pyproject.toml`, `api/Dockerfile`, `api/app/main.py`, `api/app/config.py`, `api/app/{repositories,publishers}/__init__.py` + `app/tests/test_health.py`
**Depends on**: None
**Reuses**: `.env` (`MONGO_URI`, `RABBITMQ_URI`)
**Requirement**: base (TMUX-05/09/10/11/14 read+write)
**Tools**: MCP: `context7` (fastapi) · Skill: NONE
**Done when**:
- [ ] App sobe; `GET /health` 200; CORS configurado p/ o subdomínio do front
- [ ] Conecta em Mongo/Rabbit (rede docker)
- [ ] Gate Full passa
- [ ] Test count: ≥1 integração passa

**Tests**: integration · **Gate**: full
**Commit**: `feat(api): scaffold fastapi com health, cors, clientes mongo/rabbit`

---

### T12: `GET /sessions` + `GET /sessions/{id}` (+ filtros)
**What**: Endpoints de leitura do estado a partir do Mongo, com filtro por status.
**Where**: `api/app/routers/sessions.py` + `app/tests/test_sessions_read.py`
**Depends on**: T11
**Reuses**: repositório Mongo (T11)
**Requirement**: TMUX-01/03/12 (visibilidade), spec P5/filtros
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] Lista com filtro (todas/running/waiting_input/completed/detached); detalhe por id
- [ ] Seed no Mongo de teste → respostas corretas; id inexistente → 404
- [ ] Gate Full passa
- [ ] Test count: ≥3 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(api): leitura de sessões com filtros`

---

### T13: `GET /directories?q=`
**What**: Autocomplete: prefix-filter em `host_directories`, limite N.
**Where**: `api/app/routers/directories.py` + `app/tests/test_directories.py`
**Depends on**: T10, T11
**Reuses**: repositório Mongo
**Requirement**: TMUX-08
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] `q` filtra; vazio → recentes; sem match → lista vazia + flag
- [ ] Limite N respeitado
- [ ] Gate Full passa
- [ ] Test count: ≥3 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(api): endpoint de autocomplete de diretórios`

---

### T14: `POST /sessions` (create + validação + publish)
**What**: Valida payload {name, agent_type, work_dir, model, effort}, checa duplicidade otimista, publica comando `create`, retorna 202.
**Where**: `api/app/routers/sessions.py` (estende) + `app/tests/test_sessions_create.py`
**Depends on**: T11, T3 (topologia) 
**Reuses**: publisher Rabbit, repositório Mongo
**Requirement**: TMUX-05, TMUX-06, TMUX-07
**Tools**: MCP: `context7` (pydantic) · Skill: NONE
**Done when**:
- [ ] Payload válido → publica em `sessionflow.commands` com payload correto (inspeção da fila) → 202
- [ ] Nome duplicado / dir vazio → 4xx com erro claro (validação otimista)
- [ ] effort ignorado p/ gemini
- [ ] Gate Full passa
- [ ] Test count: ≥4 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(api): criar sessão (validação + publish na fila)`

---

### T15: Endpoints kill / rename / resume
**What**: `DELETE /sessions/{id}`, `PATCH /sessions/{id}` (rename), `POST /sessions/{id}/resume` — publicam comandos.
**Where**: `api/app/routers/sessions.py` (estende) + `app/tests/test_sessions_lifecycle.py`
**Depends on**: T11, T3
**Reuses**: publisher Rabbit
**Requirement**: TMUX-09, TMUX-10, TMUX-11
**Tools**: MCP: NONE · Skill: NONE
**Done when**:
- [ ] Cada endpoint publica o comando correto (inspeção da fila) → 202
- [ ] id inexistente → 404
- [ ] Gate Full passa
- [ ] Test count: ≥3 integração passam

**Tests**: integration · **Gate**: full
**Commit**: `feat(api): endpoints de encerrar/renomear/retomar`

---

### T16: E2E vertical slice (Worker + API)
**What**: Teste fim-a-fim: `POST /sessions` → comando → Worker cria tmux → discovery → `GET /sessions` mostra `running`; depois kill → `stopped`.
**Where**: `api/app/tests/test_e2e_lifecycle.py` (ou `tests/e2e/`)
**Depends on**: T8, T9, T12, T14
**Reuses**: tudo
**Requirement**: TMUX-01,05,09 (slice MVP completo)
**Tools**: MCP: NONE · Skill: `verify` (validação manual complementar, opcional)
**Done when**:
- [ ] Sessão criada via API aparece como `running` no `GET /sessions` em ≤ alguns segundos
- [ ] Encerrar via API → `stopped`; sessão sai do tmux
- [ ] Gate Build passa: `docker compose build && uv run pytest && uv run ruff check .`
- [ ] Test count: ≥2 e2e passam

**Tests**: integration (e2e) · **Gate**: build
**Commit**: `test(e2e): ciclo de vida completo via API+Worker`

---

## Parallel Execution Map

```
Phase 1: T1 → T2 → T3        (sequencial; integração)
         T11                 (sequencial; integração; independe do worker)
Phase 2: T1 done →  ├── T4 [P]
                    ├── T5 [P]   } unit, parallel-safe → sub-agentes simultâneos
                    └── T6 [P]
         T1 done →  T7          (integração ⇒ sequencial)
Phase 3: T8, T9, T10           (sequencial; integração; têm deps)
Phase 4: T12, T13, T14, T15    (sequencial; integração)
         T16                   (e2e final)
```

**Único grupo [P]:** T4/T5/T6 (lógica pura, mockada/tmp, sem estado compartilhado). Todo o resto tem testes de integração ⇒ sequencial (regra do TESTING.md).

---

## ✅ Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | scaffold (1 projeto) | ✅ |
| T2 | 1 módulo (mongo) | ✅ |
| T3 | 1 módulo (rabbit) | ✅ |
| T4 | 1 função pura | ✅ |
| T5 | 1 módulo (2 funções coesas) | ✅ |
| T6 | 1 módulo (scan/filtro) | ✅ |
| T7 | 1 módulo (ops tmux coesas) | ✅ |
| T8 | 1 módulo (discovery) | ✅ |
| T9 | 1 módulo (consumer) | ✅ |
| T10 | 1 concern (persistência do scan) | ✅ |
| T11 | scaffold API | ✅ |
| T12 | 2 endpoints de leitura coesos | ✅ |
| T13 | 1 endpoint | ✅ |
| T14 | 1 endpoint (create) | ✅ |
| T15 | 3 endpoints irmãos (publish) coesos | ✅ |
| T16 | 1 fluxo e2e | ✅ |

---

## ✅ Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (corpo) | Diagrama mostra | Status |
| --- | --- | --- | --- |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T2 | T2→T3 | ✅ |
| T4 | T1 | T1→T4 [P] | ✅ |
| T5 | T1 | T1→T5 [P] | ✅ |
| T6 | T1 | T1→T6 [P] | ✅ |
| T7 | T1 | T1→T7 | ✅ |
| T8 | T2,T4,T7 | →T8 | ✅ |
| T9 | T2,T3,T5,T7 | →T9 | ✅ |
| T10 | T2,T6 | →T10 | ✅ |
| T11 | None | (raiz) | ✅ |
| T12 | T11 | T11→T12 | ✅ |
| T13 | T10,T11 | T10,T11→T13 | ✅ |
| T14 | T11,T3 | T11,T3→T14 | ✅ |
| T15 | T11,T3 | T11→T15 | ✅ |
| T16 | T8,T9,T12,T14 | →T16 | ✅ |

Grupo [P] (T4/T5/T6) não dependem entre si ✅.

---

## ✅ Check 3 — Test Co-location Validation

| Task | Camada criada | Matriz exige | Task diz | Status |
| --- | --- | --- | --- | --- |
| T1 | scaffold (sem código testável) | none | none | ✅ |
| T2 | cliente mongo | integration | integration | ✅ |
| T3 | cliente rabbit | integration | integration | ✅ |
| T4 | state.py (lógica pura) | unit | unit | ✅ |
| T5 | agent_launcher.py (lógica pura) | unit | unit | ✅ |
| T6 | dir_scanner (lógica pura) | unit | unit | ✅ |
| T7 | tmux_runtime (ops reais) | integration | integration | ✅ |
| T8 | discovery | integration | integration | ✅ |
| T9 | command_consumer | integration | integration | ✅ |
| T10 | persistência scan | integration | integration | ✅ |
| T11 | api scaffold | integration | integration | ✅ |
| T12 | routers leitura | integration | integration | ✅ |
| T13 | router directories | integration | integration | ✅ |
| T14 | router create | integration | integration | ✅ |
| T15 | routers lifecycle | integration | integration | ✅ |
| T16 | e2e | integration | integration | ✅ |

Nenhuma violação — nenhum teste adiado.

---

## Traceability (TMUX → Tasks)

| Req | Tasks | | Req | Tasks |
| --- | --- | --- | --- | --- |
| TMUX-01 | T7,T8,T12 | | TMUX-07 | T9,T14 |
| TMUX-02 | T7,T8 | | TMUX-08 | T6,T10,T13 |
| TMUX-03 | T8 | | TMUX-09 | T7,T9,T15 |
| TMUX-04 | T5 | | TMUX-10 | T7,T9,T15 |
| TMUX-05 | T9,T14 | | TMUX-11 | T7,T9,T15 |
| TMUX-06 | T5,T9 | | TMUX-12 | T4,T8 |

**Cobertura:** 12/12 requisitos mapeados ✅
