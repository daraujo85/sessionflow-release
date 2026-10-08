"""Funções puras do "mover sessão entre hosts" (``move_out`` / ``move_in``).

Sem Mongo/tmux/RabbitMQ: só git (subprocess síncrono, rápido), leitura/escrita
do ``.jsonl`` da conversa do claude e o empacotamento (gzip + chunks) que
viaja pela coleção ``session_transfers``. Os handlers do
:mod:`~sessionflow_worker.command_consumer` orquestram estas peças.

Erros "de negócio" voltam como mensagem pt-BR (``str``) — ``None`` = ok —
para o handler gravar em ``moving.error`` sem precisar traduzir exceção.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import uuid
from pathlib import Path

from sessionflow_worker.metrics import _encode_work_dir

#: Teto de cada chunk **bruto** fatiado antes do gzip. Cada chunk vira ~2-3 MB
#: depois de comprimido (texto do jsonl comprime bem), então N chunks de 8 MB
#: brutos cabem em um doc Mongo de 16 MB sem estourar. O gzip comprime cada
#: chunk individualmente (não o payload inteiro) — necessário pra mover
#: sessões com conversa grande (ex.: estudajunto 122 MB raw comprime pra
#: ~30 MB, que dividido em 15 chunks vira 15 docs, cada um ~2 MB).
CHUNK_SIZE = 8 * 1024 * 1024

_GIT_TIMEOUT_S = 15
#: fetch/pull falam com a rede — mais folga.
_GIT_NET_TIMEOUT_S = 120


def _git(work_dir: str, *args: str, timeout: float = _GIT_TIMEOUT_S) -> tuple[int, str, str]:
    """Roda ``git -C <work_dir> ...`` → ``(returncode, stdout, stderr)``.

    Nunca levanta: git ausente/timeout viram ``returncode`` 127/124. Sem
    prompt de credencial (``GIT_TERMINAL_PROMPT=0``) — o worker é headless.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        out = subprocess.run(
            ["git", "-C", os.path.expanduser(work_dir), *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)}: tempo esgotado"
    except OSError as exc:
        return 127, "", str(exc)
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def _is_repo(work_dir: str) -> bool:
    # ``git -C ''`` rodaria no cwd do worker — vazio nunca é repo.
    if not work_dir:
        return False
    rc, out, _ = _git(work_dir, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def _dirty_msg(host: str) -> str:
    return f"Há mudanças não commitadas em {host}: faça commit/push antes de mover."


def git_block_reason(work_dir: str, host: str = "este host") -> str | None:
    """Motivo p/ BLOQUEAR o ``move_out`` por causa do git, ou ``None``.

    ``status --porcelain`` não vazio (inclui untracked) → mudanças não
    commitadas; ``@{u}..HEAD`` > 0 → commits não enviados (o destino não
    acharia). Sem upstream ou fora de repo → ``None`` (nada a proteger).
    """
    if not _is_repo(work_dir):
        return None
    rc, out, _ = _git(work_dir, "status", "--porcelain")
    if rc == 0 and out:
        return _dirty_msg(host)
    rc, out, _ = _git(work_dir, "rev-list", "--count", "@{u}..HEAD")
    if rc == 0 and out.isdigit() and int(out) > 0:
        return f"Há commits não enviados em {host}: faça push antes de mover."
    return None


def current_branch(work_dir: str) -> str | None:
    """Branch atual de ``work_dir``; ``None`` fora de repo ou em HEAD detached."""
    if not work_dir:
        return None
    rc, out, _ = _git(work_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not out or out == "HEAD":
        return None
    return out


def git_origin(work_dir: str) -> str | None:
    """URL do remote ``origin`` de ``work_dir``; ``None`` fora de repo/sem origin.

    O ``_handle_move_out`` lê isso e envia no payload do ``move_in`` pro
    destino saber de onde clonar caso a pasta do repo não exista lá
    (ex.: host novo, repo ainda não clonado).
    """
    if not _is_repo(work_dir):
        return None
    rc, out, _ = _git(work_dir, "config", "--get", "remote.origin.url")
    if rc != 0 or not out:
        return None
    return out


#: clone fala com rede — bem mais folga que ``_GIT_NET_TIMEOUT_S`` (repo
#: grande ou link lento pode demorar).
_GIT_CLONE_TIMEOUT_S = 300


def clone_repo(origin: str, target_work_dir: str) -> str | None:
    """``git clone <origin> <target_work_dir>``; erro pt-BR ou ``None``.

    Edge: se ``target_work_dir`` existe mas está vazio (clone anterior
    interrompido) → apaga antes de clonar. Se existe com conteúdo **e não
    é repo** → recusa (defesa contra destruir trabalho alheio).
    """
    if not origin or not target_work_dir:
        return "clone_repo exige origin e target_work_dir"
    target_abs = os.path.abspath(os.path.expanduser(target_work_dir))
    if os.path.isdir(target_abs):
        if _is_repo(target_abs):
            return None  # já é repo: prepare_repo cuida do fetch/checkout
        if os.listdir(target_abs):
            return (
                f"{target_abs} existe e não é repositório git; "
                "remova ou aponte para outra pasta antes de mover."
            )
        # Vazio: clone anterior interrompido, ou criado por engano. Limpa.
        try:
            (Path(target_abs) / ".git").unlink(missing_ok=True)
            for child in Path(target_abs).iterdir():
                if child.is_dir() and not child.is_symlink():
                    import shutil
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError as exc:
            return f"não consegui limpar {target_abs} antes do clone: {exc}"
    # Garante que o pai existe (``git clone`` cria o target mas não ancestrais).
    parent = Path(target_abs).parent
    parent.mkdir(parents=True, exist_ok=True)
    # ``git clone`` direto (sem ``-C``) para evitar armadilha de cwd=dest
    # quando o target ainda não existe.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        out = subprocess.run(
            ["git", "clone", "--quiet", origin, target_abs],
            capture_output=True, text=True, timeout=_GIT_CLONE_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return f"git clone de {origin}: tempo esgotado ({_GIT_CLONE_TIMEOUT_S}s)"
    except OSError as exc:
        return f"git clone de {origin}: {exc}"
    if out.returncode != 0:
        return f"git clone de {origin} em {parent} falhou: {out.stderr.strip()}"
    return None


def _projects_dir(projects_dir: Path | None) -> Path:
    if projects_dir is not None:
        return projects_dir
    return Path(os.path.expanduser("~/.claude/projects"))


def _check_sid(claude_session_id: str) -> str:
    """Garante que ``claude_session_id`` é um UUID canônico.

    O valor vem do Mongo e vira nome de arquivo (glob/path) e argumento do
    ``claude --resume`` — qualquer outra coisa (``../``, ``*``, ``;``) é
    rejeitada com ``ValueError``.
    """
    try:
        ok = str(uuid.UUID(claude_session_id)) == claude_session_id.lower()
    except (ValueError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise ValueError(f"claude_session_id inválido: {claude_session_id!r}")
    return claude_session_id

def find_claude_jsonl(
    claude_session_id: str, projects_dir: Path | None = None
) -> Path | None:
    """Acha ``~/.claude/projects/*/<uuid>.jsonl`` pelo uuid (dispensa o slug)."""
    if not claude_session_id:
        return None
    _check_sid(claude_session_id)
    matches = sorted(_projects_dir(projects_dir).glob(f"*/{claude_session_id}.jsonl"))
    return matches[0] if matches else None


def jsonl_target_path(
    work_dir: str, claude_session_id: str, projects_dir: Path | None = None
) -> Path:
    """Onde o claude do destino procura a conversa de ``work_dir``."""
    _check_sid(claude_session_id)
    return _projects_dir(projects_dir) / _encode_work_dir(work_dir) / f"{claude_session_id}.jsonl"


def pack(data: bytes) -> list[bytes]:
    """Fatia ``data`` em pedaços de :data:`CHUNK_SIZE` brutos e gzipa cada um.

    Por que cada chunk **separado** (e não gzip do payload inteiro fatiado
    depois): gzip comprime ~3× no texto do jsonl. Um jsonl de 100 MB vira
    ~33 MB comprimidos — não cabe em 1 doc Mongo (16 MB). Fatiando o RAW
    em 8 MB por chunk e comprimindo cada um individualmente, cada chunk
    comprimido fica ~2-3 MB, e o conjunto (chunks) cabe no doc. Cada
    chunk é gravado como um item do array ``chunks`` no doc
    ``session_transfers``.

    Lista vazia se ``data`` for vazio (caso degenerado — não acontece na
    prática porque jsonl sempre tem ao menos 1 linha).
    """
    if not data:
        return []
    return [
        gzip.compress(data[i : i + CHUNK_SIZE])
        for i in range(0, len(data), CHUNK_SIZE)
    ]


def unpack(chunks: list[bytes]) -> bytes:
    """Inverso de :func:`pack`: descomprime cada chunk e concatena.

    Tolera chunks em qualquer formato (gzip individuais do ``pack`` novo
    ou gzip único de versões antigas) — cada um é tentado como gzip e,
    se falhar, tratado como texto/compressão antiga. ``b"".join`` puro
    (sem decompress) trata o caso antigo onde o payload inteiro estava
    em 1 chunk; gzip.decompress trata o caso novo onde cada chunk é
    gzip individual.
    """
    parts: list[bytes] = []
    for c in chunks:
        b = bytes(c)
        try:
            parts.append(gzip.decompress(b))
        except (OSError, EOFError, gzip.BadGzipFile):
            # Formato antigo: 1 chunk = gzip do payload inteiro
            parts.append(b)
    return b"".join(parts)


def rewrite_cwd(jsonl_text: str, new_cwd: str) -> str:
    """Troca o campo ``cwd`` de cada linha JSON (objeto) que o tenha.

    Linha que não é JSON válido, não é objeto ou não tem ``cwd`` fica
    exatamente como estava (inclusive o ``\\r`` de CRLF). Best-effort: o
    formato interno do claude pode mudar entre versões.
    """
    out: list[str] = []
    for line in jsonl_text.split("\n"):
        body, cr = (line[:-1], "\r") if line.endswith("\r") else (line, "")
        try:
            obj = json.loads(body) if body.strip() else None
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "cwd" in obj:
            obj["cwd"] = new_cwd
            line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + cr
        out.append(line)
    return "\n".join(out)


def prepare_repo(work_dir: str, branch: str | None, host: str = "este host") -> str | None:
    """Deixa ``work_dir`` do destino na ``branch`` atualizada; erro pt-BR ou ``None``.

    Fora de repo → ``None`` (nada a fazer). Sujo → bloqueia (não pisa em
    trabalho local). Senão ``fetch`` → ``checkout <branch>`` (se dada) →
    ``pull --ff-only`` (só se a branch tiver upstream).
    """
    if not _is_repo(work_dir):
        return None
    rc, out, _ = _git(work_dir, "status", "--porcelain")
    if rc == 0 and out:
        return _dirty_msg(host)

    rc, remotes, _ = _git(work_dir, "remote")
    if rc == 0 and remotes:
        rc, _, err = _git(work_dir, "fetch", "--quiet", timeout=_GIT_NET_TIMEOUT_S)
        if rc != 0:
            return f"git fetch falhou em {host}: {err}"
    if branch:
        rc, _, err = _git(work_dir, "checkout", "--quiet", branch)
        if rc != 0:
            return f"git checkout {branch} falhou em {host}: {err}"
    rc, _, _ = _git(work_dir, "rev-parse", "--abbrev-ref", "@{u}")
    if rc == 0:
        rc, _, err = _git(work_dir, "pull", "--ff-only", "--quiet", timeout=_GIT_NET_TIMEOUT_S)
        if rc != 0:
            return f"git pull --ff-only falhou em {host}: {err}"
    return None
