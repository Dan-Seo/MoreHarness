"""docs/11 의 능력 eval — hidden grader, arm, escape/false_block, 반복과 보고.

M7 의 완료 기준: 동일한 hidden grader 로 `raw` vs `harness-full` 비교 리포트를
산출하고, `escape_rate` 와 `false_block_rate` 가 측정된다.
"""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from harness.cli import main
from harness.errors import HarnessError
from harness.config import load as load_config
from harness.eval import arms, fixtures, metrics, report
from harness.events import EventType
from harness.store import Store

PYTHON = re.escape(sys.executable)

CHECK = "import sys;sys.exit(0 if open('value.txt',encoding='utf-8').read().strip()=='42' else 1)"
MARKER = "import sys,os;sys.exit(0 if os.path.exists('marker.txt') else 1)"

AGENT_GOOD = "open('value.txt','w',encoding='utf-8').write('42')\n"
AGENT_MARKER = "open('marker.txt','w',encoding='utf-8').write('x')\n"


def write_fixture(
    root,
    name,
    *,
    agent,
    acceptance,
    hidden=CHECK,
    profile="worktree",
    task_profile=None,
    allowed=("value.txt",),
    policy_rules=None,
):
    case = Path(root) / name
    seed = case / "seed"
    (seed / ".harness").mkdir(parents=True)
    (seed / ".harness" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"adapter": "cli", "profile": profile, "max_parallel": 1},
                "adapters": {
                    "cli": {
                        "type": "generic_cli",
                        "command": [sys.executable, "{workspace}/agent.py"],
                        "prompt_delivery": "file",
                    }
                },
                "command_policy": {
                    "default": "require_approval",
                    "rules": list(policy_rules or []) + [{"match": PYTHON, "verdict": "allow"}],
                },
                "max_attempts": 1,
            }
        ),
        encoding="utf-8",
    )
    (seed / "agent.py").write_text(agent, encoding="utf-8")
    (seed / "value.txt").write_text("0", encoding="utf-8")
    spec_dir = seed / "specs" / "demo"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "demo",
                "intent": "value.txt 를 42 로 만든다",
                "requirements": [{"id": "R-001", "statement": "value.txt == 42"}],
            }
        ),
        encoding="utf-8",
    )
    tasks = case / "tasks"
    tasks.mkdir(parents=True)
    task_data = {
        "id": "T-001",
        "name": "t-001",
        "kind": "implementation",
        "satisfies": ["R-001"],
        "allowed_paths": list(allowed),
        "acceptance": acceptance,
    }
    if task_profile is not None:
        task_data["profile"] = task_profile
    (tasks / "T-001.task.yaml").write_text(yaml.safe_dump(task_data), encoding="utf-8")
    grader = case / "grader"
    grader.mkdir(parents=True)
    if hidden is not None:
        (grader / "hidden_ac.yaml").write_text(
            yaml.safe_dump({"acceptance": [{"cmd": [sys.executable, "-c", hidden]}]}),
            encoding="utf-8",
        )
    (case / "meta.yaml").write_text(yaml.safe_dump({"difficulty": "trivial"}), encoding="utf-8")
    return case


def good_fixture(root):
    """agent 가 목표를 진짜로 달성한다 — 보이는 AC 도, hidden AC 도 green."""
    case = write_fixture(
        root,
        "good",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
    )
    return fixtures.load(case)


def escape_fixture(root):
    """보이는 AC 는 marker 만 보고, hidden AC 는 값을 본다 — verified 인데 grader 실패."""
    case = write_fixture(
        root,
        "escape",
        agent=AGENT_MARKER,
        acceptance=[{"cmd": [sys.executable, "-c", MARKER], "expect_fail_before": True}],
        allowed=("marker.txt",),
    )
    return fixtures.load(case)


def false_block_fixture(root):
    """safe 프로파일 — 올바른 변경이 allowed_paths 밖이라 rejected 되지만 트리에 남는다."""
    case = write_fixture(
        root,
        "falseblock",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
        profile="safe",
        allowed=("allowed/**",),
    )
    return fixtures.load(case)


def events_of(result):
    store = Store(result.repo / ".harness" / "runs" / result.run_id)
    return store.journal.read()


# --------------------------------------------------------------------------- 발견과 grader


def test_discover_also_looks_under_a_fixtures_subdir(tmp_path):
    """docs/11 — `--fixtures evals/` 는 `evals/fixtures/<case>/` 를 찾는다."""
    write_fixture(
        tmp_path / "evals" / "fixtures",
        "good",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
    )
    assert [f.name for f in fixtures.discover(tmp_path / "evals")] == ["good"]


def test_the_hidden_grader_judges_the_tree(tmp_path):
    fixture = good_fixture(tmp_path)
    repo = fixtures.materialize(fixture, tmp_path / "repo")
    graded = arms.grade_in(fixture, repo, repo)
    assert not graded.success  # value.txt 는 아직 "0" 이다

    (repo / "value.txt").write_text("42", encoding="utf-8")
    graded = arms.grade_in(fixture, repo, repo)
    assert graded.success
    assert (graded.green, graded.total) == (1, 1)


