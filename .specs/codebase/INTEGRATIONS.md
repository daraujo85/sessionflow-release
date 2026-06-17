# External Integrations

*Forward-looking (greenfield) — integrações decididas/mapeadas.*

## tmux (runtime, fonte de verdade)

**Purpose:** Toda sessão = uma sessão tmux; criar/listar/encerrar/renomear/anexar.
**Implementation:** `worker/sessionflow_worker/tmux_runtime.py` (libtmux + fallback subprocess). Host, tmux 3.6b.
**Configuration:** binário `tmux` no PATH do host.
**Auth:** N/A (local).

## CLIs de Agente (gerenciados)

**Purpose:** processos iniciados dentro dos panes tmux.
**Location:** `worker/sessionflow_worker/agent_launcher.py`.
**Flags verificadas (2026-06-16 via `--help`):**

| Agente | Modelo | Esforço |
| --- | --- | --- |
| claude (2.1.x) | `--model <m>` | `--effort <level>` |
| codex | `-m <m>` | `-c model_reasoning_effort=<level>` ⚠️ confirmar chave |
| gemini (0.45) | `-m <m>` | ❌ sem flag |
| opencode (1.14.x) | `-m <provider/model>` | `--variant <high\|...>` |

## MongoDB

**Service:** container `sessionflow-mongo` (mongo 8.2, stack local).
**Purpose:** persistência (sessions, events, tasks, host_directories, feedbacks, uploads).
**Implementation:** `motor` async. API via `MONGO_URI` (serviço `mongo`); Worker via `MONGO_URI_HOST` (`127.0.0.1`).
**Auth:** usuário de app `sessionflow` escopado ao DB `sessionflow` (criado por `docker/mongo-init.js`); root separado.

## RabbitMQ

**Queue system:** container `sessionflow-rabbitmq` (3-management-alpine, stack local).
**Purpose:** transporte Worker↔API (AD-005).
**Location:** publishers em `api/app/publishers/`; consumer em `worker/.../command_consumer.py`.
**Topologia:** exchange `sessionflow` (direct); filas `sessionflow.commands` (API→Worker), `sessionflow.events` (Worker→API); vhost `/`. Ack manual, idempotência por `command_id`.
**Auth:** `sessionflow` / senha em `.env`. URIs `RABBITMQ_URI` (API) e `RABBITMQ_URI_HOST` (Worker).

## Cloudflare Tunnel (acesso externo)

**Purpose:** expor a máquina local ao mobile/browser sob `*.boletoazap.dev.br`.
**Implementation:** container de túnel **já existente** do usuário (não gerenciado por este projeto).
**Mapeamento (AD-011):** `sessionflow.boletoazap.dev.br`→`:4200` (front); `api.sessionflow.boletoazap.dev.br`→`:8000` (API/SSE).
**Auth:** delegada à camada de túnel. **CORS** da API deve permitir `https://sessionflow.boletoazap.dev.br`.
**SSE:** headers `Cache-Control: no-cache` + `X-Accel-Buffering: no` + heartbeat ~15-30s (timeout ~100s).

## Whisper (Fase 2)

**Purpose:** transcrição local de áudio → texto → `tmux send-keys`.
**Location:** Worker (host). **Auth:** N/A (local).

## Ollama (Fase 2/3)

**Purpose:** classificação automática, resumos, sugestões de resposta.
**Location:** host (`127.0.0.1:11434` já em execução). **Auth:** N/A (local).

## Firebase Cloud Messaging (Fase 2)

**Purpose:** push notifications (app fechado). No MVP, notificação é in-app via SSE (AD-009).
**Configuration:** a definir na Fase 2 (service worker no front + credenciais server-side).

## Background Jobs

**Discovery loop:** Worker reconcilia tmux→Mongo a cada ≤5s.
**Dir scan:** Worker varre raízes permitidas periodicamente → `host_directories`.
