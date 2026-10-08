# Fila de Demandas (orquestrador) — Design

**Contexto:** hoje, pra rodar várias tarefas independentes no SessionFlow enquanto
o usuário está ausente, ele precisa disparar tudo de uma vez ou ficar clonando
sessões manualmente. O usuário quer digitar uma lista de demandas simples numa
sessão/projeto, e que uma sessão de agente ativa processe uma por vez —
síncrona, cada demanda numa branch+worktree isolada e com contexto zerado (sem
herdar histórico de nenhuma outra), pra depois poder voltar em qualquer branch
e ver a documentação do que foi feito sem misturar temas.

## Decisões

- **Escopo:** a fila pertence a UM projeto/sessão-base por vez (a `base_session_id`
  já define repo e host). Não há fila multi-repo.
- **Orquestrador:** não é um serviço novo rodando sozinho. É uma sessão de
  agente já ativa (qualquer uma — claude/gemini/codex/opencode), instruída pelo
  usuário a processar a fila daquele projeto. A lógica do loop vira uma
  skill/instrução documentada, não backend autônomo.
- **Isolamento:** cada demanda sempre cria worktree + branch novos (reaproveita
  o comando `clone` já existente), com sessão tmux nova — nunca reaproveita
  contexto de nenhuma sessão anterior.
- **Sinal de conclusão:** reaproveita o mecanismo de milestones já existente
  (`.sessionflow/milestones.<sessão>.json`, espelhado em `GET /tasks?session=`).
  Demanda é considerada concluída quando a sessão-filha tem pelo menos 1
  milestone e todos estão `done`.
- **Erro:** se uma demanda falha (timeout de 30min sem nenhum milestone
  aparecer, ou a sessão-filha entra em estado de erro), fica registrada como
  `error` na fila — a fila **não trava**, segue pra próxima demanda pendente.
- **Concorrência:** o único ponto de contenção entre execuções paralelas é o
  documento da demanda no Mongo — resolvido marcando `in_progress` ANTES de
  criar a sessão-filha. Worktrees isolados garantem que sessões em paralelo
  (da mesma fila ou não) nunca disputam o mesmo diretório em disco.

## A) Dados — coleção `demands`

Nova coleção Mongo, um documento por demanda:

```
{
  _id,
  base_session_id: str,       # sessão/projeto onde a fila foi aberta (define repo+host)
  text: str,                  # o que o usuário digitou
  status: "pending" | "in_progress" | "completed" | "error",
  target_session_id: str | None,  # sessão-filha criada pra essa demanda (preenchido no passo 3 do loop)
  branch: str | None,             # branch git da sessão-filha (preenchido junto com target_session_id)
  created_at: datetime,
  started_at: datetime | None,
  completed_at: datetime | None,
  summary: str | None,        # títulos dos milestones concluídos, só quando completed
  error_note: str | None,     # motivo do erro, só quando status=error
}
```

Índice por `(base_session_id, status, created_at)` pra listar/pegar a próxima
`pending` mais antiga de forma eficiente.

## B) API — rotas novas

- `POST /sessions/{base_id}/demands` — corpo `{text: str}`. Cria doc com
  `status: pending`. Retorna o doc criado.
- `GET /sessions/{base_id}/demands` — lista todas as demandas daquela sessão-base,
  mais recente primeiro (pra tela e pro orquestrador buscar a próxima pendente
  com `status=pending` client-side, ou aceitar `?status=` como filtro de
  conveniência).
- `PATCH /demands/{id}` — corpo parcial com os campos que mudam
  (`status`, `target_session_id`, `branch`, `started_at`, `completed_at`,
  `summary`, `error_note`). Usado pelo orquestrador pra persistir progresso —
  sem isso a fila não sobrevive a um restart da sessão orquestradora.

Nenhuma fila nova no RabbitMQ — é CRUD Mongo simples via HTTP, no mesmo padrão
que o `sf` CLI já usa hoje pra falar com a API.

## C) Frontend — aba "Fila"

Nova aba no painel da sessão (`session-panel`, ao lado de "Clonar"/"Git"):

- Campo de texto + botão "Adicionar" → `POST /demands`, aparece na lista como
  `pendente`.
- Lista abaixo, mais recente primeiro: texto da demanda + badge de status
  (`pendente` / `em execução` / `concluída` / `erro`).
- Quando `target_session_id` existe, o texto vira link clicável pra abrir a
  sessão-filha (inspecionar ou continuar manualmente).
- Quando `completed`, mostra `summary` embaixo do texto.
- Quando `error`, mostra `error_note` embaixo, em destaque (cor de erro).

