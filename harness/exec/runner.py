"""한 task 의 attempt 실행과 DAG 완주. sequential (`max_parallel = 1`).

docs/03 의 상태 기계와 docs/06 의 판정 순서를 그대로 따른다. 이 모듈은 순서를 지키고
이벤트를 남길 뿐, 판정 자체는 하지 않는다 — 그것은 `verify` 와 `handoff` 의 몫이다.

`safe` 와 `worktree` 를 실행한다. `container` 와 `unsafe` 는 거부한다 — 제공하지 못하는
격리를 제공한다고 말하지 않는다 (docs/05 의 표현 규약).

쓰기 순서는 언제나 journal append + fsync → state 갱신이다. `store.append` 가 그 순서를
갖고 있으므로 여기서는 이벤트를 남기는 순서만 지키면 된다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.adapters.base import AgentRequest, AgentResult, PreflightKind
from harness.adapters.registry import build as build_adapter
from harness.config import HARNESS_DIR, Config
from harness.dag import Dag
from harness.errors import HarnessError
from harness.events import EventType
from harness.exec import handoff as handoff_module
from harness.exec import verify as verify_module
from harness.exec.workspace import MergeResult, Workspace, Workspaces
from harness.models import ExecutionProfile, State, Task, Verdict
from harness.policy import CommandPolicy, PolicyVerdict, load_approvals
from harness.probes import check_all
from harness.store import Store

STDERR_TAIL_CHARS = 2000

# docs/05 — 나머지 프로파일은 거부한다. container 는 M8 이고, unsafe 는 아직 없다.
SUPPORTED_PROFILES = (ExecutionProfile.SAFE, ExecutionProfile.WORKTREE)

# agent 실행은 끝났고 verdict 는 없는 state. 워크트리가 남아 있으면 검증부터 재개한다 (docs/10).
RESUMABLE = (State.EXECUTED, State.VERIFYING, State.REVIEWING, State.REPAIRING)

# 자식 프로세스에 넘길 최소 환경변수. 화이트리스트이며, 여기 없는 것은 넘어가지 않는다.
# 비밀 취급은 docs/09 가 canonical 이다.
ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "windir",
    "TEMP",
    "TMP",
    "TMPDIR",
    "COMSPEC",
    "PATHEXT",
)


@dataclass(frozen=True)
class AttemptOutcome:
    """attempt 하나의 결과.

    `verdict` 가 `None` 이면 docs/03 의 "verdict 없이 state 만 갖는" 경우이고
    `next_state` 가 채워져 있다.
    """

    verdict: Verdict | None = None
    reason: str | None = None
    next_state: State | None = None


def new_run_id(now: datetime | None = None) -> str:
    return f"run-{(now or datetime.now()).strftime('%Y%m%d-%H%M')}"


def run_dag(
    repo: Path | str,
    config: Config,
    dag: Dag,
    *,
    adapter: Any = None,
    run_id: str | None = None,
    resume: bool = False,
    scratch: Path | str | None = None,
    runner_cls: type["Runner"] | None = None,
    context_builder: Any = None,
    review_stage: Any = None,
) -> Store:
    """DAG 를 끝까지 실행하고 journal 을 가진 Store 를 돌려준다.

    `resume` 이면 기존 run 의 journal 을 이어 쓴다. 무엇을 다시 하고 무엇을 건너뛸지는
    state 가 결정하며, 그 표는 docs/10 이 canonical 이다.

    `runner_cls`·`context_builder`·`review_stage` 는 옵션 레이어(docs/02)가 커널에
    끼어드는 확장점이다. 커널은 어느 것도 import 하지 않는다 — builder 가 없으면 task
    계약에 명시된 파일만 넣고, review stage 가 없으면 verified 조건 4 는 공허하게
    참이다 (docs/02 의 옵션 경계).
    """
    repo = Path(repo)
    if config.default_profile not in SUPPORTED_PROFILES:
        raise HarnessError(
            f"{config.default_profile} 프로파일은 아직 실행할 수 없다. "
            "제공하지 못하는 격리를 제공한다고 말하지 않는다 (docs/05)"
        )

    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )
    store = Store(_run_dir(repo, run_id, resume))
    cls = runner_cls or Runner
    return cls(
        repo, config, store, policy, adapter, scratch, context_builder, review_stage
    ).run(dag, resume=resume)


class Runner:
    def __init__(
        self,
        repo: Path,
        config: Config,
        store: Store,
        policy: CommandPolicy,
        adapter: Any = None,
        scratch: Path | str | None = None,
        context_builder: Any = None,
        review_stage: Any = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.store = store
        self.policy = policy
        self._fixed_adapter = adapter
        self._adapters: dict[str, Any] = {}
        self.context_builder = context_builder
        self.review_stage = review_stage
        self.workspaces = Workspaces(repo, store.run_id, config.default_profile, scratch)

    # ----------------------------------------------------------------- 런 루프

    def run(self, dag: Dag, *, resume: bool = False) -> Store:
        verified, remaining = self._begin(dag, resume)

        while True:
            ready = dag.ready(verified, remaining)
            if not ready:
                break
            task_id = ready[0]  # sequential — 한 번에 하나
            remaining.discard(task_id)
            if self._run_task(dag.tasks[task_id]):
                verified.add(task_id)

        self.store.append(EventType.RUN_FINISHED, self._summary())
        return self.store

    def _begin(self, dag: Dag, resume: bool) -> tuple[set[str], set[str]]:
        if resume:
            return self._resume_sets(dag)
        self.store.append(
            EventType.RUN_STARTED,
            {
                "manifest": {"task_ids": list(dag.order())},
                "profile": str(self.config.default_profile),
                "adapter": self.config.default_adapter,
                "max_parallel": self.config.max_parallel,
            },
        )
        return set(), set(dag.tasks)

    def _resume_sets(self, dag: Dag) -> tuple[set[str], set[str]]:
        """docs/10 의 재개 표. `done` 은 재실행하지 않고, 사람 손이 필요한 것은 건드리지 않는다."""
        stalled = (State.HUMAN_REQUIRED, State.NEEDS_REPLAN, State.INTEGRATION_CONFLICT)
        projections = self.store.state.tasks
        verified = {
            task_id
            for task_id, projection in projections.items()
            if projection.state is State.DONE
        }
        remaining = {
            task_id
            for task_id in dag.tasks
            if task_id not in verified
            and (task_id not in projections or projections[task_id].state not in stalled)
        }
        return verified, remaining

    def _summary(self) -> dict[str, Any]:
        """docs/10 — 무엇이 왜 막혔고 무엇이 끝났는지 적는다. run 은 정상 종료한다."""
        state = self.store.state
        return {
            "summary": {
                task_id: {
                    "state": str(task.state),
                    "verdict": str(task.verdict) if task.verdict else None,
                    "reason": task.reason,
                }
                for task_id, task in state.tasks.items()
            },
            "open_debts": sorted(state.open_debts),
            "human_required": list(state.human_required),
        }

    def _run_task(self, task: Task) -> bool:
        start, from_verification = self._resume_point(task)
        profile = task.profile or self.config.default_profile

        for attempt in range(start, self.config.max_attempts + 1):
            spent = self._over_budget()
            if spent:
                workspace = None
                outcome = AttemptOutcome(Verdict.BUDGET_EXHAUSTED, spent)
            else:
                workspace = (
                    self.workspaces.existing(task.id)
                    if from_verification
                    else self.workspaces.open(task.id, profile)
                )
                outcome = self._attempt(
                    task, attempt, workspace, from_verification=from_verification
                )
            from_verification = False
            next_state = self._next_state(outcome, attempt)
            self.store.append(
                EventType.VERDICT_ASSIGNED,
                {
                    "verdict": str(outcome.verdict) if outcome.verdict else None,
                    "attempt": attempt,
                    "reason": outcome.reason,
                    "next_state": str(next_state),
                },
                task_id=task.id,
                attempt=attempt,
            )
            if workspace is not None:
                self.workspaces.close(workspace, keep=next_state is not State.DONE)
            if next_state is not State.READY:
                return next_state is State.DONE
        return False

    def _resume_point(self, task: Task) -> tuple[int, bool]:
        """어느 attempt 부터, 어느 단계부터 다시 할지. docs/10 의 재개 표다.

        **verdict 가 없는 attempt 는 끝나지 않은 attempt 다.** 크래시는 재시도 한도를
        소진시키지 않으므로 같은 번호로 다시 시작한다.
        """
        projection = self.store.state.tasks.get(task.id)
        if projection is None or projection.attempt == 0:
            return 1, False
        if projection.verdict_attempt == projection.attempt:
            return projection.attempt + 1, False

        resumable = (
            projection.state in RESUMABLE and self.workspaces.existing(task.id) is not None
        )
        return projection.attempt, resumable

    def _next_state(self, outcome: AttemptOutcome, attempt: int) -> State:
        """docs/03 의 verdict → next_state 표. "시도 소진" 의 기준은 config 의 max_attempts 다."""
        if outcome.verdict is None:
            return outcome.next_state or State.NEEDS_REPLAN

        exhausted = attempt >= self.config.max_attempts
        if outcome.verdict is Verdict.VERIFIED:
            return State.DONE
        if outcome.verdict is Verdict.REJECTED:
            return State.NEEDS_REPLAN if exhausted else State.READY
        if outcome.verdict is Verdict.ERROR:
            return State.HUMAN_REQUIRED if exhausted else State.READY
        return State.HUMAN_REQUIRED  # blocked, budget_exhausted

    def _over_budget(self) -> str | None:
        """docs/06 의 예산 상한. 소비량은 전부 journal 의 projection 이다 — 카운터가 없다.

        확인 지점은 agent 를 부르기 직전이며(attempt 시작·리뷰어·fixer), 상한이 하나도
        없으면 확인하지 않고 이벤트도 남기지 않는다.
        """
        budget = self.config.budget
        if not budget.enabled:
            return None

        events = self.store.journal.read()
        calls = sum(1 for event in events if event.type is EventType.AGENT_STARTED)
        tokens, cost = 0, 0.0
        for event in events:
            if event.type is not EventType.AGENT_FINISHED:
                continue
            usage = event.payload.get("usage") or {}
            tokens += (usage.get("tokens_in") or 0) + (usage.get("tokens_out") or 0)
            cost += usage.get("cost_usd") or 0.0
        wall = 0.0
        if self.store.state.started_at:
            elapsed = datetime.now().astimezone() - datetime.fromisoformat(
                self.store.state.started_at
            )
            wall = max(0.0, elapsed.total_seconds())

        self.store.append(
            EventType.BUDGET_CHECKPOINT,
            {
                "tokens": tokens,
                "cost_usd": cost,
                "wall_time_s": wall,
                "remaining": {
                    "wall_time_s": _left(budget.max_wall_time_s, wall),
                    "agent_calls": _left(budget.max_agent_calls, calls),
                    "cost_usd": _left(budget.max_cost_usd, cost),
                },
            },
        )
        if budget.max_wall_time_s is not None and wall >= budget.max_wall_time_s:
            return f"wall_time {wall:.0f}s >= {budget.max_wall_time_s}s"
        if budget.max_agent_calls is not None and calls >= budget.max_agent_calls:
            return f"agent_calls {calls} >= {budget.max_agent_calls}"
        if budget.max_cost_usd is not None and cost >= budget.max_cost_usd:
            return f"cost_usd {cost} >= {budget.max_cost_usd}"
        return None

    # ----------------------------------------------------------------- attempt

    def _attempt(
        self,
        task: Task,
        attempt: int,
        workspace: Workspace,
        *,
        from_verification: bool = False,
    ) -> AttemptOutcome:
        task_dir = self.store.run_dir / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        cwd = workspace.path

        baseline = self._baseline_from_journal(task, attempt) if from_verification else None
        if baseline is None:
            before, baseline, outbox, stop = self._up_to_dispatch(task, attempt, workspace, task_dir)
            if stop:
                return stop
        else:
            # 재개다. 워크트리는 죽기 전 그대로이므로 뺄 것이 없다.
            before = verify_module.DiffObservation((), (), {})
            outbox = _outbox_artifacts(workspace.outbox(attempt))

        raw_claim, raw_handoff = outbox
        claim = handoff_module.normalize(handoff_module.CLAIM, raw_claim, task_dir)
        self._record_artifact(claim, task, attempt)
        handoff = handoff_module.normalize(handoff_module.HANDOFF, raw_handoff, task_dir)
        self._record_artifact(handoff, task, attempt)

        differentials = self._post(task, attempt, baseline, cwd)

        diff = verify_module.observe_diff(cwd, self._harness_paths(cwd)).without(before)
        violations = verify_module.check_paths(diff, task.allowed_paths, self._forbidden(task))
        for violation in violations:
            self.store.append(EventType.PATH_VIOLATION, violation.to_payload(), task.id, attempt)

        evidence = verify_module.judge(task, diff, differentials, violations)
        if evidence.verdict is not None or evidence.next_state is not None:
            self._write_verification(task_dir, task, evidence, handoff)
            return AttemptOutcome(evidence.verdict, evidence.reason, evidence.next_state)

        if self.review_stage is not None:
            # verified 조건 4 (docs/06) — 옵션 리뷰 단계. fixer 가 코드를 바꾸면
            # 갱신된 evidence 가 돌아온다.
            reviewed = self.review_stage.run(
                self, task, attempt, workspace, baseline, before, evidence
            )
            evidence = reviewed.evidence
            if reviewed.outcome is not None:
                self._write_verification(task_dir, task, evidence, handoff)
                return reviewed.outcome

        return self._terminal(task, attempt, evidence, handoff, task_dir, workspace)

    def _up_to_dispatch(self, task: Task, attempt: int, workspace: Workspace, task_dir: Path):
        """프롬프트 → precheck → baseline → agent. 중간에 멈추면 그 사유를 돌려준다."""
        prompt_path, manifest_ref = self._write_prompt(task, task_dir, workspace)
        self.store.append(
            EventType.TASK_DISPATCHED,
            {
                "context_manifest_ref": manifest_ref,
                "prompt_ref": str(prompt_path),
                # risk 모듈이 없으면 선언값을 그대로 쓴다 (docs/02 의 옵션 경계)
                "effective_risk": str(task.risk) if task.risk else None,
            },
            task_id=task.id,
            attempt=attempt,
        )

        empty = verify_module.DiffObservation((), (), {})
        nothing = (None, None)  # 아직 agent 가 아무것도 내놓지 않았다

        stop = self._precheck(task, attempt)
        if stop:
            return empty, None, nothing, stop

        baseline, stop = self._baseline(task, attempt, workspace.path)
        if stop:
            return empty, baseline, nothing, stop

        before = verify_module.observe_diff(workspace.path, self._harness_paths(workspace.path))
        result = self._dispatch(task, attempt, workspace, prompt_path.read_text(encoding="utf-8"))
        outbox = (result.raw_claim_path, result.raw_handoff_path)
        if result.runtime_failure is not None:
            failure = AttemptOutcome(Verdict.ERROR, str(result.runtime_failure))
            return before, baseline, outbox, failure
        return before, baseline, outbox, None

    def _harness_paths(self, cwd: Path) -> tuple[str, ...]:
        """하네스가 자기 run 디렉토리에 쓴 것은 task 의 변경이 아니다.

        워크트리 프로파일에서는 run 디렉토리가 워크스페이스 밖이므로 뺄 것이 없다.
        """
        try:
            relative = self.store.run_dir.relative_to(cwd)
        except ValueError:
            return ()
        return (f"{relative.as_posix()}/**",)

    def _forbidden(self, task: Task) -> tuple[str, ...]:
        """docs/05 — effective_forbidden = task.forbidden_paths ∪ config.forbidden_paths."""
        return (*self.config.forbidden_paths, *task.forbidden_paths)

    def _baseline_from_journal(self, task: Task, attempt: int) -> verify_module.Baseline | None:
        """docs/10 — 죽은 attempt 의 baseline 은 journal 이 갖고 있다.

        복원되면 agent 를 다시 부르지 않고 검증부터 재개한다. 하나라도 모자라면 복원하지
        않는다 — 반쪽 baseline 으로 차등 판정을 하면 판정이 거짓이 된다.
        """
        payloads = [
            event.payload
            for event in self.store.journal.read()
            if event.type is EventType.AC_BASELINE_EXECUTED
            and event.task_id == task.id
            and event.attempt == attempt
        ]
        if len(payloads) != len(task.acceptance):
            return None
        return verify_module.Baseline(
            tuple(verify_module.AcObservation.from_payload(payload) for payload in payloads)
        )

    # ----------------------------------------------------------------- 단계

    def _precheck(self, task: Task, attempt: int) -> AttemptOutcome | None:
        """docs/06 — preconditions 와 어댑터 preflight 를 하네스가 직접 실행한다."""
        results = check_all(task.preconditions, self.policy, self.repo, self.config.ac_timeout_s)
        for result in results:
            if result.decision is not None:
                self.store.append(
                    EventType.COMMAND_POLICY_DECISION, result.decision.to_payload(), task.id, attempt
                )
            self.store.append(
                EventType.PRECONDITION_CHECKED, result.to_payload(), task.id, attempt
            )

        if any(_denied(result.decision) for result in results):
            return AttemptOutcome(None, "policy_denied_at_runtime", State.NEEDS_REPLAN)

        failed = [result for result in results if not result.ok]
        if failed:
            return AttemptOutcome(Verdict.BLOCKED, f"prerequisite: {failed[0].name}")

        report = self._adapter_for(task).preflight()
        if report.ok:
            return None
        if report.kind is PreflightKind.MISSING_PREREQUISITE:
            return AttemptOutcome(Verdict.BLOCKED, report.detail)
        return AttemptOutcome(Verdict.ERROR, report.detail)

    def _post(self, task: Task, attempt: int, baseline, cwd: Path | str):
        """AC post 실행과 기록. 리뷰의 fixer 뒤에도 같은 경로로 다시 실행된다 (docs/06)."""
        differentials = verify_module.run_post(
            task, baseline, self.policy, cwd, self.config.ac_timeout_s
        )
        for differential in differentials:
            self.store.append(
                EventType.AC_POST_EXECUTED, differential.post_payload(), task.id, attempt
            )
            if differential.outcome == "debt_closed":
                self.store.append(
                    EventType.DEBT_CLOSED,
                    {"debt_id": _debt_id(differential.after.cmd), "closed_by": task.id},
                    task.id,
                    attempt,
                )
        return differentials

    def _baseline(self, task: Task, attempt: int, cwd: Path):
        baseline = verify_module.run_baseline(
            task, self.policy, cwd, self.config.ac_timeout_s
        )
        for observed in baseline.observations:
            self.store.append(
                EventType.COMMAND_POLICY_DECISION, observed.decision.to_payload(), task.id, attempt
            )
            self.store.append(
                EventType.AC_BASELINE_EXECUTED, observed.baseline_payload(), task.id, attempt
            )

        if any(_denied(observed.decision) for observed in baseline.observations):
            # analyze 가 사전에 잡았어야 한다. 런타임 도달은 task 정의 결함이다.
            return baseline, AttemptOutcome(None, "policy_denied_at_runtime", State.NEEDS_REPLAN)

        if baseline.not_discriminating:
            return baseline, AttemptOutcome(None, "ac_not_discriminating", State.NEEDS_REPLAN)

        for observed in baseline.pre_existing:
            self.store.append(
                EventType.DEBT_OPENED,
                {
                    "debt_id": _debt_id(observed.cmd),
                    "cmd": list(observed.cmd),
                    "origin_task": task.id,
                },
                task.id,
                attempt,
            )
        return baseline, None

    def _dispatch(
        self,
        task: Task,
        attempt: int,
        workspace: Workspace,
        prompt: str,
        repair: int = 0,
        outbox: Path | None = None,
    ) -> AgentResult:
        outbox = outbox or workspace.outbox(attempt, repair)
        outbox.mkdir(parents=True, exist_ok=True)
        adapter = self._adapter_for(task)
        request = AgentRequest(
            task_id=task.id,
            prompt=prompt,
            workspace=workspace.path,
            outbox=outbox,
            profile=task.profile or self.config.default_profile,
            allowed_tools=None,
            timeout_s=self.config.agent_timeout_s,
            env=self._env(task, outbox, attempt),
            attempt=attempt,
        )

        self.store.append(
            EventType.AGENT_STARTED,
            {"adapter": adapter.name, "workspace": str(workspace.path), "outbox": str(outbox)},
            task.id,
            attempt,
        )
        result = adapter.execute(request)
        self.store.append(
            EventType.AGENT_FINISHED,
            {
                "exit_code": result.exit_code,
                "duration_s": result.duration_s,
                "usage": _usage(result),
                "runtime_failure": str(result.runtime_failure) if result.runtime_failure else None,
            },
            task.id,
            attempt,
        )
        if result.exit_code != 0:
            # 증거 하나일 뿐이다. 판정을 오염시키지 않는다 (docs/06).
            self.store.append(
                EventType.AGENT_EXIT_NONZERO,
                {"exit_code": result.exit_code, "stderr_tail": result.stderr[-STDERR_TAIL_CHARS:]},
                task.id,
                attempt,
            )
        return result

    def _terminal(
        self,
        task: Task,
        attempt: int,
        evidence: verify_module.Evidence,
        handoff: handoff_module.Artifact,
        task_dir: Path,
        workspace: Workspace,
    ) -> AttemptOutcome:
        """docs/06 의 terminal 순서 — handoff 게이트 → 통합 → `verified`.

        구현은 이미 증거로 검증되었다. 코드는 그대로 두고 handoff 만 다시 만든다.
        `verified` 가 기록되는 지점은 이 함수의 마지막 한 곳뿐이다.
        """
        gate = handoff_module.gate(task, handoff)
        repairs = 0

        while not gate.ok:
            self.store.append(EventType.HANDOFF_MISSING, gate.missing_payload(), task.id, attempt)
            if repairs >= self.config.max_handoff_repairs:
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(None, "handoff_missing", State.HUMAN_REQUIRED)

            repairs += 1
            spent = self._over_budget()
            if spent:
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(Verdict.BUDGET_EXHAUSTED, spent)
            self.store.append(
                EventType.FIXER_DISPATCHED, {"wave": repairs, "scope": "handoff"}, task.id, attempt
            )
            result = self._dispatch(
                task, attempt, workspace, _repair_prompt(task, gate), repair=repairs
            )
            handoff = handoff_module.normalize(
                handoff_module.HANDOFF, result.raw_handoff_path, task_dir
            )
            self._record_artifact(handoff, task, attempt)
            gate = handoff_module.gate(task, handoff)

        merge = self.workspaces.integrate(workspace)
        self._write_verification(task_dir, task, evidence, handoff, merge)
        if not merge.ok:
            return AttemptOutcome(None, "integration_conflict", State.INTEGRATION_CONFLICT)
        return AttemptOutcome(Verdict.VERIFIED)

    # ----------------------------------------------------------------- 기록

    def _record_artifact(
        self, artifact: handoff_module.Artifact, task: Task, attempt: int
    ) -> None:
        if artifact.path is None:
            return  # 없는 것은 깨진 것이 아니다. 남길 이벤트가 없다.

        if artifact.kind == handoff_module.CLAIM:
            if artifact.valid:
                payload = {"outcome_claim": artifact.data.get("outcome_claim")}
                self.store.append(EventType.CLAIM_RECEIVED, payload, task.id, attempt)
            else:
                payload = {"error": artifact.error, "path": str(artifact.path)}
                self.store.append(EventType.CLAIM_REJECTED, payload, task.id, attempt)
            return

        if artifact.valid:
            payload = {"fields": sorted(artifact.data)}
            self.store.append(EventType.HANDOFF_RECEIVED, payload, task.id, attempt)
        else:
            payload = {"error": artifact.error, "path": str(artifact.path)}
            self.store.append(EventType.HANDOFF_REJECTED, payload, task.id, attempt)

    def _write_verification(
        self,
        task_dir: Path,
        task: Task,
        evidence: verify_module.Evidence,
        handoff: handoff_module.Artifact,
        merge: MergeResult | None = None,
    ) -> None:
        record = {
            "task_id": task.id,
            "output": handoff_module.merge_output(task, evidence.diff.to_output(), handoff),
            "differentials": [d.post_payload() for d in evidence.differentials],
        }
        if merge is not None:
            # docs/10 의 런북이 충돌 파일 목록을 여기서 찾는다.
            record["integration"] = {"ok": merge.ok, "conflicts": list(merge.conflicts)}
        (task_dir / "verification.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_prompt(
        self, task: Task, task_dir: Path, workspace: Workspace
    ) -> tuple[Path, str | None]:
        """프롬프트를 쓰고 (경로, context manifest 참조) 를 돌려준다.

        계층형 컨텍스트(docs/07)는 옵션이다. builder 가 없으면 커널은 docs/02 가 정한
        대로 task 계약에 명시된 파일만 넣는다. constitution 은 **항상 메인 저장소에서**
        읽고, 저장소 콘텐츠는 **워크스페이스에서** 읽는다 — upstream 이 만든 것이
        거기 있다 (docs/05).
        """
        path = task_dir / "prompt.md"

        if self.context_builder is not None:
            built = self.context_builder.build(
                task, run_dir=self.store.run_dir, workspace=workspace.path
            )
            manifest_path = task_dir / "context.manifest.json"
            manifest_path.write_text(
                json.dumps(built.manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            path.write_text(built.prompt, encoding="utf-8")
            return path, str(manifest_path)

        sections = [_read(self.repo / HARNESS_DIR / "constitution.md"), _task_card(task)]
        for relative in task.context.get("files") or ():
            body = _read(workspace.path / relative)
            if body:
                sections.append(f"## {relative}\n\n```\n{body}\n```")
        sections.append(
            "## 산출물\n\n"
            "claim 은 `$HARNESS_OUTBOX/result.json`, handoff 는 `$HARNESS_OUTBOX/handoff.json` 이다.\n"
            "둘 다 optional 이며, 하네스는 이 보고가 아니라 자기 관측으로 판정한다."
        )
        path.write_text("\n\n".join(s for s in sections if s) + "\n", encoding="utf-8")
        return path, None

    # ----------------------------------------------------------------- 보조

    def _adapter_for(self, task: Task) -> Any:
        if self._fixed_adapter is not None:
            return self._fixed_adapter
        name = task.agent or self.config.default_adapter
        adapter = self._adapters.get(name)
        if adapter is None:
            entry = self.config.adapters[name]
            # 워커 스레드가 겹치면 setdefault 가 먼저 넣은 쪽을 이긴다. 두 번 만드는 것은
            # 낭비일 뿐 오류가 아니다.
            adapter = self._adapters.setdefault(name, build_adapter(name, entry.type, entry.options))
        return adapter

    def _env(self, task: Task, outbox: Path, attempt: int) -> dict[str, str]:
        env = {name: os.environ[name] for name in ENV_PASSTHROUGH if name in os.environ}
        env["HARNESS_OUTBOX"] = str(outbox)
        env["HARNESS_TASK_ID"] = task.id
        env["HARNESS_ATTEMPT"] = str(attempt)
        return env


def _run_dir(repo: Path, run_id: str | None, resume: bool = False) -> Path:
    runs = repo / HARNESS_DIR / "runs"
    if resume:
        if not run_id:
            raise HarnessError("재개하려면 run-id 가 필요하다")
        if not (runs / run_id / "journal.jsonl").is_file():
            raise HarnessError(f"재개할 run 이 없다: {runs / run_id}")
        return runs / run_id
    if run_id:
        return runs / run_id
    stamp = new_run_id()
    candidate, suffix = runs / stamp, 1
    while candidate.exists():
        suffix += 1
        candidate = runs / f"{stamp}-{suffix}"
    return candidate


def _outbox_artifacts(outbox: Path) -> tuple[Path | None, Path | None]:
    """docs/04 의 outbox 규약 — 파일 이름이 고정이므로 재개할 때 어댑터 없이 찾을 수 있다."""
    claim, handoff = outbox / "result.json", outbox / "handoff.json"
    return (claim if claim.is_file() else None, handoff if handoff.is_file() else None)


def _denied(decision) -> bool:
    return decision is not None and decision.verdict is PolicyVerdict.DENY


def _left(limit: float | None, spent: float) -> float | None:
    """budget_checkpoint 의 remaining. 상한이 없으면 null 이다 (docs/06)."""
    return None if limit is None else limit - spent


def _debt_id(cmd: Sequence[str]) -> str:
    """같은 커맨드는 같은 debt 다. 그래야 나중에 green 이 됐을 때 원장에서 닫힌다."""
    digest = hashlib.sha256(" ".join(cmd).encode("utf-8")).hexdigest()
    return f"D-{digest[:8]}"


def _usage(result: AgentResult) -> Mapping[str, Any] | None:
    """벤더가 보고하지 않으면 `null` 이다. 추정하지 않는다 (docs/11)."""
    if result.usage is None:
        return None
    return {
        "tokens_in": result.usage.tokens_in,
        "tokens_out": result.usage.tokens_out,
        "cost_usd": result.usage.cost_usd,
    }


def _task_card(task: Task) -> str:
    lines = [
        "## Task",
        f"- id: {task.id}",
        f"- name: {task.name}",
        f"- kind: {task.kind}",
    ]
    if task.allowed_paths:
        lines.append(f"- allowed_paths: {', '.join(task.allowed_paths)}")
    if task.required_outputs:
        lines.append(f"- required outputs: {', '.join(task.required_outputs)}")
    if task.acceptance:
        lines.append("- acceptance (하네스가 직접 실행한다):")
        lines += [f"    {' '.join(criterion.cmd)}" for criterion in task.acceptance]
    return "\n".join(lines)


def _repair_prompt(task: Task, gate: handoff_module.HandoffGate) -> str:
    """docs/06 — 좁은 fixer. 코드를 고치라고 하지 않는다."""
    return (
        f"## Task {task.id} — handoff 만 다시 만든다\n\n"
        "구현은 이미 하네스의 증거로 검증되었다. **코드를 고치지 말 것.**\n"
        f"`$HARNESS_OUTBOX/handoff.json` 에 누락된 필드를 채워 다시 쓴다: "
        f"{', '.join(gate.missing)}\n"
        f'형식은 `{{"schema": "harness.handoff/v1", "task_id": "{task.id}", ...}}` 이다.\n'
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""
