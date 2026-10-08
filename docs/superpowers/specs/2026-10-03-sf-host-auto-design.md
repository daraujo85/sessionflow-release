# `sf delegate --host auto` — escolha automática do host mais livre

**Data:** 2026-10-03 · **Status:** aprovado (design em chat)

## Objetivo

Novo modo **opcional** de delegação em que o `sf` escolhe sozinho o host com
mais recurso livre (CPU + RAM) entre os que conseguem rodar a tarefa. Quem
delegou recebe o ID da sessão **e** qual host foi escolhido.

## Não-objetivos / compatibilidade

Comportamentos atuais ficam **intactos**:

| Comando | Comportamento |
|---|---|
| `sf delegate ...` (sem `--host`) | local (como hoje) |
| `sf delegate --host <alias>` | host indicado (como hoje) |
| `sf delegate --host auto` | **novo** — escolha automática |

Sem clone automático de repo em host que não tem a pasta (YAGNI).

## Design

### 1. Worker — heartbeat publica carga + agents

`runner.heartbeat_loop` adiciona ao `$set` de `worker_status`:

- `metrics`: `{cpu_pct, mem_pct, mem_total_gb, mem_avail_gb}` — via
  `host_metrics.sample_load()` (nova função; `mem_avail_gb` =
  `psutil.virtual_memory().available`). `cpu_pct` é suavizado por EMA
  (α=0.3) mantida em memória no loop — 1 amostra isolada é ruidosa.
- `agents`: `{<agent>: bool}` via `shutil.which` para claude, codex, gemini,
  opencode, agy. Recalculado a cada ciclo (como `tts_engines`).

No WSL2 o psutil vê a RAM da VM, não a do Windows — correto para o score,
porque o agent roda dentro da VM.

### 2. API — `/workers` repassa

`WorkerOut` ganha `metrics: dict | None` e `agents: dict | None`, copiados do
doc. Doc antigo → `None`. Sem migração.

### 3. CLI — `--host auto`

Função pura `pick_host(workers, provider, local_id, dir_ok, need_remote)` →
`(escolhido | None, ranking, descartados)`, onde `dir_ok(worker) -> bool` é
injetado (local: `os.path.isdir`; remoto: `fetch_dirs` pelo basename do
`--dir`/cwd).

**Filtros** (motivo registrado para cada descarte):

1. online e com `metrics`;
2. `agents[provider] is True` (sem campo = worker antigo = fora);
3. com `--worktree`/`--push`: só remotos;
4. piso: `cpu_pct <= 85` e `mem_avail_gb >= 2`;
5. pasta existe no host (`dir_ok`) — checado por último (custa rede).

**Score:** `mem_avail_gb × (1 − cpu_pct/100)`. Maior vence; empate (±5%) →
host local.

**Sem candidato:** cai pro local (fluxo de hoje) e imprime os motivos. Se o
local também foi exigido fora (`--worktree`), erro com os motivos.

**Execução:** escolhido local → fluxo local atual; remoto → `delegate_remote`
com o `--dir` resolvido naquele host (path absoluto do match).

**Saída:**

```
host auto → 🦆 Duck Server (67122d03…) score 15.8 | cpu 1% | ram livre 16.0G
  descartados: 🍎 Macbook Air (ram livre 0.4G < 2G)
delegado ✅
  handle:  ...  (id=...)
  host:    🦆 Duck Server (67122d03…)
```

Registro local ganha `host_id`, `auto: true`, `auto_ranking`.

**Extra:** `sf hosts` mostra `cpu X% | ram livre Y/ZG | agents=...`.

## Testes

- worker: `sample_load()` formato; `installed_agents()` com `which` mockado.
- api: `/workers` repassa `metrics`/`agents`; `None` em doc antigo.
- cli (`tools/test_sf.py`): tabela para `pick_host` — filtro de agent, pasta
  ausente, piso, `need_remote`, empate→local, nenhum→None.
