# Mover sessão entre hosts — design

**Data:** 2026-10-03 · **Status:** rascunho para revisão

## Objetivo

Transferir uma sessão **já iniciada** de um host para outro (ex.: Mac → Duck)
pela tela de Editar sessão ou por `sf move`, **retomando a mesma conversa do
agente** no destino. Hoje o Editar só troca nome; `host_id` é fixo desde o
`create`.

## Decisões do usuário (2026-10-03)

- **Conversa:** retomar a MESMA conversa (`claude --resume <uuid>`), não
  recomeçar.
- **Código não commitado no host de origem:** BLOQUEAR a transferência
  (mensagem "faça commit/push antes"). Não levar patch, não ignorar.

## Escopo

**Dentro:**
- Sessões com `agent_type = claude` e `claude_session_id` salvo.
- `work_dir` que exista no destino, achado pelo NOME da pasta (basename) no
  índice de pastas do host destino — mesma regra do `sf delegate --host auto`.
- Endpoint na API, handlers no worker, seletor de host no Editar (frontend),
  `sf move` na CLI.

**Fora (v1):**
- Retomar conversa de codex/gemini/opencode → API responde 400 "só claude por
  enquanto". (Alternativa futura: reaproveitar o handoff de contexto do
  `switch_agent`.)
- Sessões em worktree (`worktree_path` preenchido, pasta `<repo>-clones/…`) →
  400.
- Levar mudanças não commitadas.
- Sessões filhas de `sf delegate` com handoff pendente (`parent` setado e sem
  `parent_notified_at`) → 400, para não quebrar o aviso ao pai.

## Contexto do código (fatos verificados)

- Sessão ↔ host: campo `host_id` no doc de `sessions`; comandos vão para a
  fila por host via `publish_command(..., host_id=...)`; `_require_route`
  resolve `(tmux_name, host_id)` (`api/app/routers/sessions.py`).
- Unicidade: índice `uq_host_tmux_name_active` em `(host_id, tmux_name)` para
  status ativos (`worker/sessionflow_worker/mongo.py:85-108`) → o destino pode
  já ter uma sessão ativa com o mesmo `tmux_name`.
- Retomar: `_recreate_and_relaunch(name)` lê `work_dir/agent_type/model/effort/
  claude_session_id` do doc e relança com `build_launch_cmd(resume=True,
  session_id=...)` (`command_consumer.py:1019-1075`). Também chama
  `_ensure_claude_trust(work_dir)`.
- Conversa do claude: `~/.claude/projects/<cwd-encoded>/<uuid>.jsonl`, sendo
  `<cwd-encoded>` o cwd absoluto com `/` e `.` → `-` (`metrics.py:1-6`,
  `_encode_work_dir` em `metrics.py:192`).
- O worker tem acesso direto ao Mongo (`self._sessions`) e já publica na fila
  de OUTRO host (`handoff_notify.py:171-179`, `commands_queue_name(host)`).
- Índice de pastas por host: coleção do `dir_scanner` com `uq_host_path`
  `(host_id, path)`.
- Carga/CLIs do host: `worker_status.metrics` / `.agents` (heartbeat).

## Arquitetura

Cadeia **API → worker A → worker B**, sem a API esperar no meio. O `.jsonl`
viaja pelo Mongo (coleção nova `session_transfers`), não pela fila.

```
UI/sf ──POST /sessions/{id}/move──▶ API
  valida + marca doc.moving ──publish "move_out"──▶ fila A
worker A: checa git limpo → encerra agente+tmux → grava jsonl em
  session_transfers ──publish "move_in"──▶ fila B
worker B: prepara repo → grava jsonl no slug local → cria tmux →
  claude --resume → troca host_id/work_dir no doc → limpa moving
  (falha) ──publish "resume" + move_failed──▶ fila A  (rollback)
```

### 1. API — `POST /sessions/{id}/move`

Body: `{ "target_host_id": str }` → `202 { command_id, status: "accepted" }`.

