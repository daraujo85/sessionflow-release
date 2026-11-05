# tools/

## `start-worker.sh` — um comando só pra instalação de amigo

Atualiza o clone (`git pull --ff-only`, só com árvore limpa), roda `uv sync` no
`worker/`, sobe/reinicia o worker (unit systemd `sessionflow-worker` se existir;
senão sessão tmux `sessionflow-worker` com loop que se reergue) e liga o
auto-update (`sessionflow-autoupdate`, abaixo). Idempotente — rodar de novo só
reinicia na versão nova. Requer `.env` na raiz e `uv` (+ `tmux` sem systemd).

```bash
./tools/start-worker.sh
```

## `self-update.sh` / `self-update-loop.sh` — auto-atualização (pull + rebuild sozinho)

Pra instalações de amigo (Heverton, Alvarenga, Lucas…), o `origin` do clone
aponta pro mirror PÚBLICO `daraujo85/sessionflow-release` (sem precisar de
token/deploy key — repo é só leitura pra quem clona). `self-update.sh` checa
se `origin/main` avançou; se sim, faz `git merge --ff-only`, reconstrói os
containers (`docker compose --profile app up -d --build`) e reinicia o worker
(sessão tmux `sessionflow-worker`, se existir — o `uv run` resincroniza deps
sozinho). Só atualiza com a árvore de trabalho LIMPA (nunca reseta/descarta
mudança local por engano).

```bash
# Rodar 1x (ex.: via cron)
./tools/self-update.sh

# Rodar em loop (default 30min) — crie numa sessão tmux de infra dedicada
# (o app já esconde da tela de Sessões, igual sessionflow-worker/cloudflared-tunnel):
tmux new-session -d -s sessionflow-autoupdate './tools/self-update-loop.sh'
```

Cortar uma versão nova pros amigos = espelhar o `main` privado pro público:

```bash
tools/publish-release.sh ["mensagem"]   # remote "public" = https://github.com/daraujo85/sessionflow-release.git
# NUNCA `git push public main:main`: levaria o histórico privado (com segredos antigos).
# O script publica só a árvore de origin/main num commit em cima de public/main
# (fast-forward pros amigos) e aborta se achar valor secreto do .env nos arquivos.
```

## `sf` — delega tarefa / fala com sessão irmã / compartilha arquivo (Fatia 1-3)

CLI (Python 3, só stdlib) que faz uma sessão-chefe do SessionFlow **delegar uma
tarefa pesada a um worker filho em OUTRO provedor** (gemini/codex/opencode/
claude) e colher só o **resultado** via um arquivo de handoff — sem poluir a
janela de contexto do chefe (economia de token).

Modelo **delega-e-revisa**: o filho roda autônomo (yolo) e, ao terminar, escreve
um resumo em `<DIR>/.sessionflow/handoff/<NOME>.md`. O chefe lê só esse arquivo.

Lê credenciais/porta do `.env` do SessionFlow automaticamente. Sem dependências
externas.

### Uso

```bash
# Delegar
./tools/sf delegate --provider gemini \
  --task "Refatore o módulo X e rode os testes; relate o diff" \
  --dir /caminho/do/projeto
# opções: --model (omita p/ default), --effort low|medium|high, --name

# Acompanhar / colher o resultado
./tools/sf check <nome-ou-id> --dir /caminho/do/projeto

# Listar sessões ativas (nome, agente, status, host, id)
./tools/sf list

# Mandar mensagem/instrução pro terminal de uma sessão JÁ EXISTENTE (irmã)
./tools/sf send <nome-ou-id> "texto da mensagem"
# opções: --no-enter (não aperta Enter automático depois do texto)

# Rodar comando num shell de OUTRO host, SEM agente no meio (custo zero de LLM)
./tools/sf command mac "ls ~/Documents"
# opções: --name (sessão nomeada/persistente), --dir, --timeout, --no-wait, --tail

# Compartilhar um arquivo gerado (imagem/PDF/relatório) de volta com o usuário
./tools/sf share <caminho-do-arquivo>
# opções: --to <nome-ou-id> (default: a própria sessão, via $TMUX)
```

### Todos os subcomandos