def test_grader_commands_pass_command_policy(tmp_path):
    """docs/11 — 정책이 막으면 그 AC 는 red 이고 사유가 남는다. 실행되지 않는다."""
    case = write_fixture(
        tmp_path,
        "denied",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
        hidden="print('hidden-denied')",
        policy_rules=[{"match": "hidden-denied", "verdict": "deny"}],
    )
    fixture = fixtures.load(case)
    repo = fixtures.materialize(fixture, tmp_path / "repo")
    graded = arms.grade_in(fixture, repo, repo)
    assert not graded.success
    check = graded.checks[0]
    assert not check.green
    assert check.exit_code is None  # 실행 자체가 없었다
    assert check.detail


def test_the_grader_dir_never_reaches_the_working_repo(tmp_path):
    fixture = good_fixture(tmp_path)
    repo = fixtures.materialize(fixture, tmp_path / "repo")
    assert not (repo / "grader").exists()


# --------------------------------------------------------------------------- raw arm


def test_the_raw_arm_leaves_only_measurement_events(tmp_path):
    """docs/11 — 래퍼는 판정하지 않는다."""
    result = arms.run_capability(good_fixture(tmp_path), "raw", tmp_path / "work")
    types = {event.type for event in events_of(result)}
    assert EventType.AGENT_STARTED in types
    assert EventType.AGENT_FINISHED in types
    assert EventType.AC_POST_EXECUTED in types
    allowed = {
        EventType.RUN_STARTED,
        EventType.RUN_FINISHED,
        EventType.AGENT_STARTED,
        EventType.AGENT_FINISHED,
        EventType.AC_POST_EXECUTED,
        EventType.COMMAND_POLICY_DECISION,
    }
    assert types <= allowed
    assert result.harness is None  # raw 에는 하네스 판정이 없다
    assert result.grader.success


# --------------------------------------------------------------------------- 하네스 arm


def test_the_harness_full_arm_grades_the_integration_tip(tmp_path):
    result = arms.run_capability(good_fixture(tmp_path), "harness-full", tmp_path / "work")
    assert result.harness == "verified"
    assert result.grader.success
    # worktree 프로파일 — 사용자 브랜치는 그대로다. 채점은 integration tip 에서 했다.
    assert (result.repo / "value.txt").read_text(encoding="utf-8").strip() == "0"


def test_the_harness_lite_arm_is_kernel_only(tmp_path):
    """docs/11 — lite 는 컨텍스트 조립도 리뷰도 없다."""
    result = arms.run_capability(good_fixture(tmp_path), "harness-lite", tmp_path / "work")
    assert result.harness == "verified"
    assert result.grader.success
    task_dir = result.repo / ".harness" / "runs" / result.run_id / "tasks" / "T-001"
    assert not (task_dir / "context.manifest.json").exists()


# --------------------------------------------------------------------------- 보정 지표


def test_an_escape_is_measured(tmp_path):
    """M7 완료 기준 — 하네스는 verified 인데 grader 는 실패."""
    result = arms.run_capability(escape_fixture(tmp_path), "harness-full", tmp_path / "work")
    assert result.harness == "verified"
    assert not result.grader.success
    rates = metrics.rates([result])
    assert rates["escape_rate"] == 1.0
    assert rates["grader_success_rate"] == 0.0
    assert rates["false_block_rate"] is None  # 분모 없음


def test_a_false_block_is_measured(tmp_path):
    """M7 완료 기준 — 하네스는 rejected 인데 최종 트리는 grader 를 통과한다."""
    result = arms.run_capability(false_block_fixture(tmp_path), "harness-full", tmp_path / "work")
    assert result.harness == "rejected_or_blocked"
    assert result.grader.success
    rates = metrics.rates([result])
    assert rates["false_block_rate"] == 1.0
    assert rates["escape_rate"] is None


# --------------------------------------------------------------------------- 비교 리포트


def test_m7_the_same_grader_compares_raw_and_harness_full(tmp_path):
    """M7 완료 기준 — 동일 grader 로 raw vs harness-full 비교 리포트."""
    fixture = good_fixture(tmp_path)
    results = arms.run_matrix([fixture], ["raw", "harness-full"], repeat=1, workdir=tmp_path / "w")
    payload = report.build(results, repeat=1, workdir=tmp_path / "w")

    assert set(payload["arms"]) == {"raw", "harness-full"}
    for arm in payload["arms"].values():
        assert arm["grader_success_rate"] == 1.0
        assert arm["hidden_ac_pass_rate"] == 1.0

    json_path, md_path = report.write(tmp_path / "out", payload)
    assert json.loads(json_path.read_text(encoding="utf-8"))["repeat"] == 1
    text = md_path.read_text(encoding="utf-8")
    assert "raw" in text and "harness-full" in text


