# State

**Last Updated:** 2026-06-16
**Current Work:** Feature "Dashboard Mobile-First + SSE" — spec.md criada (DASH-01..15). Escopo "completo" + **áudio no MVP** (mic→upload→Whisper→input). Absorve captura de output + eventos + SSE + input texto/áudio + frontend Angular. Métricas (DASH-12) = pesquisa/incerto (não fabricar). Whisper: `openai-whisper` + ffmpeg confirmados no host. EXECUÇÃO QUASE COMPLETA: Backend D1-D8 ✅, Frontend D9-D21 ✅ (8 telas, build+31 testes verdes), **D22 Lighthouse 100/100/100/100** no app live containerizado ✅. **Stack `--profile app` no ar e validada** (api+front+mongo+rabbit em container; Worker no host). Runner do Worker ✅. **Modelos REAIS do host** ✅ (AD-012). **Permission flags max por default**. Refino de UI (D25): 8 telas fiéis ao mockup + inferência de agente real + work_dir real.

**✅ MVP 100% FUNCIONAL (verificado ao vivo no browser):** listar sessões reais (claude/running) → entrar mostra o **terminal real** (snapshot capture-pane + ANSI/backspace strip) → **digitar comando = eco imediato** + injeção via send-keys → **output ao vivo** (polling incremental do /output a cada 1.5s, robusto; SSE como bônus). Bugs corrigidos no caminho: CSS comment `*/` quebrava tema (L-004), CORS 127.0.0.1≠localhost (L-005), exchange DIRECT vs TOPIC quebrava SSE, id↔tmux_name no /output e filtro do front, captura libera todas as ativas (pipe-pane read-only), robustez a sessões que somem (3 camadas), fila `sessionflow.events` vestigial deletada. Testes: Worker 82 unit + ruff, API 45 (verde sem daemon competindo a fila), Front 33. Lighthouse 100×4.

**Métricas REAIS (DASH-12 resolvido p/ claude):** worker lê `~/.claude/projects/<cwd>/*.jsonl` (campo `message.usage`) → `metrics` no doc da sessão: model, context_used/max/pct (heurística: se used>200k → janela 1M), tokens_in/out reais. Ex planner: Opus 4.8, 21% (208,7k/1M), saída 3M. Limite diário/semanal = "—" (sem fonte local). API `SessionOut` expõe `metrics`; Detalhe mostra real. Ícone do mic corrigido (SVG do mockup). Eco no terminal: texto (`› ...`) e áudio (`🎤 áudio enviado, transcrevendo…`). PWA `SwUpdate` auto-ativa+recarrega em nova versão (fim do cache velho).

**Acesso externo (Cloudflare Tunnel — container `cloudflare`, token tunnel):** front `sessionflow.boletoazap.dev.br` → 200 OK ✅. apiBase do front agora é host-aware (externo → `https://api.sessionflow.boletoazap.dev.br`; local → `<host>:8000`). Conectei o container `cloudflare` à rede `sessionflow_sessionflow_net` (alcança `sessionflow-api:8000`/`sessionflow-frontend:80` por nome). **✅ ACESSO EXTERNO FUNCIONANDO:** front `sessionflow.boletoazap.dev.br` (→:4200) + API `api-sessionflow.boletoazap.dev.br` (→host.docker.internal:8000, DNS CNAME proxied → túnel). 1-nível p/ a API resolve o TLS (Universal SSL `*.boletoazap.dev.br`; 2-níveis `api.sessionflow.*` falhava). apiBase host-aware (externo→api-sessionflow; local→<host>:8000). CORS libera o front. /health 200, /sessions retorna dados via túnel. Dir-scan roots incluem `~/Documents/projects` (autocomplete OK). Criar sessão real testado (codex YOLO em ebd_studio).

**✅ LIMITES % REAIS:** worker raspa o `/usage` do Claude (sessão `sfusage-*` descartável, ~10min, quota-light, teardown seguro) → `host_usage` (doc único: session_pct, session_reset, week_pct, week_reset). discovery anexa em `metrics.limits` p/ sessões claude. Detalhe mostra LIMITE (sessão 5h) e LIMITE SEMANAL com % + barra colorida (threshold) + reset. Prioridade no card: limits > activity (stats-cache) > "—". Ex real: sessão 34%, semana 2%.

**✅ ESPELHO DE TELA AO VIVO (terminal):** agentes TUI (codex/claude) redesenham a tela → log linha-a-linha não mostrava a resposta. Trocado por **mirror**: Worker faz `capture-pane -p` (tela visível, ANSI strip) a cada ~1s → coleção `session_screen` (upsert), API `GET /sessions/{id}/screen`, Detalhe renderiza `<pre>` substituindo a cada 1.2s + auto-scroll. Mostra a tela real do agente ao vivo (ex: `/context` do claude). Resolve "não vejo output depois do input". Sem isso, acesso remoto (celular) não busca dados; na máquina do dono funciona via localhost. CORS já libera o subdomínio do front.