`delegate`, `check`, `request-context`, `metrics`, `dirs`, `list`, `hosts`,
`start`, `stop`, `send`, `command`, `notify`, `move`, `share`, `fetch`, `gpu`.
Rode `./tools/sf --help` e `./tools/sf <comando> --help` pra referência exata.
A skill `~/.claude/skills/sf-delegate/SKILL.md` traz o guia completo.

### Delegar para OUTRO host (`--host`, ex.: Duck Server 🦆)

Divide processamento: o filho roda na CPU/RAM do outro host, num worktree
próprio, e devolve o resultado pelo próprio SessionFlow.

```bash
./tools/sf dirs --host duck sessionflow          # pastas indexadas lá ([git] = repo)
./tools/sf delegate --host duck --dir sessionflow --worktree \
  --provider claude --task "..."                 # branch sf/<nome> em <repo>-clones/<nome>
# --push: filho faz git push da branch; sem ele, devolve <nome>.patch
./tools/sf check <nome> --summary                # baixa .md/.handoff.json/.patch p/ .sessionflow/handoff/
git am .sessionflow/handoff/<nome>.patch         # (modo patch) aplica no repo local
```

- `--host`: host_id, nome, hostname, emoji ou trecho único (`sf dirs` / `GET /workers`).
- `--dir` remoto: nome da pasta (resolvido no índice do host) ou caminho **entre aspas**
  (`--dir '~/x'`); sem aspas o zsh expande `~` pro home do Mac e o `sf` recusa.
- Ao terminar, o filho manda `✅ HANDOFF <nome> status=...` pro terminal do pai.

Pré-requisitos no host remoto: worker rodando; `sf` no PATH **dentro do WSL** com
`.env` válido; repo sob um `SESSIONFLOW_SCAN_ROOTS`; credencial git/gh se `--push`.
Repos em `/mnt/c` (9P) são lentos no WSL: prefira `~/`. Ao deletar a sessão no app,
o worktree remoto é removido com `git worktree remove --force` (descarta mudanças
não commitadas). A branch `sf/<nome>` permanece no repositório. `--push` exige
`--worktree` (sem worktree, o push iria pra branch atual do repo remoto).

### Rodar comando em OUTRO host sem gastar LLM (`command`)

Abre/usa um pane tmux com o **shell** do host alvo (`agent_type="command"`) —
sem Claude, sem modelo, sem custo de LLM — roda o comando e devolve a saída +
exit code.

```bash
./tools/sf command mac "ls ~/Documents"          # one-shot (a ponte do Mac, reusada)
./tools/sf command mac --name lab-mac "cd repo"  # apelido da ponte (p/ `sf send`)
./tools/sf command duck "screencapture -x /tmp/s.png"
cat script.sh | ./tools/sf command mac -          # stdin: conteúdo EXECUTADO
                                               # como script shell no host
```

- Opções: `--name`, `--dir`, `--timeout` (default 120s), `--no-wait` (injeta e
  sai), `--tail` (só imprime a tela), `--tail-lines`, `--color`, `--keep-alive`.
- O worker só expõe a TELA do pane; o CLI injeta o comando entre marcadores com
  nonce (`SF_BEGIN_<nonce>` … `SF_END_<nonce> rc=N`) e faz polling em
  `GET /sessions/{id}/screen` até o marcador de fim. O texto entre os marcadores
  É o output; o `rc` vira o exit code do `sf`. Timeout → exit 2 + dump da tela
  (o comando NÃO é cancelado).
- O comando roda num **sub-shell**: `exit N` (ou um `source` que termine em
  exit) NÃO mata o shell do pane — o rc volta e a sessão nomeada sobrevive.
  O `cd` dentro do comando PERSISTE na sessão (o cwd volta pro shell pai).
- **A ponte é identificada pelo HOST alvo, não pelo nome**: `sf command mac`
  reusa SEMPRE a mesma sessão do Mac (uma por host). O `--name` vira APELIDO
  (dá pra dirigir com `sf send <apelido>`) e não cria outra sessão.
- **A ponte é VOLÁTIL**: morre sozinha depois de 30 min sem uso
  (`COMMAND_BRIDGE_TTL_SECONDS`), morre em cascata quando a sessão dona dela
  acaba, e nunca passa de uma por host.
