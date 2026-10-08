# Design: latência de input e scroll no terminal da sessão

Data: 2026-08-20
Status: aprovado (aguardando plano de implementação)

## Problema

Dois sintomas reportados no componente de terminal da sessão
(`frontend/src/app/features/detalhe/detalhe.component.ts`):

1. **Envio de texto lento**: ao digitar e apertar Enter, o texto demora
   pra aparecer no terminal. Não é instantâneo como um terminal real.
2. **Scroll não fluido**: rolar o histórico do terminal trava, não
   sobe suave.

O toggle de "economia de token" (dedup por SLM local) foi descartado
como causa raiz — não está conectado ao caminho de `send()` hoje.

## Causa raiz

### Envio de texto

`send()` faz `POST /sessions/:id/input` (202, só enfileira) → worker
consome a fila → `command_publisher.py` abre **conexão RabbitMQ nova a
cada comando** (handshake AMQP completo) → `command_consumer.py`
`_type_and_submit` faz `sleep(0.15–1.2s)` antes do Enter, depois **até
2 retries de 1.3s** verificando se a tela mudou → só então o texto é
visível, e mesmo assim só no próximo snapshot do `capture_loop` (a
cada 0.3s). Não há eco local — a UI depende inteiramente do
round-trip remoto pra mostrar o que foi digitado.

Pior caso: ~2.75s de sleeps artificiais no worker, fora rede e
handshake AMQP.

### Scroll

`refreshBurst()` dispara **6 requisições HTTP** (`GET /screen`) a cada
gesto de scroll (250/600/1000/1600/2400ms). Cada resposta reparseia
ANSI da tela inteira e substitui o `innerHTML` completo de um único
`<pre>` (sem virtual scroll, sem xterm.js).

Além disso: **sessões TUI (Claude Code/Codex rodando no pane) não têm
histórico linear persistido**. `session_output` é ring buffer de 2000
linhas; `session_screen` guarda só a tela visível; tmux não mantém
scrollback de alt-screen. O único log realmente completo é um arquivo
bruto de `pipe-pane` no `/tmp`, não exposto por API e cheio de ruído
de redraw ANSI. Por isso "subir" hoje significa mandar um evento de
scroll do mouse pro app remoto e recapturar o frame redesenhado — não
existe uma cópia linear pra "carregar tudo de uma vez".

## Decisões

1. **Eco otimista no envio, via chip separado — não splice no espelho.**
   `screen` (`detalhe.component.ts:4626`) é um screenshot da tela
   remota, substituído inteiro a cada poll — não um log de linhas. Pra
   apps TUI a caixa de input remota pode estar em qualquer posição
   nesse blob de ANSI; colar o texto digitado direto nele arrisca
   aparecer no lugar errado. Em vez disso: ao apertar Enter, mostra um
   chip/balão pequeno perto do composer com o texto enviado e estado
   "pendente", com o `POST /input` disparado em paralelo. Quando a
   tela real (via SSE) chega, o chip some (a tela real já mostra o
   efeito do comando). Se o envio falhar, o chip vira estado de erro
   (cor/ícone + toast) e permanece visível até o usuário dispensar.

2. **Remoção do modo ao vivo.** Com eco otimista como padrão, o toggle
   `liveMode` (forward de tecla-a-tecla pro terminal remoto, hoje
   usado só pra habilitar autocomplete de `/` no CLI remoto) perde a
   razão de existir. Remove-se o botão, o signal `liveMode`, e
   `scheduleForward`/`forwardDiff`/`backspacePane`. O autocomplete de
   `/` é descontinuado (feature separada, se algum dia for retomada).

3. **Reconciliação mais rápida no worker.** `command_publisher.py`
   passa a reusar uma conexão AMQP em vez de abrir uma nova por
   comando. `_type_and_submit` reduz o sleep pré-Enter ao mínimo
   necessário e corta de 2 retries de 1.3s pra 1 retry mais curto
   (~0.4s) — a UI já não depende desse tempo pra dar feedback, só a
   reconciliação do "pendente" fica mais rápida.

4. **Poll de segurança único no scroll.** `refreshBurst()` cai de 6
   requisições pra 1 (~1.5s), SSE como caminho principal de
   sincronização.

5. **Buffer client-side maior.** `BUF_MAX_LINES` sobe de 5000 pra
   30000. Scroll dentro do que já foi capturado nesta sessão de uso é
   100% local (sem rede). Além do buffer (mais raro com esse
   tamanho), mantém o mecanismo atual de pedir scroll ao app remoto —
   sem mudança de schema/captura no backend (fora de escopo: ganho
   marginal, risco maior).

## Fora de escopo

- Carregar histórico completo "de uma vez" para sessões TUI — não
  existe fonte linear persistida pra isso hoje; mudar isso exigiria
  reformular a captura (`output_capture.py`), tratado como projeto à
  parte se necessário no futuro.
- Reformular `session_output`/`session_screen` no Mongo.
- Fila local de comandos com replay/diff (considerada e descartada —
  complexidade não justificada pelo padrão de uso relatado).

## Testes

- Worker: teste de que `_type_and_submit` usa os novos tempos de sleep
  reduzidos (não depende de wall-clock real — usar mock de
  clock/`asyncio.sleep`).
- Worker: teste de que `command_publisher` reusa conexão entre
  publicações sucessivas.
- Frontend: teste de que `send()` adiciona a linha ao buffer local
  antes da resposta HTTP voltar, e que marca erro se o POST falhar.
- Frontend: teste de que `BUF_MAX_LINES` é 30000 e que scroll dentro
  do buffer não dispara requisição de rede.
