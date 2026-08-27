"""sequential runner — 한 task 의 attempt 실행과 DAG 완주.

M1 완료 기준 다섯 가지를 여기서 증명한다 (docs/12).
"""

import re
import sys

import yaml
from conftest import git

from harness.adapters.base import (
    AgentResult,
    Capabilities,
    PreflightKind,
    PreflightReport,
    RuntimeFailure,
)
from harness.config import load
from harness.dag import Dag, load_tasks
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.models import State, Verdict

PYTHON = re.escape(sys.executable)

CLAIM = {"schema": "harness.claim/v1", "task_id": "T-001", "outcome_claim": "implemented"}
HANDOFF = {"schema": "harness.handoff/v1", "task_id": "T-001", "public_api": ["POST /users"]}


# --------------------------------------------------------------------------- 도구


class ScriptedAdapter:
    """호출 순서대로 미리 정해 둔 결과를 돌려주는 테스트용 어댑터.

    mock 은 task 단위로 결정론적이라 "첫 호출은 handoff 없음, 두 번째는 있음" 같은
    시나리오를 표현할 수 없다. 그 경로를 시험하기 위한 것이다.
    """

    def __init__(self, steps, preflight=None):
        self.name = "scripted"
        self.steps = list(steps)
        self.requests = []
        self._preflight = preflight or PreflightReport(True, PreflightKind.OK, "ok")

    def capabilities(self):
        return Capabilities(False, False, False)

    def preflight(self):
        return self._preflight

    def execute(self, request):
        self.requests.append(request)
        step = self.steps[min(len(self.requests) - 1, len(self.steps) - 1)]

        for relative, content in (step.get("files") or {}).items():
            target = request.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        request.outbox.mkdir(parents=True, exist_ok=True)
        paths = {}
        for key, filename in (("claim", "result.json"), ("handoff", "handoff.json")):
            content = step.get(key)
            if content is None:
                paths[key] = None
                continue
            path = request.outbox / filename
            text = content if isinstance(content, str) else yaml.dump(content, default_flow_style=True)
            path.write_text(_json(content) if not isinstance(content, str) else text, encoding="utf-8")
            paths[key] = path

        failure = step.get("runtime_failure")
        return AgentResult(
            exit_code=int(step.get("exit_code", 0)),
            stdout="",
            stderr=step.get("stderr", ""),
            raw_claim_path=paths["claim"],
            raw_handoff_path=paths["handoff"],
            duration_s=0.0,
            usage=None,
            transcript_path=None,
            runtime_failure=RuntimeFailure(failure) if failure else None,
        )


def _json(value):
    import json

    return json.dumps(value, ensure_ascii=False)


def configure(repo, scenario=None, **extra):
    """docs/04 — mock 은 시나리오 **파일**로 결과를 지정한다."""
    adapter = {"type": "mock"}
    if scenario is not None:
        path = repo / "mock-scenario.yaml"
        path.write_text(yaml.safe_dump(scenario), encoding="utf-8")
        adapter["scenario"] = str(path)

    data = {
        "version": 1,
        "defaults": {"adapter": "mock", "profile": "safe", "max_parallel": 1},
        "adapters": {"mock": adapter},
        "command_policy": {
            "default": "require_approval",
            "rules": [{"match": PYTHON, "verdict": "allow"}],
        },
        **extra,
    }
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load(repo)


def ac(code, expect_fail_before=False):
    entry = {"cmd": [sys.executable, "-c", code]}
    if expect_fail_before:
        entry["expect_fail_before"] = True
    return entry


def exits(status, expect_fail_before=False):
    return ac(f"raise SystemExit({status})", expect_fail_before)


def write_task(repo, task_id, **fields):
    data = {"id": task_id, "name": task_id.lower(), "kind": "implementation", **fields}
    directory = repo / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{task_id}.task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed tasks")


def go(repo, config=None, adapter=None):
    commit(repo)
    config = config or load(repo)
    return run_dag(repo, config, Dag(load_tasks(repo)), adapter=adapter)


def task_of(store, task_id):
    return store.state.tasks[task_id]


def types_for(store, task_id=None):
    return [
        e.type
        for e in store.journal.read()
        if task_id is None or e.task_id == task_id
    ]


# --------------------------------------------------------------------------- 완주