- **A API recusa a 2ª ponte do mesmo host** (`POST /sessions` → 409
  `command_bridge_exists`): quem quiser um shell reaproveita a ponte que já
  existe. No app, isso **abre** a ponte existente em vez de mostrar erro.
- `--keep-alive` marca uma ponte de **SERVIÇO** (ex.: relay): fica fora do
  TTL/cascata/dedupe e **não** é a ponte do host (o shell de um serviço não
  recebe comando alheio). Serviço = slot próprio, com nome próprio.
- CUIDADO: executa arbitrário no host, como o dono do tmux — sem camada de
  julgamento de agente.

### Ler arquivo de outro host (`fetch`, P2P via API)

Puxa 1 arquivo de qualquer host com worker online, sem SSH: a API publica
`fs_fetch` na fila do host alvo (`POST /hosts/{id}/fetch`), o worker de lá lê e
grava em `command_results`, e o `sf` faz polling em `GET /commands/{id}`.

```bash
./tools/sf fetch duck '~/logs/worker.log'                 # texto → stdout
./tools/sf fetch deepin /tmp/saida.png --out /tmp/x.png   # binário exige --out
./tools/sf fetch duck '~/proj/.sessionflow/handoff/x.md' --timeout 60
```

- Host: alias (`duck`, `deepin`, `hetzner`, `local`/`me`), emoji, nome ou host_id.
- `~` **entre aspas** (senão o zsh expande pro home local).
- Read-only, só dentro de `$HOME` ou `/tmp` do alvo (symlink/`..` pra fora é
  recusado), até 1 MiB (acima volta truncado).
- Host com heartbeat >5min → 409; erro do worker (path fora, inexistente,
  diretório) volta como mensagem; sem resposta → timeout (default 30s).

`SF_DEFAULT_HOST=<host>` no ambiente faz `sf delegate` sem `--host` ir pra esse host.

### Serviços de GPU sob demanda (`gpu`)

Os serviços de GPU do host (xtts, omnivoice, nemotron-asr) são pesados: ficam de
pé queimando VRAM sem ninguém falando com eles. O worker **liga sob demanda**
(quando o JARVIS vai sintetizar, p.ex.) e **desliga sozinho** quando ficam
ociosos. Este comando é o controle manual pela frente do backend:

```bash
./tools/sf gpu duck status          # todos os serviços (vem do heartbeat)
./tools/sf gpu duck status xtts     # estado do xtts (consulta o worker ao vivo)
./tools/sf gpu duck start xtts      # liga o xtts
./tools/sf gpu duck stop xtts       # desliga o xtts
```

- Publica um comando `gpu_service` na fila do worker do host alvo (mesmo
  mecanismo do `fetch`); o worker roda o `systemctl` e devolve o estado.
- `status` **sem nome** mostra todos os serviços (vem do `worker_status.gpu_services`
  do heartbeat — instantâneo, não acorda nada). Com nome, vai como comando
  (estado real do systemd).
- O worker só aceita serviço que esteja no **catálogo** dele
  (`SESSIONFLOW_GPU_SERVICES`); nome fora do catálogo é recusado.
- No host, o catálogo é quem sabe o que existe — este comando não valida o
  nome contra nada além do formato.

Catálogo (no host, env do worker):

```bash
# JSON (formato preferido)
SESSIONFLOW_GPU_SERVICES='{"xtts":{"unit":"xtts.service","scope":"system","port":5111}}'
# formato simples: nome:unit:scope:porto
SESSIONFLOW_GPU_SERVICES="xtts:xtts.service:system:5111 omnivoice:omnivoice.service:user:8790"
# segundos ocioso antes do auto-stop (0 = desliga o auto-stop)
SESSIONFLOW_GPU_IDLE_MIN=20
```

Escopo `system` usa `sudo -n systemctl start|stop <unit>` (o sudoers do host
libera só as units do catálogo); escopo `user` usa `systemctl --user`.

### Controlar a instância de OUTRO amigo (`--remote`)

