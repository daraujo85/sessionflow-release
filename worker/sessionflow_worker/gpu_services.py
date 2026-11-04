"""Serviços de GPU do host sob demanda (liga/desliga via systemd).

Os serviços de GPU (xtts, omnivoice, nemotron-asr) são PESADOS: carregam
modelo na VRAM e ficam de pé queimando GPU mesmo sem ninguém falando com
eles. Este módulo é o catálogo + o controle de liga/desliga:

- **catálogo** (``SESSIONFLOW_GPU_SERVICES``): nome → unit systemd + escopo
  (``system``/``user``) + porto TCP (usado no health check de ociosidade);
- **controle**: :func:`start_service` / :func:`stop_service` / :func:`is_active`
  via ``systemctl`` (``sudo -n`` quando o escopo é ``system``);
- **ociosidade**: :func:`get_tcp_connections` conta conexões TCP ESTABLISHED
  no porto do serviço — enquanto houver uma, o serviço está em uso e NÃO é
  desligado; sem nenhuma, o auto-stop (loop do runner) desliga.

Segurança: o catálogo é a LISTA FECHADA de unidades — o nome vem do catálogo,
nunca do payload do comando, e a unit é validada por regex antes de virar
argumento do ``systemctl`` (sem shell). Nome fora do catálogo é recusado.

Formato do catálogo (JSON)::

    {"xtts": {"unit": "xtts.service", "scope": "system", "port": 5111},
     "omnivoice": {"units": ["omnivoice.service", "omnivoice-tunnel.service"],
                   "scope": "user", "port": 8790}}

ou o formato simples (uma entrada por token, separados por espaço; várias
units do mesmo serviço separadas por ``+``)::

    xtts:xtts.service:system:5111
    omnivoice:omnivoice.service+omnivoice-tunnel.service:user:8790

Serviço com várias units: ``start`` liga na ORDEM declarada, ``stop`` desliga
na ordem INVERSA e ``is_active`` olha só a 1ª unit (a principal). ``"unit"``
(singular) continua valendo e vira ``units=(unit,)`` internamente.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger("sessionflow_worker.gpu_services")

Scope = Literal["system", "user"]

#: Env var com o catálogo de serviços de GPU gerenciados por este host.
CATALOG_ENV = "SESSIONFLOW_GPU_SERVICES"
#: Env var com os MINUTOS de ociosidade antes do auto-stop (0 = desliga o
#: auto-stop; o liga/desliga manual pela API continua funcionando).
IDLE_MIN_ENV = "SESSIONFLOW_GPU_IDLE_MIN"
DEFAULT_IDLE_MIN = 20.0
#: Default em segundos (20 min = 1200 s) — o que o watchdog compara.
DEFAULT_IDLE_SECONDS = DEFAULT_IDLE_MIN * 60.0

#: Timeout dos comandos ``systemctl`` (start de um modelo pesado pode demorar).
SYSTEMCTL_TIMEOUT_S = 30
#: Intervalo entre as sondas de health check.
HEALTH_POLL_INTERVAL_S = 1.0
#: Timeout padrão do health check (subir o modelo na GPU leva alguns minutos).
DEFAULT_HEALTH_TIMEOUT_S = 60.0

#: Onde o kernel expõe as conexões TCP (Linux). Constantes (e não literals
#: inline) pra que o teste possa apontar pra um arquivo temporário.
PROC_NET_TCP = "/proc/net/tcp"
PROC_NET_TCP6 = "/proc/net/tcp6"

#: Nome do serviço no catálogo — só caracteres seguros (vai pro log/Mongo).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
#: Unit systemd — whitelist estrita (nada de caminho, espaço ou metachar).
_UNIT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.(service|socket|target|timer)$"
)

#: Último uso registrado por serviço (nome → instante). Em memória: se o
#: worker reinicia, o serviço volta pro estado "sem uso registrado" e o
#: watchdog só o desliga depois de observar o período de ociosidade inteiro.
_LAST_USED: dict[str, datetime] = {}


class GpuServiceError(RuntimeError):
    """Falha ao operar um serviço de GPU (catálogo, systemctl ou health check)."""


@dataclass(frozen=True)
class GpuService:
    """Um serviço de GPU gerenciado por este host."""

    name: str
    units: tuple[str, ...]
    scope: Scope = "system"
    port: int | None = None

    @property
    def unit(self) -> str:
        """Unit principal (a 1ª) — a que ``is_active`` consulta."""
        return self.units[0]

    def systemctl_cmd(self, *args: str) -> list[str]:
        """Comando ``systemctl`` PRIVILEGIADO (start/stop).

        Escopo ``system`` → ``sudo -n systemctl ...`` (o sudoers do host
        libera só as units do catálogo, com ``-n`` = nunca pedir senha).
        Escopo ``user`` → ``systemctl --user ...`` (sem privilégio).
        """
        if self.scope == "user":
            return ["systemctl", "--user", *args]
        return ["sudo", "-n", "systemctl", *args]

    def query_cmd(self, *args: str) -> list[str]:
        """Comando ``systemctl`` de LEITURA (``is-active``) — sem sudo."""
        if self.scope == "user":
            return ["systemctl", "--user", *args]
        return ["systemctl", *args]


# --- Catálogo ---------------------------------------------------------------


def _clean_name(raw: object) -> str | None:
    name = str(raw or "").strip()
    return name if _NAME_RE.match(name) else None


def _clean_unit(raw: object, name: str) -> str | None:
    unit = str(raw or "").strip()
    if not unit:
        unit = f"{name}.service"
    return unit if _UNIT_RE.match(unit) else None


def _clean_scope(raw: object) -> Scope | None:
    scope = str(raw or "system").strip().lower()
    if scope in ("system", "user"):
        return scope  # type: ignore[return-value]
    return None


def _clean_port(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _clean_units(raw: object, name: str) -> tuple[str, ...] | None:
    """Lista de units (str, ``a+b`` ou lista) → tupla validada; None se inválida."""
    if isinstance(raw, (list, tuple)):
        items = [str(u or "").strip() for u in raw] or [""]
    else:
        items = [u.strip() for u in str(raw or "").split("+")]
    units: list[str] = []
    for item in items:
        clean = _clean_unit(item, name)
        if clean is None:
            return None
        units.append(clean)
    return tuple(units)

def _entry(name: str, unit: object, scope: object, port: object) -> GpuService | None:
    """Monta uma entrada do catálogo, ou None se algo não passar na whitelist.

    ``unit`` aceita uma unit, várias separadas por ``+`` ou uma lista.
    """
    clean_name = _clean_name(name)
    if clean_name is None:
        logger.warning("gpu_services: nome de serviço inválido (%r) — ignorado", name)
        return None
    clean_units = _clean_units(unit, clean_name)
    if clean_units is None:
        logger.warning(
            "gpu_services: unit inválida p/ %r (%r) — ignorado", clean_name, unit
        )
        return None
    clean_scope = _clean_scope(scope)
    if clean_scope is None:
        logger.warning(
            "gpu_services: escopo inválido p/ %r (%r) — ignorado", clean_name, scope
        )
        return None
    return GpuService(
        name=clean_name,
        units=clean_units,
        scope=clean_scope,
        port=_clean_port(port),
    )


def _parse_json_catalog(data: dict) -> dict[str, GpuService]:
    out: dict[str, GpuService] = {}
    for name, value in data.items():
        if isinstance(value, dict):
            # "units" (lista) tem precedência; "unit" singular segue valendo.
            units = value.get("units")
            svc = _entry(
                name,
                units if units is not None else value.get("unit"),
                value.get("scope", "system"),
                value.get("port"),
            )
        elif isinstance(value, str):
            svc = _entry(name, value, "system", None)
        else:
            logger.warning("gpu_services: entrada inválida p/ %r — ignorada", name)
            continue
        if svc is not None:
            out[svc.name] = svc
    return out


def _parse_simple_catalog(raw: str) -> dict[str, GpuService]:
    """Formato ``nome:unit:scope:porto`` (tokens separados por espaço)."""
    out: dict[str, GpuService] = {}
    for token in raw.split():
        parts = [p.strip() for p in token.split(":")]
        if not parts or not parts[0]:
            continue
        name = parts[0]
        unit = parts[1] if len(parts) > 1 else ""
        scope = parts[2] if len(parts) > 2 else "system"
        port = parts[3] if len(parts) > 3 else None
        svc = _entry(name, unit, scope, port)
        if svc is not None:
            out[svc.name] = svc
    return out


def parse_catalog(raw: str | None) -> dict[str, GpuService]:
    """Parseia o catálogo (JSON ou formato simples) → ``{nome: GpuService}``.

    JSON é tentado primeiro (formato preferido); se não parsear, cai no
    formato simples ``nome:unit:scope:porto``. Entradas inválidas são
    IGNORADAS (com log) — um catálogo meio torto não pode derrubar o worker.
    """
    if raw is None:
        return {}
    text = raw.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return _parse_simple_catalog(text)
    if isinstance(data, dict):
        return _parse_json_catalog(data)
    logger.warning("gpu_services: catálogo JSON não é um objeto — ignorado")
    return {}


def load_catalog(raw: str | None = None) -> dict[str, GpuService]:
    """Catálogo vigente de :data:`CATALOG_ENV` (ou do ``raw`` injetado)."""
    if raw is None:
        raw = os.environ.get(CATALOG_ENV, "")
    return parse_catalog(raw)


def idle_minutes() -> float:
    """Minutos de ociosidade antes do auto-stop (0 = auto-stop desligado).

    Lê :data:`IDLE_MIN_ENV` como MINUTOS (default 20).
    """
    raw = os.environ.get(IDLE_MIN_ENV, "").strip()
    if not raw:
        return DEFAULT_IDLE_MIN
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "gpu_services: %s inválido (%r) — usando o default", IDLE_MIN_ENV, raw
        )
        return DEFAULT_IDLE_MIN
    return max(0.0, value)


def idle_seconds() -> float:
    """Segundos de ociosidade antes do auto-stop (0 = auto-stop desligado).

    ``idle_minutes() * 60`` — o watchdog compara este valor direto com
    ``now - last_used_at`` (segundos). Default: 20 min = 1200 s.
    """
    return idle_minutes() * 60.0


def get_service(
    name: str, catalog: dict[str, GpuService] | None = None
) -> GpuService:
    """Serviço pelo nome — RECUSA nome fora do catálogo (lista fechada)."""
    cat = catalog if catalog is not None else load_catalog()
    svc = cat.get(str(name or "").strip())
    if svc is None:
        raise GpuServiceError(
            f"serviço de GPU desconhecido: {name!r} "
            f"(catálogo: {', '.join(sorted(cat)) or 'vazio'})"
        )
    return svc


# --- Controle (systemctl) ----------------------------------------------------


def _env_for(cmd: list[str]) -> dict[str, str] | None:
    """Env do subprocess. ``--user`` precisa do session bus do usuário.

    O worker roda como unit de SISTEMA (sem sessão D-Bus), então
    ``systemctl --user`` falha com "Failed to connect to bus" se
    ``XDG_RUNTIME_DIR``/``DBUS_SESSION_BUS_ADDRESS`` não existirem. Sem
    ``--user`` devolve None (herda o env do processo).
    """
    if "--user" not in cmd:
        return None
    env = os.environ.copy()
    uid = os.getuid()
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
    return env

def _run(cmd: list[str], timeout: int = SYSTEMCTL_TIMEOUT_S) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            env=_env_for(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GpuServiceError(f"comando não encontrado: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GpuServiceError(f"timeout em {' '.join(cmd)}") from exc


def _err_text(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"


def start_service(
    name: str, catalog: dict[str, GpuService] | None = None
) -> bool:
    """Liga o serviço (systemd). True se ligou.

    Escopo ``system`` usa ``sudo -n systemctl start <unit>`` (o sudoers do
    host libera exatamente as units do catálogo); escopo ``user`` usa
    ``systemctl --user start <unit>``. Várias units: liga na ordem declarada
    e aborta na primeira que falhar.
    """
    svc = get_service(name, catalog)
    for unit in svc.units:
        proc = _run(svc.systemctl_cmd("start", unit))
        if proc.returncode != 0:
            raise GpuServiceError(
                f"falha ao ligar {svc.name} ({unit}): {_err_text(proc)}"
            )
    logger.info("gpu_service %s ligado (%s)", svc.name, "+".join(svc.units))
    return True


def stop_service(
    name: str, catalog: dict[str, GpuService] | None = None
) -> bool:
    """Desliga o serviço (systemd). True se desligou.

    Várias units: desliga na ordem INVERSA da declarada (o que foi ligado por
    último — ex.: o túnel — cai primeiro). Tenta todas mesmo se uma falhar e
    só então levanta o erro.
    """
    svc = get_service(name, catalog)
    errors: list[str] = []
    for unit in reversed(svc.units):
        proc = _run(svc.systemctl_cmd("stop", unit))
        if proc.returncode != 0:
            errors.append(f"{unit}: {_err_text(proc)}")
    if errors:
        raise GpuServiceError(
            f"falha ao desligar {svc.name} ({'; '.join(errors)})"
        )
    _LAST_USED.pop(svc.name, None)
    logger.info("gpu_service %s desligado (%s)", svc.name, "+".join(svc.units))
    return True


def is_active(
    name: str, catalog: dict[str, GpuService] | None = None
) -> bool:
    """O serviço está ativo agora? (``systemctl is-active`` na 1ª unit)"""
    svc = get_service(name, catalog)
    try:
        proc = _run(svc.query_cmd("is-active", svc.unit))
    except GpuServiceError:
        return False
    return proc.returncode == 0


# --- Ociosidade (conexões TCP) ------------------------------------------------


def _count_proc_net(path: str, port: int) -> int:
    """Conta sockets ESTABLISHED cujo porto LOCAL é ``port``.

    ``/proc/net/tcp`` traz uma linha por socket: ``sl local rem st ...``, com
    os endereços em HEX e o estado em HEX (``01`` = ESTABLISHED). O socket do
    SERVIÇO é o que tem o porto local = porta do serviço (o cliente tem o
    porto efêmero local e a porta do serviço no remoto) — por isso conta-se
    o local, e o endereço é ignorado (qualquer interface serve).
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines()[1:]:  # 1ª linha = cabeçalho
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[3].strip().upper() != "01":  # 01 = ESTABLISHED
            continue
        local = parts[1]
        if ":" not in local:
            continue
        try:
            local_port = int(local.rsplit(":", 1)[1], 16)
        except ValueError:
            continue
        if local_port == port:
            count += 1
    return count


