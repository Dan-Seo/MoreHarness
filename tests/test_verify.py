"""AC 생명주기와 차등 판정. docs/06 이 canonical 이다.

여기서 증명하는 것은 하나다 — 판정은 하네스 소유 증거로만 한다.
"""

import inspect
import re
import sys

import pytest
import yaml
from conftest import git

from harness.config import load as load_config
from harness.dag import load_tasks
from harness.exec.verify import (
    DiffObservation,
    Evidence,
    check_paths,
    judge,
    matches,
    observe_diff,
    run_baseline,
    run_post,
)
from harness.models import State, Verdict
from harness.policy import CommandPolicy

PYTHON = re.escape(sys.executable)
TIMEOUT = 30


def allow_python():
    return CommandPolicy.from_config(
        {"default": "require_approval", "rules": [{"match": PYTHON, "verdict": "allow"}]}
    )


def ac(code, expect_fail_before=False):
    entry = {"cmd": [sys.executable, "-c", code]}
    if expect_fail_before:
        entry["expect_fail_before"] = True
    return entry


def exits(status, expect_fail_before=False):
    return ac(f"raise SystemExit({status})", expect_fail_before)


def flips(flag, red_when_present, expect_fail_before=False):
    """flag 파일의 유무로 green/red 가 뒤집히는 AC."""
    failing, passing = (1, 0) if red_when_present else (0, 1)
    code = f"import os; raise SystemExit({failing} if os.path.exists({str(flag)!r}) else {passing})"
    return ac(code, expect_fail_before)


def make_task(repo, kind="implementation", acceptance=(), outputs=None):
    """task 파일을 쓰고 커밋한다. 커밋해야 뒤이은 diff 관측이 task 를 잡지 않는다."""
    data = {"id": "T-001", "name": "t", "kind": kind}
    if acceptance:
        data["acceptance"] = list(acceptance)
    if outputs:
        data["outputs"] = outputs

    directory = repo / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "T-001.task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "task")
    return load_tasks(repo)["T-001"]


def cycle(repo, task, mutate=None, timeout_s=TIMEOUT, policy=None):
    """baseline → (변경) → post 한 바퀴."""
    policy = policy or allow_python()
    baseline = run_baseline(task, policy, cwd=repo, timeout_s=timeout_s)
    if mutate:
        mutate()
    return baseline, run_post(task, baseline, policy, cwd=repo, timeout_s=timeout_s)


# --------------------------------------------------------------------------- baseline


def test_baseline_classifies_each_criterion(repo):
    task = make_task(repo, acceptance=[exits(0), exits(1)])
    baseline = run_baseline(task, allow_python(), cwd=repo, timeout_s=TIMEOUT)
    assert [o.classification for o in baseline.observations] == ["green_before", "red_before"]


def test_a_criterion_that_already_passes_but_was_declared_failing_is_not_discriminating(repo):
    """docs/06 — expect_fail_before 인데 green_before 면 검증력이 없다. agent 를 실행하지 않는다."""
    task = make_task(repo, acceptance=[exits(0, expect_fail_before=True)])
    baseline = run_baseline(task, allow_python(), cwd=repo, timeout_s=TIMEOUT)
    assert len(baseline.not_discriminating) == 1
    assert baseline.pre_existing == ()


def test_an_unexpected_baseline_failure_becomes_a_debt(repo):
    """docs/06 — expect_fail_before 가 아닌데 red_before 면 pre_existing_failure 다."""
    task = make_task(repo, acceptance=[exits(1)])
    baseline = run_baseline(task, allow_python(), cwd=repo, timeout_s=TIMEOUT)
    assert len(baseline.pre_existing) == 1
    assert baseline.not_discriminating == ()


def test_baseline_payload_matches_the_event_contract(repo):
    """docs/03 — ac_baseline_executed 의 payload."""
    task = make_task(repo, acceptance=[exits(0)])
    baseline = run_baseline(task, allow_python(), cwd=repo, timeout_s=TIMEOUT)
    assert set(baseline.observations[0].baseline_payload()) == {
        "cmd",
        "exit_code",
        "classification",
        "expect_fail_before",
    }


def test_every_acceptance_command_goes_through_command_policy(repo):
    """docs/06 — AC 커맨드는 예외 없이 Command Policy 를 통과한다."""
    marker = repo / "should-not-exist"
    task = make_task(repo, acceptance=[ac(f"open({str(marker)!r}, 'w').close()")])

    baseline = run_baseline(task, CommandPolicy.from_config({}), cwd=repo, timeout_s=TIMEOUT)

    assert not marker.exists()
    assert baseline.observations[0].classification == "red_before"


def test_a_timed_out_criterion_is_red_with_a_recorded_reason(repo):
    task = make_task(repo, acceptance=[ac("import time; time.sleep(30)")])
    baseline = run_baseline(task, allow_python(), cwd=repo, timeout_s=1)
    observed = baseline.observations[0]
    assert observed.classification == "red_before"
    assert observed.timed_out


# --------------------------------------------------------------------------- 차등 판정


