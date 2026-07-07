# tools/

## `sf` — delega tarefa a worker de outro provedor (Fatia 1)

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
```

`delegate` cria a sessão (POST /sessions), faz poll até `running` (~40s) e injeta
a tarefa (+ bloco de handoff) via `/input`. Grava um registro local em
`<DIR>/.sessionflow/handoff/<NOME>.json` para o `check` resolver por nome.

`check` imprime status/activity (1 linha) e o conteúdo do handoff se já existir.

### Instalação como skill

Uma cópia deste script + um `SKILL.md` vivem em `~/.claude/skills/sf-delegate/`
(fora do git), para o chefe (Claude Code) delegar automaticamente. Este arquivo
no repo é a fonte versionada.

### Limitações
- `opencode`: no ambiente atual a CLI imprime help e cai no shell em vez de
  subir num agente interativo (worker fica `detached`). Use `gemini`, `codex` ou
  `claude` até o launcher ser ajustado.