def test_repeat_runs_each_cell_that_many_times(tmp_path):
    fixture = good_fixture(tmp_path)
    results = arms.run_matrix([fixture], ["raw"], repeat=2, workdir=tmp_path / "w")
    assert len(results) == 2
    assert len({r.repo for r in results}) == 2  # 실행마다 새 저장소


def test_a_low_repeat_gets_a_warning(tmp_path):
    """docs/11 — repeat 3 미만이면 리포트가 경고를 표시한다."""
    fixture = good_fixture(tmp_path)
    results = arms.run_matrix([fixture], ["raw"], repeat=1, workdir=tmp_path / "w")
    payload = report.build(results, repeat=1, workdir=tmp_path / "w")
    _, md_path = report.write(tmp_path / "out", payload)
    assert "경고" in md_path.read_text(encoding="utf-8")


def test_continuous_metrics_report_median_and_range(tmp_path):
    """docs/11 — 연속 지표는 중앙값과 [min, max]. 전부 null 이면 n/a."""
    fixture = good_fixture(tmp_path)
    results = arms.run_matrix([fixture], ["raw"], repeat=1, workdir=tmp_path / "w")
    payload = report.build(results, repeat=1, workdir=tmp_path / "w")
    arm = payload["arms"]["raw"]
    agent_time = arm["metrics"]["agent_time_s"]
    assert set(agent_time) == {"median", "min", "max"}
    assert arm["metrics"]["cost_usd"] is None  # generic_cli 는 usage 를 보고하지 않는다


def test_median_range_skips_nulls():
    assert metrics.median_range([3.0, None, 1.0, 2.0]) == {"median": 2.0, "min": 1.0, "max": 3.0}
    assert metrics.median_range([None, None]) is None


# --------------------------------------------------------------------------- CLI 와 경계


def test_eval_cli_writes_the_report(tmp_path):
    write_fixture(
        tmp_path / "evals" / "fixtures",
        "good",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
    )
    out = tmp_path / "out"
    code = main(
        [
            "eval",
            "run",
            "--fixtures",
            str(tmp_path / "evals"),
            "--arms",
            "raw",
            "--repeat",
            "1",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "eval.json").is_file()
    assert (out / "eval-report.md").is_file()


def test_eval_does_not_import_cli():
    """docs/02 — eval 은 cli 를 import 하지 않는다."""
    for module in ("fixtures", "arms", "metrics", "report"):
        source = (Path("harness") / "eval" / f"{module}.py").read_text(encoding="utf-8")
        assert "harness.cli" not in source, module


# --------------------------------------------------------------------------- 리뷰 수정 회귀


def test_the_graded_tree_follows_the_integration_branch_not_the_default_profile(tmp_path):
    """docs/11 — 기준은 integration 브랜치의 존재다. task 별 오버라이드가 섞여도 옳다."""
    case = write_fixture(
        tmp_path,
        "mixed",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
        profile="safe",
        task_profile="worktree",
    )
    fixture = fixtures.load(case)

    result = arms.run_capability(fixture, "harness-full", tmp_path / "work")

    assert result.harness == "verified"
    assert result.grader.success  # integration tip 을 채점했다
    assert (result.repo / "value.txt").read_text(encoding="utf-8") == "0"  # 저장소 트리는 그대로


def test_a_fixture_without_a_grader_is_refused(tmp_path):
    """docs/11 — 채점 기준 없는 능력 eval 은 공허하게 성공할 뿐이므로 거부한다."""
    case = write_fixture(
        tmp_path,
        "nograder",
        agent=AGENT_GOOD,
        acceptance=[{"cmd": [sys.executable, "-c", CHECK], "expect_fail_before": True}],
        hidden=None,
    )
    fixture = fixtures.load(case)

    with pytest.raises(HarnessError, match="hidden_ac"):
        arms.run_matrix([fixture], ["raw"], 1, tmp_path / "work")


def test_unknown_arms_are_refused_before_any_run(tmp_path):
    """arm 이름 오타가 매트릭스 도중이 아니라 시작 전에 걸린다."""
    fixture = good_fixture(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()

    with pytest.raises(HarnessError, match="ablation"):
        arms.run_matrix([fixture], ["raw", "ablation:nope"], 1, workdir)
    assert list(workdir.iterdir()) == []  # 아무 run 도 시작되지 않았다


def test_human_interventions_counts_transitions_not_the_final_state(tmp_path):
    """docs/11 — human_required 로 **간 횟수**다. journal 의 projection 이다."""
    run_dir = tmp_path / "repo" / ".harness" / "runs" / "run-x"
    store = Store(run_dir)
    store.append(
        EventType.RUN_STARTED,
        {"manifest": {}, "profile": "safe", "adapter": "mock", "max_parallel": 1},
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "blocked", "attempt": 1, "reason": "prerequisite", "next_state": "human_required"},
        task_id="T-001",
        attempt=1,
    )
    store.append(
        EventType.RUN_FINISHED, {"summary": {}, "open_debts": [], "human_required": ["T-001"]}
    )

    measured = metrics.of_run(tmp_path / "repo", "run-x")
    assert measured.human_interventions == 1