Sem tela de "criar fila" separada — a fila pertence à sessão onde foi aberta.

## D) Orquestração — o loop (skill/instrução)

Documentado como uma skill que qualquer sessão de agente segue quando o
usuário pede pra "processar a fila":

1. `GET /sessions/{base_id}/demands?status=pending`, pega a mais antiga
   (menor `created_at`).
2. Se não houver nenhuma `pending`: para, informa o usuário que a fila está
   vazia (ou totalmente processada).
3. `PATCH` essa demanda → `status: in_progress`, `started_at: now` — ANTES de
   criar a sessão-filha (evita picking duplicado se duas coisas tentarem
   processar a mesma fila ao mesmo tempo).
4. Cria a sessão-filha isolada via `POST /sessions/{base_id}/clone`, agora
   aceitando um corpo opcional `{name_hint: str}` — slugifica o texto da
   demanda pra nomear a branch/worktree de forma reconhecível
   (`clone/relatorio-mensal` em vez de `clone/sessao-clone-3`). Espera o
   `command_result` via SSE (mesmo polling que a tela de clone já usa hoje)
   pra obter `session_id`/`branch`.
5. Manda a primeira instrução pra sessão-filha, **precedida de um preâmbulo de
   autonomia** (ver nota de revisão abaixo): `POST /{session_id}/input` com
   `{text: <preâmbulo + texto da demanda>, enter: true}`.
6. `PATCH` a demanda: `target_session_id`, `branch`.
7. Fica checando periodicamente `GET /tasks?session=<tmux_name da filha>`:
   - Se existe pelo menos 1 task E todas têm `state: done` → vai pro passo 8
     com sucesso.
   - Se passarem 30 minutos sem nenhuma task aparecer, OU a sessão-filha cair
     pra `status: error` — vai pro passo 8 com falha.
8. Sucesso: `PATCH` → `status: completed`, `completed_at: now`,
   `summary: <títulos das tasks concluídas, concatenados>`.
   Falha: `PATCH` → `status: error`, `completed_at: now`,
   `error_note: <"timeout sem milestones" ou o erro da sessão-filha>`.
9. Volta pro passo 1 (próxima `pending`). Fila esvaziada → para e informa o
   usuário quantas concluíram e quantas deram erro.

## Fora de escopo (YAGNI)

- Fila multi-repo (uma demanda, um repo diferente do resto) — não existe caso
  de uso confirmado; adicionar depois se precisar.
- Cancelar/editar uma demanda já `in_progress` — pra já, cancelar é abrir a
  sessão-filha manualmente e parar por lá.
- Paralelismo real do orquestrador (processar N demandas ao mesmo tempo) —
  contraria o pedido explícito de ser síncrono, uma de cada vez.
- Sinal de conclusão novo (handoff.md exposto pela API) — descartado a favor
  de reaproveitar milestones, que já existe e já é espelhado em `GET /tasks`.

## Nota de revisão (2026-09-02) — sessão-filha travava esperando confirmação

Duas execuções da demanda "editar/excluir tarefa" ficaram `in_progress` por
90min e 23min sem publicar milestone novo, sem diff, sem commit. Causa: o
passo 5 manda só o texto puro da demanda, e o comportamento padrão de
qualquer sessão de agente (confirmar antes de travar escopo, commitar ou dar
push — convenção do `CLAUDE.md` global) faz a sessão-filha **parar e
perguntar** nesses pontos. Como o orquestrador (passo 7) só lê `GET /tasks`
e nunca o chat da filha, a pergunta nunca é respondida — a sessão fica
parada até o timeout (ou além, se já tinha ≥1 milestone, já que o timeout
do passo 7 só dispara com ZERO milestones). Correção: passo 5 agora precede
o texto da demanda com um preâmbulo explícito autorizando a sessão-filha a
decidir sozinha (sem pedir confirmação de escopo/commit/push) e documentar
a decisão nos milestones — ver `docs/fila-de-demandas-protocolo.md`.

**Revisão adicional (2026-09-02):** o timeout de 30min do passo 7 pressupunha
a mesma sessão orquestradora viva rechecando periodicamente. Se ela cai no
meio, a demanda fica `in_progress` pra sempre — status não pode voltar
pra `pending` (transições validadas em `routers/demands.py`), e o loop só
olha `pending`. Isso não reabre o item acima (continua sendo milestones,
nenhum sinal novo) — só move a checagem do timeout pra também rodar no
início/a cada iteração do loop (passo 0 do protocolo), pra qualquer
execução futura resgatar órfãs deixadas por uma execução anterior
interrompida. Sem mudança de API nem serviço em background.
