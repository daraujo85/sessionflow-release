# Dashboard Mobile-First + SSE — Tasks

**Design**: `.specs/features/dashboard-sse/design.md`
**Status**: In Progress — Backend D1-D8 ✅ · Frontend base D9-D12 ✅ · Telas D13-D21 ✅ (build+31 testes verdes) · falta D22 Lighthouse, D23 e2e, D24 métricas(P3)

### Progresso
- ✅ D1 uploads volume · D2 output_capture (7) · D3 events (4) · D4 SSE broker (3) · D5 read endpoints (7)
- ✅ D6 input (api39/worker76) · D7 audio upload (3, +python-multipart no pyproject) · D8 whisper (5)
- ✅ D9 scaffold Angular22+PWA · D10 ApiService (envelopes desembrulhados) · D11 SseService (guard EventSource) · D12 shell+nav+rotas
- ✅ D13 Início · D14 Sessões · D15 Criar · D16 audio-recorder · D17 Detalhe · D18 Responder · D19 Notificações · D20 Timeline · D21 Perfil
- ✅ D22 Lighthouse no app **live containerizado**: **100 / 100 / 100 / 100** (perf/a11y/best-practices/seo). Adicionado `frontend/public/robots.txt` p/ fechar o SEO.
- ✅ Stack `--profile app` validada: api+frontend buildados e rodando em container (api `/health` mongo:true, `/sessions` ok, front HTTP 200). Worker no host (por design).
- 🔄 D23 e2e Playwright voice-flow · D24 métricas reais (P3)
- ⚠️ GAP p/ operação live: o Worker ainda não tem um **entrypoint/daemon** (`__main__` que rode discovery + command_consumer + output_capture juntos) — módulos prontos e testados, falta o runner.
- 🔜 D-WORKER-RUN: criar o runner/daemon do Worker (discovery + consumer + dir-scan + captura on-demand) → operação ao vivo.
- 🔄 D25 (refino de UI) — EM ANDAMENTO:
  - ✅ Fix inferência de agente (worker lê cmdline da árvore de processos → claude/codex/etc; planner/portal/pratinha agora = claude). Limpeza de sessões de teste poluídas no banco.
  - ✅ Início (pulse verde+âmbar) e Sessões (cards/pills/chips/avatar fiéis ao mockup) — verificados com Playwright.
  - ✅ Detalhe, Responder, Timeline, Perfil, Notificações refinados (write-only → build limpo → container) e verificados (Sessões/Perfil/Detalhe via Playwright). Todas as 8 telas fiéis ao mockup.
  - Polish pendente (menor): ícones SVG dos settings do Perfil saíram como quadrados (innerHTML não pintou o glifo); `work_dir`/terminal das sessões externas vêm "—"/vazio (discovery pode pegar `pane_current_path`; output não é capturado de sessões externas por segurança); status-bar iPhone opcional.

> Testes: Python unit+integration (pytest+ruff); Angular serviços=unit, telas=none(e2e+build), fluxos=Playwright, tela principal=Lighthouse (ver `codebase/TESTING.md`). Integração Python NÃO é paralela (infra compartilhada). Telas Angular são paralelizáveis (dirs distintos) DESDE QUE as rotas sejam stubadas em D12.

---

## Execution Plan (fases)

- **Fase A — Backend** (Worker + API): output, eventos, SSE, input, áudio, transcrição.
- **Fase B — Frontend base**: scaffold Angular, core services, shell+nav.
- **Fase C — Telas Angular** (paralelas): Início, Sessões, Criar, Detalhe, Notificações, Timeline, Perfil, Responder.
- **Fase D — Qualidade**: e2e Playwright + Lighthouse.

```
A: D1 → (D2 ∥ D3 ∥ D4 ∥ D5) → D6 → (D7 ∥ D8)
B: D9 → (D10 ∥ D11) → D12
C: D12 done → (D13..D21 em ondas paralelas) ; D17 antes de D16/D18
D: backend+frontend → D22 (Lighthouse) , D23 (e2e)
```

---

## Fase A — Backend

### D1: Infra — volume de uploads + dir
**What**: Adicionar bind-mount `./data/uploads` ↔ API `/data/uploads` no compose; criar `data/uploads/.gitkeep`; `.gitignore` ignora `data/uploads/*`.
**Where**: `docker-compose.yml`, `data/uploads/.gitkeep`, `.gitignore`
**Depends on**: None · **Req**: DASH-14
**Tools**: MCP NONE · Skill NONE
**Done when**: `docker compose config` válido; volume mapeado no serviço `api`.
**Tests**: none · **Gate**: build (`docker compose config`)

