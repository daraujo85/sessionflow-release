# Clonar sessão — design

Data: 2026-08-28

## Objetivo

Permitir clonar uma sessão existente a partir de três pontos da UI (card na
Home, card na aba Sessões, dentro da sessão/terminal), tanto em mobile
quanto em desktop. Clonar cria uma sessão nova, com histórico zerado, rodando
em um **git worktree isolado** (branch nova), e abre a sessão nova numa
**janela própria do navegador** assim que ela estiver pronta.

## Estado atual (achados da exploração)

- Não existe nenhuma implementação de clone/worktree hoje (frontend, api,
  worker).
- Criação de sessão: `POST /sessions` (`api/app/routers/sessions.py:296`) →
  publica comando `type="create"` no RabbitMQ → worker
  (`worker/sessionflow_worker/command_consumer.py:650`) roda
  `tmux_runtime.new_session(name, work_dir)`
  (`worker/sessionflow_worker/tmux_runtime.py:285`), que **exige que
  `work_dir` já exista**. Resposta da API é 202 `{command_id, status:
  accepted}` — assíncrono, não devolve a sessão criada.
- Botão "abrir em janela nova" já existe e é reaproveitável:
  `frontend/src/app/features/detalhe/session-panel.component.ts:1453`
  (`openInNewWindow`) — `window.open(url, 'sf-janela-<id>',
  'popup=yes,width=760,height=860,...')`.
- Primitivas git já existem em `worker/sessionflow_worker/git_info.py`
  (`is_git_repo`, `current_branch`, `list_branches`, `checkout_branch`,
  `find_repos`, regex anti-flag-injection para nomes de branch).
- Modelo de sessão no Mongo (`sessions_repo.py`) não tem `worktree_path`,
  `branch` nem `base_session_id` — só `work_dir` (string, cwd do tmux) e
  `git_repos` (lista derivada do disco).
- **Gap crítico**: o fluxo de criação de sessão é fire-and-forget. O worker
  já emite um frame SSE de resultado do comando (`_emit`, `command_consumer.py:2094`),
  mas ele carrega `name` (slug tmux), não o `_id` do Mongo, e o frontend
  nunca lê `command_id`/esse frame para saber "a sessão está pronta". Sem
  isso não há como abrir a janela nova automaticamente ao final do clone —
  é um fix mínimo necessário para este design, não um refactor à parte.

## Decisões (confirmadas com o usuário)

1. **Histórico**: sessão clonada começa zerada (sem `--resume`); herda só
   config (`agent_type`, `model`, `effort`, `host_id`) e o worktree/branch.
2. **Branch**: sempre nova (`clone/<slug-da-sessao-nova>`), a partir do HEAD
   atual da branch da sessão original. Nunca reaproveita a branch original
   (git bloqueia checkout duplo da mesma branch em dois worktrees).
3. **Elegibilidade**: só clona quando `work_dir` é um único repo git na raiz
   (sem caso "guarda-chuva" de múltiplos subrepos). Se não for elegível, a
   API responde 400 e o frontend mostra toast de erro — não se busca
   `git_repos` antecipadamente só para esconder o botão.
4. **Path do worktree**: irmão do repo original —
   `<pai-do-repo>/<nome-repo>-clones/<slug-da-sessao-nova>`.
5. **Ação do botão**: 1 clique, sem diálogo. Nome da sessão clonada é gerado
   automaticamente (`<nome-original>-clone`, com sufixo numérico em caso de
   colisão, mesma checagem de duplicidade que já existe em
   `sessions.py:296`).

## Arquitetura

Reaproveita o pipeline de comando existente (fila RabbitMQ → worker por
host) em vez de um endpoint síncrono na API — sessões rodam em hosts
diferentes (`host_id`), a API não tem acesso a filesystem de host remoto.

```
Frontend (botão Clonar)
  → POST /sessions/{id}/clone   (API)
      valida elegibilidade (git repo único), gera nome deduplicado
      publica comando type="clone" na fila do host da sessão original
      responde 202 {command_id}
  → Worker consome o comando
      cria worktree (git_info.create_worktree)
      cria branch clone/<slug>
      chama tmux_runtime.new_session(nome, worktree_path)
      grava doc no Mongo (find_one_and_update, upsert) incluindo
        worktree_path, base_session_id
      emite frame SSE de resultado com session_id (o _id do Mongo) e
        command_id
  → Frontend (SseService)
      expõe signal commandResult(commandId) lido do frame acima
      botão de clonar assina esse signal pelo command_id da resposta 202
      ao receber ok:true → window.open('/sessao/<session_id>', ...)
        reaproveitando o helper de session-panel.component.ts:1453
      timeout ~15s sem resposta → toast neutro ("clonagem solicitada,
        confira na lista"), fail-soft
      ok:false → toast com a mensagem de erro do worker
```

