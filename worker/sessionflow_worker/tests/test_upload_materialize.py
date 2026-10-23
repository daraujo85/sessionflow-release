"""Upload em host REMOTO: o path publicado pela API (``/data/uploads/<sid>/<f>``)
só existe na máquina da API. Sem os bytes inline no comando, o worker remoto
não acha o arquivo e o anexo/áudio some calado (bug Duck/Planner). Aqui o
worker materializa ``content_b64`` em ``HOST_UPLOADS_DIR`` quando o path não
existe localmente.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from sessionflow_worker import command_consumer as cc
from sessionflow_worker.command_consumer import CommandConsumer


def _consumer() -> CommandConsumer:
    return CommandConsumer(
        channel=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        host_id="host-remoto",
        runtime=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
    )


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    d = tmp_path / "uploads"
    monkeypatch.setattr(cc, "HOST_UPLOADS_DIR", d)
    return d


def test_materializa_bytes_quando_path_nao_existe(uploads: Path) -> None:
    raw = b"OggS-opus-ptt"
    out = _consumer()._resolve_upload_path(
        "/data/uploads/sid123/abc.opus", base64.b64encode(raw).decode()
    )
    assert Path(out) == uploads / "sid123" / "abc.opus"
    assert Path(out).read_bytes() == raw


def test_path_local_existente_ignora_bytes(tmp_path: Path, uploads: Path) -> None:
    f = tmp_path / "local.png"
    f.write_bytes(b"real")
    out = _consumer()._resolve_upload_path(str(f), base64.b64encode(b"x").decode())
    assert out == str(f)
    assert f.read_bytes() == b"real"


def test_sem_bytes_mantem_comportamento_antigo(uploads: Path) -> None:
    out = _consumer()._resolve_upload_path("/data/uploads/sid/nao-existe.png")
    assert out == "/data/uploads/sid/nao-existe.png"


def test_nome_malicioso_nao_escapa_do_dir(uploads: Path) -> None:
    out = _consumer()._resolve_upload_path(
        "/data/uploads/../../etc/passwd", base64.b64encode(b"x").decode()
    )
    assert Path(out).resolve().is_relative_to(uploads.resolve())