def test_green_then_green_is_normal(repo):
    task = make_task(repo, acceptance=[exits(0)])
    _, differentials = cycle(repo, task)
    assert differentials[0].outcome == "ok"
    assert not differentials[0].rejects


def test_green_then_red_is_a_regression(repo):
    """docs/06 차등 판정표 — 회귀는 rejected 다."""
    flag = repo / "flip"
    task = make_task(repo, acceptance=[flips(flag, red_when_present=True)])
    _, differentials = cycle(repo, task, mutate=lambda: flag.write_text("x", encoding="utf-8"))
    assert differentials[0].outcome == "regression"
    assert differentials[0].rejects


def test_red_to_green_proves_the_goal(repo):
    flag = repo / "flip"
    task = make_task(
        repo, acceptance=[flips(flag, red_when_present=False, expect_fail_before=True)]
    )
    _, differentials = cycle(repo, task, mutate=lambda: flag.write_text("x", encoding="utf-8"))
    assert differentials[0].outcome == "proven"
    assert not differentials[0].rejects


def test_red_that_stays_red_when_it_was_expected_to_flip_is_rejected(repo):
    task = make_task(repo, acceptance=[exits(1, expect_fail_before=True)])
    _, differentials = cycle(repo, task)
    assert differentials[0].outcome == "unmet"
    assert differentials[0].rejects


def test_an_unrelated_pre_existing_failure_does_not_reject_the_task(repo):
    """M1 완료 기준 ④ — 무관한 기존 실패가 task 를 rejected 시키지 않는다."""
    task = make_task(repo, acceptance=[exits(1)])
    _, differentials = cycle(repo, task)
    assert differentials[0].outcome == "debt_kept"
    assert not differentials[0].rejects


def test_a_pre_existing_failure_that_turns_green_closes_the_debt(repo):
    flag = repo / "flip"
    task = make_task(repo, acceptance=[flips(flag, red_when_present=False)])
    _, differentials = cycle(repo, task, mutate=lambda: flag.write_text("x", encoding="utf-8"))
    assert differentials[0].outcome == "debt_closed"
    assert not differentials[0].rejects


def test_post_payload_matches_the_event_contract(repo):
    """docs/03 — ac_post_executed 의 payload."""
    task = make_task(repo, acceptance=[exits(0)])
    _, differentials = cycle(repo, task)
    assert set(differentials[0].post_payload()) == {
        "cmd",
        "exit_code",
        "classification",
        "differential",
    }


# --------------------------------------------------------------------------- diff 관측


def test_diff_observation_separates_created_from_changed(repo):
    (repo / "src").mkdir()
    (repo / "src" / "new.py").write_text("x\n", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    diff = observe_diff(repo)

    assert "src/new.py" in diff.created_files
    assert "README.md" in diff.changed_files
    assert not diff.is_empty


def test_an_untouched_repository_has_an_empty_diff(repo):
    assert observe_diff(repo).is_empty


def test_diff_stat_counts_what_the_harness_measured(repo):
    (repo / "README.md").write_text("a\nb\nc\n", encoding="utf-8")
    assert observe_diff(repo).diff_stat["files"] >= 1


def test_the_diff_is_reported_as_the_harness_owned_half_of_task_output(repo):
    """docs/03 — changed_files·created_files·diff_stat 는 하네스 산출이며 항상 존재한다."""
    assert set(observe_diff(repo).to_output()) == {"changed_files", "created_files", "diff_stat"}


# --------------------------------------------------------------------------- 증거 판정


def test_a_clean_run_leaves_the_verdict_to_the_handoff_gate(repo):
    """docs/06 — verified 는 terminal 에서만 기록한다. 증거 통과만으로 기록하지 않는다."""
    task = make_task(repo, acceptance=[exits(0)])
    _, differentials = cycle(repo, task, mutate=lambda: (repo / "src.py").write_text("x"))

    evidence = judge(task, observe_diff(repo), differentials)

    assert isinstance(evidence, Evidence)
    assert evidence.verdict is None
    assert evidence.next_state is None


def test_a_regression_rejects(repo):
    flag = repo / "flip"
    task = make_task(repo, acceptance=[flips(flag, red_when_present=True)])
    _, differentials = cycle(repo, task, mutate=lambda: flag.write_text("x", encoding="utf-8"))
    assert judge(task, observe_diff(repo), differentials).verdict is Verdict.REJECTED


def test_an_implementation_that_changed_nothing_needs_a_replan(repo):
    """docs/06 — diff 가 비었고 AC 가 전부 통과하면 no_op_detected 다. 조용히 통과시키지 않는다."""
    task = make_task(repo, acceptance=[exits(0)])
    _, differentials = cycle(repo, task)

    evidence = judge(task, observe_diff(repo), differentials)

    assert evidence.verdict is None
    assert evidence.next_state is State.NEEDS_REPLAN
    assert evidence.reason == "no_op_detected"


def test_a_readonly_task_that_changed_files_is_rejected(repo):
    task = make_task(repo, kind="readonly", acceptance=[exits(0)])
    _, differentials = cycle(repo, task, mutate=lambda: (repo / "src.py").write_text("x"))
    assert judge(task, observe_diff(repo), differentials).verdict is Verdict.REJECTED


def test_an_analysis_task_is_indifferent_to_the_diff(repo):
    """docs/03 — analysis 의 diff 기대는 무관이다."""
    task = make_task(repo, kind="analysis", acceptance=[exits(0)])
    _, differentials = cycle(repo, task)
    assert judge(task, observe_diff(repo), differentials).verdict is None


def test_a_task_with_no_acceptance_criteria_still_gets_judged(repo):
    task = make_task(repo)
    (repo / "src.py").write_text("x", encoding="utf-8")
    assert judge(task, observe_diff(repo), ()).verdict is None


def test_the_judgement_takes_no_claim_and_no_agent_exit_code(repo):
    """docs/06 — verified 조건 네 개에 claim 도 agent 의 exit code 도 없다."""
    assert set(inspect.signature(judge).parameters) == {
        "task",
        "diff",
        "differentials",
        "violations",
    }


# --------------------------------------------------------------------------- 경로 스코프


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("src/api/**", "src/api/x.py", True),
        ("src/api/**", "src/api/v1/y.py", True),
        ("src/api/**", "src/api", True),
        ("src/api/**", "src/apix/y.py", False),
        ("src/api/**", "src/core/z.py", False),
        ("src/*", "src/x.py", True),
        ("src/*", "src/x/y.py", False),
        ("*.py", "a.py", True),
        ("*.py", "src/a.py", False),
        ("**/test_*.py", "test_a.py", True),
        ("**/test_*.py", "src/deep/test_a.py", True),
        ("**", "anything/at/all.py", True),
        ("a?c", "abc", True),
        ("a?c", "a/c", False),
        (".harness/**", ".harness/config.yaml", True),
        (".harness/**", "harnessx/config.yaml", False),
    ],
)
def test_the_glob_dialect_is_gitignore_flavoured(pattern, path, expected):
    """docs/05 의 glob 방언 표."""
    assert matches(path, pattern) is expected


