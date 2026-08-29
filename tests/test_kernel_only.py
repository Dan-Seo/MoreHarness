"""M8 완료 기준 — **옵션 레이어를 전부 제거해도 커널이 동작한다** (docs/02).

옵션 모듈을 import 할 수 없게 만든 서브프로세스에서 `init → run → status` 를 완주시킨다.
서브프로세스인 이유는 제거를 흉내내는 것이 아니라 실제로 import 를 막기 위해서다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]

# docs/02 의 옵션 목록. 커널은 이 중 어느 것도 import 하지 않는다.
OPTIONS = (
    "harness.exec.scheduler",
    "harness.exec.review",
    "harness.spec",
    "harness.analyze",
    "harness.converge",
    "harness.risk",
    "harness.context",
    "harness.learn",
    "harness.adapters.claude_cli",
    "harness.adapters.codex_cli",
    "harness.eval",
)

DRIVER = textwrap.dedent(
    '''
    import json
    import sys

    OPTIONS = {options!r}


    class Removed:
        """옵션 모듈이 설치본에 없는 상태를 만든다."""

        def find_spec(self, name, path=None, target=None):
            if name in OPTIONS or any(name.startswith(f"{{o}}.") for o in OPTIONS):
                raise ImportError(f"옵션 모듈이 제거되어 있다: {{name}}")
            return None


    sys.meta_path.insert(0, Removed())

    for name in OPTIONS:
        try:
            __import__(name)
        except ImportError:
            continue
        raise SystemExit(f"{{name}} 이(가) 제거되지 않았다")

    from harness.cli import main

    print("CODES", [main(argv) for argv in json.loads(sys.argv[1])])
    '''
)

CONFIG = textwrap.dedent(
    """
    version: 1
    defaults:
      adapter: mock
      profile: worktree
      max_parallel: 1
    adapters:
      mock:
        type: mock
        tasks:
          T-001:
            files:
              src/a.py: "a\\n"
          T-002:
            files:
              src/b.py: "b\\n"
    """
)

TASKS = {
    "T-001": "id: T-001\nname: first\nkind: implementation\nallowed_paths: ['src/**']\n",
    "T-002": (
        "id: T-002\nname: second\nkind: implementation\n"
        "depends_on: [T-001]\nallowed_paths: ['src/**']\n"
    ),
}


@pytest.fixture
def kernel_repo(repo: Path) -> Path:
    (repo / ".harness" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    tasks = repo / "tasks"
    tasks.mkdir()
    for task_id, body in TASKS.items():
        (tasks / f"{task_id}.task.yaml").write_text(body, encoding="utf-8")
    return repo


def _drive(tmp_path: Path, name: str, *commands: list[str]) -> subprocess.CompletedProcess:
    driver = tmp_path / name
    driver.write_text(DRIVER.format(options=OPTIONS), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), json.dumps(commands)],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(PROJECT)},
    )


def test_the_pipeline_finishes_without_the_option_layer(kernel_repo, tmp_path):
    repo = str(kernel_repo)
    done = _drive(
        tmp_path,
        "kernel_only.py",
        ["init", "--repo", repo],
        ["run", "--repo", repo],
        ["status", "--repo", repo],
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "CODES [0, 0, 0]" in done.stdout, done.stdout + done.stderr
    # 커널이 실제로 일을 했다 — 옵션 없이도 판정이 났다
    assert (kernel_repo / ".harness" / "runs").is_dir()


def test_an_option_backed_command_says_so_instead_of_crashing(kernel_repo, tmp_path):
    """옵션이 없으면 그 커맨드는 없는 것이다. traceback 이 아니라 사유를 말한다."""
    done = _drive(
        tmp_path, "kernel_only_spec.py", ["spec", "무엇인가", "--repo", str(kernel_repo)]
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "CODES [1]" in done.stdout, done.stdout + done.stderr
    assert "harness.spec" in done.stdout
    assert "Traceback" not in done.stderr
