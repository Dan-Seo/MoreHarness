"""eval fixture 의 로딩과 작업 저장소 구성. docs/11 이 canonical 이다.

> **grader 의 AC 는 task `acceptance` 에도, 컨텍스트 조립에도 절대 들어가지 않는다.**

이것은 편의상의 관례가 아니라 eval 의 타당성 자체다. 들어가는 순간 agent 가 채점 기준에
최적화하므로 측정이 무의미해진다. 그래서 `materialize` 는 `grader/` 를 작업 저장소에
**물리적으로 복사하지 않는다.** 규약으로 막는 것이 아니라 파일이 거기 없게 만든다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.git import git

GRADER_DIR = "grader"
# hidden AC 의 argv 에서 fixture 의 `grader/` 절대 경로로 치환된다 (docs/11).
GRADER_PLACEHOLDER = "{grader}"
SEED_DIR = "seed"
TASKS_DIR = "tasks"


@dataclass(frozen=True)
class Fixture:
    name: str
    root: Path
    meta: Mapping[str, Any]
    expected: Mapping[str, Any]


def load(case_dir: Path | str) -> Fixture:
    root = Path(case_dir)
    return Fixture(
        name=root.name,
        root=root,
        meta=_yaml(root / "meta.yaml"),
        expected=_yaml(root / GRADER_DIR / "expected.yaml"),
    )


def discover(fixtures_dir: Path | str) -> list[Fixture]:
    """docs/11 — `<dir>/<case>/seed/` 를 찾고, 없으면 `<dir>/fixtures/<case>/seed/` 를 찾는다."""
    directory = Path(fixtures_dir)
    if not directory.is_dir():
        return []
    cases = [case for case in sorted(directory.iterdir()) if (case / SEED_DIR).is_dir()]
    if not cases and (directory / "fixtures").is_dir():
        return discover(directory / "fixtures")
    return [load(case) for case in cases]


def materialize(fixture: Fixture, dest: Path | str) -> Path:
    """fixture 의 `seed/` 로 git 저장소를 만든다. `grader/` 는 따라오지 않는다.

    저장소여야 하는 이유는 판정이 `git diff` 를 읽기 때문이다 (docs/06).
    """
    repo = Path(dest)
    shutil.copytree(fixture.root / SEED_DIR, repo, dirs_exist_ok=True)

    tasks = fixture.root / TASKS_DIR
    if tasks.is_dir():
        shutil.copytree(tasks, repo / TASKS_DIR, dirs_exist_ok=True)

    (repo / ".harness" / "runs").mkdir(parents=True, exist_ok=True)
    _init_repo(repo)
    return repo


def context_sources(fixture: Fixture) -> list[Path]:
    """컨텍스트 조립에 넣어도 되는 것. `grader/` 는 여기 없다."""
    return [
        path
        for path in sorted(fixture.root.rglob("*"))
        if path.is_file() and GRADER_DIR not in path.relative_to(fixture.root).parts
    ]


def _init_repo(repo: Path) -> None:
    git(["init", "-q", "-b", "main"], cwd=repo)
    git(["config", "user.email", "eval@harness.local"], cwd=repo)
    git(["config", "user.name", "harness-eval"], cwd=repo)
    git(["add", "-A"], cwd=repo)
    git(["commit", "-q", "-m", "fixture seed"], cwd=repo)


def _yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