def test_a_three_task_dag_runs_to_completion(repo):
    """M1 완료 기준 — 3-task DAG 를 완주한다."""
    config = configure(
        repo,
        scenario={
            "tasks": {
                "T-001": {"files": {"a.py": "1\n"}},
                "T-002": {"files": {"b.py": "2\n"}},
                "T-003": {"files": {"c.py": "3\n"}},
            }
        },
    )
    write_task(repo, "T-001")
    write_task(repo, "T-002", depends_on=["T-001"])
    write_task(repo, "T-003", depends_on=["T-002"])

    store = go(repo, config)

    assert [task_of(store, t).verdict for t in ("T-001", "T-002", "T-003")] == [Verdict.VERIFIED] * 3
    assert [task_of(store, t).state for t in ("T-001", "T-002", "T-003")] == [State.DONE] * 3


def test_the_run_is_bracketed_by_started_and_finished(repo):
    configure(repo)
    write_task(repo, "T-001", kind="analysis")
    store = go(repo)
    kinds = types_for(store)
    assert kinds[0] is EventType.RUN_STARTED
    assert kinds[-1] is EventType.RUN_FINISHED


def test_the_final_state_reconstructs_from_the_journal(repo):
    """docs/03 — state == fold(journal) 은 불변식이다."""
    configure(repo)
    write_task(repo, "T-001", kind="analysis")
    store = go(repo)
    assert store.rebuild() == store.state


def test_each_task_gets_exactly_one_verdict_per_attempt(repo):
    """docs/03 — verdict_assigned 는 attempt 당 최대 한 번이다."""
    configure(repo)
    write_task(repo, "T-001", kind="analysis")
    store = go(repo)
    verdicts = [e for e in store.journal.read() if e.type is EventType.VERDICT_ASSIGNED]
    assert len(verdicts) == 1
    assert verdicts[0].payload["attempt"] == 1


# --------------------------------------------------------------------------- 완료 기준 ①~⑤


def test_a_broken_claim_still_allows_verified(repo):
    """M1 완료 기준 ① — claim 이 깨져 있어도 verified 가 가능하다."""
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[exits(0)])
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}, "claim": "{ not json at all"}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert EventType.CLAIM_REJECTED in types_for(store, "T-001")


def test_a_missing_required_handoff_does_not_throw_away_the_implementation(repo):
    """M1 완료 기준 ② — repairing 은 handoff 만 복구하고 코드는 그대로 둔다."""
    config = configure(repo)
    write_task(repo, "T-001", outputs={"required": ["public_api"]})
    adapter = ScriptedAdapter(
        [
            {"files": {"a.py": "written once\n"}},  # handoff 없음
            {"handoff": HANDOFF},  # 좁은 fixer — 코드를 다시 쓰지 않는다
        ]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert (repo / "a.py").read_text(encoding="utf-8") == "written once\n"
    kinds = types_for(store, "T-001")
    assert EventType.HANDOFF_MISSING in kinds
    assert EventType.FIXER_DISPATCHED in kinds


def test_the_handoff_fixer_is_scoped_to_the_handoff(repo):
    """docs/06 — fixer_dispatched {scope: "handoff"} 다."""
    config = configure(repo)
    write_task(repo, "T-001", outputs={"required": ["public_api"]})
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}, {"handoff": HANDOFF}])

    store = go(repo, config, adapter)

    fixer = [e for e in store.journal.read() if e.type is EventType.FIXER_DISPATCHED][0]
    assert fixer.payload["scope"] == "handoff"


def test_exhausted_handoff_repairs_need_a_human(repo):
    config = configure(repo, max_handoff_repairs=1)
    write_task(repo, "T-001", outputs={"required": ["public_api"]})
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])  # 매번 handoff 없음

    store = go(repo, config, adapter)

    task = task_of(store, "T-001")
    assert task.state is State.HUMAN_REQUIRED
    assert task.reason == "handoff_missing"
    assert task.verdict is None


def test_a_denied_acceptance_command_is_never_executed(repo):
    """M1 완료 기준 ③ — deny 커맨드가 실행되지 않는다."""
    marker = repo / "should-not-exist"
    config = configure(
        repo,
        command_policy={"default": "allow", "rules": [{"match": PYTHON, "verdict": "deny"}]},
    )
    write_task(repo, "T-001", acceptance=[ac(f"open({str(marker)!r}, 'w').close()")])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])

    store = go(repo, config, adapter)

    assert not marker.exists()
    assert task_of(store, "T-001").state is State.NEEDS_REPLAN
    assert task_of(store, "T-001").reason == "policy_denied_at_runtime"
    assert adapter.requests == []  # agent 조차 실행되지 않는다


def test_an_unrelated_pre_existing_failure_does_not_reject_the_task(repo):
    """M1 완료 기준 ④ — 무관한 기존 실패가 task 를 rejected 시키지 않는다."""
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[exits(1)])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert store.state.open_debts  # 판정은 막지 않고 ship 을 막는다


