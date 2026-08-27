"""git 호출 래퍼.

여기서 도는 커맨드는 하네스가 직접 쓴 고정 argv 다. agent 나 planner 가 쓴 커맨드가
아니므로 Command Policy 의 적용 대상이 아니다 (docs/06 의 적용 대상 목록). 실행 규약은
동일하게 argv 리스트이며 셸을 거치지 않는다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from harness.errors import NotARepositoryError


@dataclass(frozen=True)
class GitResult:
    exit_code: int
    stdout: str
    stderr: str


def git(args: Sequence[str], cwd: Path | str) -> GitResult:
    if isinstance(args, str):
        raise TypeError("git 인자는 argv 리스트여야 한다")
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return GitResult(
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def is_repo(path: Path | str) -> bool:
    path = Path(path)
    if not path.is_dir():
        return False
    return git(["rev-parse", "--git-dir"], cwd=path).exit_code == 0


def repo_root(path: Path | str) -> Path:
    path = Path(path)
    if not path.is_dir():
        raise NotARepositoryError(f"{path} 은(는) 디렉토리가 아니다")
    result = git(["rev-parse", "--show-toplevel"], cwd=path)
    if result.exit_code != 0:
        raise NotARepositoryError(f"{path} 은(는) git 저장소가 아니다")
    return Path(result.stdout.strip()).resolve()
