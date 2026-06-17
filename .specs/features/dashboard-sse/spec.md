# Dashboard Mobile-First + SSE — Specification

## Problem Statement

O Worker e a API de ciclo de vida já existem, mas não há interface. O usuário precisa da central operacional mobile-first do mockup (`ui_mock/SessionFlow.dc.html`) — ver sessões ativas/históricas, criar/operar sessões, acompanhar execução em tempo real e responder ao agente — tudo de qualquer navegador, sob `sessionflow.boletoazap.dev.br`.

Esta feature, por decisão do usuário ("Dashboard completo agora" + áudio no MVP), absorve as dependências de backend necessárias para o mockup funcionar: **captura de output do tmux**, **eventos/histórico**, **SSE**, **input de texto**, **áudio + transcrição Whisper** e (com ressalvas) **métricas**.

## Goals

- [ ] App Angular mobile-first fiel ao mockup (tema dark, Prata Digital DS), servido em `:4200`
- [ ] Tempo real via SSE (mudanças de estado de sessão e novas linhas de output) com latência percebida < 2s
- [ ] Operar sessões ponta-a-ponta pela UI (criar/encerrar/renomear/retomar) consumindo a API existente
- [ ] Ver output do terminal ao vivo no Detalhe da sessão
- [ ] Timeline de eventos e notificações funcionais
- [ ] Responder ao agente por **texto e por áudio** (mic → upload → Whisper → texto no terminal)
- [ ] Nota alta no Lighthouse (perf/a11y/PWA) na tela principal

## Out of Scope

| Feature | Reason |
| --- | --- |
| Push FCM | Fase 2 (AD-009) — notificações são in-app via SSE |
| Multiusuário / login próprio | Auth delegada ao túnel (AD-008) |
| Classificação automática / resumos / sugestões (Potinhos) | Fase 3 |
| Métricas que a CLI não expõe de forma confiável | Ver DASH-12 (história de pesquisa) — degrada, não fabrica |

---

## User Stories

### P1: Endpoint SSE de eventos na API ⭐
**User Story**: Como frontend, quero um stream SSE para receber mudanças de estado e novas linhas de output sem polling.
**Why P1**: É a espinha do "tempo real" de todo o dashboard.
**Acceptance Criteria**:
1. WHEN o cliente abre `GET /events` (text/event-stream) THEN a API SHALL transmitir eventos consumidos de `sessionflow.events` (e/ou change streams do Mongo).
2. WHEN a conexão fica ociosa THEN a API SHALL enviar heartbeat (`: ping`) a cada ~15-30s (timeout ~100s do Cloudflare) e setar `Cache-Control: no-cache` + `X-Accel-Buffering: no`.
3. WHEN o cliente reconecta com `Last-Event-ID` THEN a API SHALL retomar de forma sensata (ou reenviar estado atual).
4. WHEN há filtro por sessão (`?session=`) THEN o stream SHALL limitar aos eventos daquela sessão.
**Independent Test**: `curl -N api/events` recebe heartbeats e um evento após criar/encerrar uma sessão.

### P1: Captura de output do tmux (Worker) ⭐
**User Story**: Como sistema, preciso capturar o output dos panes e publicar como eventos/linhas, para alimentar o terminal do Detalhe e refinar estados semânticos.
**Why P1**: Sem isso, o Detalhe não tem terminal e os estados `waiting_input`/`completed` não existem.
**Acceptance Criteria**:
1. WHEN uma sessão está viva THEN o Worker SHALL capturar o conteúdo do pane (`capture-pane -p`) periodicamente e/ou via pipe, detectando linhas novas (diff).
2. WHEN há linhas novas THEN o Worker SHALL persistir (coleção `session_output`/`events`) e publicar em `sessionflow.events`.
3. WHEN o pane indica que o agente pede decisão (heurística por agente) THEN o estado SHALL virar `waiting_input` e gerar notificação.
4. WHEN o processo termina THEN o estado SHALL virar `completed` (sucesso) ou `error`.
**Independent Test**: criar sessão com comando que imprime saída → as linhas aparecem persistidas e publicadas.
> ⚠️ Heurística de `waiting_input`/`completed` por agente precisa ser verificada empiricamente por CLI (não assumir).

### P1: Eventos & histórico (Worker + API) ⭐
**User Story**: Como usuário, quero ver uma timeline de eventos e o histórico, para acompanhar o que aconteceu.
**Acceptance Criteria**:
1. WHEN ocorrem fatos relevantes (criada, concluída, aguardando, encerrada, detached) THEN o Worker SHALL persistir um `event` (coleção `events`) com tipo/sessão/timestamp/descrição.
2. WHEN o cliente chama `GET /events/history?day=` THEN a API SHALL retornar eventos agrupáveis por dia.
3. WHEN o cliente chama `GET /tasks?session=` THEN a API SHALL retornar tarefas (estados todo/doing/blocked/done/attention) se existirem.
**Independent Test**: encerrar uma sessão gera um event consultável no histórico.

