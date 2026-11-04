"""Testes dos serviços de GPU sob demanda (``gpu_services.py``).

Cobre três coisas:
- o CATÁLOGO (parse JSON + formato simples + defaults + entradas inválidas);
- o HANDLER ``gpu_service`` do ``CommandConsumer`` (start/stop/status, sudo
  vs ``--user``, recusa de nome fora do catálogo) — com o ``systemctl``
  substituído por um fake (nada de systemd real aqui);
- a DECISÃO DE OCIOSIDADE (auto-stop) e a checagem de conexões TCP.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from sessionflow_worker import gpu_services, runner
from sessionflow_worker.command_consumer import CommandConsumer, CommandError

HOST = "host-a"

CATALOG_JSON = json.dumps(
    {
        "xtts": {"unit": "xtts.service", "scope": "system", "port": 5111},
        "omnivoice": {"unit": "omnivoice.service", "scope": "user", "port": 8790},
    }
)


# --------------------------------------------------------------------------- #
# dublês
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSystemctl:
    """``systemctl`` falso: registra os comandos e devolve o exit code pedido."""

    def __init__(self, active: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.active = active

    def __call__(self, cmd: list[str], timeout: int | None = None) -> _FakeProc:
        self.calls.append(list(cmd))
        if "is-active" in cmd:
            if self.active:
                return _FakeProc(0, stdout="active\n")
            return _FakeProc(3, stdout="inactive\n")
        return _FakeProc(0)


class _FakeResults:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        self.docs.setdefault(flt["_id"], {}).update(update["$set"])


class _FakeDb:
    def __init__(self) -> None:
        self.results = _FakeResults()

    def __getitem__(self, name: str) -> _FakeResults:
        assert name == "command_results"
        return self.results


def _consumer(db: _FakeDb) -> CommandConsumer:
    return CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        host_id=HOST,
        runtime=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    """Estado global do módulo (último uso) não vaza entre testes."""
    monkeypatch.setattr(gpu_services, "_LAST_USED", {})
    yield


@pytest.fixture
def catalog_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gpu_services.CATALOG_ENV, CATALOG_JSON)


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> _FakeSystemctl:
    fake = _FakeSystemctl()
    monkeypatch.setattr(gpu_services, "_run", fake)
    return fake


# --------------------------------------------------------------------------- #
# catálogo
# --------------------------------------------------------------------------- #
def test_gpu_services_parse_catalog() -> None:
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    assert set(cat) == {"xtts", "omnivoice"}
    assert cat["xtts"].unit == "xtts.service"
    assert cat["xtts"].scope == "system"
    assert cat["xtts"].port == 5111
    assert cat["omnivoice"].scope == "user"


def test_parse_formato_simples() -> None:
    cat = gpu_services.parse_catalog(
        "xtts:xtts.service:system:5111 omnivoice:omnivoice.service:user:8790"
    )
    assert cat["xtts"].unit == "xtts.service"
    assert cat["xtts"].port == 5111
    assert cat["omnivoice"].scope == "user"
    assert cat["omnivoice"].port == 8790


def test_parse_default_de_unit_e_scope() -> None:
    """Sem unit/scope/porto: unit = <nome>.service, scope = system, sem porto."""
    cat = gpu_services.parse_catalog('{"xtts": {}}')
    assert cat["xtts"].unit == "xtts.service"
    assert cat["xtts"].scope == "system"
    assert cat["xtts"].port is None


def test_parse_ignora_entrada_invalida() -> None:
    """Unit fora da whitelist é IGNORADA (não executa nada arbitrário)."""
    cat = gpu_services.parse_catalog(
        json.dumps({"bom": {"unit": "bom.service"}, "ruim": {"unit": "rm -rf /; x"}})
    )
    assert set(cat) == {"bom"}


def test_parse_catalogo_vazio() -> None:
    assert gpu_services.parse_catalog(None) == {}
    assert gpu_services.parse_catalog("   ") == {}


# --------------------------------------------------------------------------- #
# handler gpu_service
# --------------------------------------------------------------------------- #
async def test_handle_gpu_service_start_system(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    out = await _consumer(db)._handle_gpu_service(
        {"action": "start", "name": "xtts"}, "cmd-1"
    )
    assert out["active"] is True
    # escopo system → sudo -n systemctl start <unit>
    assert systemctl.calls == [["sudo", "-n", "systemctl", "start", "xtts.service"]]
    doc = db.results.docs["cmd-1"]
    assert doc["ok"] is True
    assert doc["type"] == "gpu_service"
    assert doc["result"]["name"] == "xtts"
    assert doc["result"]["active"] is True


async def test_handle_gpu_service_start_user(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    await _consumer(db)._handle_gpu_service(
        {"action": "start", "name": "omnivoice"}, "cmd-1"
    )
    # escopo user → systemctl --user start <unit> (sem sudo)
    assert systemctl.calls == [
        ["systemctl", "--user", "start", "omnivoice.service"]
    ]
    assert db.results.docs["cmd-1"]["ok"] is True


async def test_handle_gpu_service_stop(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    out = await _consumer(db)._handle_gpu_service(
        {"action": "stop", "name": "xtts"}, "cmd-1"
    )
    assert systemctl.calls == [["sudo", "-n", "systemctl", "stop", "xtts.service"]]
    assert out["active"] is False
    assert db.results.docs["cmd-1"]["ok"] is True


async def test_handle_gpu_service_status_one(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    systemctl.active = True
    db = _FakeDb()
    out = await _consumer(db)._handle_gpu_service(
        {"action": "status", "name": "xtts"}, "cmd-1"
    )
    assert out["name"] == "xtts"
    assert out["active"] is True
    assert systemctl.calls == [["systemctl", "is-active", "xtts.service"]]


async def test_handle_gpu_service_status_todos(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    out = await _consumer(db)._handle_gpu_service({"action": "status"}, "cmd-1")
    services = out["services"]
    assert set(services) == {"xtts", "omnivoice"}
    assert services["xtts"]["active"] is True
    assert services["xtts"]["unit"] == "xtts.service"
    assert db.results.docs["cmd-1"]["ok"] is True


async def test_recusa_nome_fora_do_catalogo(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="desconhecido"):
        await _consumer(db)._handle_gpu_service(
            {"action": "start", "name": "servico-que-nao-existe"}, "cmd-1"
        )
    # nada foi executado no systemd
    assert systemctl.calls == []
    # e o erro também persiste (o requester não fica em polling eterno)
    assert db.results.docs["cmd-1"]["ok"] is False


async def test_recusa_action_desconhecida(
    catalog_env: None, systemctl: _FakeSystemctl
) -> None:
    db = _FakeDb()
    with pytest.raises(CommandError, match="action"):
        await _consumer(db)._handle_gpu_service(
            {"action": "explode", "name": "xtts"}, "cmd-1"
        )
    assert systemctl.calls == []
    assert db.results.docs["cmd-1"]["ok"] is False


async def test_sem_catalogo_recusa(
    monkeypatch: pytest.MonkeyPatch, systemctl: _FakeSystemctl
) -> None:
    monkeypatch.setenv(gpu_services.CATALOG_ENV, "")
    db = _FakeDb()
    with pytest.raises(CommandError, match="nenhum serviço configurado"):
        await _consumer(db)._handle_gpu_service(
            {"action": "start", "name": "xtts"}, "cmd-1"
        )
    assert systemctl.calls == []


# --------------------------------------------------------------------------- #
# --user precisa do session bus (worker roda como unit de SISTEMA)
# --------------------------------------------------------------------------- #
def test_run_user_injeta_env_do_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        captured["env"] = kwargs.get("env")
        return _FakeProc(0)

    monkeypatch.setattr(gpu_services.subprocess, "run", fake_run)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    uid = os.getuid()

    gpu_services._run(["systemctl", "--user", "start", "omnivoice.service"])
    env = captured["env"]
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"

def test_run_user_preserva_env_existente(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        gpu_services.subprocess,
        "run",
        lambda cmd, **kw: captured.update(env=kw.get("env")) or _FakeProc(0),
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom/run")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/custom/bus")
    gpu_services._run(["systemctl", "--user", "is-active", "x.service"])
    assert captured["env"]["XDG_RUNTIME_DIR"] == "/custom/run"
    assert captured["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/bus"

def test_run_system_nao_mexe_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        gpu_services.subprocess,
        "run",
        lambda cmd, **kw: captured.update(env=kw.get("env", "ausente")) or _FakeProc(0),
    )
    gpu_services._run(["sudo", "-n", "systemctl", "start", "xtts.service"])
    assert captured["env"] is None  # herda o env do processo

# --------------------------------------------------------------------------- #
# múltiplas units por serviço
# --------------------------------------------------------------------------- #
def test_parse_json_unit_singular_vira_units() -> None:
    cat = gpu_services.parse_catalog('{"xtts": {"unit": "xtts.service"}}')
    assert cat["xtts"].units == ("xtts.service",)
    assert cat["xtts"].unit == "xtts.service"

def test_parse_json_units_multiplas() -> None:
    cat = gpu_services.parse_catalog(
        json.dumps(
            {
                "omnivoice": {
                    "units": ["omnivoice.service", "omnivoice-tunnel.service"],
                    "scope": "user",
                    "port": 8790,
                }
            }
        )
    )
    assert cat["omnivoice"].units == ("omnivoice.service", "omnivoice-tunnel.service")
    assert cat["omnivoice"].unit == "omnivoice.service"

def test_parse_simples_units_com_mais() -> None:
    cat = gpu_services.parse_catalog(
        "omnivoice:omnivoice.service+omnivoice-tunnel.service:user:8790"
    )
    assert cat["omnivoice"].units == ("omnivoice.service", "omnivoice-tunnel.service")
    assert cat["omnivoice"].scope == "user"
    assert cat["omnivoice"].port == 8790

def test_parse_units_com_item_invalido_ignora_servico() -> None:
    cat = gpu_services.parse_catalog(
        json.dumps({"x": {"units": ["ok.service", "rm -rf /"]}})
    )
    assert cat == {}

def test_start_stop_multiplas_units_em_ordem(systemctl: _FakeSystemctl) -> None:
    cat = gpu_services.parse_catalog(
        "omnivoice:omnivoice.service+omnivoice-tunnel.service:user:8790"
    )
    assert gpu_services.start_service("omnivoice", cat) is True
    assert systemctl.calls == [
        ["systemctl", "--user", "start", "omnivoice.service"],
        ["systemctl", "--user", "start", "omnivoice-tunnel.service"],
    ]
    systemctl.calls.clear()
    assert gpu_services.stop_service("omnivoice", cat) is True
    assert systemctl.calls == [
        ["systemctl", "--user", "stop", "omnivoice-tunnel.service"],
        ["systemctl", "--user", "stop", "omnivoice.service"],
    ]

def test_is_active_checa_so_a_primeira_unit(systemctl: _FakeSystemctl) -> None:
    cat = gpu_services.parse_catalog(
        "omnivoice:omnivoice.service+omnivoice-tunnel.service:user:8790"
    )
    assert gpu_services.is_active("omnivoice", cat) is True
    assert systemctl.calls == [["systemctl", "--user", "is-active", "omnivoice.service"]]

# --------------------------------------------------------------------------- #
# ociosidade (auto-stop)
# --------------------------------------------------------------------------- #
def test_decide_auto_stop_pura() -> None:
    now = datetime.now(timezone.utc)
    assert gpu_services.decide_auto_stop(now, now, 20) is False
    assert gpu_services.decide_auto_stop(now - timedelta(seconds=30), now, 20) is True
    # em uso (conexão ESTABLISHED) → nunca desliga
    assert gpu_services.decide_auto_stop(now - timedelta(minutes=5), now, 20, 1) is False
    # auto-stop desligado (idle = 0) → nunca
    assert gpu_services.decide_auto_stop(now - timedelta(minutes=5), now, 0) is False


async def test_idle_watchdog_stops_ociosa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serviço ativo, sem conexão e sem uso há 21min com idle=20min → desliga."""
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    now = datetime.now(timezone.utc)
    stopped: list[str] = []

    monkeypatch.setattr(gpu_services, "load_catalog", lambda *a, **k: cat)
    monkeypatch.setattr(gpu_services, "is_active", lambda *a, **k: True)
    monkeypatch.setattr(gpu_services, "get_tcp_connections", lambda *a, **k: 0)
    monkeypatch.setattr(
        gpu_services, "last_used_at", lambda name: now - timedelta(minutes=21)
    )
    monkeypatch.setattr(
        gpu_services, "stop_service", lambda name, *a, **k: stopped.append(name)
    )
    monkeypatch.setattr(gpu_services, "mark_used", lambda *a, **k: None)
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "20")

    result = await runner.gpu_idle_watchdog_once(now=now)
    assert result == ["xtts", "omnivoice"]
    assert sorted(stopped) == ["omnivoice", "xtts"]


