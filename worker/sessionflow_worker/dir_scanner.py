"""Descoberta e filtro de diretórios do host (TMUX-08).

Lógica pura de varredura/filtro/formatação de sugestões, mais persistência
(upsert idempotente em ``host_directories``) e agendamento do scan.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, UpdateOne

# Raízes permitidas por padrão; as funções recebem ``roots`` por parâmetro.
# Inclui ``~/Documents/projects`` (onde ficam os projetos reais do usuário),
# além de ``~/dev``/``~/work`` por portabilidade (raízes inexistentes são puladas).
DEFAULT_ROOTS: list[Path] = [
    Path.home() / "Documents" / "projects",
    Path.home() / "dev",
    Path.home() / "work",
]

# Nomes de diretório sempre ignorados na varredura.
IGNORED_NAMES: frozenset[str] = frozenset({".git", "node_modules", ".venv"})

# Coleção padrão de persistência das sugestões de diretório.
HOST_DIRECTORIES_COLLECTION = "host_directories"


def _is_ignored(name: str) -> bool:
    """Diretório oculto (começa com ``.``) ou nome na blocklist."""
    return name.startswith(".") or name in IGNORED_NAMES


def scan_roots(roots: list[Path], max_depth: int = 3) -> list[Path]:
    """Varre as raízes dadas até ``max_depth`` níveis e retorna diretórios.

    Ignora arquivos e diretórios ocultos/blocklist. Nunca varre fora das
    raízes. ``max_depth`` conta níveis abaixo da raiz (1 = filhos diretos).
    Raízes inexistentes são puladas silenciosamente.
    """
    found: list[Path] = []

    for root in roots:
        if not root.is_dir():
            continue
        _walk(root, depth=0, max_depth=max_depth, out=found)

    return found


def _walk(current: Path, depth: int, max_depth: int, out: list[Path]) -> None:
    """Recursão auxiliar de varredura, limitada por ``max_depth``."""
    if depth >= max_depth:
        return

    try:
        entries = sorted(current.iterdir())
    except OSError:
        # Sem permissão / sumiu durante a varredura: ignora.
        return

    for entry in entries:
        if not entry.is_dir() or _is_ignored(entry.name):
            continue
        out.append(entry)
        _walk(entry, depth=depth + 1, max_depth=max_depth, out=out)


def filter_dirs(dirs: list[Path], query: str, limit: int = 6) -> list[Path]:
    """Filtra/ordena diretórios para sugestão.

    - ``query`` vazio: retorna os ``limit`` mais recentes (mtime desc).
    - ``query`` preenchido: retorna os que contêm o termo (case-insensitive)
      no path, limitado a ``limit``, também por mtime desc.
    """
    term = query.strip().lower()

    if term:
        candidates = [d for d in dirs if term in str(d).lower()]
    else:
        candidates = list(dirs)

    candidates.sort(key=_safe_mtime, reverse=True)
    return candidates[:limit]


def _safe_mtime(path: Path) -> float:
    """mtime do diretório; 0.0 se inacessível (não quebra a ordenação)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def to_suggestion(path: Path) -> dict:
    """Estrutura amigável p/ a API: ``{path, parent, name, root}``.

    Caminhos sob o home são colapsados com ``~``.
    """
    return {
        "path": _collapse_home(path),
        "parent": _collapse_home(path.parent),
        "name": path.name,
        "root": _collapse_home(path.parent),
    }


def _collapse_home(path: Path) -> str:
    """Substitui o prefixo do home por ``~`` quando aplicável."""
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


async def _ensure_path_index(
    db: AsyncIOMotorDatabase,
    collection: str,
) -> None:
    """Garante o índice único em ``path`` (idempotente)."""
    await db[collection].create_index(
        [("path", ASCENDING)],
        name="uq_path",
        unique=True,
    )


async def persist_scan(
    db: AsyncIOMotorDatabase,
    roots: list[Path] = DEFAULT_ROOTS,
    collection: str = HOST_DIRECTORIES_COLLECTION,
) -> int:
    """Varre ``roots`` e faz upsert idempotente das sugestões em Mongo.

    Roda ``scan_roots``, converte via ``to_suggestion`` e faz upsert por
    ``path`` (chave única) na coleção dada, gravando também ``scanned_at``.
    Garante o índice único em ``path`` antes do upsert. Reexecuções não
    duplicam: o mesmo ``path`` apenas atualiza ``scanned_at``.

    Retorna o número de documentos upsertados (inseridos + atualizados).
    """
    await _ensure_path_index(db, collection)

    suggestions = [to_suggestion(p) for p in scan_roots(roots)]
    if not suggestions:
        return 0

    now = datetime.now(timezone.utc)
    operations = [
        UpdateOne(
            {"path": s["path"]},
            {"$set": {**s, "scanned_at": now}},
            upsert=True,
        )
        for s in suggestions
    ]

    result = await db[collection].bulk_write(operations, ordered=False)
    return result.upserted_count + result.modified_count


async def schedule_scan(
    db: AsyncIOMotorDatabase,
    interval_seconds: float,
    roots: list[Path] = DEFAULT_ROOTS,
    collection: str = HOST_DIRECTORIES_COLLECTION,
) -> None:
    """Loop infinito de persistência: scan no boot e a cada ``interval_seconds``.

    Pensado para rodar como task de fundo do Worker. Encerra apenas via
    cancelamento da task.
    """
    while True:
        await persist_scan(db, roots=roots, collection=collection)
        await asyncio.sleep(interval_seconds)
