"""워크트리·outbox 생성과 정리, 통합 브랜치 머지. docs/05 가 canonical 이다.

워크트리와 outbox 는 둘 다 **저장소 밖**이다. 워크트리를 `.harness/` 안에 두면 agent 의
cwd 가 control-plane 안이 되어 "`.harness/` 는 agent 영역이 아니다" 와 정면충돌한다.

여기서 만드는 것은 **git 수준의 분리**다. 프로파일이 무엇을 보장하고 무엇을 보장하지
않는지는 docs/05 의 표가 canonical 이며, `worktree` 는 OS 수준 격리를 제공하지 않는다.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness.errors import HarnessError
from harness.git import git
from harness.models import ExecutionProfile

# 하네스 자신의 커밋이다. 저장소의 사용자 설정에 기대지 않는다 — 설정이 없는 저장소에서도
# 통합이 성립해야 하고, 이 커밋의 저자는 사람이 아니다.
COMMITTER = ("-c", "user.name=harness", "-c", "user.email=harness@localhost")


def repo_scratch(repo: Path | str) -> Path:
    """이 저장소의 scratch 루트. 모든 run 이 그 아래에 있다.

    run-id 는 시각에서 만들어지므로 저장소가 다르면 충돌할 수 있다. 그 위에 워크트리를
    만들면 서로의 작업을 덮으므로 저장소를 경로에 넣는다 (docs/03).
    """
    return Path(tempfile.gettempdir()) / "harness" / _repo_key(Path(repo))


def scratch_root(repo: Path | str, run_id: str) -> Path:
    """`<system temp>/harness/<repo-key>/<run-id>` (docs/03)."""
    return repo_scratch(repo) / run_id


def integration_branch(run_id: str) -> str:
    return f"harness/{run_id}/integration"


def task_branch(run_id: str, task_id: str) -> str:
    return f"harness/{run_id}/{task_id}"


@dataclass(frozen=True)
class Workspace:
    """한 task 의 작업 공간. `path` 가 agent 의 cwd 다."""

    task_id: str
    path: Path
    branch: str | None  # safe 프로파일에는 브랜치 분리가 없다
    root: Path

    def outbox(self, attempt: int, repair: int = 0) -> Path:
        """docs/04 — attempt 마다 새 디렉토리. 워크트리 밖이다.

        handoff fixer 도 자기 디렉토리를 받는다. 재생성된 handoff 가 원래 attempt 의
        산출물과 섞이면 무엇을 판정했는지 알 수 없게 된다.
        """
        name = f"attempt-{attempt}" + (f"-repair-{repair}" if repair else "")
        return self.root / "outbox" / name


@dataclass(frozen=True)
class MergeResult:
    ok: bool
    conflicts: tuple[str, ...] = ()
    detail: str = ""


class Workspaces:
    """한 run 의 워크스페이스 전체와 통합 브랜치."""

    def __init__(
        self,
        repo: Path | str,
        run_id: str,
        profile: ExecutionProfile,
        scratch: Path | str | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.run_id = run_id
        self.profile = profile
        self.scratch = Path(scratch) if scratch else scratch_root(self.repo, run_id)

    # ----------------------------------------------------------------- 생성

    def open(self, task_id: str, profile: ExecutionProfile | None = None) -> Workspace:
        """워크스페이스를 연다. **멱등하다** — 남아 있던 워크트리는 지우고 새로 만든다.

        죽은 attempt 의 워크트리를 재사용하지 않는다 (docs/10).
        """
        root = self.scratch / task_id
        if (profile or self.profile) is not ExecutionProfile.WORKTREE:
            return Workspace(task_id, self.repo, None, root)

        self._ensure_integration()
        path = root / "worktree"
        branch = task_branch(self.run_id, task_id)
        self._discard(path, branch)

        # 통합 브랜치의 **현재 tip** 에서 분기한다. run 시작 시점의 HEAD 에서 분기하면
        # 앞선 task 가 만든 것이 이 워크스페이스에 없다 (docs/05).
        result = git(
            ["worktree", "add", "-b", branch, str(path), integration_branch(self.run_id)],
            cwd=self.repo,
        )
        if result.exit_code != 0:
            raise HarnessError(f"{task_id} 의 워크트리를 만들 수 없다: {result.stderr.strip()}")
        return Workspace(task_id, path, branch, root)

    def existing(self, task_id: str) -> Workspace | None:
        """재개용. 죽기 전의 워크트리가 남아 있으면 그것을 돌려준다 (docs/10)."""
        root = self.scratch / task_id
        path = root / "worktree"
        if not path.is_dir():
            return None
        return Workspace(task_id, path, task_branch(self.run_id, task_id), root)

    # ----------------------------------------------------------------- 통합

    def integrate(self, workspace: Workspace) -> MergeResult:
        """변경을 task 브랜치에 커밋하고 통합 브랜치로 머지한다.

        머지는 task 워크트리 안에서 한다. 그래야 사용자의 워크트리를 건드리지 않으면서도
        충돌이 사람이 조사할 수 있는 자리에 나타난다.
        """
        if workspace.branch is None:
            return MergeResult(True, detail="safe 프로파일에는 통합이 없다")

        self._commit(workspace)
        integration = integration_branch(self.run_id)

        merged = git([*COMMITTER, "merge", "--no-edit", integration], cwd=workspace.path)
        if merged.exit_code != 0:
            conflicts = self._conflicts(workspace.path)
            git(["merge", "--abort"], cwd=workspace.path)
            return MergeResult(False, conflicts, (merged.stdout + merged.stderr).strip())

        # task 브랜치가 통합 브랜치를 포함하므로 이것은 fast-forward 다.
        git(["branch", "-f", integration, workspace.branch], cwd=self.repo)
        return MergeResult(True)

    # ----------------------------------------------------------------- 정리

    def close(self, workspace: Workspace, *, keep: bool) -> None:
        """docs/05 — 성공 시 워크트리 제거, 실패 시 보존한다.

        보존하는 것은 사람이 조사할 수 있게 하기 위해서다. 브랜치는 어느 쪽이든 남긴다.
        """
        if workspace.branch is None or keep:
            return
        self._remove_worktree(workspace.path)

    # ----------------------------------------------------------------- 내부

    def _ensure_integration(self) -> None:
        branch = integration_branch(self.run_id)
        if git(["rev-parse", "--verify", "--quiet", branch], cwd=self.repo).exit_code == 0:
            return
        result = git(["branch", branch, "HEAD"], cwd=self.repo)
        if result.exit_code != 0:
            raise HarnessError(f"통합 브랜치를 만들 수 없다: {result.stderr.strip()}")

    def _commit(self, workspace: Workspace) -> None:
        """agent 가 커밋했는지는 판정에 영향을 주지 않는다. 남은 변경을 하네스가 마저 커밋한다."""
        git(["add", "-A"], cwd=workspace.path)
        if not git(["status", "--porcelain"], cwd=workspace.path).stdout.strip():
            return
        git([*COMMITTER, "commit", "-q", "-m", f"harness: {workspace.task_id}"], cwd=workspace.path)

    def _discard(self, path: Path, branch: str) -> None:
        self._remove_worktree(path)
        git(["branch", "-D", branch], cwd=self.repo)  # 없으면 실패한다. 그것으로 충분하다.

    def _remove_worktree(self, path: Path) -> None:
        if path.exists():
            git(["worktree", "remove", "--force", str(path)], cwd=self.repo)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], cwd=self.repo)

    @staticmethod
    def _conflicts(path: Path) -> tuple[str, ...]:
        result = git(["diff", "--name-only", "--diff-filter=U"], cwd=path)
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _repo_key(repo: Path) -> str:
    """사람이 읽을 수 있는 이름 + 절대 경로의 해시. 이름만으로는 유일하지 않다."""
    resolved = repo.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{resolved.name}-{digest}"
