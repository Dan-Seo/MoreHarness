"""TDD 강제 모드 — red→green 을 하네스가 관측한다 (docs/06).

M9 완료 기준을 여기서 증명한다 (docs/12). 증명해야 하는 것은 하나다 —
**agent 가 "TDD 로 했다"고 말하는 것은 증거가 아니고, 단계 사이의 관측만이 증거다.**
"""

import json
import subprocess
import sys

import pytest
from test_runner import commit, configure, task_of, write_task

from harness.adapters.base import (
    AgentResult,
    Capabilities,
    PreflightKind,
    PreflightReport,
    RuntimeFailure,
)
from harness.config import load
from harness.dag import Dag, load_tasks
from harness.errors import TaskDefinitionError
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.exec.tdd import IMPLEMENTATION, RED_GATE, TEST_AUTHOR, TddStage
from harness.exec.workspace import Workspaces
from harness.models import DevelopmentMode, State, Verdict

PY = sys.executable

TDD = {
    "mode": "tdd",
    "test_paths": ["tests/**"],
    "implementation_paths": ["src/**"],
}


# --------------------------------------------------------------------------- 도구


class PhasedAdapter:
    """디스패치 순서대로 미리 정해 둔 단계를 수행하는 어댑터.

    TDD 는 한 attempt 에 두 번 디스패치되므로 "1단계는 테스트, 2단계는 구현" 을
    표현할 수 있어야 한다. `files` 의 값이 `None` 이면 삭제이고, `commit: True` 면
    agent 가 스스로 커밋해 diff 를 감추는 상황을 만든다.
    """

    def __init__(self, steps):
        self.name = "phased"
        self.steps = list(steps)
        self.requests = []

    def capabilities(self):
        return Capabilities(False, False, False)

    def preflight(self):
        return PreflightReport(True, PreflightKind.OK, "ok")

    @property
    def outboxes(self):
        return [request.outbox.name for request in self.requests]

    def dispatches(self, suffix):
        return [name for name in self.outboxes if name.endswith(suffix)]

    def execute(self, request):
        self.requests.append(request)
        step = self.steps[min(len(self.requests) - 1, len(self.steps) - 1)]

        if step.get("crash"):
            raise KeyboardInterrupt("프로세스가 죽었다")

        for relative, content in (step.get("files") or {}).items():
            target = request.workspace / relative
            if content is None:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        if step.get("commit"):
            _git(request.workspace, "add", "-A")
            _git(request.workspace, "commit", "-q", "-m", "agent 가 감춘다")

        request.outbox.mkdir(parents=True, exist_ok=True)
        paths = {"claim": None, "handoff": None}
        for key, filename in (("claim", "result.json"), ("handoff", "handoff.json")):
            content = step.get(key)
            if content is None:
                continue
            path = request.outbox / filename
            path.write_text(_json(content), encoding="utf-8")
            paths[key] = path
        if step.get("findings") is not None:
            (request.outbox / "findings.json").write_text(
                _json(step["findings"]), encoding="utf-8"
            )

        failure = step.get("runtime_failure")
        return AgentResult(
            exit_code=int(step.get("exit_code", 0)),
            stdout="",
            stderr="",
            raw_claim_path=paths["claim"],
            raw_handoff_path=paths["handoff"],
            duration_s=0.0,
            usage=None,
            transcript_path=None,
            runtime_failure=RuntimeFailure(failure) if failure else None,
        )


class CrashAfterTestAuthor(TddStage):
    """test-author 완료 이벤트가 durable 해진 직후 프로세스가 죽는 재개 fixture."""

    def _record(self, runner, task, attempt, phase, base, stop):
        super()._record(runner, task, attempt, phase, base, stop)
        if phase == TEST_AUTHOR and stop is None:
            raise KeyboardInterrupt("red gate 직전에 죽었다")


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
    )


def proves(path="src/impl.py"):
    """red→green 증명용 AC — 구현 파일이 생겨야 통과한다."""
    code = f"import pathlib, sys; sys.exit(0 if pathlib.Path({path!r}).exists() else 1)"
    return {"cmd": [PY, "-c", code], "expect_fail_before": True}