`check`/`list`/`send`/`share` e os demais aceitam `--remote <alias>` pra falar
com uma instância SessionFlow de outro amigo/deploy (não a local) — útil quando
uma sessão sua quer acompanhar/mandar mensagem pra sessão de alguém em outra
instalação (ex.: a `pvax` do Heverton). Todos os subcomandos **menos `metrics`**
aceitam `--remote`. Credenciais ficam FORA do git, em
`~/.claude/secrets/sf-remotes.env`, no formato `<ALIAS>_BASE_URL`/`_EMAIL`/
`_PASSWORD` (alias em maiúsculas)::

```
HEVERTON_BASE_URL=https://api-sessionflow.anthonygabriel.com.br
HEVERTON_EMAIL=heverton@...
HEVERTON_PASSWORD=...
```

```bash
./tools/sf list --remote heverton
./tools/sf send pvax "oi, tudo certo por aí?" --remote heverton
```

`delegate` cria a sessão (POST /sessions), faz poll até `running` (~40s) e injeta
a tarefa (+ bloco de handoff) via `/input`. Grava um registro local em
`<DIR>/.sessionflow/handoff/<NOME>.json` para o `check` resolver por nome.

`check` imprime status/activity (1 linha) e o conteúdo do handoff se já existir.

`list`/`send` (Fatia 2) resolvem o caso "uma sessão quer passar contexto/
instrução pra outra sessão já rodando" sem precisar descobrir a API do
SessionFlow na mão: `list` acha o nome/id do alvo, `send` chama
`POST /sessions/{id}/input` na sessão-alvo (mesmo mecanismo do `sendKey`/
`sendInput` do frontend — roteamento multi-host automático). Resolução de
`target` aceita id, tmux_name/display_name exato, ou substring única.

`share` (Fatia 3) resolve o caso "gerei um arquivo (imagem/PDF/relatório) e o
usuário pode estar longe do computador pra ver": lê o arquivo do disco e faz
`POST /sessions/{id}/shared-files` (multipart, stdlib pura — sem dependências
externas). O app expõe um botão de arquivos na tela da sessão com link de
download/preview (`GET /shared-files/{id}/download`, `Content-Disposition:
inline` — abre a imagem/PDF direto no navegador). Sessão-alvo por `--to` ou,
por padrão, a própria sessão de onde `share` roda (detecta via `$TMUX`).
Teto de 200MB por arquivo.

### Instalação como skill

Uma cópia deste script + um `SKILL.md` vivem em `~/.claude/skills/sf-delegate/`
(fora do git), para o chefe (Claude Code) delegar automaticamente. Este arquivo
no repo é a fonte versionada.

### Limitações
- `opencode`: no ambiente atual a CLI imprime help e cai no shell em vez de
  subir num agente interativo (worker fica `detached`). Use `gemini`, `codex` ou
  `claude` até o launcher ser ajustado.

---

## `mcp_server.py` — os mesmos verbos do `sf` como tools MCP

Servidor **MCP (stdio)** que expõe o SessionFlow a um agente, sem terminal.
A API REST tem 87 endpoints, mas expô-los 1:1 como tools estouraria o system
prompt (cada schema entra em **todo** turno). Este servidor espelha os **verbos
do CLI** — a granularidade que um agente consegue usar de verdade.

| Tool | Equivale a |
|---|---|
| `list_sessions` | `sf list` |
| `list_hosts` | `sf hosts` |
| `delegate` | `sf delegate` |
| `check` | `sf check` |
| `send` | `sf send` |
| `request_context` | `sf request-context` |
| `fetch_file` / `share_file` | `sf fetch` / `sf share` |
| `run_command` | `sf command` |
| `move_session` | `sf move` |
| `start_host` / `stop_host` | `sf start` / `sf stop` |
| `gpu_service` | `sf gpu` |
| `list_dirs` | `sf dirs` |
| `metrics` / `notify` | `sf metrics` / `sf notify` |
| `api_call` | escape hatch p/ qualquer rota autenticada |

### Instalar / registrar

```bash
./tools/install-mcp.sh            # cria o venv, instala o SDK e registra
./tools/install-mcp.sh --print    # só imprime o JSON (colar à mão)
./tools/install-mcp.sh --uninstall
```

Grava em `~/.claude.json` (`mcpServers.sessionflow`) com caminhos absolutos —
o venv `tools/.venv-mcp/` é gitignored, então registrar à mão envelhece.
Para falar com uma instância de outro amigo/deploy:

