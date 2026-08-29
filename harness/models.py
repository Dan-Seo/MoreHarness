"""순수 데이터 타입.

docs/02 의 의존 방향에 따라 이 모듈은 다른 하네스 모듈에 의존하지 않는다.
verdict 와 state 의 canonical 정의는 docs/03 이며 여기서는 그것을 그대로 옮긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class Verdict(StrEnum):
    """판정 결과. docs/03 — 정확히 다섯 개다."""

    VERIFIED = "verified"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    ERROR = "error"
    BUDGET_EXHAUSTED = "budget_exhausted"


class State(StrEnum):
    """워크플로 위치. verdict 와 다른 축이다. docs/03 — 열두 개다."""

    PENDING = "pending"
    READY = "ready"
    PRECHECK = "precheck"
    RUNNING = "running"
    EXECUTED = "executed"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    NEEDS_REPLAN = "needs_replan"
    INTEGRATION_CONFLICT = "integration_conflict"
    HUMAN_REQUIRED = "human_required"
    DONE = "done"


class ExecutionProfile(StrEnum):
    """docs/05 — 프로파일별 보장/미보장은 그 문서가 canonical 이다."""

    SAFE = "safe"
    WORKTREE = "worktree"
    CONTAINER = "container"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ContainerSpec:
    """docs/05 — `container` 프로파일의 실행 계약. 조립 규칙은 그 문서가 canonical 이다."""

    image: str
    runtime: str = "docker"
    network: str | None = None
    mounts: tuple[str, ...] = ()


class TaskKind(StrEnum):
    IMPLEMENTATION = "implementation"
    ANALYSIS = "analysis"
    READONLY = "readonly"


class RiskLevel(StrEnum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TaskProjection:
    """journal 이 결정한 한 task 의 현재 모습."""

    task_id: str
    state: State = State.PENDING
    attempt: int = 0
    verdict: Verdict | None = None
    verdict_attempt: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": str(self.state),
            "attempt": self.attempt,
            "verdict": str(self.verdict) if self.verdict else None,
            "verdict_attempt": self.verdict_attempt,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskProjection:
        verdict = data.get("verdict")
        return cls(
            task_id=data["task_id"],
            state=State(data["state"]),
            attempt=data.get("attempt", 0),
            verdict=Verdict(verdict) if verdict else None,
            verdict_attempt=data.get("verdict_attempt"),
            reason=data.get("reason"),
        )


@dataclass
class Debt:
    """task 판정은 막지 않고 ship 을 막는 미해결 실패. docs/08 의 원장 항목."""

    debt_id: str
    cmd: tuple[str, ...]
    origin_task: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"debt_id": self.debt_id, "cmd": list(self.cmd), "origin_task": self.origin_task}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Debt:
        return cls(
            debt_id=data["debt_id"],
            cmd=tuple(data.get("cmd") or ()),
            origin_task=data.get("origin_task"),
        )


@dataclass
class RunState:
    """journal 의 projection. canonical 진실은 journal 이고 이것은 캐시다. docs/03"""

    run_id: str
    last_applied_seq: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    manifest: dict[str, Any] | None = None
    tasks: dict[str, TaskProjection] = field(default_factory=dict)
    open_debts: dict[str, Debt] = field(default_factory=dict)

    @property
    def human_required(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.state is State.HUMAN_REQUIRED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "last_applied_seq": self.last_applied_seq,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "manifest": self.manifest,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "open_debts": {k: v.to_dict() for k, v in self.open_debts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        return cls(
            run_id=data["run_id"],
            last_applied_seq=data.get("last_applied_seq", 0),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            manifest=data.get("manifest"),
            tasks={k: TaskProjection.from_dict(v) for k, v in (data.get("tasks") or {}).items()},
            open_debts={k: Debt.from_dict(v) for k, v in (data.get("open_debts") or {}).items()},
        )


@dataclass(frozen=True)
class AcceptanceCriterion:
    """하네스가 직접 실행하는 커맨드. docs/03 — 문자열이 아니라 argv 리스트다."""

    cmd: tuple[str, ...]
    expect_fail_before: bool = False
    shell: bool = False


@dataclass(frozen=True)
class Task:
    """docs/03 의 Task 계약.

    `risk`·`agent`·`profile` 이 `None` 인 것은 **선언되지 않았다**는 뜻이다. config
    기본값이나 effective_risk 로 해석하는 것은 실행 시점의 일이지 로딩의 일이 아니다.
    """

    id: str
    name: str
    kind: TaskKind
    satisfies: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    risk: RiskLevel | None = None
    agent: str | None = None
    profile: ExecutionProfile | None = None
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    preconditions: tuple[Mapping[str, Any], ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    acceptance: tuple[AcceptanceCriterion, ...] = ()
    required_outputs: tuple[str, ...] = ()
    optional_outputs: tuple[str, ...] = ()
    spec_hash: str | None = None