def diff_of(*paths):
    return DiffObservation(tuple(paths), (), {})


def test_a_change_outside_allowed_paths_is_a_violation():
    violations = check_paths(diff_of("src/core/z.py"), allowed=("src/api/**",), forbidden=())
    assert [v.rule for v in violations] == ["allowed_paths"]
    assert violations[0].paths == ("src/core/z.py",)


def test_a_change_inside_allowed_paths_is_not():
    assert check_paths(diff_of("src/api/x.py"), allowed=("src/api/**",), forbidden=()) == ()


def test_undeclared_allowed_paths_means_no_restriction():
    """docs/05 — 경로를 선언하게 만드는 것은 analyze 의 일이지 판정의 일이 아니다."""
    assert check_paths(diff_of("anywhere/at/all.py"), allowed=(), forbidden=()) == ()


def test_forbidden_wins_over_allowed():
    violations = check_paths(
        diff_of(".harness/config.yaml"), allowed=(".harness/**",), forbidden=(".harness/**",)
    )
    assert [v.rule for v in violations] == ["forbidden_paths"]


def test_the_control_plane_is_forbidden_by_default(repo):
    """docs/05 — `.harness/**` 는 전역 금지 목록에서 뺄 수 없다."""
    (repo / ".harness" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"adapter": "mock"},
                "adapters": {"mock": {"type": "mock"}},
                "forbidden_paths": [],
            }
        ),
        encoding="utf-8",
    )
    assert ".harness/**" in load_config(repo).forbidden_paths


def test_a_deleted_path_is_still_judged():
    """매칭은 경로 문자열만 본다. 파일이 지금 실재하는지 보지 않는다."""
    assert check_paths(diff_of("secrets/key.pem"), allowed=("src/**",), forbidden=())


def test_a_violation_matches_the_event_payload_contract():
    """docs/03 — path_violation 은 paths 와 rule 을 나른다."""
    violation = check_paths(diff_of("x.py"), allowed=("src/**",), forbidden=())[0]
    assert set(violation.to_payload()) == {"paths", "rule"}


def test_created_files_are_judged_too():
    diff = DiffObservation((), ("src/core/new.py",), {})
    assert check_paths(diff, allowed=("src/api/**",), forbidden=())


def test_a_path_violation_rejects(repo):
    """docs/06 의 verified 조건 2. M2 완료 기준이다."""
    task = make_task(repo)
    (repo / "outside.py").write_text("x", encoding="utf-8")
    diff = observe_diff(repo)
    violations = check_paths(diff, allowed=("src/api/**",), forbidden=())

    evidence = judge(task, diff, (), violations)
    assert evidence.verdict is Verdict.REJECTED
    assert evidence.reason == "path_violation"


def test_no_violation_leaves_the_verdict_to_the_handoff_gate(repo):
    task = make_task(repo)
    (repo / "src.py").write_text("x", encoding="utf-8")
    assert judge(task, observe_diff(repo), (), ()).verdict is None