```bash
SESSIONFLOW_MCP_REMOTE=heverton ./tools/install-mcp.sh
```

### Manter atualizado (`update-mcp.sh`)

O servidor MCP é um **processo stdio filho do Claude Code**: lê
`mcp_server.py` (e o `tools/sf` que importa) uma vez, no boot da sessão. Código
novo no disco não chega a um processo já em execução, e dependência nova nem
está no venv. Sem isso, cada release que mexe no MCP exige deploy manual em
cada host e as máquinas divergem em silêncio (uma com a tool nova, outra sem).

```bash
./tools/update-mcp.sh           # sincroniza deps + registro (idempotente)
./tools/update-mcp.sh --check   # diagnóstico: sai 1 se está desatualizado
```

É chamado no fim do `tools/self-update.sh`, então hosts com auto-update
recebem sozinhos. Dois diagnósticos distintos, de propósito:

| Mensagem | Significado | O que fazer |
|---|---|---|
| `desatualizado (servidor/CLI mudou)` | disco mudou, venv não | próxima execução do script sincroniza |
| `processo em execução está defasado` | disco certo, processo velho | **reiniciar o Claude Code** |

O segundo só o humano resolve — o processo em execução não relê o arquivo.
O servidor grava `tools/.venv-mcp/.loaded` no boot com o hash do que carregou;
o script compara com o hash do disco (`.state`) para detectar a defasagem.
Instalar dependências só acontece quando `mcp-requirements.txt` muda, para o
self-update de 30 em 30 minutos não martelar a rede.

### Como funciona (e por que não duplica HTTP)

Importa `tools/sf` como módulo (`SourceFileLoader`, mesmo padrão de
`test_sf.py`) e reusa `load_env`/`login`/`_request`/`resolve_host`. Um fix no
CLI propaga pro MCP automaticamente; nenhuma rota é reescrita aqui.

Duas armadilhas de servidor stdio, tratadas explicitamente:
- **`sf.die()` faz `sys.exit(1)`** — e `SystemExit` herda de `BaseException`,
  não de `Exception`. Convertido em `ToolError` (erro recuperável), senão
  derrubaria o processo inteiro.
- **stdout é o canal JSON-RPC.** Todo `print()` do CLI é capturado; um byte
  solto no stdout corromperia o protocolo.

### Decisões de contrato

- `list_sessions` filtra por padrão nas vivas (`running`/`waiting_input`) e
  aplica o filtro **na query** — o histórico passa de 290 sessões.
  `limit`/`total`/`truncated` evitam despejar tudo no contexto do agente.
- `api_call` recusa URL absoluta de propósito: a instância alvo é fixada pelo
  servidor (env), não escolhida pelo agente.

Testes: `tools/test_mcp_server.py` (26 casos, mock de rede; rode com
`tools/.venv-mcp/bin/python -m unittest test_mcp_server` a partir de `tools/`)
e `tools/test_update_mcp.py` (10 casos, sandbox de filesystem; `python3 -m
unittest test_update_mcp`).

### Colab: MCP tem que viver fora do `/root`

No Colab o worker roda como **root**, mas o agente (`claude`) roda como
**diego** e `/root` é `drwx------`. Um venv em `/root/sessionflow/tools/.venv-mcp`
fica registrado no `.claude.json` e **não executa** — o sintoma é "as tools do
MCP não aparecem", sem erro. Por isso o `tools/colab-selfboot.sh` instala em
`/opt/sessionflow-mcp` e roda o installer com `HOME=/home/diego`. Como a VM é
efêmera, isso acontece a cada boot. `tools/test_colab_selfboot.py` (4 casos,
`python3 -m unittest test_colab_selfboot`) trava esse contrato lendo o script.

---

## `watchdog/` — Auto-recuperação (Auto-Healing) do `sessionflow-api`

Monitor de saúde leve que roda em background a cada 60s. Se a API travar (deadlock de event loop, queda do RabbitMQ) por 2 checagens consecutivas, reinicia o container `sessionflow-api` automaticamente via `docker restart`.

```bash
./tools/watchdog/install-watchdog.sh
```

Ver [tools/watchdog/README.md](watchdog/README.md) para detalhes.

