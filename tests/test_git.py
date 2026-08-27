"""git 래퍼. 하네스가 직접 쓰는 고정 argv 만 다룬다."""

import pytest

from harness import git as git_module
from harness.errors import NotARepositoryError
from harness.git import git, is_repo, repo_root


def test_is_repo(repo, tmp_path):
    assert is_repo(repo)
    assert not is_repo(tmp_path / "nowhere")


def test_repo_root_from_a_subdirectory(repo):
    nested = repo / "src" / "api"
    nested.mkdir(parents=True)
    assert repo_root(nested) == repo


def test_repo_root_outside_a_repository_raises(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(NotARepositoryError):
        repo_root(outside)


def test_git_returns_the_result_without_raising_on_nonzero(repo):
    result = git(["rev-parse", "--verify", "no-such-ref"], cwd=repo)
    assert result.exit_code != 0
    assert result.stderr


def test_git_captures_stdout(repo):
    result = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    assert result.exit_code == 0
    assert result.stdout.strip() == "main"


def test_git_takes_an_argv_list_and_never_a_shell_string(repo):
    """docs/06 의 실행 규약과 같다 — argv 리스트, shell=False."""
    source = open(git_module.__file__, encoding="utf-8").read()
    assert "shell=True" not in source
    with pytest.raises(TypeError):
        git("rev-parse HEAD", cwd=repo)