**Nota operacional:** o daemon do Worker rodando consome `sessionflow.commands` → faz testes de integração do worker e os de publish/consume da API "falharem" por roubar a msg. Pare o daemon antes de rodar essas suítes.

---

## Recent Decisions (Last 60 days)

### AD-022: Tarefas reais via "marcos" do agente (arquivo no projeto) (2026-06-17)

**Decision:** A seção "Tarefas" da Home (antes decorativa) passa a refletir **marcos** que o agente mantém em `<work_dir>/.sessionflow/milestones.json` (`{milestones:[{id,title,status}]}`, status todo|doing|blocked|done). O **worker** (`milestones.py` + `milestones_loop`, 6s) lê o arquivo das sessões ativas e faz upsert na coleção `tasks` (source="milestone"), só bump de `updated_at` na mudança + poda dos removidos. API `GET /tasks` e a Home já renderizavam (ícones/labels por estado) — só faltava a fonte. Protocolo + instalação por CLI em `docs/milestones-protocol.md`.
**Reason:** Dar sentido real à seção e mostrar progresso/épicos por sessão e no geral. Arquivo escolhido (vs comando/output-marker): fonte da verdade, robusto, multi-CLI, reusa `tasks`/work_dir já existentes.
**Trade-off:** Depende do agente seguir a instrução (instrução GLOBAL no config do CLI é o caminho confiável; skill é sob demanda, hook não raciocina). Auto-injeção da instrução ao criar a sessão é alternativa zero-setup porém menos confiável (sai do contexto). Rótulo "Marcos" descartado ("parece nome de pessoa") → fica "Tarefas".
**Impact:** Worker escreve `tasks` (antes ninguém escrevia). Coleção `tasks` com `source`/`milestone_id`.

### AD-021: Web Push (VAPID) — notificação com o app fechado (2026-06-17) — implementa a Fase 2 de AD-014

**Decision:** Web Push API + **VAPID** (sem Firebase). Par de chaves gerado via py-vapid → `.env` (`SESSIONFLOW_VAPID_PUBLIC/PRIVATE/SUBJECT`). **API**: `GET /push/vapid` (chave pública), `POST /push/subscribe` + `/unsubscribe` (coleção `push_subscriptions`, dedupe por endpoint). **Frontend**: `NotifyService.enablePush()` usa `SwPush.requestSubscription({serverPublicKey})` ao conceder permissão e no boot (se já concedida) → registra a sub. O **ngsw** (service worker do Angular) exibe a notificação do push automaticamente (payload `{notification:{...}}`). **Worker**: `push_sender.send_to_all()` (pywebpush, chave privada lazy do env, roda em executor, poda subs 404/410) é chamado no `_maybe_notify_attention` junto do evento de atenção (waiting/idle), com link `/sessao/<tmux_name>`.
**Reason:** SSE/client-Notification só dispara com o app vivo; este caminho entrega com o app FECHADO (push service do navegador acorda o SW). Usuário optou por VAPID puro (mecanismo é o mesmo Push API; sem projeto/SDK Google).
**Trade-off:** `SwPush` exige SW ativo (prod/HTTPS ou localhost) — ok no túnel. iOS: só em PWA instalado. Desktop com navegador 100% morto depende da política de background do Chrome. Verificado: endpoints (201/422/200), worker lê a privada (PEM) e serve a pública; entrega real ao device é teste do usuário.
**Impact:** Fecha a história de notificação. Coleção `push_subscriptions`; deps worker: pywebpush/py-vapid/cryptography.

### AD-020: Responsividade desktop/tablet (mobile-first preservado) (2026-06-17)

**Decision:** Breakpoints em `app.css` (sem tocar no mobile): **tablet (≥768)** = coluna 760px + bottom-nav centralizada + listas em grid 2-col; **desktop (≥1024)** = bottom-nav vira **sidebar lateral esquerda** (220px, brand "SessionFlow" via `::before`, itens em linha), conteúdo `width:100%; max-width:1120px; margin:auto` na área à direita da sidebar, grids 3-col, FAB acompanha a borda do conteúdo. Listas viram grid `repeat(auto-fill, minmax(320px,1fr))` (sessoes/inicio); `.sf-sessoes` solta o `max-width:720` no desktop. Perfil/timeline/responder mantêm coluna ~720 centralizada (leitura).
**Reason:** Usuário pediu aproveitar telas largas em vez da coluna de 480px no meio.
**Trade-off:** `margin:0 auto` num flex item ENCOLHE ao conteúdo → precisou `width:100%` no `.sf-content` (lição). Componentes com `max-width` próprio precisam de override por breakpoint.
**Impact:** Desktop com sidebar + grids; tablet 2-col; mobile idêntico. Adicionado botão "Testar notificação" no Perfil (diagnóstico do push do sistema).

