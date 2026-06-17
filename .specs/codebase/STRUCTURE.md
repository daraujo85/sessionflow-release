# Project Structure

**Root:** `/Users/diegoaraujo/Documents/projects/sessionflow`
*Forward-looking (greenfield) — layout alvo. Hoje existem `.specs/`, `docker/`, `ui_mock/`, `docker-compose.yml`, `.env`.*

## Directory Tree (alvo, máx 3 níveis)

```
sessionflow/
├── docker-compose.yml          # stack: mongo, rabbit, api, frontend(profile app)
├── .env / .env.example         # segredos (gitignored) / template
├── docker/
│   └── mongo-init.js           # cria usuário de app do Mongo
├── worker/                     # Worker Python (roda no HOST, fora do Docker)
│   ├── pyproject.toml          # uv
│   └── sessionflow_worker/
│       ├── tmux_runtime.py     # ops tmux (libtmux)
│       ├── agent_launcher.py   # comando de launch por agente (model/effort)
│       ├── discovery.py        # loop de reconciliação ≤5s
│       ├── state.py            # máquina de estados
│       ├── dir_scanner.py      # cache de diretórios do host
│       ├── command_consumer.py # consome sessionflow.commands
│       ├── mongo.py / rabbit.py# clientes
│       └── tests/              # pytest (unit + integration)
├── api/                        # API FastAPI (container)
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── routers/            # sessions.py, directories.py
│       ├── repositories/       # acesso Mongo
│       ├── publishers/         # publish RabbitMQ
│       └── tests/
├── frontend/                   # Angular (container)
│   ├── Dockerfile
│   └── src/
├── ui_mock/                    # mockup de referência (Prata Digital DS)
└── .specs/                     # planejamento spec-driven
```

## Module Organization

### Worker (host)
**Purpose:** única porta para o tmux; discovery, lançamento de agentes, comandos.
**Location:** `worker/sessionflow_worker/`

### API (container)
**Purpose:** REST + SSE; publica comandos, lê estado do Mongo.
**Location:** `api/app/`

### Frontend (container)
**Purpose:** dashboard mobile-first.
**Location:** `frontend/src/`

## Where Things Live

**tmux Runtime & Discovery:**
- Lógica: `worker/sessionflow_worker/` (tmux_runtime, discovery, agent_launcher, state, dir_scanner)
- Endpoints: `api/app/routers/sessions.py`, `directories.py`
- Dados: coleções `sessions`, `host_directories` (Mongo)
- Config: `.env`

## Special Directories

**`.specs/`** — planejamento (project/, codebase/, features/, quick/).
**`ui_mock/`** — referência visual; NÃO é código de produção.
**`docker/`** — scripts de init de containers.
