"""sequential runner — 한 task 의 attempt 실행과 DAG 완주.

M1 완료 기준 다섯 가지와 M2 완료 기준 두 가지를 여기서 증명한다 (docs/12).
"""

import re
import subprocess
import sys

import pytest
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
from harness.errors import HarnessError
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.exec.workspace import integration_branch
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
        for key, filename in (
            ("claim", "result.json"),
            ("handoff", "handoff.json"),
            ("findings", "findings.json"),
        ):
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


def configure(repo, scenario=None, profile="safe", max_parallel=1, **extra):
    """docs/04 — mock 은 시나리오 **파일**로 결과를 지정한다."""
    adapter = {"type": "mock"}
    if scenario is not None:
        path = repo / "mock-scenario.yaml"
        path.write_text(yaml.safe_dump(scenario), encoding="utf-8")
        adapter["scenario"] = str(path)

    data = {
        "version": 1,
        "defaults": {"adapter": "mock", "profile": profile, "max_parallel": max_parallel},
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
    """커밋할 것이 없어도 넘어간다. 한 테스트에서 두 번 부를 수 있어야 한다."""
    git(repo, "add", "-A")
    subprocess.run(["git", "commit", "-q", "-m", "seed tasks"], cwd=repo, capture_output=True)


def go(repo, config=None, adapter=None, resume=False):
    """워크스페이스는 저장소 밖이어야 하므로 scratch 를 tmp_path 아래로 준다."""
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
    )


def out(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return result.stdout.strip()


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


def test_every_dispatched_prompt_names_the_outbox_by_absolute_path(repo):
    """docs/04 Outbox 규약 — 경로는 환경변수 **와 프롬프트 말미의 절대 경로** 로 전달된다.

    도구 allowlist 가 좁은 CLI agent 는 자기 환경변수를 읽을 수 없다. 프롬프트에 경로가
    없으면 `$HARNESS_OUTBOX` 를 알아내려다 턴을 소진한다 (2026-09-03 능력 eval 에서 관측).
    """
    config = configure(repo)
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    go(repo, config, adapter=adapter)

    assert adapter.requests
    for request in adapter.requests:
        assert str(request.outbox) in request.prompt
        assert request.env["HARNESS_OUTBOX"] == str(request.outbox)


def test_windows_system_variables_reach_the_agent(repo, monkeypatch):
    """env 화이트리스트(docs/09)는 좁지만 Windows 의 SystemDrive/ProgramData 는 통과시킨다.

    빠지면 자식 프로세스의 시스템 컴포넌트가 `%SystemDrive%` 를 문자 그대로 워크트리 안에
    만들어 path_violation 이 난다 (2026-09-03 능력 eval 에서 관측).
    """
    monkeypatch.setenv("SystemDrive", "C:")
    monkeypatch.setenv("ProgramData", "C:/ProgramData")
    config = configure(repo)
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1" + chr(10)}}])

    go(repo, config, adapter=adapter)

    env = adapter.requests[0].env
    assert env["SystemDrive"] == "C:" and env["ProgramData"] == "C:/ProgramData"


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


def test_a_profile_the_harness_cannot_provide_is_refused(repo):
    """docs/05 — container 는 M8 이다. 제공하지 못하는 격리를 제공한다고 말하지 않는다."""
    config = configure(repo, defaults={"adapter": "mock", "profile": "container"})
    write_task(repo, "T-001", kind="analysis")
    commit(repo)

    with pytest.raises(HarnessError, match="container"):
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


# --------------------------------------------------------------------------- worktree 프로파일


class Watcher(ScriptedAdapter):
    """워크스페이스에 무엇이 보였는지 기록한다. 통합이 실제로 작동하는지 보려면 필요하다."""

    def __init__(self, steps, watch):
        super().__init__(steps)
        self.watch = watch
        self.seen = {}

    def execute(self, request):
        self.seen[request.task_id] = (request.workspace / self.watch).is_file()
        return super().execute(request)


def chain(repo, count=3):
    for index in range(1, count + 1):
        task_id = f"T-00{index}"
        depends = [f"T-00{index - 1}"] if index > 1 else []
        write_task(repo, task_id, depends_on=depends)


def test_a_three_task_dag_runs_to_completion_in_worktrees(repo):
    """M2 — worktree 프로파일에서도 DAG 를 완주한다."""
    config = configure(
        repo,
        profile="worktree",
        scenario={
            "tasks": {
                "T-001": {"files": {"a.py": "1\n"}},
                "T-002": {"files": {"b.py": "2\n"}},
                "T-003": {"files": {"c.py": "3\n"}},
            }
        },
    )
    chain(repo)

    store = go(repo, config)
    assert [t.state for t in store.state.tasks.values()] == [State.DONE] * 3


def test_the_agent_never_works_in_the_repository(repo):
    """docs/05 — worktree 프로파일의 cwd 는 저장소 밖이다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    go(repo, config, adapter)
    assert adapter.requests[0].workspace != repo
    assert repo not in adapter.requests[0].workspace.parents


def test_a_downstream_task_sees_what_upstream_made(repo):
    """docs/05 — 워크트리는 통합 브랜치의 tip 에서 분기한다. 그래야 depends_on 이 의미를 갖는다."""
    config = configure(repo, profile="worktree")
    chain(repo, count=2)
    adapter = Watcher([{"files": {"a.py": "1\n"}}, {"files": {"b.py": "2\n"}}], watch="a.py")

    go(repo, config, adapter)
    assert adapter.seen == {"T-001": False, "T-002": True}


def test_the_user_branch_is_untouched_by_a_run(repo):
    """docs/05 — 사용자 브랜치로 옮기는 것은 ship 의 일이다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])
    commit(repo)
    head = out(repo, "rev-parse", "HEAD")


    go(repo, config, adapter)

    assert out(repo, "rev-parse", "HEAD") == head
    assert out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert not (repo / "a.py").exists()


