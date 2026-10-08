# SessionFlow

**Vision:** Central operacional web mobile-first para gerenciar, acompanhar e interagir remotamente com múltiplas sessões de agentes de IA (Claude Code, Codex, Gemini CLI, OpenCode) executando em terminais locais via tmux.
**For:** Desenvolvedor solo que roda agentes de IA em terminais locais e precisa operá-los de qualquer dispositivo com navegador, mesmo longe da máquina.
**Solves:** Hoje, longe da máquina, não há como ver o estado das execuções, ser notificado quando o agente precisa de uma decisão, responder (texto/voz), consultar histórico ou ter visão unificada de várias sessões — só SSH manual.

## Goals

- **Visibilidade remota em tempo real** — ver todas as sessões tmux ativas e seu estado, com atualização ao vivo via SSE (latência percebida < 2s).
- **Comunicação bidirecional** — enviar input por texto e por áudio (transcrito localmente) e receber notificações quando o agente pede decisão.
- **Histórico consolidado** — toda saída e evento persistido em SQLite, consultável por sessão e por dia.
- **Gestão completa de sessões** — criar, renomear, encerrar, retomar e descobrir automaticamente sessões tmux (inclusive criadas fora do SessionFlow).
- **Baixo atrito operacional** — rodar 100% na rede local, sem dependência de provedor de nuvem, compatível com macOS e Linux.

## Tech Stack

**Core:**

- Frontend: Angular (mobile-first; PWA no futuro)
- API: Python + FastAPI (gerenciado com `uv`)
- Database: MongoDB (na stack Docker local do SessionFlow)
- Fila/mensageria: RabbitMQ (na stack Docker local) — transporte Worker↔API
- Runtime/fonte de verdade: tmux
- Push notifications: Firebase Cloud Messaging (FCM)
- Acesso externo: Cloudflare Tunnel (expõe a máquina local), sob subdomínios em `boletoazap.dev.br` (ex.: `sessionflow.boletoazap.dev.br`)

**Infra física:**

- Tudo que precisa de tmux/Whisper/Ollama roda na máquina local (Mac host).
- Worker (`sessionflow-worker`, Python): roda direto no host (NÃO no Docker) — discovery tmux, captura de output, envio de input, processamento de áudio, notificações. Conecta no Mongo/Rabbit via `127.0.0.1`.
- **Stack Docker dedicada** (`docker-compose.yml`, projeto `sessionflow`): MongoDB + RabbitMQ (sobem já) + API (FastAPI) + Frontend (Angular) (profile `app`). Portas publicadas só em `127.0.0.1`.
- Ollama + Whisper: no host, para transcrição/processamento local de áudio.
- Acesso externo: já existe um **container local de túnel** (Cloudflare) em operação, que roteia os subdomínios `*.boletoazap.dev.br` até a máquina; a autenticação é delegada a essa camada de túnel.

**Key dependencies:** tmux (`send-keys`, `capture-pane`, `new-session`, `kill-session`, `attach`), Whisper (transcrição), Server-Sent Events (tempo real API→front), RabbitMQ (input/output Worker↔API), MongoDB (persistência), Firebase FCM (push), Cloudflare Tunnel (acesso remoto), Ollama (classificação/resumos futuros).

## Scope

**v1 (MVP) includes:**

- Descoberta automática de sessões tmux + descoberta de sessões criadas fora do SessionFlow
- Criar / encerrar / renomear / retomar sessões tmux
- Captura de output e envio de input
- Histórico persistido (MongoDB) + consulta diária / timeline de eventos
- Dashboard mobile-first com atualização em tempo real (SSE)
- Upload de áudio + transcrição local (Whisper)
- Notificações in-app quando o agente pede decisão (card/badge/lista via SSE; push FCM fica na Fase 2)
- Suporte aos tipos de agente: `claude`, `codex`, `gemini`, `opencode`
- **Seleção de modelo + esforço de raciocínio** na criação da sessão (passado como flags ao iniciar o agente)
- **Métricas de token/contexto** por sessão (input/output, % de contexto usado, aviso `/compact` ≥85%)
- **Limites de uso diário/semanal por provider** (Claude/Codex/Gemini/OpenCode), com indicadores coloridos
- **Autocomplete de diretório** (Worker descobre pastas do host ao criar sessão)
- **Tarefas** como entidade de 1ª classe (estados: todo, doing, blocked, done, attention) associadas a sessões

**Explicitly out of scope:**

- Multiusuário e SaaS / Cloud backend
- OAuth
- Marketplace
- Instalação via NPM
- App Flutter / Android nativo

## Constraints

- **Técnico:** tmux é a única fonte de verdade — toda sessão gerenciada corresponde a uma sessão tmux. Worker obrigatoriamente no host (acesso a tmux/Whisper/Ollama), nunca dentro do Docker.
- **Operacional:** funcionar offline na rede local; baixo consumo de recursos; sem dependência de provedores específicos.
- **Compatibilidade:** macOS e Linux; qualquer navegador moderno.
- **Arquitetura:** extensível para novos tipos de agente.