def _count_ss(port: int) -> int:
    """Fallback sem ``/proc``: ``ss -tn state established`` (qualquer addr)."""
    try:
        proc = subprocess.run(
            ["ss", "-tn", "state", "established"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    count = 0
    for line in (proc.stdout or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        if local.endswith(f":{port}"):
            count += 1
    return count


def get_tcp_connections(port: int) -> int:
    """Quantas conexões TCP ESTABLISHED o serviço no ``port`` tem agora.

    ``port`` <= 0 → 0 (serviço sem porto conhecido: não dá pra checar).
    """
    if not port or port <= 0:
        return 0
    if os.path.exists(PROC_NET_TCP):
        return _count_proc_net(PROC_NET_TCP, port) + _count_proc_net(PROC_NET_TCP6, port)
    return _count_ss(port)


# --- Uso / estado -------------------------------------------------------------


def mark_used(name: str, when: datetime | None = None) -> None:
    """Registra que o serviço foi usado agora (zera o relógio de ociosidade)."""
    _LAST_USED[str(name)] = when or datetime.now(timezone.utc)


def last_used_at(name: str) -> datetime | None:
    """Instante do último uso registrado (None = nunca usado neste processo)."""
    return _LAST_USED.get(str(name))


def status_snapshot(
    catalog: dict[str, GpuService] | None = None,
) -> dict[str, dict]:
    """Estado de todos os serviços do catálogo pro heartbeat.

    ``{nome: {active: bool, last_used_at: ISO8601|None, unit: str,
    units: list[str], port: int|None}}``
    """
    cat = catalog if catalog is not None else load_catalog()
    out: dict[str, dict] = {}
    for name, svc in cat.items():
        last = _LAST_USED.get(name)
        out[name] = {
            "active": is_active(name, cat),
            "last_used_at": last.isoformat() if last else None,
            "unit": svc.unit,
            "units": list(svc.units),
            "port": svc.port,
        }
    return out


# --- Health check -------------------------------------------------------------


def _health_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 300
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 300
    except Exception:  # noqa: BLE001 - health check é best-effort
        return False


def wait_for_health(
    url: str,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_S,
    interval: float = HEALTH_POLL_INTERVAL_S,
) -> bool:
    """Faz polling do GET ``url`` até dar 200 (ou estourar o timeout).

    True = o serviço respondeu; False = timeout (ou URL inválida). Usado pra
    esperar o modelo do XTTS subir na GPU antes de tentar sintetizar.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _health_ok(url):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


def wait_active(
    name: str,
    catalog: dict[str, GpuService] | None = None,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_S,
    interval: float = HEALTH_POLL_INTERVAL_S,
) -> bool:
    """Espera o serviço ficar ativo no systemd (sem health HTTP)."""
    cat = catalog if catalog is not None else load_catalog()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if is_active(name, cat):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


def ensure_started(
    name: str,
    health_url: str | None = None,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_S,
    catalog: dict[str, GpuService] | None = None,
) -> bool:
    """Garante que o serviço está ligado (e pronto) — usado sob demanda.

    Se o serviço NÃO está no catálogo, não faz nada e devolve False (não é
    gerenciado por aqui). Se já está ativo, só registra o uso. Se está
    parado, liga e espera o health check (ou o ``is-active``) ficar verde.
    """
    cat = catalog if catalog is not None else load_catalog()
    if str(name) not in cat:
        return False
    if is_active(name, cat):
        mark_used(name)
        return True
    start_service(name, cat)
    if health_url:
        ready = wait_for_health(health_url, timeout)
    else:
        ready = wait_active(name, cat, timeout)
    if ready:
        mark_used(name)
    else:
        logger.warning(
            "gpu_service %s ligado mas não ficou pronto em %ss", name, int(timeout)
        )
    return ready


# --- Decisão de ociosidade (pura — testável sem relógio real) ------------------


def decide_auto_stop(
    last_used: datetime | None,
    now: datetime,
    idle_s: float,
    active_connections: int = 0,
) -> bool:
    """O serviço deve ser desligado por estar ocioso?

    Puro (sem I/O) pra ser testável com tempo falso:
    - ``idle_s <= 0`` → nunca (auto-stop desligado);
    - conexão ESTABLISHED → nunca (tá em uso);
    - sem uso registrado → sim (nunca ninguém pediu);
    - caso contrário → sim se ``now - last_used`` passou de ``idle_s``.
    """
    if idle_s <= 0:
        return False
    if active_connections > 0:
        return False
    if last_used is None:
        return True
    return (now - last_used).total_seconds() > idle_s