### P1: App shell Angular + navegação + tema ⭐
**User Story**: Como usuário no celular, quero o app com a navegação inferior e o visual do mockup.
**Acceptance Criteria**:
1. WHEN abro o app THEN SHALL ver a bottom-nav (Início/Sessões/Timeline/Responder/Perfil) e o tema dark com tokens do Prata Digital DS.
2. WHEN navego entre abas THEN SHALL transicionar sem recarregar (SPA), mobile-first (viewport 390px ok).
3. WHEN a API/Worker está indisponível THEN SHALL mostrar estado de erro/offline gracioso.
**Independent Test**: app sobe em `:4200`, navega entre as 5 abas.

### P1: Tela Início ⭐
**Acceptance Criteria**:
1. WHEN abro Início THEN SHALL ver saudação, contagem de sessões ativas, lista de sessões ativas (status dot/pulse, badge de agente, status line) vindas de `GET /sessions`.
2. WHEN uma sessão muda de estado (SSE) THEN o card SHALL atualizar ao vivo.
3. WHEN toco numa sessão THEN SHALL abrir o Detalhe.

### P1: Tela Sessões (lista + filtros) ⭐
**Acceptance Criteria**:
1. WHEN abro Sessões THEN SHALL ver chips de filtro (Todas/Ativas/Aguardando/Concluídas/Detached) e os cards com agente/dir/status/tempo.
2. WHEN seleciono um filtro THEN a lista SHALL filtrar via `GET /sessions?status=`.
3. WHEN há FAB (+) THEN SHALL abrir o overlay de criação.

### P1: Overlay Criar sessão ⭐
**Acceptance Criteria**:
1. WHEN abro Criar THEN SHALL informar nome, tipo de agente (4 opções), modelo (lista por agente), esforço (Baixo/Médio/Alto/Máximo) e diretório.
2. WHEN o agente é **gemini** THEN o seletor de esforço SHALL ser ocultado/desabilitado.
3. WHEN digito o diretório THEN SHALL autocompletar via `GET /directories?q=`.
4. WHEN confirmo THEN SHALL chamar `POST /sessions` e voltar à lista; a nova sessão aparece (via SSE/refresh).

### P1: Overlay Detalhe da sessão ⭐
**Acceptance Criteria**:
1. WHEN abro o Detalhe THEN SHALL ver nome/dir/agente/status e ações Retomar/Encerrar (chamando os endpoints).
2. WHEN há output THEN SHALL exibir o terminal (linhas coloridas por tipo) e atualizar ao vivo via SSE.
3. WHEN envio comando pelo input THEN SHALL chamar a API de input (texto) — *injeção de input depende do canal de input; ver dependência.*
4. WHEN as métricas estão disponíveis THEN SHALL exibir contexto/tokens/limites; senão, exibir estado “indisponível” (ver DASH-12).

### P2: Notificações
**Acceptance Criteria**:
1. WHEN o agente pede decisão / conclui / detacha THEN SHALL aparecer um card na lista de Notificações com tipo (attention/info/warning/success).
2. WHEN toco numa notificação THEN SHALL abrir a sessão relacionada.
3. WHEN há notificações não lidas THEN o badge no header SHALL refletir a contagem.

### P2: Timeline
**Acceptance Criteria**:
1. WHEN abro Timeline THEN SHALL ver eventos agrupados por dia (Hoje/Ontem/…) de `GET /events/history`.

### P2: Perfil
**Acceptance Criteria**:
1. WHEN abro Perfil THEN SHALL ver status do Worker (conectado/host/uptime), stats (sessões hoje/ativas) e settings (push/SSE/tema/idioma) — push desabilitado (Fase 2).
2. WHEN o Worker está offline THEN o indicador SHALL refletir.

### P1: Responder (texto + áudio) ⭐
**Acceptance Criteria**:
1. WHEN uma sessão está `waiting_input` THEN SHALL aparecer na aba Responder com o pedido do agente e quick-replies (Aprovar/Rejeitar/…).
2. WHEN envio uma resposta de texto THEN SHALL injetar input na sessão (`POST /sessions/{id}/input`).
3. WHEN toco no mic e gravo THEN SHALL enviar o áudio (ver DASH-14) e, após transcrição (DASH-15), o texto SHALL ser injetado na sessão.

