# `sf delegate` cross-host — dividir processamento entre máquinas

Data: 2026-10-01 · Status: aprovado (design) · Escopo: `tools/sf`, `api/`, `worker/`

## Objetivo

Sessão-chefe no Mac delega tarefa pesada para worker em OUTRO host (ex.: Duck
Server 🦆, Windows + WSL2), que roda num worktree/branch próprio na cópia local
do repo naquele host, sem consumir CPU/RAM do Mac. Ao terminar, o filho
sinaliza o pai e devolve o resultado pelo próprio SessionFlow (shared-files);
push para o GitHub é opcional.

Sucesso:

```bash
sf dirs --host duck sessionflow
sf delegate --host duck --dir sessionflow --worktree [--push] \
  --provider claude --task "..."
# ... pai recebe "✅ HANDOFF <nome> status=done" no terminal
sf check <nome> --summary        # no Mac, sem SSH manual
```

## Estado atual (base)

| Peça | Onde | Estado |
|---|---|---|
| Fila RabbitMQ por host | `api/app/publishers/command_publisher.py:57`, `worker/sessionflow_worker/rabbit.py:38` | pronto |
| `SessionCreate.host_id` | `api/app/routers/sessions.py:143` | pronto; `sf` não envia (cai no host com heartbeat mais recente) |
| Índice de pastas por host | `dir_scanner.persist_scan` → `host_directories`; `GET /directories?host_id=` | pronto |
| Worktree | `git_info.create_worktree/remove_worktree`, usado só por `_handle_clone` (mesmo host) | reaproveitar |
| `parent` | gravado em `_handle_create`, sem consumidor | usado só pelo `sf` (injetado no texto) |
| Shared-files | `POST/GET /sessions/{id}/shared-files`, `GET /shared-files/{id}/download` | pronto |

Verificado em 2026-10-01 (Mongo): Duck (`67122d03-…`, `display_name="Duck
Server"`, `emoji=🦆`, `platform=wsl2`) online, 944 pastas indexadas sob
`/mnt/c/repo` e `~/Documents/projects` → `SESSIONFLOW_SCAN_ROOTS` está
configurado no Duck.

## Seção 1 — Interface `sf` e resolução de host/pasta

### `--host <alias>` (em `delegate`, `dirs`)

1. `GET /workers`.
2. Match exato em `host_id`; depois exato (case-insensitive) em
   `display_name`/`hostname`/`emoji`; depois substring única nesses campos.
3. Ambíguo ou não encontrado → `die` listando `display_name (hostname)
   host_id online`.
4. `online=false` → `die` antes de criar sessão.
5. `host_id` resolvido vai no `POST /sessions`.

Host local = `host_id` lido de `~/.claude/.sessionflow-host-id`. Sem `--host`,
ou `--host` = host local → comportamento atual inalterado.

### `sf dirs --host <alias> [termo] [--limit N]`

`GET /directories?host_id=<id>&q=<termo>&limit=<N>` (default 20). Imprime
`path` por linha (com `[git]` quando `is_git=true`).

### `--dir` com host remoto

- Começa com `/` ou `~/` → usado como veio (não passa por `abspath` local).
- Começa com o `$HOME` local (ex.: `/Users/diegoaraujo/…`) → `die`: "o shell
  expandiu `~` localmente; use aspas: `--dir '~/…'`".
- Caso contrário é **nome**: `GET /directories?host_id=…&q=<nome>`; prefere
  match exato em `name`; se único usa `path`; ambíguo → `die` listando; vazio
  → `die` sugerindo `sf dirs --host <alias>`.

### Artefatos locais no modo remoto

- Manifest NÃO é gravado em disco remoto (não existe aqui): vai **inline** no
  texto da tarefa (JSON compacto; mesmo contrato/orçamento de 32KB).
- Registry gravado no **cwd local** do Mac:
  `.sessionflow/handoff/<nome>.registry.json` com `id`, `name`, `host_id`,
  `remote: true`, `work_dir` (real, ver Seção 2), `provider`, `model`,
  `effort`, `push`, `created_at`.

## Seção 2 — Worktree no host de destino

### API

`SessionCreate` ganha `worktree_branch: str | None = None`, repassado no
payload do comando `create` (strip; vazio → `None`). Nada mais muda.

### Worker (`_handle_create`)

Se `payload.worktree_branch`:

1. Trata `work_dir` como `repo_path`; `git_info.create_worktree(repo_path,
   slug=<name>, branch=<worktree_branch>)` → `<repo_parent>/<repo>-clones/<name>`.
   (`create_worktree` hoje fixa `clone/<slug>`; ganha parâmetro `branch`
   opcional, default preservado.)
