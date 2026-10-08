# SessionFlow

Painel web (PWA) pra **acompanhar e controlar sessões de agentes de código** —
Claude Code, Codex, Gemini CLI e OpenCode — rodando em `tmux`, em uma ou várias
máquinas, de qualquer lugar (inclusive do celular).

- Espelho do terminal em tempo real + envio de texto, teclas e anexos
- Criar / retomar / parar sessões, escolher host, diretório, modelo e worktree
- Marcos (milestones) por sessão, notificações, comandos programados
- Delegação entre agentes (`tools/sf`): uma sessão "mãe" cria workers em outros hosts
- Multi-host: Mac, Linux, Windows (WSL2) e até Google Colab, no mesmo painel
- Painel de máquinas (CPU/RAM/GPU) e `sf delegate --host auto` escolhendo o host com mais folga
- Mover sessão entre hosts (auto-clone do diretório no destino)
- Sessão rápida (efêmera, expira em 24h) e resposta rápida direto do card na tela inicial
- Watchdog com auto-healing dos containers

## Arquitetura

```
Frontend (Angular PWA) ──REST/SSE──▶ API (FastAPI) ──▶ MongoDB
                                         │
                                         ▼
                                RabbitMQ (fila por host)
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
               Worker (host A)      Worker (host B)      Worker (Colab)
               tmux + CLIs          tmux + CLIs          tmux + CLIs
```

- **API** é o único ponto de escrita no Mongo e o único publisher de comandos.
- **Worker** roda no host (fora do Docker): consome `sessionflow.commands.<host_id>`,
  opera o `tmux` e publica eventos + heartbeat.
- Detalhes: [`docs/architecture.md`](docs/architecture.md) ·
  portabilidade: [`PORTABILITY.md`](PORTABILITY.md).

| Pasta | Conteúdo |
|---|---|
| `api/` | FastAPI (Python, `uv`) |
| `frontend/` | Angular PWA |
| `worker/` | `sessionflow_worker` (Python, `libtmux`) |
| `voice-stt/`, `voice-tts/` | STT/TTS opcionais (profile `voice`) |
| `docker/` | Configs de Mongo/RabbitMQ |
| `tools/` | CLI `sf`, scripts de worker, auto-update, bootstrap Colab |
| `docs/` | Arquitetura, ADRs, protocolos |

## Requisitos

- Docker + Docker Compose
- Python 3.12+ com [`uv`](https://docs.astral.sh/uv/)
- `tmux`
- Pelo menos um CLI de agente instalado e logado (`claude`, `codex`, `gemini` ou `opencode`)

## Quickstart

```bash
git clone https://github.com/daraujo85/sessionflow-release.git sessionflow
cd sessionflow

cp .env.example .env        # preencha senhas, JWT secret, e-mail e senha de login
docker compose --profile app up -d --build   # mongo + rabbitmq + api + frontend

./tools/start-worker.sh     # sobe o worker neste host (systemd ou tmux)
```

Abra o frontend na porta `FRONTEND_HOST_PORT` do `.env` e entre com
`SESSIONFLOW_EMAIL` / `SESSIONFLOW_PASSWORD`.

Voz (opcional): `docker compose --profile voice up -d`.

## Adicionar outra máquina

Na máquina nova: clone o repo, copie o `.env` (apontando `MONGO_URI_HOST` /
`RABBITMQ_URI_HOST` pro servidor) e rode `./tools/start-worker.sh`. O host
aparece no painel assim que o primeiro heartbeat chega.

## Versões

Notas de cada versão em
[Releases](https://github.com/daraujo85/sessionflow-release/releases).

## Atualização automática

```bash
tmux new-session -d -s sessionflow-autoupdate './tools/self-update-loop.sh'
```

Checa `origin/main` a cada 30 min; se avançou, faz `merge --ff-only`, rebuild dos
containers e restart do worker (só com árvore de trabalho limpa). Ver
[`tools/README.md`](tools/README.md).

## Testes

```bash
cd api && uv run pytest -q        # integração: precisa do Mongo/RabbitMQ no ar (lê o .env)
cd worker && uv run pytest -q
cd frontend && npm test
```

## Segurança

Nunca commite o `.env`. Testes e scripts leem credenciais do ambiente/`.env`;
os valores embutidos no código são apenas placeholders de desenvolvimento.
