"""Testes de ``_handle_open_terminal`` / SSH remoto (multi-host, AD-011).

Puros (sem tmux/osascript reais): ``_remote_ssh_config``/``_remote_attach_cmd``
não tocam runtime/channel/db, e o dispatch em ``_handle_open_terminal`` é
testado monkeypatchando ``_local_attach_cmd``/``_remote_attach_cmd`` (o
``osascript`` em si já é best-effort/fire-and-forget, fora de escopo aqui).
"""

from __future__ import annotations

import pytest

from sessionflow_worker.command_consumer import CommandConsumer, CommandError


def _consumer(host_id: str = "mac-host") -> CommandConsumer:
    return CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        host_id=host_id,
        runtime=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SESSIONFLOW_REMOTE_SSH_HOST",
        "SESSIONFLOW_REMOTE_SSH_PORT",
        "SESSIONFLOW_REMOTE_SSH_USER",
        "SESSIONFLOW_REMOTE_WSL_DISTRO",
    ):
        monkeypatch.delenv(key, raising=False)


def test_remote_ssh_config_none_when_unconfigured() -> None:
    assert _consumer()._remote_ssh_config() is None


def test_remote_ssh_config_reads_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_HOST", "127.0.0.1")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_PORT", "2222")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_USER", "usuario")
    assert _consumer()._remote_ssh_config() == ("127.0.0.1", "2222", "usuario", "Ubuntu")


def test_remote_ssh_config_custom_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_HOST", "127.0.0.1")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_PORT", "2222")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_USER", "usuario")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_WSL_DISTRO", "Debian")
    assert _consumer()._remote_ssh_config()[3] == "Debian"


def test_remote_attach_cmd_raises_when_unconfigured() -> None:
    with pytest.raises(CommandError, match="SESSIONFLOW_REMOTE_SSH"):
        _consumer()._remote_attach_cmd("planner")


def test_remote_attach_cmd_builds_ssh_via_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_HOST", "127.0.0.1")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_PORT", "2222")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_USER", "usuario")
    cmd = _consumer()._remote_attach_cmd("boletoazap-app")
    assert cmd == (
        "ssh -o StrictHostKeyChecking=no -p 2222 usuario@127.0.0.1 -t "
        "wsl.exe -d Ubuntu -- tmux attach -t boletoazap-app"
    )


def test_remote_attach_cmd_quotes_session_name_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_HOST", "127.0.0.1")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_PORT", "2222")
    monkeypatch.setenv("SESSIONFLOW_REMOTE_SSH_USER", "usuario")
    cmd = _consumer()._remote_attach_cmd("3 2 1 BANK")
    assert "'3 2 1 BANK'" in cmd


async def test_open_terminal_uses_remote_when_session_host_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _consumer(host_id="mac-host")
    calls: list[str] = []
    monkeypatch.setattr(
        consumer, "_remote_attach_cmd", lambda name: calls.append("remote") or "ssh ..."
    )
    monkeypatch.setattr(
        consumer, "_local_attach_cmd", lambda name: calls.append("local") or "tmux attach"
    )
    monkeypatch.setattr("sessionflow_worker.command_consumer.subprocess.Popen", lambda *a, **k: None)

    await consumer._handle_open_terminal(
        {"name": "planner", "title": "Planner", "session_host_id": "windows-host"}
    )

    assert calls == ["remote"]


async def test_open_terminal_uses_local_when_session_host_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _consumer(host_id="mac-host")
    calls: list[str] = []
    monkeypatch.setattr(
        consumer, "_remote_attach_cmd", lambda name: calls.append("remote") or "ssh ..."
    )
    monkeypatch.setattr(
        consumer, "_local_attach_cmd", lambda name: calls.append("local") or "tmux attach"
    )
    monkeypatch.setattr("sessionflow_worker.command_consumer.subprocess.Popen", lambda *a, **k: None)

    await consumer._handle_open_terminal(
        {"name": "planner", "title": "Planner", "session_host_id": "mac-host"}
    )

    assert calls == ["local"]


async def test_open_terminal_uses_local_when_session_host_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload sem session_host_id (compat com API antiga/legada) → local."""
    consumer = _consumer(host_id="mac-host")
    calls: list[str] = []
    monkeypatch.setattr(
        consumer, "_remote_attach_cmd", lambda name: calls.append("remote") or "ssh ..."
    )
    monkeypatch.setattr(
        consumer, "_local_attach_cmd", lambda name: calls.append("local") or "tmux attach"
    )
    monkeypatch.setattr("sessionflow_worker.command_consumer.subprocess.Popen", lambda *a, **k: None)

    await consumer._handle_open_terminal({"name": "planner", "title": "Planner"})

    assert calls == ["local"]