### D2: Worker — captura de output (`output_capture.py`)
**What**: `pipe-pane` por sessão, `classify_line`, `detect_waiting`, persistir `session_output` (ring/cap), publicar evento `output`.
**Where**: `worker/sessionflow_worker/output_capture.py` + tests
**Depends on**: None (reusa tmux_runtime/mongo/rabbit) · **Req**: DASH-02
**Tools**: MCP context7 (libtmux) · Skill NONE
**Done when**: integração cria sessão sftest que imprime texto → linhas persistidas + evento publicado; ruff ok.
**Tests**: integration · **Gate**: full

### D3: Worker — emissão de eventos (`events.py`)
**What**: `emit_event(db, type, kind, session_id, title, desc)` → coleção `events`; ligar emissão em discovery (created/stopped/detached) sem quebrar testes.
**Where**: `worker/sessionflow_worker/events.py` (novo) + editar `discovery.py` + tests
**Depends on**: None · **Req**: DASH-03
**Tools**: NONE
**Done when**: transições de discovery geram `events`; integração verifica; ruff ok; suíte worker verde.
**Tests**: integration · **Gate**: full

### D4: API — SSE (`EventsBroker` + `GET /events`)
**What**: Broker (consumer único de `sessionflow.events` + fan-out asyncio); router SSE com heartbeat 20s, headers anti-buffer, `Last-Event-ID`, filtro `?session=`.
**Where**: `api/app/events_broker.py` + `api/app/routers/events.py` + tests
**Depends on**: None · **Req**: DASH-01
**Tools**: context7 (fastapi sse/starlette) · Skill NONE
**Done when**: integração: publica em `sessionflow.events` → cliente SSE recebe; heartbeat presente; ruff ok.
**Tests**: integration · **Gate**: full

### D5: API — leitura (output/history/notifications/tasks)
**What**: `GET /sessions/{id}/output?after=`, `GET /events/history?day=`, `GET /notifications`, `GET /tasks?session=`.
**Where**: `api/app/routers/outputs.py` + `api/app/routers/history.py` + repos + tests (NÃO editar events.py de D4 nem sessions.py)
**Depends on**: None · **Req**: DASH-03/09/10
**Tools**: NONE
**Done when**: seed em coleções isoladas → respostas corretas; ruff ok.
**Tests**: integration · **Gate**: full

### D6: Input — `POST /sessions/{id}/input` + handler Worker
**What**: API publica comando `input`; consumer Worker injeta via `send-keys`.
**Where**: editar `api/app/routers/sessions.py` + `worker/.../command_consumer.py` + tests
**Depends on**: None · **Req**: DASH-13 (input)
**Tools**: NONE
**Done when**: API publica `input` (verifica fila); consumer injeta (integração tmux sftest); ruff ok ambos.
**Tests**: integration · **Gate**: full

### D7: Áudio — `POST /sessions/{id}/audio` (upload)
**What**: multipart → salva em `/data/uploads`, registra `uploads`, publica comando `audio`.
**Where**: editar `api/app/routers/sessions.py` (após D6) + `api/app/repositories/uploads_repo.py` + tests
**Depends on**: D6 (mesmo arquivo sessions.py) · **Req**: DASH-14
**Tools**: NONE
**Done when**: upload de wav → arquivo no volume + doc `uploads` + comando `audio` publicado; ruff ok.
**Tests**: integration · **Gate**: full

### D8: Transcrição — `transcriber.py` + handler `audio`
**What**: `transcribe(path)` via openai-whisper (executor, modelo `base`); consumer handler `audio` → transcreve → `send-keys` → evento `input`.
**Where**: `worker/sessionflow_worker/transcriber.py` (novo) + editar `command_consumer.py` (após D6) + tests
**Depends on**: D6 (mesmo command_consumer.py) · **Req**: DASH-15
**Tools**: context7 (openai-whisper) · Skill NONE
**Done when**: wav curto com fala → texto transcrito injetado + evento; falha não trava; ruff ok.
**Tests**: integration · **Gate**: full

---

## Fase B — Frontend base

