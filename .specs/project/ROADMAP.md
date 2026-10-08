# Roadmap

**Current Milestone:** Fase 1 — Núcleo de Operação tmux
**Status:** Planning

---

## Fase 1 — Núcleo de Operação tmux

**Goal:** Operar sessões tmux remotamente pelo dashboard — ver estado ao vivo, criar/encerrar/renomear/retomar, capturar output e enviar input por texto, com histórico persistido. Esta fase já é utilizável no dia a dia.
**Target:** MVP funcional ponta a ponta (Worker host ↔ API/Docker ↔ Frontend).

### Features

**tmux Runtime & Discovery** - ✅ COMPLETE *(RF015, RF016, RF001-RF004)*

- Worker no host (Python): discovery automático de sessões tmux
- Descoberta de sessões criadas fora do SessionFlow
- Criar sessão (nome, tipo, diretório, **modelo, esforço de raciocínio**) + iniciar agente (`claude`/`codex`/`gemini`/`opencode`) com as flags corretas
- **Autocomplete de diretório** (Worker lista pastas do host)
- Encerrar (`kill-session`), renomear, retomar (`attach`)
- Mapear estados: running, waiting_input, waiting_external, completed, error, stopped, detached

**Captura de Output & Input Remoto** - PLANNED *(RF005, RF006)*

- Captura de output do tmux (`capture-pane`) → API → SQLite
- Envio de input por texto: Frontend → API → PendingInput (SQLite) → Worker → `tmux send-keys`

**Persistência & Histórico** - PLANNED *(RF009, RF011, RF013, RF014)*

- Coleções MongoDB: sessions, events, tasks, feedbacks, uploads
- Métricas de token/contexto por sessão + limites diário/semanal por provider
- Consulta de histórico diário e detalhes da sessão
- Visualização de eventos (timeline agrupada por dia)

**Dashboard Mobile-First + SSE** - PLANNED *(RNF001, RNF003, RF012, RF014)*

- Dashboard Angular mobile-first (tema dark do mockup, Prata Digital DS)
- Lista de sessões ativas e históricas + filtros
- Atualização em tempo real via SSE
- Tela de detalhes da sessão
- **Meta de qualidade: nota alta no Lighthouse** (perf/a11y/PWA) — usar skill `lighthouse-ci` quando o front existir

---

## Fase 2 — Áudio, Voz & Notificações

**Goal:** Comunicação por voz e alertas proativos — gravar áudio no mobile, transcrever localmente e injetar no terminal; ser notificado quando o agente pede decisão.

### Features

**Áudio & Transcrição Local** - ⬆ MOVIDO PARA FASE 1 *(RF007, RF008)* — incluído na feature Dashboard+SSE (DASH-14/15) por decisão do usuário

- Upload de áudio (Mobile → API → Storage)
- Worker transcreve via Whisper local (`openai-whisper`) → `tmux send-keys`

**Notificações** - PLANNED *(RF010)*

- Detecção de evento "agente pede decisão" → persiste → push → card no dashboard
- Push notifications

**Classificação Automática** - PLANNED

- Classificação automática de eventos/sessões (via Ollama)

---

## Fase 3 — Inteligência & Organização

**Goal:** Reduzir carga cognitiva com agrupamento, resumos e assistência de resposta.

### Features

- **Potinhos por assunto** - PLANNED — agrupar sessões/eventos por tema
- **Resumos automáticos** - PLANNED — resumo de execuções (Ollama)
- **Sugestões de resposta** - PLANNED — sugerir respostas para decisões pendentes

---

## Fase 4 — Plataforma (fora do MVP)

**Goal:** Transformar de ferramenta local em plataforma.

### Features

- **SaaS / Cloud Backend** - PLANNED
- **Multiusuário** - PLANNED
- **Instalação via NPM** - PLANNED

---

## Future Considerations

- PWA (instalável no mobile)
- Suporte a novos tipos de agente além dos 4 atuais (arquitetura extensível — RNF010)
- App Flutter / Android nativo (atualmente fora de escopo)