def absent(path):
    """회귀 감시용 AC — 그 파일이 없어야 통과한다. baseline 에서 green 이다."""
    code = f"import pathlib, sys; sys.exit(0 if not pathlib.Path({path!r}).exists() else 1)"
    return {"cmd": [PY, "-c", code]}


def present(path):
    """기존 debt 용 AC — 파일이 있어야 통과하지만 red→green 증명 대상은 아니다."""
    code = f"import pathlib, sys; sys.exit(0 if pathlib.Path({path!r}).exists() else 1)"
    return {"cmd": [PY, "-c", code]}


def tdd_task(repo, acceptance=None, development=None, **fields):
    write_task(
        repo,
        "T-001",
        allowed_paths=["src/**", "tests/**"],
        acceptance=acceptance if acceptance is not None else [proves()],
        development=TDD if development is None else development,
        **fields,
    )


def setup(repo, attempts=1, **extra):
    """TDD 는 worktree 가 필요하다. `attempts` 기본이 1 인 것은 대부분의 테스트가 한
    attempt 의 결과를 단언하기 때문이다 — 재시도하면 시나리오 스크립트가 어긋난다."""
    return configure(repo, profile="worktree", max_attempts=attempts, **extra)


def go(repo, adapter, config=None, resume=False, review_stage=None, tdd_stage=None):
    if not resume:
        commit(repo)
    config = config or load(repo)
    return run_dag(
        repo,
        config,
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        resume=resume,
        scratch=repo.parent / "scratch",
        tdd_stage=tdd_stage or TddStage(repo, config),
        review_stage=review_stage,
    )


def phases(store, ok=None):
    return [
        event.payload["phase"]
        for event in store.journal.read()
        if event.type is EventType.TDD_PHASE_COMPLETED
        and (ok is None or event.payload["ok"] is ok)
    ]


def payloads(store, event_type):
    return [e.payload for e in store.journal.read() if e.type is event_type]


TESTS_ONLY = {"files": {"tests/test_impl.py": "def test_x():\n    assert True\n"}}
IMPL_ONLY = {"files": {"src/impl.py": "VALUE = 1\n"}}


# --------------------------------------------------------------------------- 정상 경로


def test_a_tdd_task_runs_test_author_then_red_gate_then_implementation(repo):
    """M9 — 한 attempt 가 두 번 디스패치되고, 그 사이에 red gate 가 있다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    store = go(repo, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert phases(store) == [TEST_AUTHOR, RED_GATE, IMPLEMENTATION]
    assert adapter.dispatches("-test-author") == ["attempt-1-test-author"]
    assert adapter.outboxes == ["attempt-1-test-author", "attempt-1"]


def test_the_red_evidence_is_the_baseline_of_the_expect_fail_ac(repo):
    """docs/06 — expect_fail_before AC 의 before 는 테스트가 존재하는 시점에 잰다.

    red gate 를 통과했다는 말은 그 AC 가 `red_before` 로 관측됐다는 뜻이고, 그 관측이
    곧 차등 판정의 before 다. green gate 는 그 짝인 `ac_post_executed` 다.
    """
    setup(repo)
    tdd_task(repo)

    store = go(repo, PhasedAdapter([TESTS_ONLY, IMPL_ONLY]))

    baselines = payloads(store, EventType.AC_BASELINE_EXECUTED)
    assert [b["classification"] for b in baselines] == ["red_before"]
    assert baselines[0]["expect_fail_before"] is True
    assert [p["differential"] for p in payloads(store, EventType.AC_POST_EXECUTED)] == [
        "proven"
    ]


def test_the_phase_prompts_name_the_scope_of_each_phase(repo):
    """프롬프트는 강제 수단이 아니라 안내다 — 강제는 diff 가 한다. 안내는 있어야 한다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    go(repo, adapter)

    author, implementation = adapter.requests
    assert "tests/**" in author.prompt and "src/**" in author.prompt
    assert "tests/**" in implementation.prompt and "src/**" in implementation.prompt
    assert author.prompt != implementation.prompt