### D9: Scaffold Angular (PWA + DS + Dockerfile)
**What**: app Angular standalone, PWA (manifest+SW), import dos tokens do Prata Digital DS, tema dark base, Dockerfile (build→nginx), entrada no compose profile `app`.
**Where**: `frontend/` (projeto), `frontend/Dockerfile`, tokens em `src/styles`
**Depends on**: None · **Req**: DASH-04
**Tools**: context7 (angular, angular pwa) · Skill NONE
**Done when**: `npm run build` ok; `npm test -- --watch=false` ok (smoke); app sobe local; `docker compose build frontend` ok.
**Tests**: none(scaffold) · **Gate**: build

### D10: core — models + ApiService [P]
**What**: `models.ts` (Session, EventItem, Notification, Directory, Task) + `ApiService` (sessions CRUD, directories, input, audio upload, output, events history, notifications, tasks). baseURL configurável (`api.sessionflow...`).
**Where**: `frontend/src/app/core/{models.ts,api.service.ts}` + `api.service.spec.ts`
**Depends on**: D9 · **Req**: DASH-04
**Tools**: context7 (angular httpclient) · Skill NONE
**Done when**: unit (HttpTestingController) cobre os métodos; `npm test` verde; lint ok.
**Tests**: unit · **Gate**: quick

### D11: core — SseService [P]
**What**: `SseService` com EventSource em `GET /events`, reconexão c/ backoff, expõe signals (sessionUpdates, outputLines, notifications).
**Where**: `frontend/src/app/core/sse.service.ts` + spec
**Depends on**: D9 · **Req**: DASH-01 (cliente)
**Tools**: context7 (angular signals) · Skill NONE
**Done when**: unit (mock EventSource) cobre parse + reconexão; `npm test` verde; lint ok.
**Tests**: unit · **Gate**: quick

### D12: App shell + bottom-nav + rotas stub
**What**: shell com status bar + bottom-nav (5 abas) + roteamento. **Define rotas/stubs de TODAS as telas e overlays** (componentes vazios) p/ as tarefas de tela ficarem paralelas sem editar o arquivo de rotas.
**Where**: `frontend/src/app/shell/*` + `app.routes.ts` + stubs em `features/*`
**Depends on**: D9, D10 · **Req**: DASH-04
**Tools**: context7 (angular router) · Skill NONE
**Done when**: navega entre 5 abas; overlays abrem/fecham; build ok; lint ok.
**Tests**: none · **Gate**: build

---

## Fase C — Telas Angular (paralelas após D12; cada uma só mexe no seu dir)

### D13: Tela Início [P] — DASH-05
**Where**: `features/inicio/*`. Saudação, contagem ativas, cards de sessões ativas (status/pulse/badge), tarefas recentes; live via SseService. **Depends**: D12,D10,D11. **Tests**: none · **Gate**: build.

### D14: Tela Sessões + filtros [P] — DASH-06
**Where**: `features/sessoes/*`. Chips de filtro, cards (agente/dir/status/tempo), FAB→Criar. **Depends**: D12,D10. **Tests**: none · **Gate**: build.

### D15: Overlay Criar [P] — DASH-07
**Where**: `features/criar/*`. Nome, grid de agentes, modelos por agente, esforço (**oculto p/ gemini**), diretório c/ autocomplete (`/directories`), `POST /sessions`. **Depends**: D12,D10. **Tests**: none · **Gate**: build.

### D16: Shared AudioRecorder + upload [P] — DASH-14 (cliente)
**Where**: `shared/audio-recorder/*`. MediaRecorder (start/stop, indicador), upload multipart via ApiService; trata permissão negada. **Depends**: D10. **Tests**: unit (lógica de estado, mock MediaRecorder) · **Gate**: quick. *(feito antes de D17/D18)*

### D17: Overlay Detalhe [P] — DASH-08 + DASH-13(input) + DASH-12(degrade)
**Where**: `features/detalhe/*`. Header/status, Retomar/Encerrar, **terminal ao vivo (SSE + GET output)**, bloco de métricas com **degrade "indisponível"**, input bar (texto + mic via D16). **Depends**: D12,D10,D11,D16. **Tests**: none · **Gate**: build.

### D18: Tela Responder [P] — DASH-13
**Where**: `features/responder/*`. Sessões `waiting_input`, quick-replies, textarea + envio (`/input`), mic (D16). **Depends**: D12,D10,D16. **Tests**: none · **Gate**: build.