Validações (todas síncronas, 4xx com mensagem em pt-BR):

| Regra | Erro |
|---|---|
| sessão existe e tem `host_id` | 404 |
| `target_host_id != host_id` | 400 "já está nesse host" |
| destino online (heartbeat recente) | 409 "host offline" |
| `agent_type == claude` e `claude_session_id` presente | 400 "só claude por enquanto" |
| sem `worktree_path` | 400 "sessão em worktree não pode mover" |
| não é filha com handoff pendente | 400 |
| `doc.moving` ausente ou velho (> 5 min) | 409 "já está movendo" |
| destino tem CLI `claude` (`worker_status.agents.claude`) — se o campo faltar (worker antigo), não bloqueia | 409 |
| pasta destino: 1 match exato do basename de `work_dir` no índice de pastas do destino | 409 "pasta X não encontrada no host Y" / "ambígua" |
| sem sessão ativa com o mesmo `tmux_name` no destino | 409 |

Se passar: `$set doc.moving = {target_host_id, target_work_dir, started_at,
phase: "exporting", error: null}` e publica `move_out` na fila de A com
`{name, session_id, target_host_id, target_work_dir}`.

### 2. Worker A — handler `move_out`

1. `git status --porcelain` em `work_dir` (se for repo). Saída não vazia →
   `moving.phase = "failed"`, `moving.error = "há mudanças não commitadas em
   <host A>: faça commit/push antes"`; agente segue rodando; evento
   `ok=False`. FIM.
2. Registra branch atual (`git rev-parse --abbrev-ref HEAD`) e se há commits
   não enviados (`git rev-list @{u}..HEAD --count` > 0 → também bloqueia com
   "faça push antes", senão B não acha os commits).
3. Encerra o agente e o tmux (mesmo caminho do `kill`, sem marcar a sessão
   como apagada). Status do doc → `stopped`.
4. Lê `~/.claude/projects/*/<claude_session_id>.jsonl` (glob pelo uuid, como
   `command_consumer.py:1615-1631`). Não achou → falha "conversa não
   encontrada" e relança local (rollback).
5. Grava em `session_transfers`: `{_id: transfer_id, session_id, chunks:
   [Binary gzip], branch, source_host_id, created_at}`. Documento Mongo tem
   teto de 16 MB → gzip e, se passar de 12 MB comprimido, dividir em docs
   `{transfer_id, seq, data}`.
6. `moving.phase = "importing"`; publica `move_in` na fila de B com
   `{name, session_id, transfer_id, target_work_dir, branch, source_host_id}`.

### 3. Worker B — handler `move_in`

1. Repo em `target_work_dir`: se `git status --porcelain` não vazio → falha
   "host destino tem mudanças não commitadas". Senão `git fetch` →
   `git checkout <branch>` → `git pull --ff-only`. Falhou → falha com a
   mensagem do git.
2. Lê e descomprime o transfer; reescreve o campo `cwd` de cada linha JSON do
   `.jsonl` para `target_work_dir` (linha que não for JSON válido fica como
   está); grava em `~/.claude/projects/<encode(target_work_dir)>/<uuid>.jsonl`.
   Se o arquivo já existir, sobrescreve.
3. `_ensure_claude_trust(target_work_dir)`, cria o tmux e relança com
   `build_launch_cmd(resume=True, session_id=claude_session_id)` — mesmo
   caminho de `_recreate_and_relaunch`, refatorado para aceitar
   `work_dir` explícito.
4. Atualiza o doc numa operação: `host_id = B`, `work_dir = target_work_dir`,
   `status = running`, `$unset moving`, `moved_at`, e anexa em
   `move_history` `{from, to, at}`. Apaga o transfer.
5. Evento `ok=True` (`type: "move_in"`) → SSE para o frontend.