2. Abre o tmux no worktree; grava `worktree_path`/`worktree_repo` na sessão
   (mesmos campos do clone).
3. Falha no create após criar o worktree → `remove_worktree`.
4. `work_dir` não-repo → falha como hoje (evento `ok:false`).

### Indexador

`dir_scanner.to_suggestion` grava `is_git: bool` (`(path / ".git").exists()`;
cobre worktree, onde `.git` é arquivo). `DirectoryOut` expõe `is_git: bool |
None`. Docs antigos sem o campo → `None` (sem pré-checagem; worker valida).

### `sf --worktree`

- Envia `worktree_branch="sf/<nome>"`.
- Pasta resolvida com `is_git=false` → `die` antes de criar.
- Após sessão `running`, lê `work_dir` real da sessão (`GET /sessions` já
  devolve) e usa esse caminho nas instruções de handoff e no registry.

## Seção 3 — Retorno, sinalização, erros, testes

### Instruções injetadas no filho remoto (fim da tarefa)

1. Commit no worktree (branch `sf/<nome>`), mensagem descritiva.
2. Escreve `<work_dir>/.sessionflow/handoff/<nome>.md` e
   `<nome>.handoff.json` (contrato atual, `schema_version=1`).
3. `--push`: `git push -u origin sf/<nome>`; branch + SHA em `diff`.
   Sem `--push`: `git format-patch <base>..HEAD --stdout >
   .sessionflow/handoff/<nome>.patch` (`<base>` = SHA que o próprio filho
   anota com `git rev-parse HEAD` como PRIMEIRO passo da tarefa — instrução
   injetada no topo do texto; o `sf` no Mac não tem acesso ao repo remoto).
4. `sf share` de cada arquivo existente (`.md`, `.handoff.json`, `.patch`) —
   sem `--to` (própria sessão).
5. Se houver pai (`detect_parent()` no Mac, injetado no texto):
   `sf send <pai> "✅ HANDOFF <nome> status=<status>"`.

Sem `--worktree` os passos 1/3 só valem se o diretório for repo; o texto
instrui a pular git caso contrário.

### `sf check <nome>` no Mac

Registry com `remote: true` → `GET /sessions/{id}/shared-files`, baixa os
arquivos `<nome>.*` para `.sessionflow/handoff/` local (sobrescreve), depois
segue o fluxo atual (validação JSON, `--summary`, imprime `.md`). Ausentes →
"ainda rodando". `.patch` presente → imprime dica `git am <arquivo>`.

### Erros

| Caso | Comportamento |
|---|---|
| host desconhecido/ambíguo/offline | `die` + lista de hosts |
| `--dir` com `$HOME` local | `die` + dica de aspas |
| nome de pasta ambíguo/ausente | `die` + candidatos / `sf dirs` |
| `--worktree` em `is_git=false` | `die` antes de criar |
| timeout 40s, host remoto | mensagem cita host, `--dir` e "é repo git?" |
| `sf` ausente no PATH do host remoto | fora do controle do pai; documentado como pré-requisito |

### Testes

- `tools/test_sf.py` (HTTP mockado): resolução de host (exato/substring/
  ambíguo/offline), resolução de `--dir` (caminho/`$HOME` local/nome único/
  ambíguo), manifest inline no modo remoto, `worktree_branch` no payload,
  `check` baixando de shared-files.
- Worker: `create` com `worktree_branch` cria worktree na branch pedida;
  rollback com `remove_worktree` quando create falha; `to_suggestion` com
  `is_git`.
- API: `worktree_branch` chega ao payload publicado; `DirectoryOut.is_git`.

## Pré-requisitos no host remoto

- Worker SessionFlow rodando (Duck: systemd `sessionflow-worker.service`).
- `sf` no PATH **dentro do WSL** com `.env` válido apontando pra API central
  (o filho usa `sf share`/`sf send`).
- Repo clonado sob um `SESSIONFLOW_SCAN_ROOTS` do host; credencial git/gh
  configurada se usar `--push`.
- Nota de desempenho: repos em `/mnt/c/...` (9P) têm git/IO lentos no WSL;
  preferir clones no FS Linux (`~/…`) para tarefas pesadas.

## Fora de escopo

- `--host auto` por carga (fase B: ligar `host_metrics.sample_host_metrics`
  no heartbeat e escolher host menos carregado).
- Limpeza automática de worktree remoto.
- Auto-clone do repo no host de destino.
- Sincronizar `~/.claude/skills/sf-delegate/sf` (cópia antiga, sem
  `--role`/manifest) com `tools/sf` — recomendado symlink, pendente de OK.
