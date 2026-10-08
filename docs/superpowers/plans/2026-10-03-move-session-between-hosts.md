# Mover sessão entre hosts — plano de implementação

> Executado por workers SessionFlow no host **Google Colab** (`sf delegate --host colab --worktree --push`), 1 worker por task, em paralelo (as tasks só se tocam pelo CONTRATO abaixo). O orquestrador revisa e faz o merge.

**Goal:** mover uma sessão claude já iniciada do host A pro host B retomando a mesma conversa.

**Spec:** `docs/superpowers/specs/2026-10-03-move-session-between-hosts-design.md` (LEIA antes de começar — é a autoridade).

**Tech:** API FastAPI + Motor (`api/app`), worker Python asyncio + aio_pika + Motor (`worker/sessionflow_worker`), frontend Angular signals (`frontend/src/app`), CLI Python stdlib (`tools/sf`).

## Global Constraints

- Mensagens visíveis ao usuário em pt-BR (texto exato na seção "Erros e mensagens" da spec).
- Comentários/docstrings em pt-BR, mesmo estilo do arquivo que você edita.
- Não mudar comportamento de nenhum comando/endpoint existente.
- Testes: API `cd api && uv run pytest app/tests -q`; worker `cd worker && uv run pytest sessionflow_worker/tests -q`; CLI `python3 -m unittest tools/test_sf.py`. Falhas pré-existentes (já falham em `main`) não são suas — liste no handoff, não conserte.
- Commits pequenos, mensagem `feat(<área>): ...`, terminando com `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## CONTRATO compartilhado (valores exatos — não inventar outros)

**Campo `moving` no doc de `sessions`** (ausente = não está movendo):
```json
{"target_host_id": "str", "target_work_dir": "str (caminho absoluto no destino)",
 "phase": "exporting" | "importing" | "failed",
 "error": "str | null", "started_at": "datetime UTC", "transfer_id": "str | null"}
