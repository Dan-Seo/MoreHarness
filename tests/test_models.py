"""docs/03 의 verdict·state canonical 정의를 검증한다."""

import pytest

from harness import errors, models
from harness.models import (
    Debt,
    ExecutionProfile,
    RiskLevel,
    RunState,
    State,
    TaskKind,
    TaskProjection,
    Verdict,
)


def test_verdict_is_exactly_five():
    assert {v.value for v in Verdict} == {
        "verified",
        "rejected",
        "blocked",
        "error",
        "budget_exhausted",
    }


@pytest.mark.parametrize("not_a_verdict", ["repairing", "needs_replan", "integration_conflict", "human_required"])
def test_state_values_are_not_verdicts(not_a_verdict):
    """docs/03 — 이 넷은 verdict 가 아니라 state 다."""
    assert not_a_verdict in {s.value for s in State}
    with pytest.raises(ValueError):
        Verdict(not_a_verdict)


def test_state_is_exactly_twelve():
    assert {s.value for s in State} == {
        "pending",
        "ready",
        "precheck",
        "running",
        "executed",
        "verifying",
        "reviewing",
        "repairing",
        "needs_replan",
        "integration_conflict",
        "human_required",
        "done",
    }


def test_claimed_state_does_not_exist():
    """claim 은 optional 이므로 상태 기계가 claim 을 기다릴 수 없다. (docs/03)"""
    assert "claimed" not in {s.value for s in State}


def test_no_derived_verdict_like_verified_with_warning():
    """docs/00 — verified_with_warning 같은 파생 verdict 를 만들지 않는다."""
    assert "verified_with_warning" not in {v.value for v in Verdict}


def test_execution_profiles():
    assert {p.value for p in ExecutionProfile} == {"safe", "worktree", "container", "unsafe"}


def test_task_kinds():
    assert {k.value for k in TaskKind} == {"implementation", "analysis", "readonly"}


def test_risk_levels():
    assert {r.value for r in RiskLevel} == {"trivial", "low", "medium", "high", "critical"}


def test_enums_serialize_as_plain_strings():
    import json

    assert json.dumps({"v": Verdict.VERIFIED, "s": State.DONE}) == '{"v": "verified", "s": "done"}'


def test_models_imports_no_other_harness_module():
    """docs/02 — models 와 errors 는 어떤 harness 모듈도 import 하지 않는다."""
    for module in (models, errors):
        source = open(module.__file__, encoding="utf-8").read()
        assert "import harness" not in source, module.__name__
        assert "from harness" not in source, module.__name__
        assert "from ." not in source, module.__name__


def test_task_projection_defaults_to_pending():
    p = TaskProjection(task_id="T-001")
    assert p.state is State.PENDING
    assert p.attempt == 0
    assert p.verdict is None


def test_run_state_human_required_is_derived_from_tasks():
    state = RunState(run_id="run-1")
    state.tasks["T-001"] = TaskProjection(task_id="T-001", state=State.DONE)
    state.tasks["T-002"] = TaskProjection(task_id="T-002", state=State.HUMAN_REQUIRED)
    assert state.human_required == ["T-002"]


def test_run_state_round_trips_through_dict():
    state = RunState(run_id="run-1", last_applied_seq=7, started_at="2026-08-27T14:32:00+09:00")
    state.tasks["T-001"] = TaskProjection(
        task_id="T-001",
        state=State.DONE,
        attempt=2,
        verdict=Verdict.VERIFIED,
        verdict_attempt=2,
        reason=None,
    )
    state.open_debts["D-001"] = Debt(debt_id="D-001", cmd=("npm", "test"), origin_task="T-001")

    restored = RunState.from_dict(state.to_dict())
    assert restored == state