### AD-019: Push do espelho via SSE (latência) — substitui o poll de 1,2s (2026-06-17)

**Decision:** O worker EMPURRA o espelho da tela pelo SSE (frame `kind:"screen"` com `tmux_name/text/at`, routing key `sessionflow.events`) dentro de `snapshot_screen`, com **dedupe por hash** (só quando a tela muda). Frontend: `SseService.screens` (último por `tmux_name`); o detalhe aplica via `effect` o frame da sua sessão. O poll de GET /screen virou **fallback lento (4s)** caso o SSE caia; mantém o `refreshScreen` imediato após input/key. App/detalhe conectam o SSE (idempotente).
**Reason:** Reduz a latência percebida "enviei → vi refletir": antes esperava até 1,2s pelo poll; agora chega ~instantâneo após a captura (cadência 1s).
**Trade-off:** O worker faz broadcast do espelho de TODAS as sessões ativas a todos os clientes (sem filtro por sessão na conexão app-wide) — ~poucos KB/s p/ uso pessoal, aceitável. Piso de latência = `CAPTURE_INTERVAL` (1s); dá p/ baixar se quiser mais snappy (custa CPU).
**Impact:** Espelho quase em tempo real. Novo frame SSE `kind:"screen"` reaproveitável.

### AD-018: Modo "ao vivo" — autocomplete inline encaminhando teclas ao pane (2026-06-17)

**Decision:** Toggle no input do detalhe. Ligado, cada mudança do texto é encaminhada ao pane EM TEMPO REAL (debounce 130ms) via diff: prefixo comum → apaga o sufixo divergente com Backspace (`/key`) e digita o novo trecho como texto SEM Enter (`/input` com `enter:false`). Assim o CLI mostra o autocomplete dele no espelho. Enviar = só Enter (`/key enter`); navegar = keypad ↑↓. Desligar limpa no pane o que foi digitado (Backspace) p/ não duplicar no modo lote. Desligado (default) = modo lote (compõe local, envia de uma vez — melhor p/ texto longo/latência).
**Reason:** Experiência de terminal real (ver sugestões de `/ $ @` enquanto digita) sem parsing frágil por CLI — quem renderiza o autocomplete é o próprio CLI, no espelho.
**Trade-off:** Mais requests no modo ao vivo (1 por pausa de digitação); pode divergir se o CLI auto-editar a linha (aceita-se; Esc/Backspace recupera). Por isso é opt-in. Worker: `_send_keys` agora `literal=True` (texto cru) + flag `enter`; API `SessionInput.enter` (não strip quando `enter=False`).
**Impact:** `/input` aceita `enter`; novo primitivo "texto sem Enter" reaproveitável.

### AD-017: Transcrição de áudio via Parakeet (MLX), substitui o Whisper (2026-06-17)

**Decision:** `transcriber.py` usa **`parakeet-mlx`** (`mlx-community/parakeet-tdt-0.6b-v3`), nativo Apple Silicon (host é MacBook Air arm64). Removidos `openai-whisper` + torch/sympy/networkx/tiktoken das deps e o cache `~/.cache/whisper` (672 MB). API pública `transcribe(path)` inalterada (cache global do modelo, run_in_executor). Aceita webm direto (ffmpeg).
**Reason:** Usuário relatou Whisper impreciso; Parakeet (mesmo modelo do app Range) transcreveu PT-BR com pontuação perfeita em ~2-4s. MLX dispensa CUDA/torch → muito mais leve.
**Trade-off:** Específico de Apple Silicon (MLX); outro host precisaria de outro backend. Modelo baixa ~600 MB do HF no 1º uso (cacheado em `~/.cache/huggingface`); 1ª transcrição após boot carrega do cache (~3-4s).
**Impact:** Áudio melhor e projeto mais enxuto. `transcribe(..., model_name=...)` aceita repo HF; default = Parakeet v3.

### AD-016: Notificações de atenção — detecção no worker + Web Notifications (2026-06-17)

