"""워크트리·outbox·통합 브랜치의 생명주기. docs/05 가 canonical 이다.

여기서 증명하는 것은 둘이다 — 워크스페이스는 **저장소 밖**에 있고, 통합은 **사용자의
브랜치를 건드리지 않는다.**
"""

import subprocess

import pytest

from harness.exec import workspace as workspace_module
from harness.exec.workspace import (
    Workspaces,
    integration_branch,
    scratch_root,
    task_branch,
)
from harness.git import GitResult
from harness.models import ExecutionProfile


def out(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def spaces(repo, tmp_path):
    return Workspaces(
        repo, "run-1", ExecutionProfile.WORKTREE, scratch=tmp_path / "scratch"
    )


def write(workspace, name, text):
    (workspace.path / name).write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- 배치


def test_the_worktree_is_outside_the_repository(spaces, repo):
    """docs/05 — 워크트리가 `.harness/` 안이면 agent 의 cwd 가 control-plane 안이 된다."""
    workspace = spaces.open("T-001")
    assert repo not in workspace.path.parents
    assert workspace.path.is_dir()


def test_the_outbox_is_outside_the_worktree(spaces):
    """docs/04 — outbox 가 워크트리 안이면 claim 파일이 diff 를 오염시킨다."""
    workspace = spaces.open("T-001")
    assert workspace.path not in workspace.outbox(1).parents


def test_each_attempt_gets_its_own_outbox(spaces):
    """docs/04 — 이전 attempt 의 산출물이 다음 판정에 섞이지 않는다."""
    workspace = spaces.open("T-001")
    assert workspace.outbox(1) != workspace.outbox(2)


def test_scratch_paths_are_scoped_to_the_repository(tmp_path):
    """run-id 는 시각에서 만들어진다. 저장소가 다른데 경로가 같으면 워크트리가 서로를 덮는다."""
    assert scratch_root(tmp_path / "a", "run-1") != scratch_root(
        tmp_path / "b", "run-1"
    )


# --------------------------------------------------------------------------- 통합 브랜치


def test_the_integration_branch_starts_at_head(spaces, repo):
    spaces.open("T-001")
    assert out(repo, "rev-parse", integration_branch("run-1")) == out(
        repo, "rev-parse", "HEAD"
    )


def test_the_user_branch_never_moves(spaces, repo):
    """docs/05 — run 결과를 사용자 브랜치로 옮기는 것은 ship 의 일이다."""
    before = out(repo, "rev-parse", "main")
    workspace = spaces.open("T-001")
    write(workspace, "a.py", "x\n")
    assert spaces.integrate(workspace).ok

    assert out(repo, "rev-parse", "main") == before
    assert out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_integration_advances_the_integration_branch(spaces, repo):
    workspace = spaces.open("T-001")
    write(workspace, "a.py", "x\n")
    head_before = out(repo, "rev-parse", integration_branch("run-1"))

    assert spaces.integrate(workspace).ok
    assert out(repo, "rev-parse", integration_branch("run-1")) != head_before
    assert out(repo, "show", f"{integration_branch('run-1')}:a.py") == "x"


def test_a_task_worktree_branches_from_the_integration_tip(spaces):
    """docs/05 — 앞선 task 가 만든 것이 뒤 task 의 워크스페이스에 있어야 한다."""
    first = spaces.open("T-001")
    write(first, "a.py", "from upstream\n")
    spaces.integrate(first)

    second = spaces.open("T-002")
    assert (second.path / "a.py").read_text(encoding="utf-8") == "from upstream\n"


def test_a_task_that_changed_nothing_still_integrates(spaces, repo):
    """analysis · readonly task 다. 머지할 것이 없는 것은 충돌이 아니다."""
    workspace = spaces.open("T-001")
    tip = out(repo, "rev-parse", integration_branch("run-1"))

    assert spaces.integrate(workspace).ok
    assert out(repo, "rev-parse", integration_branch("run-1")) == tip


def test_the_branch_name_carries_the_run_and_the_task(spaces, repo):
    workspace = spaces.open("T-001")
    assert workspace.branch == task_branch("run-1", "T-001") == "harness/run-1/T-001"
    assert out(repo, "rev-parse", "--abbrev-ref", "HEAD") != workspace.branch


# --------------------------------------------------------------------------- 충돌


def conflicting_pair(spaces):
    """같은 파일을 서로 다르게 바꾼 두 워크스페이스. 앞의 것이 먼저 통합된다."""
    late = spaces.open("T-001")
    early = spaces.open("T-002")
    write(late, "a.py", "late\n")
    write(early, "a.py", "early\n")
    spaces.integrate(early)
    return late


def test_a_merge_conflict_names_the_conflicting_paths(spaces):
    result = spaces.integrate(conflicting_pair(spaces))
    assert not result.ok
    assert result.conflicts == ("a.py",)


def test_a_conflict_does_not_move_the_integration_branch(spaces, repo):
    late = conflicting_pair(spaces)
    tip = out(repo, "rev-parse", integration_branch("run-1"))

    spaces.integrate(late)
    assert out(repo, "rev-parse", integration_branch("run-1")) == tip


def test_a_conflicted_worktree_is_left_in_a_clean_state(spaces):
    """사람이 조사할 워크트리다. 반쯤 머지된 상태로 넘겨주지 않는다."""
    late = conflicting_pair(spaces)
    spaces.integrate(late)
    assert out(late.path, "rev-parse", "--verify", "MERGE_HEAD") == ""
    assert "UU" not in out(late.path, "status", "--porcelain")


# --------------------------------------------------------------------------- 정리


def test_a_finished_workspace_is_removed(spaces):
    workspace = spaces.open("T-001")
    spaces.close(workspace, keep=False)
    assert not workspace.path.exists()


def test_a_failed_workspace_is_kept_for_inspection(spaces):
    """docs/05 — 실패한 워크트리를 남기는 것은 사람이 조사할 수 있게 하기 위해서다."""
    workspace = spaces.open("T-001")
    write(workspace, "a.py", "half done\n")
    spaces.close(workspace, keep=True)
    assert (workspace.path / "a.py").is_file()


def test_removing_a_workspace_removes_its_git_registration(spaces, repo):
    workspace = spaces.open("T-001")
    spaces.close(workspace, keep=False)
    assert str(workspace.path) not in out(repo, "worktree", "list")


def test_opening_the_same_task_again_starts_from_a_clean_worktree(spaces):
    """docs/10 — 죽은 attempt 의 워크트리를 재사용하지 않는다."""
    first = spaces.open("T-001")
    write(first, "leftover.py", "from a dead attempt\n")

    second = spaces.open("T-001")
    assert not (second.path / "leftover.py").exists()


def test_an_existing_workspace_is_found_for_resume(spaces):
    assert spaces.existing("T-001") is None
    opened = spaces.open("T-001")
    assert spaces.existing("T-001").path == opened.path


def test_a_removed_workspace_is_no_longer_found(spaces):
    workspace = spaces.open("T-001")
    spaces.close(workspace, keep=False)
    assert spaces.existing("T-001") is None


def test_checkpoint_does_not_return_the_old_head_when_commit_fails(spaces, monkeypatch):
    workspace = spaces.open("T-001")
    write(workspace, "new_test.py", "def test_new():\n    assert False\n")
    old_head = out(workspace.path, "rev-parse", "HEAD")
    real_git = workspace_module.git

    def reject_commit(args, cwd):
        if "commit" in args:
            return GitResult(1, "", "hook rejected commit")
        return real_git(args, cwd)

    monkeypatch.setattr(workspace_module, "git", reject_commit)

    assert spaces.checkpoint(workspace, "red tests") is None
    assert out(workspace.path, "rev-parse", "HEAD") == old_head


# --------------------------------------------------------------------------- safe 프로파일


def test_the_safe_profile_works_in_the_repository_itself(repo, tmp_path):
    """docs/05 — safe 에는 branch/worktree 분리가 없다. 있다고 말하지 않는다."""
    spaces = Workspaces(
        repo, "run-1", ExecutionProfile.SAFE, scratch=tmp_path / "scratch"
    )
    workspace = spaces.open("T-001")
    assert workspace.path == repo
    assert workspace.branch is None


def test_the_safe_profile_has_no_integration(repo, tmp_path):
    spaces = Workspaces(
        repo, "run-1", ExecutionProfile.SAFE, scratch=tmp_path / "scratch"
    )
    workspace = spaces.open("T-001")

    assert spaces.integrate(workspace).ok
    assert out(repo, "rev-parse", "--verify", integration_branch("run-1")) == ""


def test_the_safe_profile_still_gets_an_outbox_outside_the_repository(repo, tmp_path):
    spaces = Workspaces(
        repo, "run-1", ExecutionProfile.SAFE, scratch=tmp_path / "scratch"
    )
    workspace = spaces.open("T-001")
    assert repo not in workspace.outbox(1).parents


def test_closing_a_safe_workspace_never_touches_the_repository(repo, tmp_path):
    spaces = Workspaces(
        repo, "run-1", ExecutionProfile.SAFE, scratch=tmp_path / "scratch"
    )
    workspace = spaces.open("T-001")
    spaces.close(workspace, keep=False)
    assert (repo / "README.md").is_file()


# --------------------------------------------------------------------------- 프로파일 선택


def test_a_task_can_declare_its_own_profile(repo, tmp_path):
    """docs/03 — task 계약의 `profile` 이 run 기본값을 이긴다."""
    spaces = Workspaces(
        repo, "run-1", ExecutionProfile.WORKTREE, scratch=tmp_path / "scratch"
    )
    workspace = spaces.open("T-001", profile=ExecutionProfile.SAFE)
    assert workspace.path == repo


def test_git_state_stays_usable_after_a_run(spaces, repo):
    """워크트리를 만들고 지운 뒤에도 저장소가 정상이어야 한다."""
    for task_id in ("T-001", "T-002"):
        workspace = spaces.open(task_id)
        write(workspace, f"{task_id}.py", "x\n")
        spaces.integrate(workspace)
        spaces.close(workspace, keep=False)

    assert out(repo, "status", "--porcelain") == ""
    assert out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
