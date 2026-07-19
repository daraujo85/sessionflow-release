# tools/

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

# Compartilhar um arquivo gerado (imagem/PDF/relatório) de volta com o usuário
./tools/sf share <caminho-do-arquivo>
# opções: --to <nome-ou-id> (default: a própria sessão, via $TMUX)
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
Teto de 50MB por arquivo.

### Instalação como skill

Uma cópia deste script + um `SKILL.md` vivem em `~/.claude/skills/sf-delegate/`
(fora do git), para o chefe (Claude Code) delegar automaticamente. Este arquivo
no repo é a fonte versionada.

### Limitações
- `opencode`: no ambiente atual a CLI imprime help e cai no shell em vez de
  subir num agente interativo (worker fica `detached`). Use `gemini`, `codex` ou
  `claude` até o launcher ser ajustado.