**Decision:** Worker detecta, na sessão ativa, (a) **aguardando decisão** (footer de picker "to select"/"to navigate"/"esc to cancel" ou y/n, ou pergunta nas 3 últimas linhas via `detect_waiting`) → status `waiting_input` + evento `type=attention kind=attention`; (b) **ocioso/terminou** (tela parada ≥ `IDLE_SECONDS=12s` após atividade) → `type=attention kind=success`. Emite só na TRANSIÇÃO (in-memory por sessão), pula sessões internas `sfusage-/sfmodel-`. Frontend: `NotifyService` (Notifications API + `registration.showNotification` via ngsw, `onActionClick` abre a sessão), `SseService` dispara a notificação do sistema ao chegar o evento (dedupe por id), toggle "Notificações" no Perfil pede permissão, badge no Início conta sessões `waiting_input`. App conecta SSE app-wide quando autenticado (`connect()` idempotente).
**Reason:** Avisar "terminou / precisa de você" mesmo sem o usuário olhar a sessão. Notifications API + SW cobre app aberto E em segundo plano SEM Firebase; só "navegador 100% morto" exige FCM (AD-014, Fase 2).
**Trade-off:** Heurística de "waiting/idle" é PROVISÓRIA por CLI (calibrada p/ evitar falso-positivo do prompt pronto do Claude — `❯` foi removido dos marcadores). `waiting_input` é transitório (setado no ciclo do discovery).
**Impact:** `detect_waiting`/`waiting_input`, antes mortos, agora vivos. EVENT_TYPES ganhou `attention`.

### AD-015: Teclado de controle TUI — comando `key` (2026-06-17)

**Decision:** Além de `input` (texto literal + Enter), há o comando `key` que envia UMA tecla nomeada do tmux (`Up/Down/Left/Right/Enter/Space/Escape/Tab/BSpace/C-c`) SEM Enter (`send_keys(literal=False, enter=False)`). API: `POST /sessions/{id}/key {key}` com allowlist (422 fora dela). Frontend: keypad (↑↓←→ Espaço Enter Esc) acima da barra de input no detalhe.
**Reason:** Agentes TUI (picker de `/model`, listas de seleção multi-escolha, confirmações) exigem setas/espaço/enter — impossível digitar no celular. Solução genérica (vale p/ qualquer picker), não parsing frágil por CLI.
**Trade-off:** Conjunto de teclas fechado (sem combinações arbitrárias) — cobre navegação, não terminal completo.
**Impact:** Espelho da tela vira interativo. Verificado: cada tecla entrega o escape correto no pane (`Down→^[[B` etc).

### AD-001: tmux como única fonte de verdade (2026-06-16)

**Decision:** Toda sessão gerenciada pelo SessionFlow corresponde 1:1 a uma sessão tmux; o estado real é sempre lido do tmux, não do banco.
**Reason:** Evita divergência entre estado persistido e realidade do terminal; permite descobrir sessões criadas fora do SessionFlow.
**Trade-off:** SQLite vira cache/histórico, não autoridade — exige discovery contínuo no Worker.
**Impact:** Worker precisa rodar polling/discovery do tmux; status de sessão sempre reconciliado com `tmux`.

### AD-002: Worker roda no host, não no Docker (2026-06-16)

**Decision:** `sessionflow-worker` executa diretamente no Mac/host. API, Frontend e SQLite ficam no Docker.
**Reason:** Worker precisa de acesso direto a tmux, Whisper e Ollama do host.
**Trade-off:** Setup não é 100% containerizado; deploy envolve processo no host + containers.
**Impact:** Comunicação Worker↔API atravessa fronteira host↔Docker (definir protocolo na fase de design).

### AD-003: Stack revisada — Angular / FastAPI / MongoDB / RabbitMQ (2026-06-16)

**Decision:** Frontend **Angular**; API **Python + FastAPI** (gerenciada com `uv`); persistência **MongoDB**; fila **RabbitMQ**; tempo real via SSE (API→front). **SQLite descartado** (era ruído da spec original — nenhuma necessidade de DB local).
**Reason:** Preferência do usuário; substitui o stack original React+Vite/Node+Fastify/SQLite.
**Trade-off:** Worker em Python precisa de mecanismo de IPC com tmux (subprocess) e cliente Mongo/Rabbit; sem cache local.
**Impact:** Supersede a stack original. Base para todas as features.

### AD-004: Stack Docker dedicada e 100% local (2026-06-16) — SUPERSEDE decisão anterior de infra remota

