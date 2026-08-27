"""회귀 eval — 오케스트레이션이 규약대로 동작하는가. docs/11 이 canonical 이다.

`mock` 으로만 돌고, 완전히 결정론적이며, 비용이 0 이다. **CI 필수다.**
"""

from pathlib import Path

import pytest

from harness.eval import arms, fixtures
from harness.eval.arms import grade, run_regression
from harness.eval.fixtures import GRADER_DIR, context_sources, discover, load, materialize
from harness.models import RunState, State, TaskProjection, Verdict

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"


def all_fixtures():
    return discover(FIXTURES)


# --------------------------------------------------------------------------- 러너


@pytest.mark.parametrize("fixture", all_fixtures(), ids=lambda f: f.name)
def test_the_fixture_matches_what_it_claims(fixture, tmp_path):
    result = run_regression(fixture, tmp_path)
    assert result.passed, result.mismatches


def test_there_are_fixtures_to_run():
    """비어 있는 스위트는 통과가 아니다."""
    assert len(all_fixtures()) >= 3


def test_the_regression_arm_is_the_kernel_running_on_mock():
    assert arms.REGRESSION_ARM == "harness-full"


# --------------------------------------------------------------------------- hidden grader


def test_the_grader_never_reaches_the_working_repository(tmp_path):
    """docs/11 의 절대 규칙 — grader 는 컨텍스트에도 task acceptance 에도 들어가지 않는다.

    규약으로 막지 않고 파일이 거기 없게 만든다.
    """
    fixture = load(FIXTURES / "three-task-dag")
    repo = materialize(fixture, tmp_path / "work")
    assert list(repo.rglob(GRADER_DIR)) == []
    assert list(repo.rglob("hidden_ac.yaml")) == []


def test_context_sources_exclude_the_grader():
    fixture = load(FIXTURES / "three-task-dag")
    assert all(GRADER_DIR not in str(path) for path in context_sources(fixture))
    assert context_sources(fixture)  # 남는 것이 있어야 제외가 의미 있다


def test_a_materialized_fixture_is_a_git_repository(tmp_path):
    """판정이 git diff 를 읽으므로 저장소가 아니면 아무것도 관측할 수 없다."""
    from harness.git import is_repo

    fixture = load(FIXTURES / "three-task-dag")
    assert is_repo(materialize(fixture, tmp_path / "work"))


# --------------------------------------------------------------------------- 채점


def state_with(**tasks):
    state = RunState(run_id="run-1")
    for task_id, (verdict, task_state) in tasks.items():
        state.tasks[task_id] = TaskProjection(task_id=task_id, verdict=verdict, state=task_state)
    return state


def test_grading_reports_a_verdict_mismatch():
    state = state_with(**{"T-001": (Verdict.REJECTED, State.READY)})
    expected = {"tasks": {"T-001": {"verdict": "verified"}}}
    assert grade(state, expected)


def test_grading_ignores_keys_the_fixture_does_not_claim():
    """docs/11 — 적지 않은 키는 채점하지 않는다."""
    state = state_with(**{"T-001": (Verdict.VERIFIED, State.DONE)})
    assert grade(state, {"tasks": {"T-001": {"verdict": "verified"}}}) == ()


def test_grading_can_claim_a_null_verdict():
    """docs/03 — verdict 없이 state 만 갖는 경우도 주장이다."""
    state = state_with(**{"T-001": (None, State.NEEDS_REPLAN)})
    assert grade(state, {"tasks": {"T-001": {"verdict": None, "state": "needs_replan"}}}) == ()


def test_grading_notices_a_task_the_run_never_saw():
    assert grade(state_with(), {"tasks": {"T-404": {"verdict": "verified"}}})


def test_grading_compares_open_debts_by_command():
    """debt_id 는 해시다. fixture 가 주장하는 것은 어떤 커맨드가 남았는가 이다."""
    state = state_with()
    assert grade(state, {"open_debts": ["npm test"]})
    assert grade(state, {"open_debts": []}) == ()


# --------------------------------------------------------------------------- 의존 방향


def test_eval_does_not_import_the_cli():
    """docs/02, docs/11 — cli 가 eval 을 호출한다. 그 반대가 아니다."""
    for module in (fixtures, arms):
        source = open(module.__file__, encoding="utf-8").read()
        assert "harness.cli" not in source, module.__name__
