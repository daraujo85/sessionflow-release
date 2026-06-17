# Tech Stack

**Analyzed:** 2026-06-16 — *forward-looking (greenfield; ainda sem código). Destilado das decisões AD-001…AD-011.*

## Core

- Runtime backend: Python 3.12+ (gerenciado com `uv`)
- Runtime frontend: Node.js (Angular CLI)
- Fonte de verdade do domínio: **tmux 3.6b**
- Orquestração local: Docker Compose (projeto `sessionflow`)

## Frontend

- UI Framework: **Angular** (mobile-first, dark theme)
- Design System: **Prata Digital DS** (tokens em `ui_mock/_ds/...`) — accent mint `#00E4B4`, navy, Inter + JetBrains Mono (terminal)
- Styling: CSS custom properties (tokens do DS) — tema dark próprio do SessionFlow (mockup `ui_mock/SessionFlow.dc.html`)
- State Management: serviços Angular + signals (a confirmar na feature de Dashboard)
- Package manager: npm (default do Angular CLI)

## Backend (API)

- API Style: **REST + SSE** (FastAPI)
- Framework: **FastAPI** (Python, `uv`)
- Database driver: **motor** (MongoDB async)
- Mensageria: **aio-pika** (RabbitMQ async)
- Auth: delegada à camada de túnel Cloudflare (sem login próprio no MVP — AD-008)

## Worker (host)

- Linguagem: Python (mesmo runtime da API; roda no host, fora do Docker — AD-002)
- tmux: **libtmux** (+ fallback `subprocess`)
- Drivers: motor (Mongo), aio-pika (RabbitMQ)

## Database / Messaging

- **MongoDB** (stack local, container `sessionflow-mongo`, v8.2) — DB `sessionflow`, usuário de app dedicado
- **RabbitMQ** (stack local, container `sessionflow-rabbitmq`, 3-management-alpine) — exchange/filas `sessionflow.*`

## Testing

- Unit/Integration (Python): **pytest**
- Lint/format (Python): **ruff**
- Unit (Angular): default Angular (Karma/Jasmine) — *a confirmar; possível Jest/Vitest*
- E2E (futuro): Playwright (skill disponível no ambiente)

## External Services

- Transcrição: **Whisper** (local, host) — Fase 2
- LLM auxiliar: **Ollama** (local, host) — classificação/resumos, Fase 2/3
- Push: **Firebase FCM** — Fase 2
- Acesso externo: **Cloudflare Tunnel** (container existente) → `*.boletoazap.dev.br`
- Agentes gerenciados (CLIs no host): `claude` 2.1.x, `codex`, `gemini` 0.45, `opencode` 1.14.x

## Development Tools

- Gerenciador de pacotes Python: `uv`
- Containerização: Docker Compose (`docker-compose.yml`, profile `app`)
- Planejamento: skill `tlc-spec-driven` (`.specs/`)