def test_the_full_attempt_diff_still_covers_both_phases(repo):
    """TDD 는 증거를 더할 뿐 기존 판정을 대체하지 않는다 (docs/06)."""
    setup(repo)
    tdd_task(repo)

    store = go(repo, PhasedAdapter([TESTS_ONLY, IMPL_ONLY]))

    record = json.loads(
        (store.run_dir / "tasks" / "T-001" / "verification.json").read_text(
            encoding="utf-8"
        )
    )
    changed = record["output"]["harness"]
    assert sorted(changed["changed_files"] + changed["created_files"]) == [
        "src/impl.py",
        "tests/test_impl.py",
    ]


# --------------------------------------------------------------------------- test-author 단계


def test_a_test_author_that_touches_implementation_paths_is_rejected(repo):
    """M9 — 프롬프트 지시가 아니라 하네스의 diff 가 막는다. 구현은 디스패치되지 않는다."""
    setup(repo, attempts=2)
    tdd_task(repo)
    adapter = PhasedAdapter([{"files": {**TESTS_ONLY["files"], **IMPL_ONLY["files"]}}])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "path_violation"
    assert adapter.outboxes == ["attempt-1-test-author", "attempt-2-test-author"]
    assert phases(store, ok=False) == [TEST_AUTHOR, TEST_AUTHOR]
    assert ["src/impl.py"] in [
        p["paths"] for p in payloads(store, EventType.PATH_VIOLATION)
    ]


def test_a_test_author_that_writes_nothing_is_rejected(repo):
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([{}])

    store = go(repo, adapter)

    assert task_of(store, "T-001").reason == "tdd_no_tests"
    assert adapter.dispatches("attempt-1") == []  # 구현 단계로 가지 않았다