async def test_idle_watchdog_nao_para_antes_do_limite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão: IDLE_MIN é em MINUTOS — 120s de ociosidade com idle=20min NÃO desliga."""
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    now = datetime.now(timezone.utc)
    stopped: list[str] = []

    monkeypatch.setattr(gpu_services, "load_catalog", lambda *a, **k: cat)
    monkeypatch.setattr(gpu_services, "is_active", lambda *a, **k: True)
    monkeypatch.setattr(gpu_services, "get_tcp_connections", lambda *a, **k: 0)
    monkeypatch.setattr(
        gpu_services, "last_used_at", lambda name: now - timedelta(seconds=120)
    )
    monkeypatch.setattr(
        gpu_services, "stop_service", lambda name, *a, **k: stopped.append(name)
    )
    monkeypatch.setattr(gpu_services, "mark_used", lambda *a, **k: None)
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "20")

    assert await runner.gpu_idle_watchdog_once(now=now) == []
    assert stopped == []


def test_idle_seconds_converte_minutos(monkeypatch: pytest.MonkeyPatch) -> None:
    """A env é em MINUTOS; ``idle_seconds()`` devolve minutos * 60."""
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "20")
    assert gpu_services.idle_minutes() == 20.0
    assert gpu_services.idle_seconds() == 1200.0
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "0.5")
    assert gpu_services.idle_seconds() == 30.0
    monkeypatch.delenv(gpu_services.IDLE_MIN_ENV)
    assert gpu_services.idle_seconds() == gpu_services.DEFAULT_IDLE_SECONDS == 1200.0
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "lixo")
    assert gpu_services.idle_seconds() == 1200.0
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "0")
    assert gpu_services.idle_seconds() == 0.0


async def test_idle_watchdog_keeps_em_uso(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com conexão ESTABLISHED o serviço fica de pé (e o relógio zera)."""
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    now = datetime.now(timezone.utc)
    stopped: list[str] = []
    marked: list[str] = []

    monkeypatch.setattr(gpu_services, "load_catalog", lambda *a, **k: cat)
    monkeypatch.setattr(gpu_services, "is_active", lambda *a, **k: True)
    monkeypatch.setattr(gpu_services, "get_tcp_connections", lambda *a, **k: 3)
    monkeypatch.setattr(
        gpu_services, "last_used_at", lambda name: now - timedelta(seconds=120)
    )
    monkeypatch.setattr(
        gpu_services, "stop_service", lambda name, *a, **k: stopped.append(name)
    )
    monkeypatch.setattr(
        gpu_services, "mark_used", lambda name, *a, **k: marked.append(name)
    )
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "20")

    result = await runner.gpu_idle_watchdog_once(now=now)
    assert result == []
    assert stopped == []
    assert sorted(marked) == ["omnivoice", "xtts"]


