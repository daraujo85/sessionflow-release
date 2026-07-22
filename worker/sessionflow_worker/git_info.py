"""Branch git ativa de um work_dir — leitura/listagem/troca (checkout).

Pensado pro badge de branch no card de sessão (Detalhe): mostra em qual
branch o projeto daquela sessão está, deixa listar as outras e trocar de
verdade (``git checkout``). Tudo best-effort e síncrono (subprocess rápido,
~ms) — chamadas vêm de ``run_in_executor`` pra não bloquear o loop asyncio.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: Nomes de branch só com caracteres comuns em nomes reais (letras, números,
#: /, -, _, .) e SEM começar com "-" — evita que um valor controlado pelo
#: usuário (vindo da API) seja interpretado como uma FLAG do git (ex.:
#: "--upload-pack=...") ao ser passado pro subprocess.
_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _run_git(work_dir: str, args: list[str], timeout: float = 5.0) -> str | None:
    # `-C` é passado pro binário git puro, que (como qualquer processo) não
    # expande `~` sozinho — só o shell faz isso.
    resolved = str(Path(work_dir).expanduser())
    try:
        out = subprocess.run(
            ["git", "-C", resolved, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_git_repo(work_dir: str | None) -> bool:
    if not work_dir:
        return False
    # work_dir vem como foi salvo pelo tmux (ex.: "~/Documents/projects/x") —
    # `~` só é expandido pelo SHELL, nunca pela lib padrão sozinha.
    return (Path(work_dir).expanduser() / ".git").exists()


def current_branch(work_dir: str) -> str | None:
    """Branch (ou tag/commit, no estado "detached HEAD") ativa agora."""
    if not is_git_repo(work_dir):
        return None
    return _run_git(work_dir, ["rev-parse", "--abbrev-ref", "HEAD"])


def list_branches(work_dir: str) -> list[str]:
    """Branches locais, no formato curto (``main``, ``feature/x``…)."""
    if not is_git_repo(work_dir):
        return []
    out = _run_git(work_dir, ["branch", "--format=%(refname:short)"])
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def checkout_branch(work_dir: str, branch: str) -> tuple[bool, str]:
    """Troca a branch ativa do repo em ``work_dir``.

    Devolve ``(ok, mensagem)`` — ``mensagem`` é o nome da branch atual
    (após a troca, sucesso ou não) ou um erro curto pro usuário entender
    o que bloqueou (ex.: mudanças não commitadas).
    """
    if not is_git_repo(work_dir):
        return False, "não é um repositório git"
    if not _BRANCH_NAME_RE.match(branch):
        return False, "nome de branch inválido"
    try:
        out = subprocess.run(
            ["git", "-C", work_dir, "checkout", branch],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "checkout expirou (timeout)"
    if out.returncode != 0:
        # git manda o motivo real (ex. "error: Your local changes...") no
        # stderr — corta pra caber num toast/badge.
        reason = (out.stderr or out.stdout or "falhou").strip().splitlines()
        return False, (reason[-1] if reason else "checkout falhou")[:200]
    return True, current_branch(work_dir) or branch
