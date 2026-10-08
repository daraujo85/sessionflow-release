# Protocolo da Fila de Demandas (SessionFlow)

Faz uma sessão de agente ativa (qualquer uma — claude/gemini/codex/opencode)
processar, **uma de cada vez**, a fila de demandas de um projeto: cada
demanda roda isolada (branch+worktree+tmux novos, contexto zerado) e o
progresso fica registrado na própria fila (visível na aba "Fila" da tela de
detalhe da sessão).

## Quando seguir este protocolo

Quando o usuário pedir explicitamente pra "processar a fila" (ou
equivalente) na sessão atual. `base_id` é o id da sessão/projeto onde a fila
foi aberta — normalmente a própria sessão que recebeu o pedido.

## O loop

0. **Resgate de órfãs** (garante que a fila nunca trava mesmo se a sessão
   orquestradora anterior morreu/reiniciou no meio de uma demanda):
   `GET /sessions/{base_id}/demands?status=in_progress`. Para cada item cujo
   `started_at` tenha mais de 30 minutos, ele é órfão — nenhuma sessão está
   mais de fato acompanhando (passo 7 exige a MESMA sessão orquestradora
   viva rechecando periodicamente; se ela caiu, ninguém mais olha pra essa
   demanda, e o status `in_progress` não pode voltar pra `pending` — só
   avança pra `completed`/`error`). Resolva:

   `PATCH /demands/{id}` com
   `{"status": "error", "completed_at": "<agora>", "error_note": "órfã: sem atualização por 30+ min, orquestrador provavelmente interrompido"}`.

   Faça isso ANTES do passo 1, toda vez que iniciar (ou retomar) o
   processamento da fila — inclusive dentro do próprio loop, a cada
   iteração, é seguro repetir (idempotente: só afeta itens ainda
   `in_progress`).

1. `GET /sessions/{base_id}/demands?status=pending` — dentre os itens
   retornados, pegue o de menor `created_at` (o endpoint devolve mais
   recente primeiro; escolha o último da lista, ou ordene por `created_at`
   ascendente antes de escolher).

2. Se não houver nenhum item `pending`: pare e informe o usuário que a fila
   está vazia (ou totalmente processada) — não é erro, é o fim normal do
   loop.

3. **Antes de criar a sessão-filha**, marque a demanda como em execução:

   `PATCH /demands/{id}` com corpo `{"status": "in_progress", "started_at": "<agora, ISO-8601>"}`.

   Isso evita picking duplicado se duas execuções do loop (em sessões
   diferentes) tentarem processar a mesma fila ao mesmo tempo.

4. Crie a sessão-filha isolada:

   `POST /sessions/{base_id}/clone` com corpo `{"name_hint": "<slug curto do texto da demanda>"}`
   (ex.: demanda "revisar PRs abertos e comentar achados" → `name_hint:
   "revisar-prs-abertos"`, algumas palavras já bastam). A resposta é
   `202 {command_id, status: "accepted"}` — aguarde o resultado do comando
   (mecanismo de SSE do próprio SessionFlow; se você não tiver acesso a
   SSE, faça polling em `GET /sessions?status=running` procurando uma
   sessão nova com o `branch`/nome esperado, com timeout curto, ex. 20s)
   até obter `session_id` e `branch` da sessão-filha.

5. Envie o texto da demanda como primeira instrução pra sessão-filha, **precedido
   do preâmbulo de autonomia abaixo** (sem ele, a sessão-filha para em pontos de
   confirmação normais — fechar escopo, commit, push — esperando um humano que
   não está presente, e trava até o timeout):

   ```
   [Fila de demandas — execução autônoma, sem humano presente] Você está numa
   branch/worktree isolada criada só pra esta demanda. Ninguém vai responder
   perguntas nem aprovar passos aqui — decida sozinho e documente a decisão
   (trade-offs, escopo escolhido) nos milestones em vez de perguntar. Não pare
   pedindo confirmação de escopo, de commit ou de push: dentro deste worktree,
   commitar e dar push ao final são esperados e autorizados sem pedir permissão.
   Guardrails de segurança de verdade continuam valendo (nada destrutivo fora
   deste worktree, nada que exija credencial/decisão que só o usuário tem).
   Ao concluir, garanta que os milestones reflitam o estado final (`done`).

   Demanda: <texto da demanda>
   ```

   `POST /{session_id}/input` com corpo `{"text": "<preâmbulo + texto da demanda>", "enter": true}`.

6. Registre a sessão-filha na demanda:

   `PATCH /demands/{id}` com corpo `{"target_session_id": "<session_id>", "branch": "<branch>"}`.

7. Acompanhe periodicamente (ex. a cada 1-2 minutos) `GET /tasks?session=<tmux_name da filha>`:
   - Se existe pelo menos 1 item **e** todos têm `state: "done"` → sucesso, vá pro passo 8.
   - Se passarem 30 minutos desde o `started_at` sem nenhum item aparecer,
     **ou** a sessão-filha caiu pra `status: "error"` (`GET /sessions/{target_session_id}`)
     → falha, vá pro passo 8.

8. Finalize a demanda:
   - **Sucesso:** `PATCH /demands/{id}` com
     `{"status": "completed", "completed_at": "<agora>", "summary": "<títulos dos itens concluídos, concatenados por '; '>"}`.
   - **Falha:** `PATCH /demands/{id}` com
     `{"status": "error", "completed_at": "<agora>", "error_note": "<'timeout sem milestones' ou o erro reportado pela sessão-filha>"}`.

   Uma demanda em `error` **não trava a fila** — ela fica registrada pro
   usuário revisar depois (aba "Fila" mostra o `error_note` em destaque).

9. Volte ao passo 0 (resgatar novas órfãs antes de pegar a próxima
   `pending`). Quando a fila esvaziar, pare e informe ao usuário quantas
   demandas concluíram e quantas deram erro nesta execução do loop.

## Fora de escopo (YAGNI)

Ver `docs/superpowers/specs/2026-08-29-fila-de-demandas-design.md`, seção
"Fora de escopo": sem fila multi-repo, sem cancelar/editar demanda
`in_progress`, sem paralelismo real do orquestrador, sem sinal de conclusão
novo (é sempre milestones).
