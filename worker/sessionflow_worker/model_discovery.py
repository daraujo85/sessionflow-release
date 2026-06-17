"""Descoberta dos modelos REAIS disponíveis no host por agente (MODEL-01).

Os modelos exibidos no "Criar sessão" do front devem refletir o que o host
**de fato** tem instalado/configurado — não uma lista hardcoded. Este módulo
descobre, por agente, a lista de modelos e a persiste no MongoDB (coleção
``host_models``), de onde a API a expõe via ``GET /models``.

Estratégias por agente
----------------------
- **opencode** (``source="config"``): lê ``~/.config/opencode/opencode.json``.
  As *keys* de ``provider.<prov>.models`` são os nomes dos modelos; o id final
  é ``provider/model`` (ex.: ``ollama/qwen2.5-coder:latest``). O default vem de
  ``.model`` (já no formato ``provider/model``).
- **claude** (``source="picker"``): sobe uma sessão tmux efêmera, roda o binário
  ``claude``, manda ``/model`` (que **não** consome quota — só abre o seletor) e
  parseia o picker capturado. Formato real::

        ❯ 1. Default (recommended) ✔  Opus 4.8 with 1M context · Best for…
          2. Opus                     Opus 4.8 with 1M context · Best for…
          3. Sonnet                   Sonnet 4.6 · Efficient for routine tasks
          4. Haiku                    Haiku 4.5 · Fastest for quick answers
          5. Fable (disabled)         Claude Fable 5 is currently unavailable…

- **codex** (``source="config"``): o picker ``/model`` da TUI do codex se mostrou
  **inviável** de raspar de forma confiável neste host: o boot da TUI fica
  bloqueado por até 120 s subindo MCP servers (e os comandos de barra ficam
  enfileirados até lá), além de o popup do ``/model`` não renderizar no
  ``capture-pane``. Fallback robusto: lê o ``model`` de ``~/.codex/config.toml``.
- **gemini** (``source="picker"``): sobe uma sessão tmux efêmera e roda o
  binário ``gemini``. O picker abre em DOIS passos: ``/model`` mostra primeiro
  uma tela **"Select Model"** com ``● 1. Auto`` / ``2. Manual``; só depois de
  escolher **Manual** (seta pra baixo + Enter) é que aparece a lista real::

        Select Model
        ● 1. gemini-3.1-pro-preview
          2. gemini-3-flash-preview
          3. gemini-2.5-pro
          ...

  Cada ``N. <model-id>`` vira ``id``/``label`` (o id é direto, usável com
  ``-m``); ``●`` marca o default. Em qualquer falha → ``[]``.

Salvaguarda das sessões tmux
----------------------------
⚠️ O *scraping* cria sessões efêmeras com prefixo ``sfmodel-`` e **SEMPRE** as
mata no ``finally``, com um ``assert`` de prefixo antes de matar — nenhuma outra
sessão (real do usuário ou ``sftest-``) é jamais tocada.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import libtmux
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("sessionflow_worker.model_discovery")

# Coleção que guarda 1 doc por agente com a lista de modelos descobertos.
HOST_MODELS_COLLECTION = "host_models"

# Prefixo OBRIGATÓRIO das sessões efêmeras de scraping (cinto de segurança).
SCRAPE_PREFIX = "sfmodel-"

# Caminhos de config dos agentes (expandidos no uso).
OPENCODE_CONFIG = Path("~/.config/opencode/opencode.json")
CODEX_CONFIG = Path("~/.codex/config.toml")

# Timings do scraping (segundos). A TUI do claude pode levar ~10-12s para ficar
# pronta para aceitar slash commands; abaixo disso o ``/model`` é descartado.
_BOOT_WAIT = 11.0
_PICKER_WAIT = 4.0
# Limite duro para um scrape inteiro não travar o loop de discovery.
_SCRAPE_TIMEOUT = 30.0

# O gemini é bem mais lento para subir (banner de migração + "MCP issues") e o
# picker abre em dois passos (Select Model -> Manual), então tem timings próprios.
_GEMINI_BOOT_WAIT = 20.0
_GEMINI_PICKER_WAIT = 3.0

# Regex ANSI (CSI + OSC) para limpar o capture-pane.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")

# Linha do picker do claude: "N. <alias>  <nome> · <desc>" (com ❯/✔ opcionais).
_CLAUDE_LINE_RE = re.compile(
    r"^\s*[❯>]?\s*(\d+)\.\s+(.+?)\s{2,}(.+)$",
)

# Linha do picker do gemini: "N. <model-id>" (com ● opcional marcando o default).
# As linhas vêm dentro de uma box do tmux, então toleramos ``│`` nas pontas. O id
# é uma única "palavra" sem espaços (ex.: ``gemini-3.1-pro-preview``); isso exclui
# naturalmente o painel "Model usage" (barras ▬ e textos com espaços) e linhas de
# descrição/"Select Model".
_GEMINI_LINE_RE = re.compile(r"^\s*│?\s*(●)?\s*(\d+)\.\s+(\S+)\s*│?\s*$")
# Só aceitamos ids que pareçam de modelo (gemini-*/gemma-*); blinda contra a tela
# inicial ("1. Auto"/"2. Manual") caso ela seja capturada por engano.
_GEMINI_ID_RE = re.compile(r"^(?:gemini|gemma)[\w.\-]*$", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    """Remove sequências ANSI/escape de um texto capturado do terminal."""
    return _ANSI_RE.sub("", text)


# --------------------------------------------------------------------------- #
# opencode — leitura de config
# --------------------------------------------------------------------------- #
def discover_opencode(config_path: Path | None = None) -> list[dict]:
    """Lê os modelos do ``opencode.json``.

    Para cada provider em ``provider.<prov>.models`` (objeto cujas *keys* são os
    modelos), monta o id ``<prov>/<model_key>``. O ``label`` usa o ``name``
    declarado quando houver; senão, a própria key. O default vem do ``.model``
    de nível raiz (já no formato ``provider/model``).

    Retorna ``[]`` se o arquivo não existir ou for inválido (nunca levanta).
    """
    path = (config_path or OPENCODE_CONFIG).expanduser()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("opencode: falha ao ler %s: %s", path, exc)
        return []

    default_id = data.get("model") if isinstance(data.get("model"), str) else None
    providers = data.get("provider")
    if not isinstance(providers, dict):
        return []

    models: list[dict] = []
    for prov_name, prov in providers.items():
        if not isinstance(prov, dict):
            continue
        prov_models = prov.get("models")
        if not isinstance(prov_models, dict):
            continue
        for model_key, model_def in prov_models.items():
            model_id = f"{prov_name}/{model_key}"
            label = model_key
            if isinstance(model_def, dict) and isinstance(model_def.get("name"), str):
                label = model_def["name"]
            models.append(
                {
                    "id": model_id,
                    "label": label,
                    "description": None,
                    "is_default": model_id == default_id,
                }
            )
    return models


# --------------------------------------------------------------------------- #
# codex — leitura de config (fallback do picker, ver docstring do módulo)
# --------------------------------------------------------------------------- #
def discover_codex_config(config_path: Path | None = None) -> list[dict]:
    """Lê o ``model`` configurado em ``~/.codex/config.toml``.

    Retorna uma lista com (no máximo) 1 modelo — o configurado, marcado como
    default. ``[]`` se o arquivo/campo não existir.
    """
    path = (config_path or CODEX_CONFIG).expanduser()
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("codex: falha ao ler %s: %s", path, exc)
        return []

    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        return []
    return [
        {
            "id": model.strip(),
            "label": model.strip(),
            "description": None,
            "is_default": True,
        }
    ]


# --------------------------------------------------------------------------- #
# Parsers de picker
# --------------------------------------------------------------------------- #
def parse_claude_picker(text: str) -> list[dict]:
    """Parseia o seletor ``/model`` do claude já com ANSI removido.

    Cada linha ``N. <alias>  <nome> · <desc>``: o ``id``/``label`` é o alias
    (sem sufixos como ``(recommended)``/``(disabled)``), ``description`` é o
    nome+desc à direita, e ``is_default`` é True quando a linha contém ``✔``.
    Linhas marcadas ``(disabled)`` são ignoradas (modelo indisponível).
    """
    models: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        match = _CLAUDE_LINE_RE.match(line)
        if not match:
            continue
        alias_raw = match.group(2).strip()
        description = match.group(3).strip().lstrip("✔").strip()
        is_default = "✔" in line
        if "(disabled)" in alias_raw.lower():
            continue
        # O ✔ (default) pode estar colado no alias quando vem antes do gap de
        # 2 espaços: "Default (recommended) ✔  Opus 4.8...". Remove ✔ e o
        # sufixo entre parênteses: "Default (recommended) ✔" -> "Default".
        alias = alias_raw.replace("✔", "").strip()
        alias = re.sub(r"\s*\([^)]*\)\s*$", "", alias).strip()
        if not alias or alias in seen:
            continue
        seen.add(alias)
        models.append(
            {
                "id": alias,
                "label": alias,
                "description": description or None,
                "is_default": is_default,
            }
        )
    return models


def parse_gemini_picker(text: str) -> list[dict]:
    """Parseia a lista **Manual** do ``/model`` do gemini (ANSI já removido).

    Espera linhas ``N. <model-id>`` (dentro da box do tmux), onde ``<model-id>``
    é usável direto com ``-m`` (ex.: ``gemini-3.1-pro-preview``). O ``●`` no
    início marca o modelo atual/default. Filtra para ids ``gemini*``/``gemma*``,
    o que descarta a tela inicial ("1. Auto"/"2. Manual") e o painel de uso.
    """
    models: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        match = _GEMINI_LINE_RE.match(raw.rstrip())
        if not match:
            continue
        model_id = match.group(3).strip()
        if not _GEMINI_ID_RE.match(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            {
                "id": model_id,
                "label": model_id,
                "description": None,
                "is_default": match.group(1) == "●",
            }
        )
    return models


# --------------------------------------------------------------------------- #
# Scraping via tmux
# --------------------------------------------------------------------------- #
def _scrape_session_name() -> str:
    return f"{SCRAPE_PREFIX}{uuid.uuid4().hex[:8]}"


def scrape_models(
    agent: str,
    *,
    server: libtmux.Server | None = None,
    boot_wait: float = _BOOT_WAIT,
    picker_wait: float = _PICKER_WAIT,
) -> list[dict]:
    """Sobe uma sessão tmux efêmera, roda o agente, manda ``/model`` e parseia.

    Suporta ``agent="claude"`` e ``agent="gemini"`` (pickers confiáveis neste
    host; ver módulo). Para outros agentes retorna ``[]`` — a descoberta cai no
    fallback de config.

    **Garantias de segurança**: o nome da sessão SEMPRE começa com
    ``sfmodel-`` e a sessão é morta no ``finally`` (com ``assert`` de prefixo).
    Em qualquer falha/timeout retorna ``[]`` em vez de propagar — o discovery
    não pode ser derrubado por uma TUI travada.
    """
    if agent not in ("claude", "gemini"):
        return []
    # Gemini tem timings próprios (boot lento) salvo override explícito do caller.
    if agent == "gemini" and boot_wait == _BOOT_WAIT and picker_wait == _PICKER_WAIT:
        boot_wait, picker_wait = _GEMINI_BOOT_WAIT, _GEMINI_PICKER_WAIT

    srv = server if server is not None else libtmux.Server()
    name = _scrape_session_name()
    session = None
    try:
        session = srv.new_session(
            session_name=name,
            start_directory=str(Path.home()),
            detach=True,
            x=200,
            y=50,
        )
        pane = session.active_window.active_pane
        # Usamos ``cmd('send-keys', ...)`` (tmux cru) em vez de ``send_keys``:
        # é o equivalente exato de ``tmux send-keys "<txt>" Enter`` e se mostrou
        # confiável para a TUI, ao contrário do helper do libtmux.
        pane.cmd("send-keys", agent, "Enter")
        _sleep(boot_wait)
        # Ambos os agentes abrem um diálogo "trust this folder?" em diretórios
        # novos; o default é confiar, então um Enter o dispensa (e vira input
        # vazio ignorado pela TUI quando o diálogo não aparece).
        pane.cmd("send-keys", "Enter")
        _sleep(2.0)
        pane.cmd("send-keys", "/model", "Enter")
        _sleep(picker_wait)

        if agent == "gemini":
            # O /model do gemini precisa de um Enter extra (o primeiro cai no
            # autocomplete do slash) para abrir a tela "Select Model" (Auto/
            # Manual). Então navegamos pra "Manual" (Down + Enter) e só aí surge
            # a lista numerada real de modelos.
            pane.cmd("send-keys", "Enter")
            _sleep(picker_wait)
            pane.cmd("send-keys", "Down")
            _sleep(1.0)
            pane.cmd("send-keys", "Enter")
            _sleep(picker_wait)
            captured = pane.capture_pane()
            text = (
                "\n".join(captured) if isinstance(captured, list) else str(captured or "")
            )
            return parse_gemini_picker(strip_ansi(text))

        captured = pane.capture_pane()
        text = "\n".join(captured) if isinstance(captured, list) else str(captured or "")
        return parse_claude_picker(strip_ansi(text))
    except Exception:  # noqa: BLE001 - TUI/tmux instável não pode derrubar discovery
        logger.exception("scrape_models(%s): falha no scraping", agent)
        return []
    finally:
        # Cinto de segurança: NUNCA mate nada que não seja nossa sessão efêmera.
        assert name.startswith(SCRAPE_PREFIX)
        try:
            if srv.has_session(name, exact=True):
                srv.kill_session(name)
        except Exception:  # noqa: BLE001
            logger.warning("scrape_models(%s): falha ao matar sessão %s", agent, name)


def _sleep(seconds: float) -> None:
    """Indireção testável do sleep do scraping."""
    import time

    time.sleep(seconds)


# --------------------------------------------------------------------------- #
# Orquestração + persistência
# --------------------------------------------------------------------------- #
def discover_all_data(
    *, server: libtmux.Server | None = None
) -> dict[str, tuple[list[dict], str]]:
    """Descobre os modelos dos 4 agentes (parte **bloqueante**, sem Mongo).

    Retorna ``{agent: (models, source)}`` com ``source`` ∈
    {``config``, ``picker``, ``fallback``}. Esta função faz I/O bloqueante
    (lê configs e raspa a TUI do claude via ``time.sleep``); por isso é separada
    da persistência async — o caller a roda em thread e persiste no loop certo.
    Falhas por agente não abortam os demais.
    """
    out: dict[str, tuple[list[dict], str]] = {}

    # opencode — config
    try:
        out["opencode"] = (discover_opencode(), "config")
    except Exception:  # noqa: BLE001
        logger.exception("discover_all_data: opencode falhou")
        out["opencode"] = ([], "fallback")

    # claude — picker (quota-free)
    try:
        out["claude"] = (scrape_models("claude", server=server), "picker")
    except Exception:  # noqa: BLE001
        logger.exception("discover_all_data: claude falhou")
        out["claude"] = ([], "fallback")

    # codex — config (fallback do picker inviável; ver docstring do módulo)
    try:
        out["codex"] = (discover_codex_config(), "config")
    except Exception:  # noqa: BLE001
        logger.exception("discover_all_data: codex falhou")
        out["codex"] = ([], "fallback")

    # gemini — picker (Select Model -> Manual). Sem lista → fallback.
    try:
        gemini_models = scrape_models("gemini", server=server)
        out["gemini"] = (
            (gemini_models, "picker") if gemini_models else ([], "fallback")
        )
    except Exception:  # noqa: BLE001
        logger.exception("discover_all_data: gemini falhou")
        out["gemini"] = ([], "fallback")

    return out


async def persist_models(
    db: AsyncIOMotorDatabase,
    data: dict[str, tuple[list[dict], str]],
    *,
    collection: str = HOST_MODELS_COLLECTION,
) -> None:
    """Upsert de 1 doc por agente em ``host_models`` (parte async).

    Doc persistido::

        {agent, models:[{id,label,description,is_default}], source, scanned_at}
    """
    scanned_at = datetime.now(UTC)
    for agent, (models, source) in data.items():
        doc = {
            "agent": agent,
            "models": models,
            "source": source,
            "scanned_at": scanned_at,
        }
        try:
            await db[collection].update_one(
                {"agent": agent}, {"$set": doc}, upsert=True
            )
        except Exception:  # noqa: BLE001
            logger.exception("persist_models: upsert de %s falhou", agent)


async def latest_scanned_at(
    db: AsyncIOMotorDatabase,
    *,
    collection: str = HOST_MODELS_COLLECTION,
) -> datetime | None:
    """Retorna o ``scanned_at`` mais recente em ``host_models`` (ou ``None``).

    ``None`` quando o cache está **vazio** (nenhum doc) ou nenhum doc tem um
    ``scanned_at`` válido. Usado para decidir, no boot, se a descoberta deve
    rodar (cache vazio/velho) ou ser pulada (cache fresco).
    """
    doc = await db[collection].find_one(
        {"scanned_at": {"$ne": None}},
        sort=[("scanned_at", -1)],
        projection={"scanned_at": 1},
    )
    if not doc:
        return None
    scanned = doc.get("scanned_at")
    if not isinstance(scanned, datetime):
        return None
    # Mongo devolve naive (UTC); normaliza para comparar com datetime aware.
    if scanned.tzinfo is None:
        scanned = scanned.replace(tzinfo=UTC)
    return scanned


async def cache_is_fresh(
    db: AsyncIOMotorDatabase,
    *,
    max_age_seconds: float,
    collection: str = HOST_MODELS_COLLECTION,
) -> bool:
    """True se o cache ``host_models`` existe e tem < ``max_age_seconds``.

    Vazio (sem ``scanned_at``) ou mais velho que o limite → False (deve
    redescobrir).
    """
    scanned = await latest_scanned_at(db, collection=collection)
    if scanned is None:
        return False
    age = (datetime.now(UTC) - scanned).total_seconds()
    return age < max_age_seconds


async def discover_all(
    db: AsyncIOMotorDatabase,
    *,
    collection: str = HOST_MODELS_COLLECTION,
    server: libtmux.Server | None = None,
) -> dict[str, list[dict]]:
    """Descobre os modelos dos 4 agentes e faz upsert (1 doc/agente).

    Conveniência que junta :func:`discover_all_data` (bloqueante) e
    :func:`persist_models` (async). Retorna o mapa ``{agent: models}``.

    ⚠️ Contém I/O bloqueante (scraping). Em loops async, prefira rodar
    :func:`discover_all_data` em ``asyncio.to_thread`` e ``persist_models`` no
    loop do daemon (ver ``runner.model_discovery_loop``).
    """
    data = discover_all_data(server=server)
    await persist_models(db, data, collection=collection)
    results = {agent: models for agent, (models, _src) in data.items()}
    logger.info(
        "discover_all: %s",
        ", ".join(f"{a}={len(m)}" for a, m in results.items()),
    )
    return results
