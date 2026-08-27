"""task 집합의 로딩과 의존 그래프.

task 계약은 docs/03 이, ready-set 규칙은 docs/05 가 canonical 이다.

여기서 거부하는 것(스키마 위반, 순환, 미해결 의존)은 전부 docs/10 의 "task definition"
분류다. 재시도가 무의미하므로 예외로 올린다. M6 의 `analyze` 가 같은 검사를 실행 전에
보고하는 게이트가 되고, 여기서는 그래프를 쓸 수 없다는 사실만 말한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Collection, Mapping

import yaml

from harness.errors import TaskDefinitionError
from harness.models import AcceptanceCriterion, ExecutionProfile, RiskLevel, Task, TaskKind
from harness.schemas import first_error

TASKS_DIR = "tasks"


def load_tasks(repo_root: Path | str) -> dict[str, Task]:
    """docs/03 파일 배치 — `tasks/T-###.task.yaml`."""
    directory = Path(repo_root) / TASKS_DIR
    if not directory.is_dir():
        return {}

    tasks: dict[str, Task] = {}
    for path in sorted(directory.glob("*.task.yaml")):
        task = _load_one(path)
        tasks[task.id] = task
    return tasks


class Dag:
    """의존 그래프. 생성 시점에 쓸 수 있는 그래프인지 확인한다."""

    def __init__(self, tasks: Mapping[str, Task]) -> None:
        self.tasks = dict(tasks)
        self._check_dependencies_exist()
        self._order = self._topological()

    def order(self) -> tuple[str, ...]:
        """위상 정렬. 같은 층은 task id 순이라 실행 순서가 재현된다."""
        return self._order

    def ready(self, verified: Collection[str], remaining: Collection[str]) -> tuple[str, ...]:
        """docs/05 — ready_set = { t | t.depends_on 이 전부 verified }."""
        verified, remaining = set(verified), set(remaining)
        return tuple(
            task_id
            for task_id in self._order
            if task_id in remaining and set(self.tasks[task_id].depends_on) <= verified
        )

    def descendants(self, task_id: str) -> frozenset[str]:
        """이 task 가 막히면 함께 막히는 task 들. 나머지 가지는 계속 진행한다 (docs/03)."""
        blocked: set[str] = set()
        frontier = [task_id]
        while frontier:
            current = frontier.pop()
            for other_id, task in self.tasks.items():
                if current in task.depends_on and other_id not in blocked:
                    blocked.add(other_id)
                    frontier.append(other_id)
        return frozenset(blocked)

    def _check_dependencies_exist(self) -> None:
        for task in self.tasks.values():
            for dependency in task.depends_on:
                if dependency not in self.tasks:
                    raise TaskDefinitionError(
                        f"{task.id} 이(가) 존재하지 않는 {dependency} 에 의존한다"
                    )

    def _topological(self) -> tuple[str, ...]:
        remaining = {task_id: set(task.depends_on) for task_id, task in self.tasks.items()}
        ordered: list[str] = []
        while remaining:
            layer = sorted(task_id for task_id, deps in remaining.items() if not deps)
            if not layer:
                raise TaskDefinitionError(f"의존 그래프에 순환이 있다: {sorted(remaining)}")
            for task_id in layer:
                del remaining[task_id]
            for deps in remaining.values():
                deps.difference_update(layer)
            ordered.extend(layer)
        return tuple(ordered)


def _load_one(path: Path) -> Task:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskDefinitionError(f"{path.name} 을(를) 읽을 수 없다: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskDefinitionError(f"{path.name} 의 최상위는 매핑이어야 한다")

    error = first_error("task", data)
    if error:
        raise TaskDefinitionError(f"{path.name} 이(가) task 계약을 만족하지 않는다 — {error}")

    expected_id = path.name[: -len(".task.yaml")]
    if data["id"] != expected_id:
        raise TaskDefinitionError(f"{path.name} 의 id 가 {data['id']!r} 이다. 파일명과 달라야 할 이유가 없다")

    outputs = data.get("outputs") or {}
    return Task(
        id=data["id"],
        name=data["name"],
        kind=TaskKind(data["kind"]),
        satisfies=tuple(data.get("satisfies") or ()),
        depends_on=tuple(data.get("depends_on") or ()),
        risk=_optional(RiskLevel, data.get("risk")),
        agent=data.get("agent"),
        profile=_optional(ExecutionProfile, data.get("profile")),
        allowed_paths=tuple(data.get("allowed_paths") or ()),
        forbidden_paths=tuple(data.get("forbidden_paths") or ()),
        preconditions=tuple(data.get("preconditions") or ()),
        context=data.get("context") or {},
        acceptance=tuple(_criterion(entry) for entry in (data.get("acceptance") or ())),
        required_outputs=tuple(outputs.get("required") or ()),
        optional_outputs=tuple(outputs.get("optional") or ()),
        spec_hash=data.get("spec_hash"),
    )


def _criterion(entry: Mapping[str, Any]) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        cmd=tuple(entry["cmd"]),
        expect_fail_before=bool(entry.get("expect_fail_before", False)),
        shell=bool(entry.get("shell", False)),
    )


def _optional(enum: type, raw: Any) -> Any:
    return enum(raw) if raw is not None else None