**Decision:** Subir uma **stack própria do SessionFlow** (`docker-compose.yml`, projeto `sessionflow`) com **Mongo + RabbitMQ + API + Frontend**. NÃO reusar a infra remota (5.78.115.249). Custo medido era baixo: ~1.1 GB de imagens + ~210 MB RAM. Worker continua no host (AD-002). Portas publicadas só em `127.0.0.1`. Credenciais novas e fortes (não as fracas do remoto). Usuário Mongo de aplicação `sessionflow` escopado ao DB `sessionflow` (criado por `docker/mongo-init.js`).
**Reason:** Elimina o túnel SSH do Mongo (B-001), tira a dependência de rede/disponibilidade remota e o isolamento forçado da infra compartilhada (L-001). Simplicidade > os ~210 MB de RAM.
**Trade-off:** ~1.1 GB de disco + ~210 MB RAM na máquina; duplica infra que já existia remota.
**Impact:** API conecta via nomes de serviço (`mongo`/`rabbitmq`); Worker (host) via `127.0.0.1`. Data stack já validada e no ar (mongo+rabbit healthy, auth OK). API/Frontend sob profile `app`.

### AD-005: RabbitMQ como transporte Worker↔API (2026-06-16)

**Decision:** Usar RabbitMQ como transporte dos fluxos de input/output entre Worker (host) e API. Output: Worker → fila → API → MongoDB + SSE. Input: Frontend → API → fila → Worker → `tmux send-keys`.
**Reason:** Já há RabbitMQ disponível; desacopla Worker (host) de API e resolve a fronteira de comunicação cross-process.
**Trade-off:** Mais uma peça de infra no caminho crítico; ordering/at-least-once a tratar.
**Impact:** Substitui o TODO de "protocolo Worker↔API". Definir nomes de filas/exchanges no Design.

### AD-006: Acesso externo via Cloudflare Tunnel (2026-06-16)

**Decision:** Expor a máquina local (front Angular + API FastAPI) ao mobile/browser externo via **Cloudflare Tunnel**, sob **subdomínios em `boletoazap.dev.br`** (ex.: `sessionflow.boletoazap.dev.br`, possivelmente `api.sessionflow.boletoazap.dev.br` para a API/SSE).
**Reason:** Permite alcançar a máquina local sem abrir portas/IP fixo; domínio já gerenciado no Cloudflare.
**Trade-off:** Dependência do Cloudflare; SSE precisa funcionar através do túnel.
**Impact:** Validar SSE e uploads de áudio através do túnel; configurar `cloudflared`; definir split de subdomínios front/API e CORS.

### AD-008: Deploy — Docker local p/ API+Front, Worker no host, túnel via container existente (2026-06-16)

**Decision:** API (FastAPI) + Frontend (Angular) + Mongo + RabbitMQ rodam na **stack Docker local** (`docker-compose.yml`, profile `app` p/ api+front). Worker continua direto no host (tmux/Whisper/Ollama). O acesso externo usa um **container de túnel Cloudflare já existente** de vocês, que roteia `*.boletoazap.dev.br` até a máquina; **autenticação delegada à camada de túnel**.
**Reason:** Preferência do usuário (tudo num stack só); reaproveita infra de túnel já em operação.
**Trade-off:** Worker (host) ↔ API (container) atravessam fronteira — resolvido via RabbitMQ local (AD-005), Worker conecta em `127.0.0.1:5672`.
**Impact:** App não implementa login próprio no MVP. Confirmar detalhes da camada de auth do túnel existente. Falta criar Dockerfiles de `api/` e `frontend/` para o profile `app` subir.

### AD-013: Autenticação (email+senha → JWT) + biometria WebAuthn (2026-06-17)

**Decision:** Auth single-user no app (substitui o "delegar ao túnel" do AD-008, que deixava a API pública — C-SEC-01). API: middleware exige JWT (HS256) em TODAS as rotas exceto `/health`, `/auth/*`, OPTIONS; token via `Authorization: Bearer` OU `?token=` (p/ SSE). `POST /auth/login {email,password}` valida contra `SESSIONFLOW_EMAIL`/`SESSIONFLOW_PASSWORD` (.env) → JWT (7d). **Biometria = WebAuthn/passkey** (py_webauthn + @simplewebauthn/browser): após login c/ senha, registra credencial (rp_id=`sessionflow.boletoazap.dev.br`); login com Face ID/Touch ID no celular (domínio externo). Front: login screen, authGuard em todas as features, interceptor Bearer (401→/login), SSE token na query. Deps: `pyjwt`, `webauthn` (api); `@simplewebauthn/browser` (front). Verificado: 401 sem token, login→JWT→app, guard redireciona.
**Trade-off:** Biometria só no domínio externo (rp_id); em localhost só senha. Middleware é no-op se `SESSIONFLOW_EMAIL` vazio (não quebra testes de integração sem token).
**⚠️ AÇÃO:** trocar `SESSIONFLOW_PASSWORD` (hoje placeholder `trocar-esta-senha`) no `.env` + restart api.