def test_a_test_author_that_leaves_the_declared_scope_is_rejected(repo):
    """스코프 밖 변경은 기존 path violation 규칙 그대로다 (docs/05)."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([{"files": {"docs/note.md": "밖\n"}}])

    store = go(repo, adapter)

    assert task_of(store, "T-001").reason == "path_violation"


def test_a_failed_red_checkpoint_stops_before_the_red_gate(repo, monkeypatch):
    """커밋되지 않은 테스트를 red 기준이라고 기록하면 implementation 스코프가 무너진다."""
    setup(repo)
    tdd_task(repo)
    monkeypatch.setattr(Workspaces, "checkpoint", lambda self, workspace, message: None)

    store = go(repo, PhasedAdapter([TESTS_ONLY, IMPL_ONLY]))

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.ERROR
    assert task.reason == "tdd_checkpoint_failed"
    assert phases(store) == [TEST_AUTHOR]
    assert phases(store, ok=False) == [TEST_AUTHOR]


# --------------------------------------------------------------------------- red gate


def test_a_red_gate_that_is_already_green_does_not_dispatch_the_implementation(repo):
    """M9 — 이미 통과하는 테스트는 아무것도 증명하지 못한다. 기존 개념을 그대로 쓴다."""
    setup(repo)
    tdd_task(
        repo,
        acceptance=[
            {"cmd": [PY, "-c", "raise SystemExit(0)"], "expect_fail_before": True}
        ],
    )
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is None
    assert task.reason == "ac_not_discriminating"
    assert task.state is State.NEEDS_REPLAN
    assert adapter.outboxes == ["attempt-1-test-author"]
    assert phases(store, ok=False) == [RED_GATE]


def test_a_tdd_task_without_an_expect_fail_ac_cannot_prove_anything(repo):
    setup(repo)
    tdd_task(repo, acceptance=[{"cmd": [PY, "-c", "raise SystemExit(0)"]}])
    adapter = PhasedAdapter([TESTS_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.reason == "ac_not_discriminating"
    assert task.state is State.NEEDS_REPLAN
    assert adapter.outboxes == []  # agent 를 부를 이유가 없다


def test_a_denied_red_gate_command_is_never_executed(repo):
    """docs/06 — deny 가 런타임에 도달하면 task 정의 결함이다."""
    setup(
        repo,
        command_policy={
            "default": "allow",
            "rules": [{"match": "danger", "verdict": "deny"}],
        },
    )
    tdd_task(
        repo, acceptance=[{"cmd": [PY, "-c", "danger"], "expect_fail_before": True}]
    )
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.reason == "policy_denied_at_runtime"
    assert task.state is State.NEEDS_REPLAN
    assert adapter.outboxes == ["attempt-1-test-author"]


def test_an_unapproved_red_gate_command_blocks_instead_of_counting_as_red(repo):
    """docs/06 — 무인 실행에서 미승인이면 blocked 다. 실행되지 않은 것을 red 로 세지 않는다."""
    setup(repo, command_policy={"default": "require_approval", "rules": []})
    tdd_task(repo, acceptance=[{"cmd": ["needs-approval"], "expect_fail_before": True}])
    adapter = PhasedAdapter([TESTS_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.BLOCKED
    assert task.reason.startswith("unapproved_command")
    assert task.state is State.HUMAN_REQUIRED
    assert adapter.dispatches("attempt-1") == []


def test_a_red_gate_command_that_cannot_run_is_not_red_evidence(repo):
    """docs/06 — 실행되지 않은 커맨드는 red 증거가 아니다."""
    setup(repo, ac_timeout_s=1)
    tdd_task(
        repo,
        acceptance=[
            {
                "cmd": [PY, "-c", "import time; time.sleep(30)"],
                "expect_fail_before": True,
            }
        ],
    )
    adapter = PhasedAdapter([TESTS_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.ERROR
    assert task.reason.startswith("tdd_red_gate_unexecuted")
    assert task.state is State.HUMAN_REQUIRED
    assert adapter.dispatches("attempt-1") == []  # 구현 단계로 가지 않았다


# --------------------------------------------------------------------------- implementation 단계


def test_modifying_a_test_during_implementation_is_detected(repo):
    """M9 — agent 가 테스트를 고쳐 green 을 만드는 경로를 막는다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter(
        [
            TESTS_ONLY,
            {
                "files": {
                    **IMPL_ONLY["files"],
                    "tests/test_impl.py": "def test_x():\n    pass\n",
                }
            },
        ]
    )

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "path_violation"
    violations = [p for p in payloads(store, EventType.PATH_VIOLATION)]
    assert any(v["paths"] == ["tests/test_impl.py"] for v in violations)


def test_deleting_a_test_during_implementation_is_detected(repo):
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter(
        [TESTS_ONLY, {"files": {**IMPL_ONLY["files"], "tests/test_impl.py": None}}]
    )

    store = go(repo, adapter)

    assert task_of(store, "T-001").reason == "path_violation"
    assert any(
        v["paths"] == ["tests/test_impl.py"]
        for v in payloads(store, EventType.PATH_VIOLATION)
    )