def test_verified_work_lands_on_the_integration_branch(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    go(repo, config, adapter)
    assert out(repo, "show", f"{integration_branch('run-1')}:a.py") == "1"


def test_a_finished_worktree_is_removed(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    go(repo, config, adapter)
    assert not adapter.requests[0].workspace.exists()


def test_a_worktree_is_kept_when_the_task_did_not_finish(repo):
    """docs/05 — 실패한 워크트리는 사람이 조사할 수 있게 남긴다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", acceptance=[exits(1, expect_fail_before=True)])
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    store = go(repo, config, adapter)
    assert task_of(store, "T-001").verdict is Verdict.REJECTED
    assert adapter.requests[-1].workspace.is_dir()


# --------------------------------------------------------------------------- 경로 스코프 (M2 완료 기준 ①)


def test_a_change_outside_allowed_paths_is_rejected(repo):
    """M2 완료 기준 ① — 경로 위반이 자동으로 rejected 가 된다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"elsewhere.py": "1\n"}}])

    store = go(repo, config, adapter)
    assert task_of(store, "T-001").verdict is Verdict.REJECTED
    assert task_of(store, "T-001").reason == "path_violation"


def test_a_path_violation_is_recorded_as_evidence(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"elsewhere.py": "1\n"}}])

    store = go(repo, config, adapter)
    violations = [
        event.payload
        for event in store.journal.read()
        if event.type is EventType.PATH_VIOLATION
    ]
    assert violations[0] == {"paths": ["elsewhere.py"], "rule": "allowed_paths"}