### AD-012: Descoberta de modelos REAIS do host (2026-06-17)

**Decision:** Modelos no Criar vêm do host, não hardcoded. Captura por CLI: **opencode** lê `~/.config/opencode/opencode.json` (`provider.models`); **claude/gemini/codex** via scrape do seletor `/model` numa sessão tmux descartável `sfmodel-*` (`/model` não gasta quota — é só o picker), com fallback ao modelo configurado. Persiste em coleção `host_models`; API expõe `GET /models?agent=`; frontend Criar consome dinâmico.
**Reason:** Pedido do usuário ("modelos reais do host, sem inventar"). Provado que o claude `/model` é parseável via capture-pane (Opus 4.8/Sonnet 4.6/Haiku 4.5 reais da conta).
**Trade-off:** Scrape de TUI é frágil por-CLI (ANSI/layout); spawn de sessão descartável ~10-20s por CLI. **Estratégia: cache em `host_models` + rotina diária (1×/dia)** — não re-scrapear a cada boot/30min (decisão do usuário). gemini: o `/model` tem 2 passos (Auto/Manual); a lista real (gemini-3.1-pro-preview, 3-flash-preview, 2.5-pro, 3.1-flash-lite, 2.5-flash, gemma-4-31b-it, gemma-4-26b-a4b-it) só aparece após escolher **Manual**.
**Impact:** Worker ganha `model_discovery.py`; runner dispara no boot + periódico; segurança: só sessões `sfmodel-*`, teardown garantido, nunca tocar sessões reais.

### AD-011: Mapa de acesso externo (subdomínios ↔ portas) (2026-06-16)

**Decision:** Túneis Cloudflare já configurados pelo usuário:
- `sessionflow.boletoazap.dev.br` → `localhost:4200` (Frontend Angular)
- `api.sessionflow.boletoazap.dev.br` → `localhost:8000` (API FastAPI + SSE)
Mongo/Rabbit/management NÃO expostos. Portas batem com o `.env` (4200/8000) — sem mudança.
**Reason:** Acesso remoto pelo celular/browser sob domínio já gerenciado.
**Trade-off:** 4200/8000 são defaults de `ng serve`/`uvicorn` — risco se rodar outro projeto igual em paralelo (aceito).
**Impact:**
- **CORS** na API deve permitir origin `https://sessionflow.boletoazap.dev.br`.
- **SSE através do túnel**: API precisa enviar `Cache-Control: no-cache` + `X-Accel-Buffering: no` e **heartbeat** (`: ping`) a cada ~15-30s (timeout ~100s do Cloudflare). Amarrar no design da feature de SSE.

### AD-010: Isolamento na infra remota compartilhada (2026-06-16)

**Decision:** O MongoDB (já tem DB `tripflow`) e o RabbitMQ são compartilhados com `tripflow`/`ebd-studio`. SessionFlow usa **DB dedicado `sessionflow`** no Mongo e **filas/exchange com prefixo `sessionflow.*`** no RabbitMQ (idealmente um **vhost dedicado `sessionflow`**).
**Reason:** Evitar colisão de coleções/filas com os outros projetos que dividem a mesma infra.
**Trade-off:** Não há isolamento físico — uma falha/lotação na infra afeta todos os projetos.
**Impact:** Definir nomes de coleções com escopo no DB `sessionflow`; nomear filas `sessionflow.input`/`sessionflow.output` (ou vhost próprio) no Design.

### AD-014: Push fica no Firebase FCM (2026-06-17) — confirma AD-007/AD-009; descarta a alternativa Web Push/VAPID

**Decision:** O push da Fase 2 usa **Firebase Cloud Messaging (FCM)**, como no plano original. A alternativa Web Push API + VAPID foi avaliada e **descartada** pelo usuário.
**Reason:** Na prática, o comportamento desejado (entrega confiável com o navegador encerrado / always-on) não acontece com Web Push pura — no desktop, navegador com processo morto só recebe ao reabrir. O FCM (com app/serviço nativo por trás) cobre esse caso; o usuário validou que "não rola como quero sem ele".
**Trade-off:** Dependência do Firebase/Google (projeto, SDK, `google-services`/credenciais de servidor). **iOS** continua exigindo PWA instalado p/ web push — daí o botão "Instalar como app" segue útil.
**Impact:** Fase 2 = projeto Firebase + service worker `firebase-messaging-sw.js` + registro de token FCM no gateway + envio via Admin SDK. Mantém o roadmap original.

### AD-009: Push adiado para Fase 2 (2026-06-16)

