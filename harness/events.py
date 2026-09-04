"""이벤트 타입과 페이로드 계약.

docs/03 "이벤트 목록 — canonical" 을 그대로 옮긴다. 새 이벤트 타입을 더할 때는
여기에 추가하고 `store` 의 fold 에 반영한다 (docs/02 확장 지점 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from harness.errors import EventPayloadError


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    PRECONDITION_CHECKED = "precondition_checked"
    COMMAND_POLICY_DECISION = "command_policy_decision"
    TASK_DISPATCHED = "task_dispatched"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    AGENT_EXIT_NONZERO = "agent_exit_nonzero"
    CLAIM_RECEIVED = "claim_received"
    CLAIM_REJECTED = "claim_rejected"
    CLAIM_UNCORROBORATED = "claim_uncorroborated"
    HANDOFF_RECEIVED = "handoff_received"
    HANDOFF_REJECTED = "handoff_rejected"
    HANDOFF_MISSING = "handoff_missing"
    AC_BASELINE_EXECUTED = "ac_baseline_executed"
    AC_POST_EXECUTED = "ac_post_executed"
    DEBT_OPENED = "debt_opened"
    DEBT_CLOSED = "debt_closed"
    DEBT_WAIVED = "debt_waived"
    PATH_VIOLATION = "path_violation"
    RISK_ESCALATED = "risk_escalated"
    REVIEW_FINDING = "review_finding"
    FIXER_DISPATCHED = "fixer_dispatched"
    TDD_PHASE_COMPLETED = "tdd_phase_completed"
    VERDICT_ASSIGNED = "verdict_assigned"
    BUDGET_CHECKPOINT = "budget_checkpoint"
    RUN_FINISHED = "run_finished"


# docs/03 의 "payload 핵심" 열이다. 여기 적힌 키는 반드시 있어야 하고,
# 값이 null 인 것은 허용한다 (예: 벤더가 보고하지 않은 usage).
# 추가 키는 금지하지 않는다 — 표는 핵심 키의 목록이지 전부의 목록이 아니다.
REQUIRED_PAYLOAD_KEYS: dict[EventType, frozenset[str]] = {
    EventType.RUN_STARTED: frozenset({"manifest", "profile", "adapter", "max_parallel"}),
    EventType.PRECONDITION_CHECKED: frozenset({"kind", "name", "ok", "detail"}),
    EventType.COMMAND_POLICY_DECISION: frozenset({"cmd", "verdict", "rule", "approver"}),
    EventType.TASK_DISPATCHED: frozenset(
        {"context_manifest_ref", "prompt_ref", "effective_risk", "base"}
    ),
    EventType.AGENT_STARTED: frozenset({"adapter", "workspace", "outbox"}),
    EventType.AGENT_FINISHED: frozenset(
        {"exit_code", "duration_s", "usage", "runtime_failure", "transcript_ref"}
    ),
    EventType.AGENT_EXIT_NONZERO: frozenset({"exit_code", "stderr_tail"}),
    EventType.CLAIM_RECEIVED: frozenset({"outcome_claim"}),
    EventType.CLAIM_REJECTED: frozenset({"error", "path"}),
    EventType.CLAIM_UNCORROBORATED: frozenset({"hint", "probe", "probe_result"}),
    EventType.HANDOFF_RECEIVED: frozenset({"fields"}),
    EventType.HANDOFF_REJECTED: frozenset({"error", "path"}),
    EventType.HANDOFF_MISSING: frozenset({"required", "present"}),
    EventType.AC_BASELINE_EXECUTED: frozenset(
        {"cmd", "exit_code", "classification", "expect_fail_before"}
    ),
    EventType.AC_POST_EXECUTED: frozenset({"cmd", "exit_code", "classification", "differential"}),
    EventType.DEBT_OPENED: frozenset({"debt_id", "cmd", "origin_task"}),
    EventType.DEBT_CLOSED: frozenset({"debt_id", "closed_by"}),
    EventType.DEBT_WAIVED: frozenset({"debt_id", "approver", "reason"}),
    EventType.PATH_VIOLATION: frozenset({"paths", "rule"}),
    EventType.RISK_ESCALATED: frozenset({"declared", "path_floor", "diff_floor", "effective"}),
    EventType.REVIEW_FINDING: frozenset(
        {"wave", "reviewer", "severity", "rule", "file", "line", "blocking"}
    ),
    EventType.FIXER_DISPATCHED: frozenset({"wave", "scope"}),
    EventType.TDD_PHASE_COMPLETED: frozenset({"phase", "base", "ok", "detail"}),
    EventType.VERDICT_ASSIGNED: frozenset({"verdict", "attempt", "reason", "next_state"}),
    EventType.BUDGET_CHECKPOINT: frozenset({"tokens", "cost_usd", "wall_time_s", "remaining"}),
    EventType.RUN_FINISHED: frozenset({"summary", "open_debts", "human_required"}),
}


def event_id(run_id: str, seq: int) -> str:
    """docs/03 — id = <run_id>-<seq:06d>"""
    return f"{run_id}-{seq:06d}"


def now_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Event:
    """journal 의 불변 레코드. 정정은 수정이 아니라 새 이벤트로 한다."""

    id: str
    seq: int
    ts: str
    type: EventType
    run_id: str
    task_id: str | None
    attempt: int | None
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "ts": self.ts,
            "type": str(self.type),
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            id=data["id"],
            seq=data["seq"],
            ts=data["ts"],
            type=EventType(data["type"]),
            run_id=data["run_id"],
            task_id=data.get("task_id"),
            attempt=data.get("attempt"),
            payload=dict(data.get("payload") or {}),
        )


def make_event(
    run_id: str,
    seq: int,
    type: EventType,
    payload: Mapping[str, Any],
    task_id: str | None = None,
    attempt: int | None = None,
    ts: str | None = None,
) -> Event:
    event_type = EventType(type)
    missing = REQUIRED_PAYLOAD_KEYS[event_type] - set(payload)
    if missing:
        raise EventPayloadError(f"{event_type} 페이로드에 {sorted(missing)} 이(가) 없다")
    return Event(
        id=event_id(run_id, seq),
        seq=seq,
        ts=ts or now_ts(),
        type=event_type,
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        payload=dict(payload),
    )