```
Após sucesso: `moving` removido (`$unset`), `moved_at` = datetime, `move_history` ganha `{"from": host_a, "to": host_b, "at": datetime}` via `$push`.

**Comandos RabbitMQ (fila do host, via `publish_command`/`commands_queue_name`)**:
- `move_out` → host A. payload: `{"name": tmux_name, "session_id": str(_id), "target_host_id": str, "target_work_dir": str}`
- `move_in` → host B. payload: `{"name": tmux_name, "session_id": str, "transfer_id": str, "target_work_dir": str, "branch": str | null, "source_host_id": str}`
- Rollback: `resume` (comando JÁ existente) → host A, payload `{"name": tmux_name}`.
- Sessões são localizadas por `session_id` (`_id` ObjectId), nunca só por `tmux_name` (não é único entre hosts).

**Coleção `session_transfers`**: `{"_id": transfer_id (uuid4 str), "session_id": str, "claude_session_id": str, "branch": str | null, "source_host_id": str, "chunks": [bytes gzip, ...], "created_at": datetime}`. Concatenar `chunks` e `gzip.decompress` = conteúdo original do `.jsonl`. Cada chunk ≤ 8 MB (vários chunks no mesmo doc até 15 MB; acima disso falha "conversa grande demais para mover").

**Endpoint**: `POST /sessions/{session_id}/move` body `{"target_host_id": str}` → `202 {"command_id": str, "status": "accepted"}`; erros 400/404/409 com `detail` pt-BR.

**Evento SSE**: o worker B emite `type: "move_in", ok: true, session_id, host_id` (via `_emit`); falhas emitem `ok: false, error`.

---

### Task 1: Worker — `move_out` / `move_in`

**Files:** `worker/sessionflow_worker/command_consumer.py` (dispatch, `_VALID_TYPES`, handlers, refator de `_recreate_and_relaunch` p/ aceitar `work_dir` opcional), novo `worker/sessionflow_worker/session_move.py` (funções puras: git checks, leitura/escrita/reescrita do jsonl, chunking), testes `worker/sessionflow_worker/tests/test_session_move.py`.

Reuse: `_encode_work_dir` (`metrics.py:192`), glob do jsonl por uuid (`command_consumer.py:~1615-1631`), `_ensure_claude_trust`, `build_launch_cmd(resume=True, session_id=...)`, `commands_queue_name` + `publish` (`rabbit.py`; ver como `handoff_notify.py:171-179` publica na fila de OUTRO host).

Comportamento: spec seções 2 e 3 + rollback. Funções puras em `session_move.py` com testes primeiro (TDD):
- `git_block_reason(work_dir) -> str | None` (porcelain não vazio → msg "mudanças não commitadas"; `@{u}..HEAD` > 0 → "commits não enviados"; sem upstream ou não-repo → None).
- `current_branch(work_dir) -> str | None`.
- `find_claude_jsonl(claude_session_id) -> Path | None`.
- `pack(data: bytes) -> list[bytes]` / `unpack(chunks) -> bytes` (gzip, chunk 8 MB, levanta erro acima de 15 MB comprimido).
- `rewrite_cwd(jsonl_text, new_cwd) -> str` (troca o campo `cwd` de cada linha JSON que o tenha; linha inválida preservada byte a byte).
- `jsonl_target_path(work_dir, claude_session_id) -> Path` (`~/.claude/projects/<encode>/<uuid>.jsonl`).
- `prepare_repo(work_dir, branch) -> str | None` (porcelain sujo → msg; `git fetch`, `checkout branch` se dado, `pull --ff-only`; retorna msg de erro ou None).

Handlers: idempotência — `move_in` ignora (ok, `skipped`) se o doc não tem mais `moving.transfer_id == payload.transfer_id`.

Tests mínimos: cada função pura (repo git real em `tmp_path`, com remote bare p/ testar "commits não enviados"); handlers com runtime/collections mockados: repo sujo não mata o agente; sucesso de `move_in` faz `$set host_id/work_dir/status` + `$unset moving` + apaga transfer; falha de `prepare_repo` publica `resume` na fila do host A e marca `phase=failed`.

### Task 2: API — `POST /sessions/{id}/move` + limpeza de `moving`

**Files:** `api/app/routers/sessions.py` (modelo `SessionMove`, endpoint), `api/app/models` se houver modelo de saída de sessão (expor `moving`, `moved_at`, `move_history` como opcionais), testes `api/app/tests/test_sessions_move.py`.

Reuse: `_require_route`, `_is_host_online`, `publish_command`, `WORKER_STATUS_COLLECTION`. Índice de pastas: mesma coleção usada por `api/app/routers/directories.py` (confira o nome lá) filtrando `host_id = target` e basename igual ao de `work_dir`.

Validações: tabela da spec seção 1, na ordem dela. `target_work_dir` = `path` do único match (expandir `~` NÃO — o worker B expande; mande como está no índice).

Também: no endpoint `resume` existente, se o doc tiver `moving.phase in ("failed","importing")` com `started_at` > 5 min, `$unset moving` antes de publicar (sem mudar o resto do resume). Stale > 5 min também libera um novo `move`.

Tests: um teste por linha da tabela (status + trecho do `detail`), caminho feliz (grava `moving.phase="exporting"`, publica `move_out` com payload exato no host A), resume limpa `moving` stale.

### Task 3: Frontend — seletor de host no Editar sessão

**Files:** `frontend/src/app/core/models.ts` (campos `moving`, `moved_at`, `move_history` na interface de sessão), `frontend/src/app/core/api.service.ts` (`moveSession(id, targetHostId)`), componente do modal Editar (procure onde `renameSession`/`setDisplayName` são chamados — `session-panel.component.ts`), indicador no card/detalhe.

Comportamento: spec seção 4. Hosts vindos de `WorkersStore.workers()` (online, ≠ host atual), mostrando emoji + nome + `⚡ CPU x%` + `🧠 y GB livre` (campos `metrics.cpu_pct`, `metrics.mem_avail_gb`). Desabilitado com dica quando `agent_type !== 'claude'` ou `worktree_path`. Confirmação antes de chamar. `moving` presente → badge "movendo para <host>…"; `moving.phase === 'failed'` → mostra `moving.error`.

Validação: o projeto não tem node_modules local; rode `docker compose --profile app build frontend` se docker existir, senão `npx ng build` após `npm ci` em `frontend/`. Se spec Jest existir para o componente, adicione caso para o seletor desabilitado.

### Task 4: CLI — `sf move`

**Files:** `tools/sf` (subcomando `move`), `tools/test_sf.py` (classe `MoveCommandTests`), `~/.claude/skills/sf-delegate/SKILL.md` NÃO (o orquestrador espelha depois).

Comportamento: spec seção 5. `sf move <sessão> --host <alias|auto>`: resolve sessão como `cmd_send`; host como `cmd_delegate` (para `auto`, reusar o ranking existente excluindo o host atual da sessão); `POST /sessions/{id}/move`; polling `GET /sessions/{id}` a cada 3s até `moving` sumir com `host_id == alvo` (imprime "movida para <host>", exit 0) ou `moving.phase == "failed"` (imprime `moving.error`, exit 1); timeout 180s (exit 1). Erro HTTP 4xx → imprime `detail`, exit 1.

Tests (mock da camada HTTP como as classes existentes): sucesso, falha, timeout, 409 com detail, `--host auto` exclui host atual.
