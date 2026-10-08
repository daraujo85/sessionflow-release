"""Testes de ``session_move`` (funções puras) e dos handlers ``move_out``/``move_in``.

Funções puras: repo git REAL em ``tmp_path`` (com remote bare p/ "commits não
enviados"). Handlers: runtime/collections/publish mockados — sem tmux, Mongo
ou RabbitMQ reais.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
from pathlib import Path

import pytest

from sessionflow_worker import session_move as sm


# -- helpers git ------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return out.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Repo ``work`` com upstream num remote bare ``origin``."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = _init_repo(tmp_path / "work")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "main")
    return work, bare


@pytest.fixture(autouse=True)
def _git_isolado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Config global do host (safe.directory, hooks, signing) não interfere.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


# -- git_block_reason -------------------------------------------------------

def test_git_block_reason_nao_repo(tmp_path: Path) -> None:
    assert sm.git_block_reason(str(tmp_path)) is None


def test_work_dir_vazio_nao_usa_cwd_do_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("sujo\n")
    monkeypatch.chdir(repo)
    assert sm.git_block_reason("") is None
    assert sm.current_branch("") is None
    assert sm.prepare_repo("", "main") is None


def test_git_block_reason_limpo_sem_upstream(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    assert sm.git_block_reason(str(repo)) is None


def test_git_block_reason_sujo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("mudou\n")
    msg = sm.git_block_reason(str(repo), host="mac")
    assert msg == "Há mudanças não commitadas em mac: faça commit/push antes de mover."


def test_git_block_reason_untracked_conta_como_sujo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    (repo / "novo.txt").write_text("x\n")
    assert "não commitadas" in (sm.git_block_reason(str(repo)) or "")


def test_git_block_reason_commits_nao_enviados(tmp_path: Path) -> None:
    work, _ = _repo_with_remote(tmp_path)
    assert sm.git_block_reason(str(work)) is None
    (work / "b.txt").write_text("b\n")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "b")
    msg = sm.git_block_reason(str(work), host="mac")
    assert msg == "Há commits não enviados em mac: faça push antes de mover."


# -- current_branch ---------------------------------------------------------

def test_current_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    assert sm.current_branch(str(repo)) == "main"
    _git(repo, "checkout", "-q", "-b", "feat/x")
    assert sm.current_branch(str(repo)) == "feat/x"


def test_current_branch_nao_repo_e_detached(tmp_path: Path) -> None:
    assert sm.current_branch(str(tmp_path)) is None
    repo = _init_repo(tmp_path / "r")
    _git(repo, "checkout", "-q", "--detach")
    assert sm.current_branch(str(repo)) is None


# -- find_claude_jsonl / jsonl_target_path ------------------------------------

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"

def test_find_claude_jsonl(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    f = projects / "-Users-d-x" / f"{SID}.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}\n")
    assert sm.find_claude_jsonl(SID, projects_dir=projects) == f
    assert sm.find_claude_jsonl(SID2, projects_dir=projects) is None
    assert sm.find_claude_jsonl("", projects_dir=projects) is None


def test_find_claude_jsonl_default_usa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / ".claude" / "projects" / "-x" / f"{SID}.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}\n")
    assert sm.find_claude_jsonl(SID) == f


def test_jsonl_target_path_slug(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    wd = tmp_path / "meu.repo_x"
    wd.mkdir()
    p = sm.jsonl_target_path(str(wd), SID, projects_dir=projects)
    slug = str(wd.resolve()).replace("/", "-").replace(".", "-").replace("_", "-")
    assert p == projects / slug / f"{SID}.jsonl"

@pytest.mark.parametrize("bad", ["../../etc/passwd", "*", "abc-123", "u1; rm -rf ~"])
def test_claude_session_id_invalido_levanta(tmp_path: Path, bad: str) -> None:
    projects = tmp_path / "projects"
    with pytest.raises(ValueError, match="claude_session_id inválido"):
        sm.find_claude_jsonl(bad, projects_dir=projects)
    with pytest.raises(ValueError, match="claude_session_id inválido"):
        sm.jsonl_target_path(str(tmp_path), bad, projects_dir=projects)


# -- pack / unpack ----------------------------------------------------------

def test_pack_unpack_pequeno() -> None:
    data = b'{"cwd":"/a"}\n' * 1000
    chunks = sm.pack(data)
    assert len(chunks) == 1
    # Cada chunk é gzip individual (não gzip do payload inteiro + slice).
    assert gzip.decompress(chunks[0]) == data
    assert sm.unpack(chunks) == data


def test_pack_grande_vira_chunks_individuais_e_remonta() -> None:
    data = os.urandom(9 * 1024 * 1024)  # aleatório não comprime → > 8 MB
    chunks = sm.pack(data)
    assert len(chunks) == 2
    # Random data não comprime: chunk comprimido pode ser MAIOR que o raw.
    # O invariante é: cada chunk descomprime pra sua fatia raw.
    assert gzip.decompress(chunks[0]) == data[: sm.CHUNK_SIZE]
    assert gzip.decompress(chunks[1]) == data[sm.CHUNK_SIZE :]
    assert sm.unpack(chunks) == data


def test_pack_chunk_comprimido_texto_eh_menor_que_raw() -> None:
    """Texto/jsonl comprime ~3×: 8 MB raw → ~2-3 MB comprimido, cabe
    folgadamente no doc Mongo de 16 MB. Esta é a propriedade que faz
    sessões grandes passarem: o número de chunks é dimensionado pelo
    raw (CHUNK_SIZE), não pelo comprimido."""
    # Texto realista: bytes repetidos comprime bem.
    data = (b'{"cwd":"/home/u/proj","msg":"ola"}\n' * 300_000)  # ~10.5 MB > 8 MB
    assert len(data) > sm.CHUNK_SIZE
    chunks = sm.pack(data)
    # Texto comprime ~3×: 10.5 MB raw → ~2 chunks de 8 MB raw cada →
    # cada chunk comprimido fica bem menor que 8 MB.
    assert len(chunks) == 2
    assert all(len(c) < sm.CHUNK_SIZE for c in chunks)
    assert sm.unpack(chunks) == data


def test_pack_escalona_com_tamanho() -> None:
    """Sem teto global: número de chunks cresce com o jsonl. 16 MB raw
    vira 2 chunks de 8 MB. Texto (comprime bem) vira chunks bem menores
    que 8 MB comprimidos — o importante é o raw ser fatiado em 8 MB."""
    data = os.urandom(16 * 1024 * 1024)
    chunks = sm.pack(data)
    assert len(chunks) == 2
    assert sm.unpack(chunks) == data

    data = os.urandom(24 * 1024 * 1024)
    chunks = sm.pack(data)
    assert len(chunks) == 3
    assert sm.unpack(chunks) == data


def test_pack_vazio_retorna_lista_vazia() -> None:
    assert sm.pack(b"") == []


def test_pack_chunk_unico_e_multiplos_gzip_individuais() -> None:
    """Garante que cada chunk é gzip do SEU pedaço — não gzip do payload
    inteiro fatiado depois (que daria 1 header gzip + lixo nos demais)."""
    data = b"x" * (sm.CHUNK_SIZE * 2 + 100)
    chunks = sm.pack(data)
    assert len(chunks) == 3
    # Cada chunk descomprime sozinho e bate com a fatia correspondente.
    raw0 = gzip.decompress(chunks[0])
    raw1 = gzip.decompress(chunks[1])
    raw2 = gzip.decompress(chunks[2])
    assert raw0 == data[0:sm.CHUNK_SIZE]
    assert raw1 == data[sm.CHUNK_SIZE:2 * sm.CHUNK_SIZE]
    assert raw2 == data[2 * sm.CHUNK_SIZE:]
    assert sm.unpack(chunks) == data


def test_unpack_tolera_formato_antigo_single_gzip() -> None:
    """Compat: payload gravado por versão antiga (1 chunk = gzip do
    payload inteiro) ainda descomprime — workers podem estar em versões
    mistas durante o deploy."""
    data = b'{"cwd":"/a"}\n' * 100
    antigo = [gzip.compress(data)]
    assert sm.unpack(antigo) == data


def test_unpack_ignora_chunk_nao_gzip_sem_estourar() -> None:
    """Chunk que não é gzip (formato legacy puro texto): vira fallback
    (bytes crus concatenados). Não pode levantar."""
    chunks = [b"sem-gzip-aqui"]
    assert sm.unpack(chunks) == b"sem-gzip-aqui"


# -- rewrite_cwd ------------------------------------------------------------

def test_rewrite_cwd() -> None:
    linhas = [
        json.dumps({"type": "user", "cwd": "/Users/d/proj", "msg": "olá"}),
        "isto não é json {",
        json.dumps({"type": "summary"}),
        "",
        json.dumps({"cwd": "/Users/d/proj", "x": [1, 2]}),
    ]
    texto = "\n".join(linhas) + "\n"
    out = sm.rewrite_cwd(texto, "/home/u/proj")
    novas = out.split("\n")
    assert json.loads(novas[0]) == {"type": "user", "cwd": "/home/u/proj", "msg": "olá"}
    assert novas[1] == "isto não é json {"  # preservada byte a byte
    assert novas[2] == linhas[2]  # sem cwd: intocada
    assert novas[3] == ""
    assert json.loads(novas[4])["cwd"] == "/home/u/proj"
    assert out.endswith("\n")


def test_rewrite_cwd_preserva_crlf_e_json_nao_objeto() -> None:
    texto = '{"cwd":"/a"}\r\n[1,2]\r\n"str"'
    out = sm.rewrite_cwd(texto, "/b")
    assert out == '{"cwd":"/b"}\r\n[1,2]\r\n"str"'


# -- prepare_repo -----------------------------------------------------------

def test_prepare_repo_nao_repo(tmp_path: Path) -> None:
    assert sm.prepare_repo(str(tmp_path), "main") is None


def test_prepare_repo_sujo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("sujo\n")
    msg = sm.prepare_repo(str(repo), "main", host="duck")
    assert msg == "Há mudanças não commitadas em duck: faça commit/push antes de mover."


def test_prepare_repo_fetch_checkout_pull(tmp_path: Path) -> None:
    # Origem (A) cria branch nova e dá push; destino (B) é um clone antigo.
    work, bare = _repo_with_remote(tmp_path)
    dest = tmp_path / "dest"
    subprocess.run(["git", "clone", "-q", str(bare), str(dest)], check=True)
    _git(work, "checkout", "-q", "-b", "feat/y")
    (work / "y.txt").write_text("y\n")
    _git(work, "add", "y.txt")
    _git(work, "commit", "-q", "-m", "y")
    _git(work, "push", "-q", "-u", "origin", "feat/y")

    assert sm.prepare_repo(str(dest), "feat/y") is None
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "feat/y"
    assert (dest / "y.txt").exists()

    # Mais um commit em A na mesma branch → B faz pull --ff-only.
    (work / "z.txt").write_text("z\n")
    _git(work, "add", "z.txt")
    _git(work, "commit", "-q", "-m", "z")
    _git(work, "push", "-q")
    assert sm.prepare_repo(str(dest), "feat/y") is None
    assert (dest / "z.txt").exists()


def test_prepare_repo_branch_inexistente_retorna_erro(tmp_path: Path) -> None:
    work, _ = _repo_with_remote(tmp_path)
    msg = sm.prepare_repo(str(work), "nao-existe", host="duck")
    assert msg is not None and "nao-existe" in msg


def test_prepare_repo_sem_branch_e_sem_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    assert sm.prepare_repo(str(repo), None) is None


# -- git_origin / clone_repo (cross-host resiliente) ------------------------

def test_git_origin_retorna_url(tmp_path: Path) -> None:
    work, bare = _repo_with_remote(tmp_path)
    assert sm.git_origin(str(work)) == str(bare)


def test_git_origin_sem_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    assert sm.git_origin(str(repo)) is None


def test_git_origin_fora_de_repo(tmp_path: Path) -> None:
    assert sm.git_origin(str(tmp_path)) is None


def test_git_origin_vazio(tmp_path: Path) -> None:
    assert sm.git_origin("") is None


def test_clone_repo_sucesso_em_dir_novo(tmp_path: Path) -> None:
    work, _ = _repo_with_remote(tmp_path)
    (work / "x.txt").write_text("x\n")
    _git(work, "add", "x.txt")
    _git(work, "commit", "-q", "-m", "x")
    _git(work, "push", "-q", "-u", "origin", "main")
    origin = sm.git_origin(str(work))
    assert origin is not None

    target = tmp_path / "fresh" / "proj"
    err = sm.clone_repo(origin, str(target))
    assert err is None
    assert (target / ".git").exists()
    assert (target / "x.txt").exists()
    assert _git(target, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_clone_repo_em_dir_vazio_limpa_e_clona(tmp_path: Path) -> None:
    """Clone anterior interrompido deixou o dir vazio: apaga e tenta de novo."""
    work, _ = _repo_with_remote(tmp_path)
    origin = sm.git_origin(str(work))
    assert origin is not None

    target = tmp_path / "meu.repo"
    target.mkdir()
    (target / "lixo.txt").write_text("resto\n")  # único arquivo: limpo e recria

    err = sm.clone_repo(origin, str(target))
    assert err is None, err
    assert (target / ".git").exists()


def test_clone_repo_dir_com_conteudo_nao_repo_recusa(tmp_path: Path) -> None:
    """Defesa: se a pasta existe com arquivos de trabalho, não apaga — devolve
    erro claro pedindo intervenção manual."""
    target = tmp_path / "meu.dir"
    target.mkdir()
    (target / "do_not_delete.txt").write_text("trabalho\n")
    err = sm.clone_repo("https://example.com/foo.git", str(target))
    assert err is not None
    assert "não é repositório" in err
    assert (target / "do_not_delete.txt").exists()  # intacto


def test_clone_repo_dir_ja_repo_retorna_none(tmp_path: Path) -> None:
    """Já é repo: retorna ``None`` (no-op); ``prepare_repo`` segue o trabalho."""
    target = _init_repo(tmp_path / "r")
    err = sm.clone_repo("https://example.com/foo.git", str(target))
    assert err is None


def test_clone_repo_origin_invalido_retorna_erro(tmp_path: Path) -> None:
    target = tmp_path / "novo" / "proj"
    err = sm.clone_repo("https://example.com/nao-existe-xyz.git", str(target))
    assert err is not None
    assert "git clone" in err
    assert not target.exists()  # clone falhou: nada criado


def test_clone_repo_sem_origin_ou_target(tmp_path: Path) -> None:
    assert sm.clone_repo("", str(tmp_path / "x")) is not None
    assert sm.clone_repo("https://example.com", "") is not None