### Novo endpoint

`POST /sessions/{id}/clone` — sem body. Resposta 202 `{command_id, status:
"accepted"}` (mesmo formato do create). 400 se `work_dir` da sessão de
origem não for elegível (não é repo git único na raiz).

### Novo comando (worker)

`type="clone"`, payload: `source_session_id`, `name` (já deduplicado pela
API), `agent_type`, `model`, `effort`, `host_id`, `work_dir` (da sessão
original), `repo_path` (raiz do repo dentro de `work_dir`).

Nova função em `git_info.py`:

```python
def create_worktree(repo_path: str, dest_path: str, branch_name: str) -> None:
    """git worktree add -b <branch_name> <dest_path> (a partir do HEAD atual)."""
```

Reaproveita a regex anti-flag-injection já existente para validar
`branch_name`.

### Confirmação de prontidão (fix mínimo)

- Worker: no upsert do doc de sessão (tanto para `create` quanto para
  `clone`), usa `find_one_and_update` com `return_document=AFTER` e inclui
  `session_id=str(doc["_id"])` no frame emitido por `_emit`.
- Frontend `SseService`: novo signal `commandResult(commandId): {ok, session_id?,
  error?} | undefined`, populado ao ler frames com `command_id` (o campo já
  trafega, só não é lido hoje). Não altera o comportamento atual de
  `criar.component.ts` — o signal fica disponível, quem quiser consumir
  consome.

### Limpeza no delete

Sessão clonada grava `worktree_path` no doc. Ao deletar, o worker tenta
`git worktree remove --force <worktree_path>` best-effort (loga aviso em
falha, não bloqueia o delete da sessão/tmux).

## UI — três pontos de entrada

Botão "Clonar" (ícone de cópia), mesmo comportamento nos três:

| Local | Arquivo | Referência |
|---|---|---|
| Card na Home | `frontend/src/app/features/inicio/inicio.component.ts` | perto de `openSession`, :222-233 |
| Card na aba Sessões | `frontend/src/app/features/sessoes/sessoes.component.ts` | :275-285 |
| Dentro da sessão | `frontend/src/app/features/detalhe/session-panel.component.ts` | ao lado de `openInNewWindow`, :1453 |

Comportamento: clique → `api.cloneSession(id)` → spinner curto no botão →
assina `commandResult` pelo `command_id` → abre janela nova ao confirmar.
Mobile e desktop usam o mesmo componente de botão (sem variante por
viewport).

## Erros e casos de borda

- `work_dir` não elegível → 400 da API → toast de erro, botão não fica em
  loading.
- Falha no `git worktree add` (branch/dir já existe, disco cheio, etc.) →
  worker emite `ok:false` com mensagem → frontend mostra toast com o erro.
- Timeout de confirmação (~15s, host offline etc.) → toast neutro, sessão
  pode ter sido criada mesmo assim.
- Nome colidindo → resolvido automaticamente pela API antes de publicar o
  comando (sufixo numérico), sem envolver o usuário.

## Testes

- `worker/sessionflow_worker/tests/`: teste unitário de `create_worktree`
  (repo git temporário, assert dir + branch criados) e teste do handler de
  `type="clone"` no `command_consumer` (mock de host/fila).
- API: teste do endpoint `/sessions/{id}/clone` — rejeição de work_dir não
  elegível (400) e dedupe de nome.
- Frontend: teste do signal `commandResult` no `SseService` (frame com
  `command_id` → signal atualizado) e teste de que o botão de clonar chama
  `api.cloneSession`.

## Fora de escopo

- Clonar sessões com `work_dir` "guarda-chuva" (múltiplos subrepos).
- Editar config (model/effort/nome) no momento do clone — sempre herda 1:1.
- Continuar conversa/histórico do agente clonado.
- Corrigir o fluxo de confirmação de `criar.component.ts` (fora do escopo
  desta feature; o fix de emissão/signal fica disponível para uso futuro).
