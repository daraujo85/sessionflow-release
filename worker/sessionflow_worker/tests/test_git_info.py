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


def test_create_worktree_creates_dir_and_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_at(repo)
    dest = tmp_path / "repo-clones" / "minha-sessao-clone"

    ok, msg = git_info.create_worktree(str(repo), str(dest), "clone/minha-sessao-clone")

    assert ok is True
    assert msg == str(dest)
    assert dest.is_dir()
    assert (dest / ".git").exists()
    assert "clone/minha-sessao-clone" in git_info.list_branches(str(dest))
    assert git_info.current_branch(str(dest)) == "clone/minha-sessao-clone"


def test_create_worktree_rejects_non_repo(tmp_path):
    dest = tmp_path / "dest"

    ok, msg = git_info.create_worktree(str(tmp_path), str(dest), "clone/x")

    assert ok is False
    assert "repositório" in msg
    assert not dest.exists()


@pytest.mark.parametrize(
    "branch_name",
    ["--upload-pack=evil", "-x", "; rm -rf /", "branch with spaces"],
)
def test_create_worktree_rejects_flag_like_branch_names(tmp_path, branch_name):
    repo = _init_repo(tmp_path)
    dest = tmp_path / "clones" / "x"

    ok, msg = git_info.create_worktree(str(repo), str(dest), branch_name)

    assert ok is False
    assert "inválido" in msg
    assert not dest.exists()


def test_remove_worktree_removes_dir_and_registration(tmp_path):
    repo = _init_repo(tmp_path)
    dest = tmp_path / "repo-clones" / "x"
    ok, _ = git_info.create_worktree(str(repo), str(dest), "clone/x")
    assert ok is True

    ok, msg = git_info.remove_worktree(str(repo), str(dest))

    assert ok is True
    assert msg == "removido"
    assert not dest.exists()
    remaining = git_info._run_git(str(repo), ["worktree", "list", "--porcelain"]) or ""
    assert str(dest) not in remaining


def test_remove_worktree_reports_failure_for_missing_path(tmp_path):
    repo = _init_repo(tmp_path)

    ok, msg = git_info.remove_worktree(str(repo), str(tmp_path / "never-existed"))

    assert ok is False
    assert msg
