# Code Conventions

*Forward-looking (greenfield) — convenções alvo, a serem reforçadas pelo lint/CI conforme o código nasce.*

## Python (Worker + API)

**Files:** `snake_case.py`. Módulos por responsabilidade (`tmux_runtime.py`, `agent_launcher.py`).
**Functions/Methods:** `snake_case`. Async-first (`async def`) onde houver I/O (Mongo, Rabbit, subprocess).
**Variables:** `snake_case`. **Constants:** `UPPER_SNAKE_CASE`.
**Classes/Models:** `PascalCase` (ex: `SessionDoc`, `Command`).

**Type safety:** type hints obrigatórios em assinaturas públicas; modelos via Pydantic (API) / dataclasses ou Pydantic (Worker).
**Imports:** ordenados pelo ruff (stdlib → terceiros → locais).
**Error handling:** exceções específicas; nunca engolir erro silenciosamente; operações de fila são idempotentes e fazem ack manual só após sucesso.
**Lint/format:** `ruff` é a autoridade (check + format). Nada de estilo manual divergente.
**Comments:** explicar o *porquê*, não o *o quê*; densidade baixa, código autoexplicativo.

## Angular (Frontend)

**Files:** kebab-case (`session-list.component.ts`, `sessions.service.ts`).
**Components/Services:** `PascalCase` na classe; seletor `sf-` prefix (ex: `sf-session-card`).
**Estado:** serviços + signals; evitar lógica pesada no template.
**Estilo:** usar tokens do Prata Digital DS (CSS custom properties); tema dark do mockup como referência visual.

## Convenções gerais

**Commits (atômicos, 1 por task):** Conventional Commits — `feat(worker): ...`, `fix(api): ...`, `chore(infra): ...`, `test(worker): ...`.
**Segredos:** nunca em código nem em `.specs/`; só em `.env` (gitignored). URIs por ambiente: `*_HOST` (Worker no host) vs nome de serviço (API no Docker).
**Nomes de domínio:** filas/coleções com escopo `sessionflow` (AD-010).
**Idioma:** docs/UI em PT-BR; identificadores de código em inglês.