async def test_idle_watchdog_desligado(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SESSIONFLOW_GPU_IDLE_MIN=0`` → auto-stop desligado."""
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    monkeypatch.setattr(gpu_services, "load_catalog", lambda *a, **k: cat)
    monkeypatch.setenv(gpu_services.IDLE_MIN_ENV, "0")
    assert await runner.gpu_idle_watchdog_once() == []


# --------------------------------------------------------------------------- #
# conexões TCP
# --------------------------------------------------------------------------- #
def test_tcp_check_detects_established(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parse de /proc/net/tcp: só conta ESTABLISHED (01) no porto local."""
    proc = tmp_path / "tcp"
    proc.write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
        "retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:13F7 00000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000  1000        0 1 1 0 100 0 0 10 0\n"
        "   1: 0100007F:13F7 0100007F:C1D4 01 00000000:00000000 00:00000000 "
        "00000000  1000        0 2 1 0 100 0 0 10 0\n"
        "   2: 0100007F:C1D4 0100007F:13F7 01 00000000:00000000 00:00000000 "
        "00000000  1000        0 3 1 0 100 0 0 10 0\n"
    )
    monkeypatch.setattr(gpu_services, "PROC_NET_TCP", str(proc))
    monkeypatch.setattr(gpu_services, "PROC_NET_TCP6", str(tmp_path / "tcp6"))
    # 0x13F7 = 5111: 1 socket LISTEN (0A) + 1 ESTABLISHED (01) no porto local
    assert gpu_services.get_tcp_connections(5111) == 1
    # 0xC1D4 = 49620: só o socket do cliente (porto local efêmero)
    assert gpu_services.get_tcp_connections(49620) == 1
    assert gpu_services.get_tcp_connections(9999) == 0


def test_tcp_check_sem_porto() -> None:
    assert gpu_services.get_tcp_connections(0) == 0
    assert gpu_services.get_tcp_connections(-1) == 0


# --------------------------------------------------------------------------- #
# health check / start sob demanda
# --------------------------------------------------------------------------- #
def test_ensure_started_liga_e_espera(
    monkeypatch: pytest.MonkeyPatch, systemctl: _FakeSystemctl
) -> None:
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    monkeypatch.setattr(gpu_services, "is_active", lambda *a, **k: False)
    monkeypatch.setattr(gpu_services, "wait_for_health", lambda *a, **k: True)
    assert gpu_services.ensure_started("xtts", health_url="http://x/health", catalog=cat)
    assert systemctl.calls == [["sudo", "-n", "systemctl", "start", "xtts.service"]]


def test_ensure_started_fora_do_catalogo_nao_faz_nada(
    monkeypatch: pytest.MonkeyPatch, systemctl: _FakeSystemctl
) -> None:
    """Serviço fora do catálogo → não mexe em nada (returns False)."""
    cat = gpu_services.parse_catalog(CATALOG_JSON)
    assert gpu_services.ensure_started("nao-catalogado", catalog=cat) is False
    assert systemctl.calls == []