### P1: Áudio — gravação no front + upload ⭐
**User Story**: Como usuário no celular, quero gravar um áudio e mandar pro agente, para responder por voz.
**Why P1**: Pedido explícito do usuário — entra no MVP.
**Acceptance Criteria**:
1. WHEN toco no botão de mic THEN o app SHALL gravar áudio via MediaRecorder (com indicador de gravação) e parar ao tocar de novo.
2. WHEN finalizo a gravação THEN o app SHALL fazer upload do blob para `POST /sessions/{id}/audio` (multipart).
3. WHEN o upload conclui THEN a API SHALL armazenar o arquivo, registrar em `uploads` e publicar um comando `audio` para o Worker.
4. WHEN o navegador nega permissão de mic THEN o app SHALL exibir erro claro.
**Independent Test**: gravar um áudio curto → arquivo persiste e comando `audio` é publicado.

### P1: Transcrição Whisper (Worker) ⭐
**User Story**: Como sistema, preciso transcrever o áudio localmente e injetar o texto no terminal.
**Why P1**: Fecha o fluxo de voz do MVP.
**Acceptance Criteria**:
1. WHEN o Worker recebe o comando `audio` THEN SHALL transcrever o arquivo via **openai-whisper** local (módulo Python já instalado; `ffmpeg` presente).
2. WHEN a transcrição conclui THEN o Worker SHALL injetar o texto na sessão (`tmux send-keys`) e publicar um evento com o texto transcrito.
3. WHEN a transcrição falha THEN o Worker SHALL publicar evento de erro e NÃO travar.
4. WHEN o áudio é grande THEN a transcrição SHALL rodar fora do loop crítico (task/async) sem bloquear o consumer.
**Independent Test**: enviar um wav com fala conhecida → texto transcrito aparece injetado e no evento.
> Modelo Whisper (tiny/base/small) a definir no Design conforme custo/latência no host.

### P3: Métricas reais (token/contexto/limites)
**User Story**: Como usuário, quero ver tokens/contexto/limites por sessão e provider.
**Why P3**: Alto valor visual, mas **fonte de dados incerta por CLI** — não fabricar.

---

## Edge Cases
- WHEN o SSE cai (timeout/rede) THEN o cliente SHALL reconectar com backoff.
- WHEN não há sessões THEN as telas SHALL mostrar estado vazio claro.
- WHEN o output é enorme THEN o terminal SHALL limitar/scrollar (ring buffer) sem travar.
- WHEN a sessão é externa (agente desconhecido) THEN a UI SHALL exibir sem quebrar.
- WHEN métricas indisponíveis THEN exibir “—”/“indisponível”, nunca número inventado.

---

## Requirement Traceability

| ID | Story | Phase | Status |
| --- | --- | --- | --- |
| DASH-01 | P1 SSE endpoint (API) | Design | Pending |
| DASH-02 | P1 Captura de output (Worker) | Design | Pending |
| DASH-03 | P1 Eventos & histórico | Design | Pending |
| DASH-04 | P1 App shell + nav + tema | Design | Pending |
| DASH-05 | P1 Tela Início | Design | Pending |
| DASH-06 | P1 Tela Sessões + filtros | Design | Pending |
| DASH-07 | P1 Overlay Criar | Design | Pending |
| DASH-08 | P1 Overlay Detalhe | Design | Pending |
| DASH-09 | P2 Notificações | Design | Pending |
| DASH-10 | P2 Timeline | Design | Pending |
| DASH-11 | P2 Perfil | Design | Pending |
| DASH-12 | P3 Métricas reais (pesquisa) | Design | Pending |
| DASH-13 | P1 Responder (texto + áudio) | Design | Pending |
| DASH-14 | P1 Áudio: gravação front + upload | Design | Pending |
| DASH-15 | P1 Transcrição Whisper (Worker) | Design | Pending |

**Coverage:** 15 total, 0 mapeados a tasks ⚠️

---

## Dependências entre features (importante)
- **DASH-08 (Detalhe terminal)** depende de **DASH-02 (captura de output)**.
- **Responder/Detalhe input** depende de um canal de **injeção de input** (texto) — esta feature inclui o mínimo: `POST /sessions/{id}/input` → comando `input` → Worker `send-keys`.
- **DASH-15 (transcrição)** reaproveita o canal de input (texto transcrito é injetado igual ao texto digitado). Usa `openai-whisper` + `ffmpeg` no host.
- **DASH-12 (métricas)** depende de pesquisa por CLI (claude/codex/gemini/opencode) — pode degradar.

## Success Criteria
- [ ] Usuário cria, vê ao vivo, opera e encerra uma sessão 100% pela UI mobile
- [ ] Terminal do Detalhe atualiza ao vivo via SSE
- [ ] Responder por áudio: gravar no mic → transcrição Whisper → texto injetado na sessão
- [ ] Timeline e Notificações refletem eventos reais
- [ ] Lighthouse com nota alta na tela principal
- [ ] Métricas: ou número real, ou “indisponível” — nunca fabricado