### D19: Overlay Notificações [P] — DASH-09
**Where**: `features/notificacoes/*`. Lista por kind, badge, abre sessão; live via SSE. **Depends**: D12,D10,D11. **Tests**: none · **Gate**: build.

### D20: Tela Timeline [P] — DASH-10
**Where**: `features/timeline/*`. Eventos agrupados por dia (`/events/history`). **Depends**: D12,D10. **Tests**: none · **Gate**: build.

### D21: Tela Perfil [P] — DASH-11
**Where**: `features/perfil/*`. Status do Worker, stats, settings (push desabilitado). **Depends**: D12,D10. **Tests**: none · **Gate**: build.

---

## Fase D — Qualidade

### D22: PWA + Lighthouse na tela principal
**What**: afinar manifest/SW/perf/a11y; rodar `lighthouse-ci` na tela principal e iterar até nota alta.
**Where**: `frontend/` (manifest, SW, ajustes)
**Depends on**: D12,D13 (mínimo navegável) · **Req**: success criteria
**Tools**: Skill `lighthouse-ci`
**Done when**: Lighthouse com nota alta (perf/a11y/best-practices/PWA) documentada.
**Tests**: qualidade · **Gate**: build

### D23: E2E Playwright (fluxo de voz e ciclo)
**What**: fluxo real: criar sessão → ver ao vivo → responder por texto e por **áudio** → encerrar.
**Where**: `frontend/e2e/*`
**Depends on**: Fase A + C (stack `--profile app` + worker) · **Req**: DASH success
**Tools**: Skill `playwright-skill`
**Done when**: fluxo passa contra a stack rodando.
**Tests**: e2e · **Gate**: full

### D24 (P3): Métricas reais (pesquisa) — DASH-12
**What**: investigar fonte real de token/contexto/limite por CLI; popular o que for verídico; senão manter "indisponível". **Não fabricar.**
**Where**: worker (coleta) + detalhe (exibe). **Depends**: D17. **Tests**: integration (se houver fonte) · **Gate**: full. *(pode resultar em "indisponível" para algumas CLIs.)*

---

## ✅ Check 1 — Granularidade
Todas as tasks = 1 módulo/serviço/tela/endpoint-group coeso. D5/D6/D7 agrupam endpoints irmãos no mesmo arquivo (coeso). ✅

## ✅ Check 2 — Diagrama ↔ Deps
| Task | Depends (corpo) | Diagrama | OK |
|---|---|---|---|
| D6 | — | A: …→D6 | ✅ |
| D7 | D6 | D6→D7 | ✅ |
| D8 | D6 | D6→D8 | ✅ |
| D12 | D9,D10 | B: D10→D12 | ✅ |
| D13/D19 | D12,D10,D11 | C | ✅ |
| D14/D15/D20/D21 | D12,D10 | C | ✅ |
| D16 | D10 | C (antes D17/D18) | ✅ |
| D17 | D12,D10,D11,D16 | C | ✅ |
| D18 | D12,D10,D16 | C | ✅ |
| D22 | D12,D13 | D | ✅ |
| D23 | A+C | D | ✅ |
D7∥D8 (arquivos distintos pós-D6) ✅. Telas C [P] não dependem entre si (rotas stubadas em D12) ✅.

## ✅ Check 3 — Co-locação de testes
| Task | Camada | Matriz exige | Task diz | OK |
|---|---|---|---|---|
| D2,D3,D4,D5,D6,D7,D8 | Python integração | integration | integration | ✅ |
| D1,D9,D12 | infra/scaffold/shell | none/build | none | ✅ |
| D10,D11,D16 | serviços/lógica Angular | unit | unit | ✅ |
| D13,D14,D15,D17,D18,D19,D20,D21 | componentes UI | none (e2e+build) | none | ✅ |
| D23 | fluxo | e2e | e2e | ✅ |
| D22 | tela principal | qualidade | qualidade | ✅ |
Sem violação (UI verificada por D23 e2e + builds).

## Traceability (DASH → Tasks)
DASH-01→D4,D11 · DASH-02→D2 · DASH-03→D3,D5 · DASH-04→D9,D10,D12 · DASH-05→D13 · DASH-06→D14 · DASH-07→D15 · DASH-08→D17 · DASH-09→D19 · DASH-10→D20 · DASH-11→D21 · DASH-12→D24 · DASH-13→D6,D17,D18 · DASH-14→D1,D7,D16 · DASH-15→D8
**Cobertura:** 15/15 ✅