**Decision:** MVP entrega notificações **in-app** (card/badge/lista via SSE). Push real (Firebase FCM, ver AD-014) fica na **Fase 2**.
**Reason:** Simplifica o MVP; push exige service worker + subscrição.
**Trade-off:** Sem alerta com app fechado no MVP.
**Impact:** UC03 no MVP = detecção + persistência + card in-app; entrega push entra na Fase 2.

### AD-007: Escopo MVP ampliado pelo mockup (2026-06-16)

**Decision:** Incluir no MVP (confirmado pelo usuário): métricas de token/contexto, limites diário/semanal por provider, seleção de modelo + esforço de raciocínio na criação, autocomplete de diretório do host. Push via **Firebase FCM**.
**Reason:** Presentes no mockup `ui_mock/SessionFlow.dc.html` e confirmados como MVP.
**Trade-off:** Mais trabalho de backend (parsear output dos agentes p/ tokens, rastrear quotas por provider).
**Impact:** Vão além dos RF001–RF020 originais — adicionar requisitos novos na spec da feature.

---

## Active Blockers

### B-001: ~~MongoDB remoto não é alcançável de fora~~ — RESOLVIDO (2026-06-16)

**Resolução:** Eliminado por AD-004 — o SessionFlow passou a usar Mongo local na própria stack, então não há mais túnel SSH nem porta não publicada. Mantido aqui só como registro.

---

## Lessons Learned

### L-001: Credenciais fracas/compartilhadas na infra remota

**Context:** Inspeção dos containers em 5.78.115.249 (2026-06-16) para reuso de Mongo/RabbitMQ.
**Problem:** O root do Mongo (`tripflow_admin`) usa senha placeholder literal `troque_esta_senha_em_producao`; é o root compartilhado. RabbitMQ exposto publicamente (5672/15672) — credencial OK, mas painel de management aberto na internet.
**Solution:** Para o SessionFlow, criar **usuário Mongo dedicado** escopado só ao DB `sessionflow` (não usar o root). Manter Mongo sem exposição pública (B-001). Revisar exposição do management do RabbitMQ.
**Prevents:** Vazamento amplo caso uma credencial seja comprometida; blast radius limitado ao DB do SessionFlow.

---

### L-002: Usuário de app do Mongo é escopado ao DB `sessionflow` — testes não criam DB próprio

**Context:** T2 (cliente Mongo) tentou usar um DB `sessionflow_test` nos testes de integração.
**Problem:** O usuário `sessionflow` tem `readWrite` só no DB `sessionflow` (L-001/AD-004) → criar/usar outro DB dá `Unauthorized` (code 13).
**Solution:** Testes de integração usam **coleções isoladas dentro do DB `sessionflow`** (nome único por execução, ex: `sessions_test_<uuid>`) com drop no teardown. Funções de persistência aceitam `collection`/`db_name` injetáveis. Vale para Worker e API.
**Prevents:** Falha de autorização nos testes de integração das tasks T8/T9/T10/T12-T16.

### L-003: Índice parcial do Mongo não aceita `$ne`

**Context:** índice único parcial de `tmux_name` para sessões ativas.
**Problem:** `partialFilterExpression: {status: {$ne: "stopped"}}` → `CannotCreateIndex: Expression not supported`.
**Solution:** enumerar status ativos com `$in` (constante `ACTIVE_STATUSES` derivada do enum `SessionState`). `mongo.py` passou a depender de `state.py`.
**Prevents:** Erro de criação de índice.

### L-004: `*/` dentro de comentário CSS quebrou todo o tema

**Context:** `frontend/src/styles.css` tinha um comentário com o caminho `prata-digital-design-system-*/tokens/*.css`.
**Problem:** A sequência `-*/` fecha o comentário CSS prematuramente e `/*.css` reabre outro, deixando a palavra `tokens` solta antes de `:root` → vira seletor `tokens :root{}` (inválido) → NENHUMA CSS variable é definida → app renderiza sem tema (serif/branco). Build/Lighthouse não pegam (CSS "válido" sintaticamente).
**Solution:** Remover qualquer `*/` (e `/*`) de dentro de comentários CSS. Verificado com Playwright: body bg `#0E1113`, fonte Inter.
**Prevents:** Tema global silenciosamente quebrado. Também: PWA service worker cacheia o CSS — exige hard-refresh após corrigir.

### L-008: Service Worker (ngsw) defasado servia build antigo (2026-06-17)

