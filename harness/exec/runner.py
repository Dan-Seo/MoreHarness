"""한 task 의 attempt 실행과 DAG 완주. sequential (`max_parallel = 1`).

docs/03 의 상태 기계와 docs/06 의 판정 순서를 그대로 따른다. 이 모듈은 순서를 지키고
이벤트를 남길 뿐, 판정 자체는 하지 않는다 — 그것은 `verify` 와 `handoff` 의 몫이다.

`safe` · `worktree` · `container` 를 실행한다. `unsafe` 는 거부한다 — 제공하지 못하는
격리를 제공한다고 말하지 않는다 (docs/05 의 표현 규약).

쓰기 순서는 언제나 journal append + fsync → state 갱신이다. `store.append` 가 그 순서를
갖고 있으므로 여기서는 이벤트를 남기는 순서만 지키면 된다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict, deque
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
from harness.git import git
from harness.models import DevelopmentMode, ExecutionProfile, State, Task, Verdict
from harness.policy import CommandPolicy, PolicyVerdict, load_approvals
from harness.probes import check_all, matching_blocked_signal
from harness.store import Store

STDERR_TAIL_CHARS = 2000

# docs/05 — 나머지 프로파일은 거부한다. container 는 M8 이고, unsafe 는 아직 없다.
SUPPORTED_PROFILES = (
    ExecutionProfile.SAFE,
    ExecutionProfile.WORKTREE,
    ExecutionProfile.CONTAINER,
)

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
    # 없으면 Windows 시스템 컴포넌트가 cwd(워크트리) 아래에 `%SystemDrive%` 를 문자 그대로
    # 만들어 path_violation 이 난다 (2026-09-03 능력 eval 에서 관측).
    "SystemDrive",
    "ProgramData",
    "TEMP",
    "TMP",
    "TMPDIR",
    "COMSPEC",
    "PATHEXT",
)


@dataclass(frozen=True)
class ResumePoint:
    """어느 attempt 를, 어느 단계부터 다시 할지. docs/10 의 재개 표다."""

    attempt: int
    from_verification: bool = False  # agent 실행은 끝났다. 검증부터 재개한다
    from_implementation: bool = False  # TDD — red gate 는 통과했다 (docs/06)

    @property
    def reuse_workspace(self) -> bool:
        return self.from_verification or self.from_implementation


@dataclass(frozen=True)
class AttemptOutcome:
    """attempt 하나의 결과.

    `verdict` 가 `None` 이면 docs/03 의 "verdict 없이 state 만 갖는" 경우이고
    `next_state` 가 채워져 있다.
    """

    verdict: Verdict | None = None
    reason: str | None = None
    next_state: State | None = None


class _BudgetExhausted(Exception):
    """Agent 호출 직전의 hard cap 검사에서 더 실행할 수 없다는 신호."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


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
    tdd_stage: Any = None,
) -> Store:
    """DAG 를 끝까지 실행하고 journal 을 가진 Store 를 돌려준다.

    `resume` 이면 기존 run 의 journal 을 이어 쓴다. 무엇을 다시 하고 무엇을 건너뛸지는
    state 가 결정하며, 그 표는 docs/10 이 canonical 이다.

    `runner_cls`·`context_builder`·`review_stage`·`tdd_stage` 는 옵션 레이어(docs/02)가
    커널에 끼어드는 확장점이다. 커널은 어느 것도 import 하지 않는다 — builder 가 없으면
    task 계약에 명시된 파일만 넣고, review stage 가 없으면 verified 조건 4 는 공허하게
    참이다 (docs/02 의 옵션 경계).

    `tdd_stage` 만은 공허하게 참이 되지 않는다. TDD 모드를 선언한 task 를 검증할 수
    없는데 표준 모드로 조용히 돌리면 하네스가 거짓말을 하게 되므로, 스테이지가 없으면
    fail-closed 로 verdict `error` 다 (docs/06).
    """
    repo = Path(repo)
    if config.default_profile not in SUPPORTED_PROFILES:
        raise HarnessError(
            f"{config.default_profile} 프로파일은 아직 실행할 수 없다. "
            "제공하지 못하는 격리를 제공한다고 말하지 않는다 (docs/05)"
        )

    if (
        config.default_profile is ExecutionProfile.CONTAINER
        and config.container is None
    ):
        raise HarnessError(
            "container 프로파일인데 config 에 container 블록이 없다 (docs/05)"
        )

    policy = CommandPolicy.from_config(
        config.command_policy,
        load_approvals(repo / HARNESS_DIR / "approved_commands.yaml"),
    )
    store = Store(_run_dir(repo, run_id, resume))
    cls = runner_cls or Runner
    return cls(
        repo,
        config,
        store,
        policy,
        adapter,
        scratch,
        context_builder,
        review_stage,
        tdd_stage,
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
        tdd_stage: Any = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.store = store
        self.policy = policy
        self._fixed_adapter = adapter
        self._adapters: dict[str, Any] = {}
        self.context_builder = context_builder
        self.review_stage = review_stage
        self.tdd_stage = tdd_stage
        self.workspaces = Workspaces(
            repo, store.run_id, config.default_profile, scratch
        )

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
            and (
                task_id not in projections or projections[task_id].state not in stalled
            )
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
        point = self._resume_point(task)
        profile = task.profile or self.config.default_profile

        for attempt in range(point.attempt, self.config.max_attempts + 1):
            spent = self._over_budget()
            if spent:
                workspace = None
                outcome = AttemptOutcome(Verdict.BUDGET_EXHAUSTED, spent)
            else:
                workspace = (
                    self.workspaces.existing(task.id)
                    if point.reuse_workspace
                    else self.workspaces.open(task.id, profile)
                )
                try:
                    outcome = self._attempt(task, attempt, workspace, resume=point)
                except _BudgetExhausted as exhausted:
                    outcome = AttemptOutcome(Verdict.BUDGET_EXHAUSTED, exhausted.reason)
            point = ResumePoint(attempt)  # 다음 attempt 는 처음부터다
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

    def _resume_point(self, task: Task) -> ResumePoint:
        """어느 attempt 부터, 어느 단계부터 다시 할지. docs/10 의 재개 표다.

        **verdict 가 없는 attempt 는 끝나지 않은 attempt 다.** 크래시는 재시도 한도를
        소진시키지 않으므로 같은 번호로 다시 시작한다.
        """
        projection = self.store.state.tasks.get(task.id)
        if projection is None or projection.attempt == 0:
            return ResumePoint(1)
        if projection.verdict_attempt == projection.attempt:
            return ResumePoint(projection.attempt + 1)

        attempt = projection.attempt
        if self.workspaces.existing(task.id) is None:
            return ResumePoint(attempt)

        # TDD 는 한 attempt 안에 agent 호출이 둘이다. 첫 호출의 `agent_finished` 만으로도
        # projection 은 `executed` 가 되므로 일반 state 표보다 단계 증거를 먼저 본다.
        stage = self._tdd(task)
        if stage is not None:
            if stage.implementation_completed(self, task, attempt):
                return ResumePoint(attempt, from_verification=True)
            if stage.resumes_at_implementation(self, task, attempt):
                return ResumePoint(attempt, from_implementation=True)
            return ResumePoint(attempt)

        resumable = projection.state in RESUMABLE
        return ResumePoint(attempt, from_verification=resumable)

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
        resume: ResumePoint | None = None,
    ) -> AttemptOutcome:
        resume = resume or ResumePoint(attempt)
        if task.development.mode is DevelopmentMode.TDD and self.tdd_stage is None:
            # fail-closed — 검증할 수 없는 모드를 조용히 표준 모드로 돌리지 않는다 (docs/06).
            return AttemptOutcome(Verdict.ERROR, "tdd_mode_unavailable")

        task_dir = self.store.run_dir / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        cwd = workspace.path

        baseline = (
            self._baseline_from_journal(task, attempt)
            if resume.from_verification
            else None
        )
        if baseline is None:
            before, baseline, outbox, base, stop = self._up_to_dispatch(
                task, attempt, workspace, task_dir, resume
            )
            if stop:
                return stop
        else:
            # 재개다. 워크트리는 죽기 전 그대로이므로 뺄 것이 없다.
            before = verify_module.DiffObservation((), (), {})
            outbox = _outbox_artifacts(workspace.outbox(attempt))
            base = self._base_from_journal(task, attempt)

        raw_claim, raw_handoff = outbox
        claim = handoff_module.normalize(handoff_module.CLAIM, raw_claim, task_dir)
        self._record_artifact(claim, task, attempt)
        handoff = handoff_module.normalize(
            handoff_module.HANDOFF, raw_handoff, task_dir
        )
        self._record_artifact(handoff, task, attempt)

        differentials = self._post(task, attempt, baseline, cwd)

        diff = verify_module.observe_diff(
            cwd, self._harness_paths(cwd), base=base
        ).without(before)
        violations = (
            *verify_module.check_paths(diff, task.allowed_paths, self._forbidden(task)),
            *self._tdd_violations(task, attempt, workspace),
        )
        for violation in violations:
            self.store.append(
                EventType.PATH_VIOLATION, violation.to_payload(), task.id, attempt
            )

        evidence = verify_module.judge(task, diff, differentials, violations)
        if evidence.verdict is not None or evidence.next_state is not None:
            outcome = AttemptOutcome(
                evidence.verdict, evidence.reason, evidence.next_state
            )
            outcome = self._reclassified(task, attempt, evidence, claim, outcome)
            self._write_verification(task_dir, task, evidence, handoff)
            return outcome

        if self.review_stage is not None:
            # verified 조건 4 (docs/06) — 옵션 리뷰 단계. fixer 가 코드를 바꾸면
            # 갱신된 evidence 가 돌아온다.
            reviewed = self.review_stage.run(
                self, task, attempt, workspace, baseline, before, evidence, base=base
            )
            evidence = reviewed.evidence
            if reviewed.outcome is not None:
                outcome = self._reclassified(
                    task, attempt, evidence, claim, reviewed.outcome
                )
                self._write_verification(task_dir, task, evidence, handoff)
                return outcome

            tampered = self._tdd_violations(task, attempt, workspace)
            if tampered:
                for violation in tampered:
                    self.store.append(
                        EventType.PATH_VIOLATION,
                        violation.to_payload(),
                        task.id,
                        attempt,
                    )
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(Verdict.REJECTED, "path_violation")

        return self._terminal(task, attempt, evidence, handoff, task_dir, workspace)

    def _up_to_dispatch(
        self,
        task: Task,
        attempt: int,
        workspace: Workspace,
        task_dir: Path,
        resume: ResumePoint,
    ):
        """프롬프트 → precheck → baseline → agent. 중간에 멈추면 그 사유를 돌려준다."""
        prompt_path, manifest_ref = self._write_prompt(task, task_dir, workspace)
        if resume.from_implementation:
            # 이 attempt 는 이미 디스패치됐다. 관측 기준은 그때의 것을 그대로 쓴다 (docs/10).
            base = self._base_from_journal(task, attempt)
        else:
            base = self._head(workspace.path)
            self.store.append(
                EventType.TASK_DISPATCHED,
                {
                    "context_manifest_ref": manifest_ref,
                    "prompt_ref": str(prompt_path),
                    # risk 모듈이 없으면 선언값을 그대로 쓴다 (docs/02 의 옵션 경계)
                    "effective_risk": str(task.risk) if task.risk else None,
                    # diff 관측의 기준 (docs/06). 재개는 journal 에서 이 값을 복원한다.
                    "base": base,
                },
                task_id=task.id,
                attempt=attempt,
            )

        empty = verify_module.DiffObservation((), (), {})
        nothing = (None, None)  # 아직 agent 가 아무것도 내놓지 않았다

        stop = self._precheck(task, attempt)
        if stop:
            return empty, None, nothing, base, stop

        prompt = prompt_path.read_text(encoding="utf-8")
        stage = self._tdd(task)
        if stage is not None:
            # TDD 는 단계를 나눠 디스패치하고 그 사이에 게이트를 둔다 (docs/06).
            phased = stage.run(
                self, task, attempt, workspace, task_dir, base, prompt, resume
            )
            return phased.before, phased.baseline, phased.outbox, base, phased.stop

        baseline, stop = self._baseline(task, attempt, workspace.path)
        if stop:
            return empty, baseline, nothing, base, stop

        before = verify_module.observe_diff(
            workspace.path, self._harness_paths(workspace.path), base=base
        )
        result = self._dispatch(task, attempt, workspace, prompt)
        outbox = (result.raw_claim_path, result.raw_handoff_path)
        if result.runtime_failure is not None:
            failure = AttemptOutcome(Verdict.ERROR, str(result.runtime_failure))
            return before, baseline, outbox, base, failure
        return before, baseline, outbox, base, None

    def _tdd(self, task: Task) -> Any:
        """TDD 모드 task 에만 스테이지를 준다. 모드인데 없으면 `_attempt` 가 fail-closed 다."""
        if task.development.mode is not DevelopmentMode.TDD:
            return None
        return self.tdd_stage

    def _tdd_violations(self, task: Task, attempt: int, workspace: Workspace):
        """docs/06 의 단계 스코프 위반. 리뷰의 fixer 가 만든 변경도 같은 기준을 받는다."""
        stage = self._tdd(task)
        if stage is None:
            return ()
        return stage.phase_violations(self, task, attempt, workspace)

    @staticmethod
    def _head(cwd: Path) -> str | None:
        result = git(["rev-parse", "HEAD"], cwd=cwd)
        return result.stdout.strip() if result.exit_code == 0 else None

    def _base_from_journal(self, task: Task, attempt: int) -> str | None:
        """docs/10 — 죽은 attempt 의 관측 기준은 journal 의 task_dispatched 가 갖고 있다."""
        for event in reversed(self.store.journal.read()):
            if (
                event.type is EventType.TASK_DISPATCHED
                and event.task_id == task.id
                and event.attempt == attempt
            ):
                return event.payload.get("base")
        return None

    def _reclassified(
        self,
        task: Task,
        attempt: int,
        evidence: verify_module.Evidence,
        claim: handoff_module.Artifact,
        outcome: AttemptOutcome,
    ) -> AttemptOutcome:
        """docs/06 — 실행 후 실패의 재분류. `blocked` 는 하네스 소유 증거로만 도달한다.

        AC 실패로 rejected 된 경우에만 environment verifier 가 돈다. claim 의
        blocked_hint 는 probe 를 돌리는 트리거일 뿐이며, probe 가 통과하면 인정하지
        않고 `claim_uncorroborated` 를 남긴다.
        """
        if outcome.verdict is not Verdict.REJECTED or outcome.reason not in (
            "regression",
            "unmet",
        ):
            return outcome

        for differential in evidence.differentials:
            if not differential.rejects:
                continue
            pattern = matching_blocked_signal(
                differential.after.stderr_tail, self.config.blocked_signals
            )
            if pattern:
                return AttemptOutcome(Verdict.BLOCKED, f"blocked_signal: {pattern}")

        hint = None
        if claim.valid:
            hint = claim.data.get("blocked_hint") or (
                "blocked" if claim.data.get("outcome_claim") == "blocked" else None
            )
        if not task.preconditions and hint is None:
            return outcome  # 재실행할 probe 가 없다

        results = check_all(
            task.preconditions, self.policy, self.repo, self.config.ac_timeout_s
        )
        for result in results:
            if result.decision is not None:
                self.store.append(
                    EventType.COMMAND_POLICY_DECISION,
                    result.decision.to_payload(),
                    task.id,
                    attempt,
                )
            self.store.append(
                EventType.PRECONDITION_CHECKED, result.to_payload(), task.id, attempt
            )
        failed = [result for result in results if not result.ok]
        if failed:
            return AttemptOutcome(Verdict.BLOCKED, f"prerequisite: {failed[0].name}")

        if hint is not None:
            self.store.append(
                EventType.CLAIM_UNCORROBORATED,
                {
                    "hint": hint,
                    "probe": [result.name for result in results],
                    "probe_result": "pass",
                },
                task.id,
                attempt,
            )
        return outcome

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

    def _baseline_from_journal(
        self, task: Task, attempt: int
    ) -> verify_module.Baseline | None:
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
        # 새 journal 은 criterion_index 를 기록한다. 같은 attempt 를 여러 번 재개해 같은
        # AC 관측이 중복돼도 각 index 의 최신 완전한 집합을 안전하게 복원할 수 있다.
        indexed = {
            payload["criterion_index"]: payload
            for payload in payloads
            if _valid_criterion_index(payload, task)
        }
        if len(indexed) == len(task.acceptance):
            return verify_module.Baseline(
                tuple(
                    verify_module.AcObservation.from_payload(indexed[index])
                    for index in range(len(task.acceptance))
                )
            )

        # 기존 journal 에는 index 가 없다. 명령과 expect_fail_before 를 키로 삼되 각
        # occurrence 를 큐로 보존해 TDD 의 "non-red 먼저, red 나중" 기록 순서를 task
        # 선언 순서로 되돌린다.
        if len(payloads) != len(task.acceptance):
            return None
        grouped: dict[tuple[tuple[str, ...], bool], deque[Mapping[str, Any]]] = (
            defaultdict(deque)
        )
        for payload in payloads:
            grouped[_baseline_key(payload)].append(payload)

        ordered = []
        for criterion in task.acceptance:
            matches = grouped[(tuple(criterion.cmd), criterion.expect_fail_before)]
            if not matches:
                return None
            ordered.append(verify_module.AcObservation.from_payload(matches.popleft()))
        if any(grouped.values()):
            return None
        return verify_module.Baseline(tuple(ordered))

    # ----------------------------------------------------------------- 단계

    def _precheck(self, task: Task, attempt: int) -> AttemptOutcome | None:
        """docs/06 — preconditions 와 어댑터 preflight 를 하네스가 직접 실행한다."""
        results = check_all(
            task.preconditions, self.policy, self.repo, self.config.ac_timeout_s
        )
        for result in results:
            if result.decision is not None:
                self.store.append(
                    EventType.COMMAND_POLICY_DECISION,
                    result.decision.to_payload(),
                    task.id,
                    attempt,
                )
            self.store.append(
                EventType.PRECONDITION_CHECKED, result.to_payload(), task.id, attempt
            )

        if any(_denied(result.decision) for result in results):
            return AttemptOutcome(None, "policy_denied_at_runtime", State.NEEDS_REPLAN)

        failed = [result for result in results if not result.ok]
        if failed:
            return AttemptOutcome(Verdict.BLOCKED, f"prerequisite: {failed[0].name}")

        container = self._container_for(task)
        if self._profile(task) is ExecutionProfile.CONTAINER:
            if container is None:
                return AttemptOutcome(
                    Verdict.ERROR,
                    "container 프로파일인데 config 에 container 블록이 없다",
                )
            if shutil.which(container.runtime) is None:
                return AttemptOutcome(
                    Verdict.BLOCKED,
                    f"container runtime: {container.runtime} 을(를) 찾을 수 없다",
                )

        report = self._adapter_for(task).preflight()
        if report.ok:
            return None
        if report.kind is PreflightKind.MISSING_PREREQUISITE:
            # docs/05 — container 에서 agent CLI 는 이미지 안에 있고 호스트에 없을 수 있다.
            if container is not None:
                return None
            return AttemptOutcome(Verdict.BLOCKED, report.detail)
        return AttemptOutcome(Verdict.ERROR, report.detail)

    def _profile(self, task: Task) -> ExecutionProfile:
        return task.profile or self.config.default_profile

    def _container_for(self, task: Task) -> Any:
        """docs/05 — spec 은 container 프로파일에서만 요청에 실린다."""
        if self._profile(task) is not ExecutionProfile.CONTAINER:
            return None
        return self.config.container

    def _post(self, task: Task, attempt: int, baseline, cwd: Path | str):
        """AC post 실행과 기록. 리뷰의 fixer 뒤에도 같은 경로로 다시 실행된다 (docs/06)."""
        differentials = verify_module.run_post(
            task, baseline, self.policy, cwd, self.config.ac_timeout_s
        )
        for differential in differentials:
            self.store.append(
                EventType.AC_POST_EXECUTED,
                differential.post_payload(),
                task.id,
                attempt,
            )
            if differential.outcome == "debt_closed":
                self.store.append(
                    EventType.DEBT_CLOSED,
                    {"debt_id": _debt_id(differential.after.cmd), "closed_by": task.id},
                    task.id,
                    attempt,
                )
        return differentials

    def _baseline(self, task: Task, attempt: int, cwd: Path, criteria=None):
        """`criteria` 는 실행할 AC 를 좁힌다 — TDD 의 단계 분할이 쓴다 (docs/06)."""
        selected = tuple(task.acceptance if criteria is None else criteria)
        baseline = verify_module.run_baseline(
            task, self.policy, cwd, self.config.ac_timeout_s, selected
        )
        indexes = _criterion_indexes(task.acceptance, selected)
        for criterion_index, observed in zip(indexes, baseline.observations):
            self.store.append(
                EventType.COMMAND_POLICY_DECISION,
                observed.decision.to_payload(),
                task.id,
                attempt,
            )
            payload = observed.baseline_payload()
            payload["criterion_index"] = criterion_index
            self.store.append(EventType.AC_BASELINE_EXECUTED, payload, task.id, attempt)

        if any(_denied(observed.decision) for observed in baseline.observations):
            # analyze 가 사전에 잡았어야 한다. 런타임 도달은 task 정의 결함이다.
            return baseline, AttemptOutcome(
                None, "policy_denied_at_runtime", State.NEEDS_REPLAN
            )

        if baseline.not_discriminating:
            return baseline, AttemptOutcome(
                None, "ac_not_discriminating", State.NEEDS_REPLAN
            )

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
        adapter_name: str | None = None,
    ) -> AgentResult:
        outbox = outbox or workspace.outbox(attempt, repair)
        outbox.mkdir(parents=True, exist_ok=True)
        adapter = self._adapter_for(task, adapter_name)
        request = AgentRequest(
            task_id=task.id,
            prompt=prompt + _outbox_footer(outbox),
            workspace=workspace.path,
            outbox=outbox,
            profile=self._profile(task),
            container=self._container_for(task),
            allowed_tools=None,
            timeout_s=self.config.agent_timeout_s,
            env=self._env(task, outbox, attempt),
            attempt=attempt,
        )

        # Hard cap 은 모든 호출 경로의 실제 agent 시작 직전에 확인한다. attempt 시작의
        # 선행 검사는 빠른 종료용일 뿐이며, 다단계 실행 사이의 소비량도 여기서 잡는다.
        spent = self._over_budget()
        if spent:
            raise _BudgetExhausted(spent)

        self.store.append(
            EventType.AGENT_STARTED,
            {
                "adapter": adapter.name,
                "workspace": str(workspace.path),
                "outbox": str(outbox),
            },
            task.id,
            attempt,
        )
        result = adapter.execute(request)
        transcript = write_transcript(
            self.store.run_dir / "tasks" / task.id, outbox.name, adapter.name, result
        )
        self.store.append(
            EventType.AGENT_FINISHED,
            {
                "exit_code": result.exit_code,
                "duration_s": result.duration_s,
                "usage": _usage(result),
                "runtime_failure": str(result.runtime_failure)
                if result.runtime_failure
                else None,
                "transcript_ref": str(transcript),
            },
            task.id,
            attempt,
        )
        if result.exit_code != 0:
            # 증거 하나일 뿐이다. 판정을 오염시키지 않는다 (docs/06).
            self.store.append(
                EventType.AGENT_EXIT_NONZERO,
                {
                    "exit_code": result.exit_code,
                    "stderr_tail": result.stderr[-STDERR_TAIL_CHARS:],
                },
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
            self.store.append(
                EventType.HANDOFF_MISSING, gate.missing_payload(), task.id, attempt
            )
            if repairs >= self.config.max_handoff_repairs:
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(None, "handoff_missing", State.HUMAN_REQUIRED)

            repairs += 1
            spent = self._over_budget()
            if spent:
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(Verdict.BUDGET_EXHAUSTED, spent)
            self.store.append(
                EventType.FIXER_DISPATCHED,
                {"wave": repairs, "scope": "handoff"},
                task.id,
                attempt,
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
            return AttemptOutcome(
                None, "integration_conflict", State.INTEGRATION_CONFLICT
            )
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
            "output": handoff_module.merge_output(
                task, evidence.diff.to_output(), handoff
            ),
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

        sections = [
            _read(self.repo / HARNESS_DIR / "constitution.md"),
            _task_card(task),
        ]
        for relative in task.context.get("files") or ():
            body = _read(workspace.path / relative)
            if body:
                sections.append(f"## {relative}\n\n```\n{body}\n```")
        sections.append("## 산출물\n\n" + handoff_module.output_contract(task.id))
        path.write_text("\n\n".join(s for s in sections if s) + "\n", encoding="utf-8")
        return path, None

    # ----------------------------------------------------------------- 보조

    def _adapter_for(self, task: Task, name: str | None = None) -> Any:
        """`name` 은 옵션 레이어가 다른 어댑터를 지정하는 확장점이다 (docs/06 의 adversarial)."""
        if self._fixed_adapter is not None:
            return self._fixed_adapter
        name = name or task.agent or self.config.default_adapter
        adapter = self._adapters.get(name)
        if adapter is None:
            entry = self.config.adapters[name]
            # 워커 스레드가 겹치면 setdefault 가 먼저 넣은 쪽을 이긴다. 두 번 만드는 것은
            # 낭비일 뿐 오류가 아니다.
            adapter = self._adapters.setdefault(
                name, build_adapter(name, entry.type, entry.options)
            )
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


def write_transcript(
    task_dir: Path, label: str, adapter_name: str, result: AgentResult
) -> Path:
    """agent 의 stdout/stderr 를 dispatch 단위로 남긴다 (docs/03 의 파일 배치).

    `label` 은 그 dispatch 의 outbox 디렉토리 이름이다 — 한 task 가 여러 번 dispatch
    되므로 이름이 겹치면 무엇을 보고 있는지 알 수 없다. 판정에는 쓰지 않는다. 조용히
    실패한 agent(권한 거부, 턴 소진)를 사후에 진단할 길이 stdout 밖에 없어서 남긴다.
    """
    directory = task_dir / "transcript"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}.log"
    failure = str(result.runtime_failure) if result.runtime_failure else "none"
    header = (
        f"# adapter={adapter_name} exit={result.exit_code} "
        f"duration_s={result.duration_s:.3f} runtime_failure={failure}"
    )
    path.write_text(
        "\n".join([header, "## stdout", result.stdout, "## stderr", result.stderr, ""]),
        encoding="utf-8",
    )
    return path


def _outbox_footer(outbox: Path) -> str:
    """docs/04 Outbox 규약 — 경로는 환경변수와 **프롬프트 말미의 절대 경로** 로 전달된다.

    도구 allowlist 가 좁은 CLI agent 는 자기 환경변수를 읽을 수 없다. 모든 dispatch
    (구현·리뷰·fixer·repair)가 이 한 곳을 지나므로 여기서 붙인다.
    """
    return f"\n## Outbox\n\n`$HARNESS_OUTBOX` = `{outbox}` — 산출물은 이 디렉토리에 쓴다.\n"


def _outbox_artifacts(outbox: Path) -> tuple[Path | None, Path | None]:
    """docs/04 의 outbox 규약 — 파일 이름이 고정이므로 재개할 때 어댑터 없이 찾을 수 있다."""
    claim, handoff = outbox / "result.json", outbox / "handoff.json"
    return (claim if claim.is_file() else None, handoff if handoff.is_file() else None)


def _criterion_indexes(
    all_criteria: Sequence[Any], selected: Sequence[Any]
) -> tuple[int, ...]:
    """부분 baseline 의 각 관측을 task acceptance 의 occurrence index 에 대응시킨다."""
    remaining = list(enumerate(all_criteria))
    indexes = []
    for criterion in selected:
        for position, (index, candidate) in enumerate(remaining):
            if candidate == criterion:
                indexes.append(index)
                remaining.pop(position)
                break
        else:  # pragma: no cover - 내부 호출자가 task 밖의 criterion 을 넘긴 프로그래밍 오류
            raise ValueError("baseline criterion 이 task.acceptance 에 없다")
    return tuple(indexes)


def _baseline_key(payload: Mapping[str, Any]) -> tuple[tuple[str, ...], bool]:
    return tuple(payload["cmd"]), bool(payload["expect_fail_before"])


def _valid_criterion_index(payload: Mapping[str, Any], task: Task) -> bool:
    index = payload.get("criterion_index")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index < len(task.acceptance)
    ):
        return False
    criterion = task.acceptance[index]
    return _baseline_key(payload) == (
        tuple(criterion.cmd),
        criterion.expect_fail_before,
    )


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
