"""Testes de sessionflow_worker.git_info — branch ativa/lista/checkout."""

from __future__ import annotations

import subprocess

import pytest

from sessionflow_worker import git_info


def _init_repo_at(repo):
    """Inicializa um repo git de verdade (1 commit) EM CIMA de um dir já criado."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("1")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    # Nome de branch estável (independe do default global do usuário rodando o teste).
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    return repo


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return _init_repo_at(repo)


def test_is_git_repo_false_for_plain_dir(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert git_info.is_git_repo(str(d)) is False


def test_is_git_repo_false_for_missing_work_dir():
    assert git_info.is_git_repo(None) is False
    assert git_info.is_git_repo("") is False


def test_current_branch_expands_tilde(tmp_path, monkeypatch):
    # work_dir como o worker de fato grava (ex. "~/Documents/projects/x") —
    # git/Path não expandem "~" sozinhos, só o shell faz isso.
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert git_info.current_branch("~/repo") == "main"


def test_current_branch_on_fresh_repo(tmp_path):
    repo = _init_repo(tmp_path)
    assert git_info.current_branch(str(repo)) == "main"


def test_current_branch_none_outside_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert git_info.current_branch(str(d)) is None


def test_list_branches_includes_new_branch(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "branch", "feature/x"], cwd=repo, check=True)
    branches = git_info.list_branches(str(repo))
    assert set(branches) == {"main", "feature/x"}


def test_checkout_branch_switches_and_reports_current(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "branch", "feature/x"], cwd=repo, check=True)
    ok, msg = git_info.checkout_branch(str(repo), "feature/x")
    assert ok is True
    assert msg == "feature/x"
    assert git_info.current_branch(str(repo)) == "feature/x"


def test_checkout_unknown_branch_fails(tmp_path):
    repo = _init_repo(tmp_path)
    ok, msg = git_info.checkout_branch(str(repo), "does-not-exist")
    assert ok is False
    assert msg


@pytest.mark.parametrize(
    "malicious",
    ["--upload-pack=/bin/sh", "-x", "; rm -rf /", "--help"],
)
def test_checkout_rejects_flag_like_branch_names(tmp_path, malicious):
    repo = _init_repo(tmp_path)
    ok, msg = git_info.checkout_branch(str(repo), malicious)
    assert ok is False
    assert msg == "nome de branch inválido"


def test_find_repos_root_only(tmp_path):
    repo = _init_repo(tmp_path)
    repos = git_info.find_repos(str(repo))
    assert repos == [{"name": ".", "path": str(repo), "branch": "main"}]


def test_find_repos_umbrella_folder_with_sub_repos(tmp_path):
    # Pasta "guarda-chuva" (ex.: prata-digital/) que NÃO é repo ela mesma,
    # mas contém vários sub-projetos que são — caso real do Prata Digital
    # (admin/api/client, cada um seu próprio .git).
    umbrella = tmp_path / "umbrella"
    umbrella.mkdir()
    admin = umbrella / "admin"
    admin.mkdir()
    _init_repo_at(admin)
    api = umbrella / "api"
    api.mkdir()
    _init_repo_at(api)
    subprocess.run(["git", "branch", "feature/x"], cwd=api, check=True)
    subprocess.run(["git", "checkout", "feature/x"], cwd=api, check=True, capture_output=True)
    (umbrella / "not_a_repo").mkdir()

    repos = git_info.find_repos(str(umbrella))
    by_name = {r["name"]: r["branch"] for r in repos}
    assert by_name == {"admin": "main", "api": "feature/x"}


def test_find_repos_root_repo_plus_sub_repo(tmp_path):
    repo = _init_repo(tmp_path)
    nested = repo / "vendor-fork"
    nested.mkdir()
    _init_repo_at(nested)

    repos = git_info.find_repos(str(repo))
    names = [r["name"] for r in repos]
    assert names == [".", "vendor-fork"]


def test_find_repos_empty_for_plain_folder(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "sub").mkdir()
    assert git_info.find_repos(str(d)) == []


def test_find_repos_skips_noise_dirs(tmp_path):
    umbrella = tmp_path / "umbrella"
    umbrella.mkdir()
    noisy = umbrella / "node_modules"
    noisy.mkdir()
    _init_repo_at(noisy)  # nunca deveria contar, mesmo sendo um repo de verdade
    assert git_info.find_repos(str(umbrella)) == []