**Rollback (falha em qualquer passo de B):** `moving.phase = "failed"`,
`moving.error = <motivo>`, publica `resume` na fila de A (host_id ainda é A,
o `.jsonl` original continua lá) e apaga o transfer. A sessão volta a rodar
em A com a mesma conversa.

**B some no meio (offline):** o doc fica com `host_id = A`, `status =
stopped`, `moving.phase = "importing"`. "Retomar" pela UI relança em A
normalmente; a API limpa `moving` ao receber `resume` ou um novo `move` se
`started_at` tiver mais de 5 min.

### 4. Frontend

- Modal Editar sessão: seletor **Host** com os hosts online (emoji, nome,
  `⚡ cpu` e `🧠 livre` vindos de `WorkersStore`). Mudar e salvar → confirma
  ("A sessão vai parar aqui e retomar em X. Continuar?") → `POST /move`.
- Seletor desabilitado com dica quando a sessão não é claude ou está em
  worktree.
- Card/detalhe: com `moving` presente mostra "movendo para X…"; com `phase =
  failed` mostra `moving.error` com botão fechar (limpa via `PUT` simples ou
  some na próxima ação).
- `models.ts`: `SessionDoc.moving?`, `move_history?`.

### 5. CLI — `sf move <sessão> --host <alias|auto>`

- Resolve a sessão como `sf send` (substring única); `--host` como no
  `delegate` (`auto` usa o ranking existente, excluindo o host atual).
- Chama `POST /move`, depois faz polling do doc até `moving` sumir (sucesso:
  imprime "movida para X") ou `phase = failed` (imprime o erro, exit 1).
  Timeout 3 min.
- Espelhar em `prata/sf-delegate/sf` + `SKILL.md` no repo de skills
  (ver memória prata-skills-sync).

## Erros e mensagens (pt-BR, visíveis ao usuário)

- "Há mudanças não commitadas em <host>: faça commit/push antes de mover."
- "Há commits não enviados em <host>: faça push antes de mover."
- "Pasta <nome> não encontrada em <host>." / "… ambígua em <host>."
- "Conversa do claude não encontrada em <host>."
- "<host> ficou offline durante a transferência — a sessão continua em
  <origem>; use Retomar."

## Testes

- **API** (`api/app/tests`): cada linha da tabela de validação → status e
  mensagem; caminho feliz publica `move_out` com payload certo e grava
  `moving`.
- **Worker** (unit, sem tmux real — mocks como `test_host_metrics.py`):
  - `move_out`: repo sujo bloqueia sem parar agente; commits não enviados
    bloqueiam; jsonl ausente → rollback; caminho feliz grava transfer e
    publica `move_in` na fila de B.
  - `move_in`: reescrita de `cwd` no jsonl (linha inválida preservada);
    slug do destino correto (`/` e `.` → `-`); falha de git → publica
    `resume` para A e apaga transfer; sucesso troca `host_id`/`work_dir` e
    remove `moving`.
  - Transfer > 12 MB vira chunks e é remontado igual.
- **CLI** (`tools/test_sf.py`): `sf move` resolve host/auto, imprime sucesso
  e falha, timeout.
- **Smoke manual:** mover uma sessão claude real Mac → Duck → Mac e conferir
  que o agente lembra do contexto anterior.

## Riscos

- **Formato interno do `.jsonl` do claude** pode mudar entre versões da CLI;
  a reescrita do `cwd` é best-effort. Mitigação: o smoke manual valida; se o
  `--resume` falhar no destino, o rollback relança em A.
- **Versões diferentes do claude CLI** entre hosts podem não ler o jsonl
  uma da outra → mesmo rollback.
- **Arquivos fora do git** (`.env`, build local) não vão junto — o agente no
  destino pode estranhar. Documentar no tooltip do seletor.
- **Deepin com 2 workers duplicados** consumindo a mesma fila pode executar
  `move_in` duas vezes → handler idempotente (checa se `moving.transfer_id`
  ainda existe antes de agir). Recomendado desligar a unit duplicada.