def test_committing_the_tampered_test_does_not_hide_it(repo):
    """docs/06 — 관측 기준이 red gate 커밋이므로 agent 의 커밋은 diff 를 감추지 못한다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter(
        [
            TESTS_ONLY,
            {
                "files": {
                    **IMPL_ONLY["files"],
                    "tests/test_impl.py": "# 지워진 검증\n",
                },
                "commit": True,
            },
        ]
    )

    store = go(repo, adapter)

    assert task_of(store, "T-001").reason == "path_violation"


def test_a_test_committed_by_the_test_author_is_still_the_red_gate_base(repo):
    """1단계에서 agent 가 커밋해도 관측 기준은 그 뒤다 — 2단계 diff 에 테스트가 없다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([{**TESTS_ONLY, "commit": True}, IMPL_ONLY])

    store = go(repo, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


def test_implementation_that_leaves_the_tests_red_is_rejected(repo):
    """green gate 는 기존 차등 판정 그대로다 (docs/06)."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, {"files": {"src/other.py": "x = 1\n"}}])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "unmet"


def test_implementation_runtime_failure_is_not_recorded_as_success(repo):
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, {"runtime_failure": "timeout"}])

    store = go(repo, adapter)

    implementation = [
        payload
        for payload in payloads(store, EventType.TDD_PHASE_COMPLETED)
        if payload["phase"] == IMPLEMENTATION
    ]
    assert len(implementation) == 1
    assert implementation[0]["ok"] is False
    assert implementation[0]["detail"] == "timeout"
    assert task_of(store, "T-001").verdict is Verdict.ERROR


def test_implementation_completion_is_recorded_after_the_agent_finishes(repo):
    setup(repo)
    tdd_task(repo)

    store = go(repo, PhasedAdapter([TESTS_ONLY, IMPL_ONLY]))

    events = store.journal.read()
    completed = next(
        event
        for event in events
        if event.type is EventType.TDD_PHASE_COMPLETED
        and event.payload["phase"] == IMPLEMENTATION
    )
    last_agent_finished = max(
        event.seq for event in events if event.type is EventType.AGENT_FINISHED
    )
    assert last_agent_finished < completed.seq


def test_tdd_checks_the_budget_again_before_implementation(repo):
    setup(repo, budget={"max_agent_calls": 1})
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.BUDGET_EXHAUSTED
    assert task.reason == "agent_calls 1 >= 1"
    assert adapter.outboxes == ["attempt-1-test-author"]
    assert phases(store) == [TEST_AUTHOR, RED_GATE]


def test_a_green_tdd_test_does_not_excuse_a_regression(repo):
    """최종 verified 조건은 기존보다 약해지지 않는다 (docs/06)."""
    setup(repo)
    tdd_task(repo, acceptance=[proves(), absent("src/regressed.py")])
    adapter = PhasedAdapter(
        [TESTS_ONLY, {"files": {**IMPL_ONLY["files"], "src/regressed.py": "x\n"}}]
    )

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "regression"


def test_a_claim_of_tdd_complete_without_evidence_does_not_pass(repo):
    """agent 의 보고는 증거가 아니다 (docs/06)."""
    setup(repo)
    tdd_task(repo)
    claim = {
        "schema": "harness.claim/v1",
        "task_id": "T-001",
        "outcome_claim": "implemented",
    }
    adapter = PhasedAdapter(
        [TESTS_ONLY, {"files": {"src/other.py": "x = 1\n"}, "claim": claim}]
    )

    store = go(repo, adapter)

    assert task_of(store, "T-001").verdict is Verdict.REJECTED


def test_a_fixer_that_touches_the_tests_is_rejected(repo):
    """리뷰가 만든 변경도 같은 단계 스코프를 통과해야 한다 (docs/06)."""
    from harness.exec.review import ReviewStage

    config = setup(repo, max_review_waves=2)
    tdd_task(repo, risk="low")
    finding = {
        "schema": "harness.findings/v1",
        "task_id": "T-001",
        "findings": [
            {
                "severity": "high",
                "rule": "x",
                "file": "src/impl.py",
                "line": 1,
                "message": "고쳐라",
            }
        ],
    }
    clean = {"schema": "harness.findings/v1", "task_id": "T-001", "findings": []}
    adapter = PhasedAdapter(
        [
            TESTS_ONLY,
            IMPL_ONLY,
            {"findings": finding},  # wave 1 리뷰어
            {"files": {"tests/test_impl.py": "# fixer 가 지웠다\n"}},  # fixer
            {"findings": clean},  # wave 2 리뷰어
        ]
    )

    store = go(repo, adapter, config=config, review_stage=ReviewStage(repo, config))

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "path_violation"


# --------------------------------------------------------------------------- 크래시와 재개


def test_resume_after_the_red_gate_does_not_rerun_the_test_author(repo):
    """M9 — red 증거는 journal 에 있고 테스트는 커밋되어 있다. 다시 만들 이유가 없다."""
    setup(repo)
    tdd_task(repo)
    dying = PhasedAdapter([TESTS_ONLY, {"crash": True}])

    with pytest.raises(KeyboardInterrupt):
        go(repo, dying)

    assert dying.dispatches("-test-author") == ["attempt-1-test-author"]

    survivor = PhasedAdapter([IMPL_ONLY])
    store = go(repo, survivor, resume=True)

    assert survivor.dispatches("-test-author") == []
    assert survivor.outboxes == ["attempt-1"]
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert phases(store).count(TEST_AUTHOR) == 1  # 크래시 전 한 번뿐이다


def test_resume_restores_split_baselines_in_acceptance_order(repo):
    """TDD 는 non-red baseline 을 먼저 기록해도 post 는 task 선언 순서와 짝지어야 한다."""
    setup(repo)
    tdd_task(repo, acceptance=[proves("src/required.py"), present("src/debt.py")])

    with pytest.raises(KeyboardInterrupt):
        go(repo, PhasedAdapter([TESTS_ONLY, {"crash": True}]))

    store = go(
        repo,
        PhasedAdapter([{"files": {"src/debt.py": "DEBT_FIXED = True\n"}}]),
        resume=True,
    )

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "unmet"
    assert [p["differential"] for p in payloads(store, EventType.AC_POST_EXECUTED)] == [
        "unmet",
        "debt_closed",
    ]


def test_a_crash_before_the_red_gate_restarts_the_attempt(repo):
    """docs/10 — red gate 를 통과하지 못한 attempt 는 예외가 아니다. 처음부터 다시 한다."""
    setup(repo)
    tdd_task(repo)
    dying = PhasedAdapter([{"crash": True}])

    with pytest.raises(KeyboardInterrupt):
        go(repo, dying)

    survivor = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])
    store = go(repo, survivor, resume=True)

    assert survivor.dispatches("-test-author") == ["attempt-1-test-author"]
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


def test_a_crash_after_test_author_completion_still_restarts_cleanly(repo):
    """첫 agent_finished 의 executed state 는 TDD 전체 구현 완료를 뜻하지 않는다."""
    config = setup(repo)
    tdd_task(repo)

    with pytest.raises(KeyboardInterrupt):
        go(
            repo,
            PhasedAdapter([TESTS_ONLY]),
            config=config,
            tdd_stage=CrashAfterTestAuthor(repo, config),
        )

    survivor = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])
    store = go(repo, survivor, resume=True)

    assert survivor.dispatches("-test-author") == ["attempt-1-test-author"]
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


def test_resuming_twice_is_idempotent(repo):
    """docs/10 — 같은 run-id 로 몇 번을 재개해도 결과가 같다."""
    setup(repo)
    tdd_task(repo)

    store = go(repo, PhasedAdapter([TESTS_ONLY, IMPL_ONLY]))
    assert task_of(store, "T-001").state is State.DONE

    again = go(repo, PhasedAdapter([{"crash": True}]), resume=True)
    assert task_of(again, "T-001").state is State.DONE


# --------------------------------------------------------------------------- 프로파일과 fail-closed


def test_tdd_requires_a_profile_with_worktree_separation(repo):
    """단계 경계를 커밋으로 만들 수 없으면 red 증거를 고정할 수 없다 (docs/06)."""
    configure(repo, profile="safe", max_attempts=1)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.ERROR
    assert task.reason == "tdd_requires_worktree"
    assert adapter.outboxes == []


def test_a_tdd_task_fails_closed_when_the_stage_is_not_installed(repo):
    """docs/02 — 옵션이 없으면 검증할 수 없다. 조용히 표준 모드로 돌지 않는다."""
    setup(repo)
    tdd_task(repo)
    adapter = PhasedAdapter([TESTS_ONLY, IMPL_ONLY])
    commit(repo)

    store = run_dag(
        repo,
        load(repo),
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        scratch=repo.parent / "scratch",
    )

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.ERROR
    assert task.reason == "tdd_mode_unavailable"
    assert adapter.outboxes == []


# --------------------------------------------------------------------------- 계약과 하위 호환


def test_a_task_without_development_is_standard_mode(repo):
    write_task(repo, "T-001", allowed_paths=["src/**"])

    task = load_tasks(repo)["T-001"]
    assert task.development.mode is DevelopmentMode.STANDARD
    assert task.development.test_paths == ()


def test_a_standard_task_is_dispatched_once_and_leaves_no_tdd_evidence(repo):
    """하위 호환 — `development` 를 선언하지 않으면 동작이 그대로다."""
    setup(repo)
    write_task(repo, "T-001", allowed_paths=["src/**"], acceptance=[proves()])
    adapter = PhasedAdapter([IMPL_ONLY])

    store = go(repo, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert adapter.outboxes == ["attempt-1"]
    assert phases(store) == []


def test_tdd_mode_requires_both_path_lists(repo):
    write_task(repo, "T-001", development={"mode": "tdd", "test_paths": ["tests/**"]})

    with pytest.raises(TaskDefinitionError):
        load_tasks(repo)


def test_an_unknown_development_mode_is_a_task_definition_error(repo):
    write_task(repo, "T-001", development={"mode": "vibes"})

    with pytest.raises(TaskDefinitionError):
        load_tasks(repo)


# --------------------------------------------------------------------------- end-to-end


def _python_project(repo):
    """실제 파이썬 저장소의 최소 형태. `.gitignore` 가 있어야 하네스가 직접 돌린 pytest 의
    캐시가 task 의 변경으로 세어지지 않는다 (docs/05 의 경로 스코프 판정)."""
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")


REAL_TEST = """\
from src.slugify import slugify


def test_lowercases_and_hyphenates():
    assert slugify("Hello World") == "hello-world"


def test_strips_punctuation():
    assert slugify("Hello, World!") == "hello-world"
"""

REAL_IMPL = """\
import re


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
"""

WEAK_IMPL = """\
def slugify(text):
    return text.lower().replace(" ", "-")
"""


def test_a_real_pytest_red_to_green_cycle_is_verified_end_to_end(repo):
    """실제 fixture 하나로 red→green 을 통째로 증명한다.

    하네스가 직접 pytest 를 돌린다 — 테스트가 없을 때 red, 구현 뒤 green 이라는 관측
    두 개가 verdict 의 전부이며 agent 의 보고는 들어오지 않는다.
    """
    setup(repo)
    _python_project(repo)
    tdd_task(
        repo,
        acceptance=[
            {"cmd": [PY, "-m", "pytest", "-q", "tests"], "expect_fail_before": True}
        ],
    )
    adapter = PhasedAdapter(
        [
            {"files": {"tests/test_slugify.py": REAL_TEST}},
            {"files": {"src/slugify.py": REAL_IMPL}},
        ]
    )

    store = go(repo, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED, task_of(
        store, "T-001"
    ).reason
    assert phases(store) == [TEST_AUTHOR, RED_GATE, IMPLEMENTATION]
    assert [
        p["classification"] for p in payloads(store, EventType.AC_BASELINE_EXECUTED)
    ] == ["red_before"]
    assert [p["differential"] for p in payloads(store, EventType.AC_POST_EXECUTED)] == [
        "proven"
    ]


def test_a_real_pytest_cycle_rejects_an_implementation_that_does_not_pass(repo):
    """같은 fixture 로 실패도 증명한다 — 하네스가 pytest 결과를 보고 판정한다."""
    setup(repo)
    _python_project(repo)
    tdd_task(
        repo,
        acceptance=[
            {"cmd": [PY, "-m", "pytest", "-q", "tests"], "expect_fail_before": True}
        ],
    )
    adapter = PhasedAdapter(
        [
            {"files": {"tests/test_slugify.py": REAL_TEST}},
            {"files": {"src/slugify.py": WEAK_IMPL}},
        ]
    )

    store = go(repo, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "unmet"