O ngsw só reagia a `VERSION_READY` quando *por acaso* detectava nova versão — nunca checava proativamente. No PWA instalado isso prendia o usuário num build velho (favicon antigo, toggle de notificação "sumido", sem opção de instalar). Fix em `app.ts::setupServiceWorker`: `checkForUpdate()` no boot + a cada 5 min + a CADA `visibilitychange` visível (reabrir a aba/PWA é o gatilho pós-deploy), `VERSION_READY`→`activateUpdate`+reload, e `unrecoverable`→reload. **Para escapar de um estado já preso:** uma vez, desinstalar o PWA + limpar dados do site no Chrome + reabrir (instala SW novo); daí em diante atualiza sozinho. Ícone do PWA (WebAPK) e favicon da aba são caches À PARTE — só atualizam com reinstalar/limpar.

### L-007: Áudio quebrou — path de upload do container ≠ host (2026-06-17)

A API (Docker) grava uploads em `/data/uploads/<sid>/<file>` (path do CONTAINER) e publica esse path no comando `audio`. O Worker roda no HOST, onde `/data/uploads` não existe (o volume mapeia p/ `<repo>/data/uploads`) → `FileNotFoundError` → áudio nunca transcrito/injetado, sem log de erro visível. Fix: `_resolve_upload_path` no worker re-rooteia os 2 últimos componentes (`<sid>/<file>`) em `HOST_UPLOADS_DIR` (`<repo>/data/uploads`, override `SESSIONFLOW_UPLOADS_DIR_HOST`) quando o path recebido não existe. **Regra geral:** qualquer path trocado entre container e host precisa de tradução — nunca assumir o mesmo FS.

### L-006: Espelho do terminal sem cor — `capture-pane -p` descarta SGR; usar `-e` (2026-06-17)

`capture-pane -p` + `strip_ansi` removia TODAS as cores → espelho monocromático, diferente do terminal real do Mac. Fix: capturar com `capture-pane -e -p` (preserva SGR) e `clean_screen_keep_color()` (protege os SGR com sentinelas U+E000/U+E001, aplica o strip normal, restaura) no worker; no frontend `ansiToHtml()` traduz SGR→`<span style>` (16 cores + 256 + truecolor) renderizado com `[innerHTML]` + `bypassSecurityTrustHtml` (texto sempre escapado antes). **Cuidado:** o sentinela NÃO pode ser dígito puro — o regex de restauração casaria números reais ("29%"); usar U+E000/E001 (sobrevivem ao strip, não colidem com texto).

### L-005: CORS `127.0.0.1` ≠ `localhost` quebrava TODAS as chamadas do browser

**Context:** App acessado em `http://127.0.0.1:4200`, mas a API só liberava `http://localhost:4200` no CORS.
**Problem:** Origens `127.0.0.1` e `localhost` são DIFERENTES p/ CORS → preflight 400, toda chamada do browser falhava silenciosamente → telas mostravam estado vazio/fallback (ex: Criar caía no campo-livre de modelo; Início mostrava "0 sessões"). Não aparece em testes (httpx não faz CORS) nem em curl.
**Solution:** Adicionar `http://127.0.0.1:4200` em `cors_origins` (config.py). Idealmente o front deveria chamar a API no mesmo host de onde é servido.
**Prevents:** App "sem dados" no browser apesar de backend OK.

## Quick Tasks Completed

| #   | Description | Date | Commit | Status |
| --- | ----------- | ---- | ------ | ------ |
| 001 | Stack Docker dedicada (Mongo+Rabbit) criada, no ar e validada (auth OK) | 2026-06-16 | — | ✅ Done |
| 002 | Feature tmux Runtime & Discovery (T1..T16) implementada via sub-agentes | 2026-06-16 | — | ✅ Done |

---

## Deferred Ideas

- [ ] PWA instalável — Captured during: inicialização (escopo "PWA futuro")
- [ ] Suporte a novos tipos de agente além dos 4 atuais — Captured during: inicialização (RNF010)

---

## Todos

- [ ] Confirmar detalhes da camada de auth do container de túnel existente (Cloudflare Access? como a API valida que a request veio do túnel?)
- [ ] Design: definir nomes de filas/exchanges RabbitMQ (input/output) e estratégia de entrega (at-least-once, ordering)
- [ ] Design: como o container da API alcança RabbitMQ/Mongo remotos + como o túnel encaminha à API (subdomínios front/API, CORS, SSE através do túnel)
- [x] Coletar credenciais/endpoints das instâncias remotas (MongoDB, RabbitMQ) — feito, guardado em `.env` (gitignored). Ver B-001/AD-010/L-001.
- [x] ~~Design: túnel do Mongo + usuário Mongo dedicado~~ — resolvido por AD-004 (stack local; usuário `sessionflow` criado via mongo-init.js)
- [ ] Criar Dockerfiles de `api/` (FastAPI) e `frontend/` (Angular) para o profile `app` da stack subir
- [ ] Fase 2: config do Firebase (FCM) para push

---

## Preferences

**Model Guidance Shown:** never