def test_a_nonzero_agent_exit_can_still_be_verified(repo):
    """M1 완료 기준 ⑤ — non-zero exit 여도 증거가 통과하면 verified 다."""
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[exits(0)])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}, "exit_code": 7, "stderr": "노이즈"}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert EventType.AGENT_EXIT_NONZERO in types_for(store, "T-001")


# --------------------------------------------------------------------------- precheck


def test_a_missing_precondition_blocks_without_running_the_agent(repo):
    """docs/06 — precheck 실패는 agent 를 실행하지 않는다."""
    config = configure(repo)
    write_task(repo, "T-001", preconditions=[{"kind": "file", "path": "absent.txt"}])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.BLOCKED
    assert task_of(store, "T-001").state is State.HUMAN_REQUIRED
    assert adapter.requests == []
    assert EventType.PRECONDITION_CHECKED in types_for(store, "T-001")


def test_a_missing_prerequisite_from_preflight_blocks(repo):
    """docs/04, docs/10 — missing_prerequisite 는 blocked 다."""
    config = configure(repo)
    write_task(repo, "T-001")
    adapter = ScriptedAdapter(
        [{}], preflight=PreflightReport(False, PreflightKind.MISSING_PREREQUISITE, "CLI 미설치")
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.BLOCKED
    assert adapter.requests == []


def test_a_misconfigured_adapter_is_an_error_not_a_block(repo):
    """docs/10 — 코드·설정을 고쳐야 하는 것은 error 다."""
    config = configure(repo)
    write_task(repo, "T-001")
    adapter = ScriptedAdapter(
        [{}], preflight=PreflightReport(False, PreflightKind.MISCONFIGURED, "placeholder 미해석")
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.ERROR


def test_a_denied_precondition_command_needs_a_replan(repo):
    config = configure(
        repo,
        command_policy={"default": "allow", "rules": [{"match": PYTHON, "verdict": "deny"}]},
    )
    write_task(repo, "T-001", preconditions=[{"kind": "command", "cmd": [sys.executable, "-c", "pass"]}])

    store = go(repo, config, ScriptedAdapter([{}]))

    assert task_of(store, "T-001").state is State.NEEDS_REPLAN
    assert task_of(store, "T-001").reason == "policy_denied_at_runtime"


# --------------------------------------------------------------------------- baseline


def test_a_non_discriminating_criterion_needs_a_replan_without_running_the_agent(repo):
    """docs/06 — expect_fail_before 인데 이미 통과하면 agent 를 실행하지 않는다."""
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[exits(0, expect_fail_before=True)])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.NEEDS_REPLAN
    assert task_of(store, "T-001").reason == "ac_not_discriminating"
    assert adapter.requests == []


def test_a_pre_existing_failure_opens_a_debt(repo):
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[exits(1)])
    store = go(repo, config, ScriptedAdapter([{"files": {"a.py": "x\n"}}]))

    debt = list(store.state.open_debts.values())[0]
    assert debt.origin_task == "T-001"
    assert EventType.DEBT_OPENED in types_for(store, "T-001")


def test_a_debt_closes_when_the_criterion_turns_green(repo):
    flag = repo / "flip"
    code = f"import os; raise SystemExit(0 if os.path.exists({str(flag)!r}) else 1)"
    config = configure(repo)
    write_task(repo, "T-001", acceptance=[ac(code)])
    adapter = ScriptedAdapter([{"files": {"flip": "x\n"}}])

    store = go(repo, config, adapter)

    assert store.state.open_debts == {}
    assert EventType.DEBT_CLOSED in types_for(store, "T-001")


# --------------------------------------------------------------------------- 재시도


def test_a_rejected_task_retries_until_the_attempt_limit(repo):
    config = configure(repo, max_attempts=3)
    write_task(repo, "T-001", acceptance=[exits(1, expect_fail_before=True)])
    adapter = ScriptedAdapter([{"files": {"a.py": "x\n"}}])

    store = go(repo, config, adapter)

    assert len(adapter.requests) == 3
    assert task_of(store, "T-001").attempt == 3


def test_exhausted_attempts_need_a_replan(repo):
    """docs/03 — rejected 이고 시도가 소진되면 needs_replan 이다."""
    config = configure(repo, max_attempts=1)
    write_task(repo, "T-001", acceptance=[exits(1, expect_fail_before=True)])

    store = go(repo, config, ScriptedAdapter([{"files": {"a.py": "x\n"}}]))

    assert task_of(store, "T-001").verdict is Verdict.REJECTED
    assert task_of(store, "T-001").state is State.NEEDS_REPLAN


