"""docs/03 의 이벤트 목록 canonical 정의를 검증한다."""

import pytest

from harness import events
from harness.errors import EventPayloadError
from harness.events import REQUIRED_PAYLOAD_KEYS, Event, EventType, event_id, make_event

# docs/03 "이벤트 목록 — canonical" 표를 그대로 옮긴 것.
CANONICAL_EVENTS = {
    "run_started": {"manifest", "profile", "adapter", "max_parallel"},
    "precondition_checked": {"kind", "name", "ok", "detail"},
    "command_policy_decision": {"cmd", "verdict", "rule", "approver"},
    "task_dispatched": {"context_manifest_ref", "prompt_ref", "effective_risk", "base"},
    "agent_started": {"adapter", "workspace", "outbox"},
    "agent_finished": {"exit_code", "duration_s", "usage", "runtime_failure", "transcript_ref"},
    "agent_exit_nonzero": {"exit_code", "stderr_tail"},
    "claim_received": {"outcome_claim"},
    "claim_rejected": {"error", "path"},
    "claim_uncorroborated": {"hint", "probe", "probe_result"},
    "handoff_received": {"fields"},
    "handoff_rejected": {"error", "path"},
    "handoff_missing": {"required", "present"},
    "ac_baseline_executed": {"cmd", "exit_code", "classification", "expect_fail_before"},
    "ac_post_executed": {"cmd", "exit_code", "classification", "differential"},
    "debt_opened": {"debt_id", "cmd", "origin_task"},
    "debt_closed": {"debt_id", "closed_by"},
    "debt_waived": {"debt_id", "approver", "reason"},
    "path_violation": {"paths", "rule"},
    "risk_escalated": {"declared", "path_floor", "diff_floor", "effective"},
    "review_finding": {"wave", "reviewer", "severity", "rule", "file", "line", "blocking"},
    "fixer_dispatched": {"wave", "scope"},
    "tdd_phase_completed": {"phase", "base", "ok", "detail"},
    "verdict_assigned": {"verdict", "attempt", "reason", "next_state"},
    "budget_checkpoint": {"tokens", "cost_usd", "wall_time_s", "remaining"},
    "run_finished": {"summary", "open_debts", "human_required"},
}


def test_event_catalog_matches_canonical_list():
    assert {e.value for e in EventType} == set(CANONICAL_EVENTS)


def test_every_event_type_declares_required_payload_keys():
    assert set(REQUIRED_PAYLOAD_KEYS) == set(EventType)


@pytest.mark.parametrize("type_name,keys", sorted(CANONICAL_EVENTS.items()))
def test_required_payload_keys_match_the_canonical_table(type_name, keys):
    assert REQUIRED_PAYLOAD_KEYS[EventType(type_name)] == keys


def test_event_id_format():
    """docs/03 — id = <run_id>-<seq:06d>"""
    assert event_id("run-20260827-1432", 42) == "run-20260827-1432-000042"


def test_make_event_builds_the_common_envelope():
    ev = make_event(
        run_id="run-1",
        seq=1,
        type=EventType.AGENT_STARTED,
        payload={"adapter": "mock", "workspace": "/w", "outbox": "/o"},
        task_id="T-003",
        attempt=1,
    )
    assert ev.id == "run-1-000001"
    assert ev.seq == 1
    assert ev.type is EventType.AGENT_STARTED
    assert ev.run_id == "run-1"
    assert ev.task_id == "T-003"
    assert ev.attempt == 1
    assert ev.ts


def test_run_level_events_have_null_task_id_and_attempt():
    """docs/03 — task_id 와 attempt 는 run 수준 이벤트에서 null 이다."""
    ev = make_event(
        run_id="run-1",
        seq=1,
        type=EventType.RUN_STARTED,
        payload={"manifest": {}, "profile": "worktree", "adapter": "mock", "max_parallel": 1},
    )
    assert ev.task_id is None
    assert ev.attempt is None


def test_make_event_rejects_missing_required_payload_key():
    with pytest.raises(EventPayloadError):
        make_event(
            run_id="run-1",
            seq=1,
            type=EventType.VERDICT_ASSIGNED,
            payload={"verdict": "verified", "attempt": 1},  # reason, next_state 누락
            task_id="T-001",
            attempt=1,
        )


def test_make_event_allows_extra_payload_keys():
    """docs/03 의 표는 '핵심' 키다. 추가 키를 금지하지 않는다."""
    ev = make_event(
        run_id="run-1",
        seq=1,
        type=EventType.AGENT_EXIT_NONZERO,
        payload={"exit_code": 2, "stderr_tail": "boom", "note": "extra"},
        task_id="T-001",
        attempt=1,
    )
    assert ev.payload["note"] == "extra"


def test_required_key_may_hold_null():
    """usage 는 벤더가 보고하지 않으면 null 이다. 키의 존재만 요구한다."""
    ev = make_event(
        run_id="run-1",
        seq=1,
        type=EventType.AGENT_FINISHED,
        payload={
            "exit_code": 0,
            "duration_s": 1.5,
            "usage": None,
            "runtime_failure": None,
            "transcript_ref": None,
        },
        task_id="T-001",
        attempt=1,
    )
    assert ev.payload["usage"] is None


def test_event_round_trips_through_dict():
    ev = make_event(
        run_id="run-1",
        seq=3,
        type=EventType.DEBT_OPENED,
        payload={"debt_id": "D-001", "cmd": ["npm", "test"], "origin_task": "T-001"},
        task_id="T-001",
        attempt=1,
    )
    assert Event.from_dict(ev.to_dict()) == ev


def test_timestamp_carries_an_offset():
    ev = make_event(
        run_id="run-1",
        seq=1,
        type=EventType.RUN_FINISHED,
        payload={"summary": {}, "open_debts": [], "human_required": []},
    )
    assert ev.ts[-6] in "+-" or ev.ts.endswith("Z")


def test_events_module_does_not_import_store_or_higher():
    """docs/02 의 의존 방향 — events 는 상위 계층을 import 하지 않는다."""
    source = open(events.__file__, encoding="utf-8").read()
    for forbidden in ("store", "dag", "adapters", "cli", "config"):
        assert f"harness.{forbidden}" not in source
