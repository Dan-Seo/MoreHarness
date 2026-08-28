import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness.exec.workspace import repo_scratch
from harness.schemas import SCHEMA_DIR

DEFAULT_CONFIG = textwrap.dedent(
    """
    version: 1
    defaults:
      adapter: mock
      profile: worktree
      max_parallel: 1
    adapters:
      mock:
        type: mock
    """
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo), "PATH": _path()},
    )


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


@pytest.fixture
def plain_repo(tmp_path: Path) -> Path:
    """`.harness/` 가 아직 없는 git 저장소 — `harness init` 의 입력."""
    root = tmp_path / "plain-repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """`.harness/` 를 갖춘 git 저장소."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")

    harness_dir = root / ".harness"
    (harness_dir / "knowledge").mkdir(parents=True)
    (harness_dir / "runs").mkdir()
    (harness_dir / "config.yaml").write_text(DEFAULT_CONFIG, encoding="utf-8")
    (harness_dir / "constitution.md").write_text("# constitution\n", encoding="utf-8")
    (harness_dir / "approved_commands.yaml").write_text("approvals: []\n", encoding="utf-8")

    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    yield root

    # 워크트리와 outbox 는 저장소 밖(시스템 temp)이므로 tmp_path 정리가 닿지 않는다.
    shutil.rmtree(repo_scratch(root), ignore_errors=True)


@pytest.fixture
def schemas_dir() -> Path:
    return SCHEMA_DIR