def test_a_runtime_failure_is_an_error(repo):
    """docs/06 — timeout·kill·프로토콜 위반은 실행이 유효하게 성립하지 않은 것이다."""
    config = configure(repo, max_attempts=1)
    write_task(repo, "T-001")

    store = go(repo, config, ScriptedAdapter([{"runtime_failure": "timeout", "exit_code": -9}]))

    assert task_of(store, "T-001").verdict is Verdict.ERROR


def test_an_implementation_that_changed_nothing_needs_a_replan(repo):
    config = configure(repo)
    write_task(repo, "T-001")

    store = go(repo, config, ScriptedAdapter([{}]))

    assert task_of(store, "T-001").state is State.NEEDS_REPLAN
    assert task_of(store, "T-001").reason == "no_op_detected"


# --------------------------------------------------------------------------- 부분 실패


def test_a_blocked_branch_does_not_stop_the_rest_of_the_run(repo):
    """docs/03, docs/10 — blocked 는 해당 task 와 하위 의존만 막고 run 은 정상 종료한다."""
    config = configure(
        repo,
        scenario={"default": {"files": {"ok.py": "1\n"}}},
    )
    write_task(repo, "T-001", preconditions=[{"kind": "file", "path": "absent.txt"}])
    write_task(repo, "T-002", depends_on=["T-001"])
    write_task(repo, "T-003")

    store = go(repo, config)

    assert task_of(store, "T-001").verdict is Verdict.BLOCKED
    assert task_of(store, "T-002").state is State.PENDING  # 하위는 막힌다
    assert task_of(store, "T-003").verdict is Verdict.VERIFIED  # 다른 가지는 진행한다
    assert store.state.finished_at is not None


def test_the_run_summary_says_what_was_blocked(repo):
    config = configure(repo)
    write_task(repo, "T-001", preconditions=[{"kind": "file", "path": "absent.txt"}])

    store = go(repo, config)

    finished = store.journal.read()[-1]
    assert finished.type is EventType.RUN_FINISHED
    assert "T-001" in finished.payload["human_required"]


# --------------------------------------------------------------------------- 프로파일


def test_diff_is_attributed_to_the_task_that_made_it(repo):
    """safe 프로파일은 워크트리를 나누지 않는다. 앞선 task 의 변경이 섞이면 판정이 무의미해진다."""
    config = configure(
        repo,
        scenario={
            "tasks": {"T-001": {"files": {"a.py": "1\n"}}, "T-002": {"files": {"b.py": "2\n"}}}
        },
    )
    write_task(repo, "T-001")
    write_task(repo, "T-002", depends_on=["T-001"])

    store = go(repo, config)
    output = (store.run_dir / "tasks" / "T-002" / "verification.json").read_text(encoding="utf-8")

    assert "b.py" in output
    assert "a.py" not in output


def test_m1_runs_the_safe_profile_only(repo):
    """M2 가 worktree 프로파일을 만든다. 그전까지는 제공하지 못하는 것을 제공한다고 하지 않는다."""
    import pytest

    from harness.errors import HarnessError

    config = configure(repo, defaults={"adapter": "mock", "profile": "worktree"})
    write_task(repo, "T-001", kind="analysis")
    commit(repo)

    with pytest.raises(HarnessError, match="worktree"):
        run_dag(repo, config, Dag(load_tasks(repo)))


WRITES_A_FILE = (
    "import os, sys; open(os.path.join(sys.argv[1], sys.argv[2] + '.py'), 'w').write('x')"
)


def test_a_three_task_dag_runs_to_completion_on_generic_cli(repo):
    """M1 완료 기준 — mock 과 generic_cli **양쪽**에서 3-task DAG 를 완주한다."""
    data = {
        "version": 1,
        "defaults": {"adapter": "cli", "profile": "safe", "max_parallel": 1},
        "adapters": {
            "cli": {
                "type": "generic_cli",
                "command": [sys.executable, "-c", WRITES_A_FILE, "{workspace}", "{task_id}"],
            }
        },
        "command_policy": {"default": "require_approval", "rules": [{"match": PYTHON, "verdict": "allow"}]},
    }
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    write_task(repo, "T-001", acceptance=[exits(0)])
    write_task(repo, "T-002", depends_on=["T-001"])
    write_task(repo, "T-003", depends_on=["T-002"])

    store = go(repo, load(repo))

    assert [task_of(store, t).verdict for t in ("T-001", "T-002", "T-003")] == [Verdict.VERIFIED] * 3
    assert (repo / "T-003.py").is_file()
