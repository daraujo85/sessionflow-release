# Distribuição de skills — contrato (MVP)

Spec de produto: `docs/specs/skills-google-drive-mvp.md`.
Objetivo: skill criada no Mac vira catálogo central; QUALQUER host sob este
servidor (Mac, Deepin, Duck, Hetzner, Colab) pede sob demanda, recebe, valida e
instala. Versão nova → mesmo fluxo. Este documento é o CONTRATO entre as 3
partes (API, scanner, `sf skill`); mudar aqui antes de mudar o código.

## Identidade e versão

- `skill_id` = `<scope>:<name>`. `scope` = `global` (`~/.claude/skills`) ou
  `project:<repo-basename>` (`<repo>/.claude/skills`). `name` = nome da pasta.
- `content_hash` = sha256 canônico: para cada arquivo incluído, ordenado por
  caminho relativo POSIX, alimentar `path + "\0" + sha256(bytes) + "\n"`.
  Sem timestamps/permissões. Mesmo conteúdo ⇒ mesmo hash em qualquer máquina.
- `version_id` = primeiros 16 hex do `content_hash`. Versões são imutáveis;
  publicar o mesmo hash de novo é idempotente (200, sem upload).
- "Mais recente" = maior `published_at` com `status=published` no catálogo.

## Pacote

- `tar.gz` com caminhos relativos à pasta da skill (sem prefixo de pasta).
- Exclui: `.git/ .venv/ node_modules/ __pycache__/ .DS_Store *.pyc .env*`,
  `*.key *.pem`, arquivos > 20 MiB e symlinks que apontem pra fora da pasta
  (symlink interno vira arquivo regular). Pacote total ≤ 100 MiB.
- `package_sha256` = sha256 dos bytes do tar.gz (verificação de download).
- `manifest` (opcional, `skill.manifest.json` na pasta): `setup` (comando),
  `verify` (comando), `required_env` (nomes), `supported_platforms`
  (`darwin`/`linux`). Sem manifesto = skill documental (sem setup).

## API (FastAPI, auth Bearer igual ao resto)

Coleções Mongo: `skills` (1 doc por skill_id) e `skill_versions`
(1 doc por skill_id+version_id). Arquivo no Drive guardado por `drive_file_id`.

| Método | Rota | Corpo/resposta |
|---|---|---|
| GET | `/skills?q=` | `[{skill_id, name, scope, description, latest_version_id, updated_at}]` |
| GET | `/skills/{skill_id}` | skill + `versions: [{version_id, content_hash, package_sha256, size, published_at, manifest}]` |
| POST | `/skills/publish` | multipart: `meta` (JSON: skill_id, name, scope, description, content_hash, package_sha256, size, manifest, origin_host, origin_path) + `package` (arquivo). 201 nova versão / 200 já existia. 422 se sha256 do upload ≠ `package_sha256`. |
| GET | `/skills/{skill_id}/versions/{version_id}/package` | streaming dos bytes (`application/gzip`), header `X-Package-Sha256`. |
| POST | `/sessions/{session_id}/skills/resolve` | `{skill_id, version_id?}` → `{skill_id, version_id, content_hash, package_sha256}`. Fixa atomicamente em `sessions.skill_pins[skill_id]` (`$setOnInsert`-style: só grava se ausente). Já fixado com outra versão explícita → 409. Sessão com `parent` herda o pin do pai. |

`skill_id` na URL vai url-encoded (`global%3Asf-delegate`).

### Storage

Interface `SkillStorage` (`put(name, stream) -> file_id`, `open(file_id) -> async iter bytes`).
Impl `GoogleDriveStorage`: OAuth refresh token (env `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`), pasta raiz `SKILLS_DRIVE_FOLDER_ID`
(ou cria "sessionflow-skills" e loga o id). Upload resumable/multipart via httpx,
download `files/{id}?alt=media` em streaming. Impl `LocalStorage` (dir) pra
testes/dev quando as envs do Google não existem. Nada fica salvo no backend além
de temporário com limpeza garantida.

## Scanner (Mac) — `sf skill scan`

- Raízes: `~/.claude/skills` + `<repo>/.claude/skills` para cada repo em
  `SF_SKILL_PROJECT_ROOTS` (default `~/Documents/projects/*`, profundidade 1).
- Pasta de skill = contém `SKILL.md`. `description` = campo do frontmatter.
- Estado local leve: `~/.sessionflow/skills-scan.json` `{skill_id: content_hash}`.
  Hash igual ao último publicado → pula. Mudou → empacota em tmp, publica,
  apaga tmp (sempre, inclusive em falha). Nunca apaga versão remota.
- `--dry-run` lista o que publicaria. Saída: 1 linha por skill (`=`, `+`, `!erro`).

## Consumidor (qualquer host) — `sf skill ...`

- `sf skill list [q]` — catálogo.
- `sf skill ensure <nome|skill_id> [--version V]` — resolve (pin da sessão
  atual via `$TMUX`/`detect_parent`; fora de sessão usa a latest), baixa se o
  cache não tiver, valida `package_sha256`, extrai em tmp com proteção contra
  path traversal/links/absolutos, promove atomicamente para
  `~/.claude/skills/<name>` e grava `.sf-skill.json` (`skill_id, version_id,
  content_hash, installed_at`). Se a pasta já existe com o mesmo `content_hash`
  → reutiliza (não baixa). Se existe SEM `.sf-skill.json` (skill local feita à
  mão) → não sobrescreve; erro claro (a não ser `--force`).
  Manifesto com `setup` → roda com timeout, depois `verify`; só marca
  `setup_status=ready` se `verify` passar. Plataforma não suportada → erro.
- Cache de pacotes: `~/.cache/sessionflow/skills/<package_sha256>.tar.gz`, LRU
  com teto `SF_SKILL_CACHE_MB` (default 1024). Lock por skill (`fcntl`) evita
  instalação duplicada concorrente.
- Saída de sucesso (stdout, 1 linha JSON): `{skill_id, version_id, content_hash,
  path, setup_status, cache_hit}`. Falha: exit≠0 + `{error, stage, reason}`.

## Fora do MVP

MinIO, marketplace, sync bidirecional, update forçado em sessão ativa, instalar
tudo antecipadamente, apagar versões remotas.