def test_a_change_inside_allowed_paths_is_verified(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/api.py": "1\n"}}])

    store = go(repo, config, adapter)
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


def test_touching_the_control_plane_is_rejected(repo):
    """docs/05 — `.harness/**` 는 전역 금지 목록이다. 하네스는 승인하지 않고 탐지한다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    adapter = ScriptedAdapter([{"files": {".harness/sneaky.yaml": "x\n"}}])

    store = go(repo, config, adapter)
    assert task_of(store, "T-001").reason == "path_violation"


# --------------------------------------------------------------------------- 재개 (M2 완료 기준 ②)


class ByTask(ScriptedAdapter):
    """task_id 로 결과를 정하고, 지정한 task 에서 죽는다.

    재개하면 호출 **순서**가 달라지므로 순서로 시나리오를 표현할 수 없다.
    """

    def __init__(self, by_task, dies_on=None):
        super().__init__([{}])
        self.by_task = by_task
        self.dies_on = dies_on

    def execute(self, request):
        if request.task_id == self.dies_on:
            raise RuntimeError("강제 종료")
        self.steps = [self.by_task.get(request.task_id, {})]
        return super().execute(request)


FILES = {
    "T-001": {"files": {"a.py": "1\n"}},
    "T-002": {"files": {"b.py": "2\n"}},
}


class Refuses(ScriptedAdapter):
    """호출되면 안 되는 어댑터."""

    def execute(self, request):
        raise AssertionError(f"{request.task_id} 을(를) 다시 실행했다")


def worktree_chain(repo, count=2):
    config = configure(repo, profile="worktree")
    chain(repo, count)
    return config


def test_resume_finishes_what_the_crash_interrupted(repo):
    """M2 완료 기준 ② — 강제 종료 후 재개가 journal 로 복원된다."""
    config = worktree_chain(repo)
    with pytest.raises(RuntimeError):
        go(repo, config, ByTask(FILES, dies_on="T-002"))

    store = go(repo, config, ByTask(FILES), resume=True)
    assert [t.state for t in store.state.tasks.values()] == [State.DONE, State.DONE]


def test_resume_does_not_rerun_a_finished_task(repo):
    """docs/10 — done 은 재실행하지 않는다."""
    config = worktree_chain(repo, count=1)
    go(repo, config, ScriptedAdapter([{"files": {"a.py": "1\n"}}]))

    store = go(repo, config, Refuses([{}]), resume=True)
    assert task_of(store, "T-001").state is State.DONE


def test_resume_is_idempotent(repo):
    """docs/10 — 같은 run-id 로 몇 번을 재개해도 결과가 같아야 한다."""
    config = worktree_chain(repo)
    with pytest.raises(RuntimeError):
        go(repo, config, ByTask(FILES, dies_on="T-002"))

    first = go(repo, config, ByTask(FILES), resume=True).state
    second = go(repo, config, Refuses([{}]), resume=True).state
    assert {k: v.to_dict() for k, v in first.tasks.items()} == {
        k: v.to_dict() for k, v in second.tasks.items()
    }


def test_resume_restarts_a_dead_attempt_with_the_same_number(repo):
    """docs/10 — 크래시는 재시도 한도를 소진시키지 않는다."""
    config = worktree_chain(repo, count=1)
    with pytest.raises(RuntimeError):
        go(repo, config, ByTask(FILES, dies_on="T-001"))

    store = go(repo, config, ByTask(FILES), resume=True)
    assert task_of(store, "T-001").verdict_attempt == 1


def test_resume_replays_verification_without_calling_the_agent_again(repo, monkeypatch):
    """docs/10 — baseline 은 journal 에 있다. agent 를 다시 부를 이유가 없다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", acceptance=[exits(0)])
    adapter = ScriptedAdapter([{"files": {"a.py": "1\n"}}])

    def crash(*_args, **_kwargs):
        raise RuntimeError("검증 중 강제 종료")

    monkeypatch.setattr("harness.exec.verify.run_post", crash)
    with pytest.raises(RuntimeError):
        go(repo, config, adapter)
    monkeypatch.undo()

    store = go(repo, config, adapter, resume=True)
    assert task_of(store, "T-001").state is State.DONE
    assert len(adapter.requests) == 1


def test_resume_needs_an_existing_run(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001")
    commit(repo)

    with pytest.raises(HarnessError, match="재개할 run"):
        run_dag(repo, config, Dag(load_tasks(repo)), run_id="run-404", resume=True)


def test_a_task_that_needs_a_human_is_not_resumed(repo):
    """docs/10 — human_required 는 사람이 조치한 뒤의 일이다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", preconditions=[{"kind": "file", "path": "no-such-file"}])
    go(repo, config, ScriptedAdapter([{}]))

    store = go(repo, config, Refuses([{}]), resume=True)
    assert task_of(store, "T-001").state is State.HUMAN_REQUIRED


# --------------------------------------------------------------------------- agent 의 커밋 (docs/05)


class CommittingAdapter(ScriptedAdapter):
    """step 의 `committed` 파일을 워크스페이스에서 실제로 커밋한다.

    실제 코딩 agent 는 자주 커밋한다. docs/05 — 커밋 여부는 판정에 영향을 주지 않는다.
    """

    def execute(self, request):
        result = super().execute(request)
        step = self.steps[min(len(self.requests) - 1, len(self.steps) - 1)]
        committed = step.get("committed") or {}
        for relative, content in committed.items():
            target = request.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if committed:
            subprocess.run(
                ["git", "add", "-A"], cwd=request.workspace, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "agent"],
                cwd=request.workspace,
                check=True,
                capture_output=True,
            )
        return result


def test_a_committed_change_outside_allowed_paths_is_still_rejected(repo):
    """docs/05·06 — 커밋으로 숨긴 경로 위반도 같은 diff 로 관측되고 탐지된다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["allowed/**"])
    adapter = CommittingAdapter(
        [{"files": {"allowed/w.py": "1\n"}, "committed": {"secrets/leak.txt": "oops\n"}}]
    )

    store = go(repo, config, adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    assert task.reason == "path_violation"
    assert EventType.PATH_VIOLATION in types_for(store, "T-001")
    assert out(repo, "show", f"{integration_branch('run-1')}:secrets/leak.txt") == ""


def test_an_agent_that_commits_all_its_work_is_still_verified(repo):
    """docs/05 — 전부 커밋해도 no_op 로 오판하지 않고, 작업은 통합된다."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = CommittingAdapter([{"committed": {"src/a.py": "1\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.VERIFIED
    assert out(repo, "show", f"{integration_branch('run-1')}:src/a.py") == "1"


def test_a_commit_in_the_safe_profile_is_observed_too(repo):
    """safe 프로파일도 dispatch 시점 HEAD 가 기준이다."""
    config = configure(repo)
    write_task(repo, "T-001")
    adapter = CommittingAdapter([{"committed": {"a.py": "1\n"}}])

    store = go(repo, config, adapter)
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


# --------------------------------------------------------------------------- 실행 후 blocked 재분류 (docs/06)


def test_an_environmental_failure_signature_reclassifies_to_blocked(repo):
    """docs/06 — AC stderr 가 blocked_signals 와 맞으면 rejected 가 아니라 blocked 다."""
    config = configure(
        repo,
        blocked_signals=["connection refused"],
        scenario={"tasks": {"T-001": {"files": {"a.py": "1\n"}}}},
    )
    write_task(
        repo,
        "T-001",
        acceptance=[
            ac(
                "import sys; sys.stderr.write('psql: connection refused'); raise SystemExit(1)",
                expect_fail_before=True,
            )
        ],
    )

    store = go(repo, config)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.BLOCKED
    assert "connection refused" in task.reason
    assert task.state is State.HUMAN_REQUIRED


def test_a_precondition_that_broke_mid_run_reclassifies_to_blocked(repo):
    """docs/06 — AC 실패 시 preconditions 를 재실행한다. precheck 는 통과했었다."""
    (repo / ".env.local").write_text("x", encoding="utf-8")
    # max_attempts=1 — 재시도의 precheck 가 아니라 같은 attempt 안의 재분류를 증명한다
    config = configure(
        repo, max_attempts=1, scenario={"tasks": {"T-001": {"files": {"a.py": "1\n"}}}}
    )
    write_task(
        repo,
        "T-001",
        preconditions=[{"kind": "file", "path": ".env.local"}],
        acceptance=[
            ac(
                "import os, sys; os.path.exists('.env.local') and os.remove('.env.local'); sys.exit(1)",
                expect_fail_before=True,
            )
        ],
    )

    store = go(repo, config)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.BLOCKED
    assert task.reason.startswith("prerequisite")


def test_an_uncorroborated_blocked_hint_stays_rejected(repo):
    """docs/06 — probe 가 통과하면 힌트를 인정하지 않는다. claim_uncorroborated 가 남는다."""
    configure(repo)
    write_task(
        repo,
        "T-001",
        preconditions=[{"kind": "file", "path": "README.md"}],
        acceptance=[exits(1, expect_fail_before=True)],
    )
    adapter = ScriptedAdapter(
        [
            {
                "files": {"a.py": "1\n"},
                "claim": {
                    "schema": "harness.claim/v1",
                    "task_id": "T-001",
                    "outcome_claim": "blocked",
                    "blocked_hint": "DB 가 없어 보임",
                },
            }
        ]
    )

    store = go(repo, load(repo), adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.REJECTED
    event = next(
        e for e in store.journal.read() if e.type is EventType.CLAIM_UNCORROBORATED
    )
    assert event.payload["hint"] == "DB 가 없어 보임"
    assert event.payload["probe_result"] == "pass"
