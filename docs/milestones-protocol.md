# Protocolo de Marcos (SessionFlow)

Faz o agente de cada CLI **trabalhar em marcos** (pontos importantes / épicos) e
registrar o progresso num arquivo que o SessionFlow lê e mostra na Home
("Marcos"), com status **a fazer / em andamento / concluído**.

## Como funciona

O agente mantém, na **raiz do projeto** (o `work_dir` da sessão), um arquivo
**namespaced pelo nome da sessão** (evita colisão quando várias sessões
compartilham o mesmo diretório):

```
.sessionflow/milestones.<nome-da-sessão>.json
```

Quando o SessionFlow injeta a instrução, ele já manda o **nome exato do
arquivo** (com o nome da sessão embutido) — o agente só precisa escrevê-lo.
Para a instrução GLOBAL (estática), o agente descobre o nome da própria sessão
com `tmux display-message -p '#S'`. (Há fallback para `milestones.json` genérico
quando há só uma sessão no diretório — retrocompat.)

O **worker** lê esse arquivo a cada poucos segundos (só nas sessões ativas) e
reflete na Home — sem precisar de rede/CLI extra no host.

### Formato

```json
{
  "milestones": [
    { "id": "resp-desktop", "title": "Responsividade desktop/tablet", "status": "done" },
    { "id": "web-push",     "title": "Web Push (VAPID)",              "status": "doing" },
    { "id": "multi-host",   "title": "Suporte multi-host por SSH",     "status": "todo" }
  ]
}
```

- `id` — estável (kebab-case). Se faltar, o `title` é usado como chave.
- `title` — texto curto do marco.
- `status` — `todo` | `doing` | `blocked` | `done` (sinônimos aceitos:
  `in_progress`→`doing`, `completed`→`done`).
- Marcos **removidos** do arquivo somem da Home (o arquivo é a fonte da verdade).

## Instrução para o agente (cole no config global do CLI)

> **Trabalhe sempre em marcos.** No início de uma tarefa relevante, quebre o
> trabalho em marcos (pontos importantes/épicos) e **mantenha o arquivo
> `.sessionflow/milestones.<sua-sessão-tmux>.json`** (descubra o nome com
> `tmux display-message -p '#S'`) na raiz do projeto atualizado:
> crie cada marco com `status: "todo"`, mude para `"doing"` ao começar,
> `"blocked"` se travar, e `"done"` ao concluir. Use o formato:
> `{"milestones":[{"id":"<kebab>","title":"<curto>","status":"<todo|doing|blocked|done>"}]}`.
> Atualize o arquivo assim que o status mudar. Não remova marcos concluídos no
> meio do trabalho. Mantenha 3–8 marcos por vez (granularidade de épico, não de
> micro-tarefa).

## Onde instalar (por CLI)

| CLI         | Arquivo de instrução global (sempre lido) |
|-------------|-------------------------------------------|
| Claude Code | `~/.claude/CLAUDE.md`                      |
| Codex CLI   | `~/.codex/AGENTS.md` (ou o `AGENTS.md` do projeto) |
| Gemini CLI  | `~/.gemini/GEMINI.md`                      |
| OpenCode    | arquivo de regras/instruções global do OpenCode |

Cole a "Instrução para o agente" acima em cada um. (Instrução **global** = o
agente lê toda sessão → mais confiável que skill sob demanda.)

### Alternativa: auto-injeção pelo SessionFlow

Em vez (ou além) do config global, o SessionFlow pode **enviar essa instrução
automaticamente** ao criar a sessão pela tela "Nova sessão". É zero-setup, mas a
instrução pode sair do contexto em sessões longas (menos confiável que a global).
As duas convivem: a global é o lastro; a auto-injeção é conveniência.
