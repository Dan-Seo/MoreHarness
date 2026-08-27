"""한 task 의 attempt 실행과 DAG 완주. sequential (`max_parallel = 1`).

docs/03 의 상태 기계와 docs/06 의 판정 순서를 그대로 따른다. 이 모듈은 순서를 지키고
이벤트를 남길 뿐, 판정 자체는 하지 않는다 — 그것은 `verify` 와 `handoff` 의 몫이다.

**M1 은 `safe` 프로파일만 실행한다.** 저장소 밖 워크트리는 M2 가 만든다. 제공하지 못하는
격리를 제공한다고 말하지 않는다 (docs/05 의 표현 규약).

쓰기 순서는 언제나 journal append + fsync → state 갱신이다. `store.append` 가 그 순서를
갖고 있으므로 여기서는 이벤트를 남기는 순서만 지키면 된다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
from harness.models import ExecutionProfile, State, Task, Verdict
from harness.policy import CommandPolicy, PolicyVerdict, load_approvals
from harness.probes import check_all
from harness.store import Store

STDERR_TAIL_CHARS = 2000

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
) -> Store:
    """DAG 를 끝까지 실행하고 journal 을 가진 Store 를 돌려준다."""
    repo = Path(repo)
    if config.default_profile is not ExecutionProfile.SAFE:
        raise HarnessError(
            f"M1 은 safe 프로파일만 실행한다. {config.default_profile} 격리는 M2 가 만든다"
        )

    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )
    store = Store(_run_dir(repo, run_id))
    return Runner(repo, config, store, policy, adapter).run(dag)


class Runner:
    def __init__(
        self,
        repo: Path,
        config: Config,
        store: Store,
        policy: CommandPolicy,
        adapter: Any = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.store = store
        self.policy = policy
        self._fixed_adapter = adapter
        self._adapters: dict[str, Any] = {}
        self.scratch = Path(tempfile.gettempdir()) / "harness" / store.run_id

    # ----------------------------------------------------------------- 런 루프

    def run(self, dag: Dag) -> Store:
        self.store.append(
            EventType.RUN_STARTED,
            {
                "manifest": {"task_ids": list(dag.order())},
                "profile": str(ExecutionProfile.SAFE),
                "adapter": self.config.default_adapter,
                "max_parallel": self.config.max_parallel,
            },
        )

        remaining, verified = set(dag.tasks), set()
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
        for attempt in range(1, self.config.max_attempts + 1):
            outcome = self._attempt(task, attempt)
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
            if next_state is not State.READY:
                return next_state is State.DONE
        return False

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

    # ----------------------------------------------------------------- attempt

    def _attempt(self, task: Task, attempt: int) -> AttemptOutcome:
        task_dir = self.store.run_dir / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = self._write_prompt(task, task_dir)

        self.store.append(
            EventType.TASK_DISPATCHED,
            {
                "context_manifest_ref": None,  # 계층형 컨텍스트는 M4 다
                "prompt_ref": str(prompt_path),
                # risk 모듈이 없으면 선언값을 그대로 쓴다 (docs/02 의 옵션 경계)
                "effective_risk": str(task.risk) if task.risk else None,
            },
            task_id=task.id,
            attempt=attempt,
        )

        stop = self._precheck(task, attempt)
        if stop:
            return stop

        baseline, stop = self._baseline(task, attempt)
        if stop:
            return stop

        before = verify_module.observe_diff(self.repo)
        outbox = self.scratch / task.id / "outbox" / f"attempt-{attempt}"
        result = self._dispatch(task, attempt, outbox, prompt_path.read_text(encoding="utf-8"))
        if result.runtime_failure is not None:
            return AttemptOutcome(Verdict.ERROR, str(result.runtime_failure))

        claim = handoff_module.normalize(handoff_module.CLAIM, result.raw_claim_path, task_dir)
        self._record_artifact(claim, task, attempt)
        handoff = handoff_module.normalize(handoff_module.HANDOFF, result.raw_handoff_path, task_dir)
        self._record_artifact(handoff, task, attempt)

        differentials = verify_module.run_post(
            task, baseline, self.policy, self.repo, self.config.ac_timeout_s
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

        diff = verify_module.observe_diff(self.repo).without(before)
        evidence = verify_module.judge(task, diff, differentials)
        if evidence.verdict is not None or evidence.next_state is not None:
            self._write_verification(task_dir, task, evidence, handoff)
            return AttemptOutcome(evidence.verdict, evidence.reason, evidence.next_state)

        return self._handoff_gate(task, attempt, evidence, handoff, task_dir)

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

    def _baseline(self, task: Task, attempt: int):
        baseline = verify_module.run_baseline(
            task, self.policy, self.repo, self.config.ac_timeout_s
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

    def _dispatch(self, task: Task, attempt: int, outbox: Path, prompt: str) -> AgentResult:
        outbox.mkdir(parents=True, exist_ok=True)
        adapter = self._adapter_for(task)
        request = AgentRequest(
            task_id=task.id,
            prompt=prompt,
            workspace=self.repo,
            outbox=outbox,
            profile=ExecutionProfile.SAFE,
            allowed_tools=None,
            timeout_s=self.config.agent_timeout_s,
            env=self._env(task, outbox, attempt),
            attempt=attempt,
        )

        self.store.append(
            EventType.AGENT_STARTED,
            {"adapter": adapter.name, "workspace": str(self.repo), "outbox": str(outbox)},
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

    def _handoff_gate(
        self,
        task: Task,
        attempt: int,
        evidence: verify_module.Evidence,
        handoff: handoff_module.Artifact,
        task_dir: Path,
    ) -> AttemptOutcome:
        """docs/06 — 구현은 이미 증거로 검증되었다. 코드는 그대로 두고 handoff 만 다시 만든다."""
        gate = handoff_module.gate(task, handoff)
        repairs = 0

        while not gate.ok:
            self.store.append(EventType.HANDOFF_MISSING, gate.missing_payload(), task.id, attempt)
            if repairs >= self.config.max_handoff_repairs:
                self._write_verification(task_dir, task, evidence, handoff)
                return AttemptOutcome(None, "handoff_missing", State.HUMAN_REQUIRED)

            repairs += 1
            self.store.append(
                EventType.FIXER_DISPATCHED, {"wave": repairs, "scope": "handoff"}, task.id, attempt
            )
            outbox = self.scratch / task.id / "outbox" / f"attempt-{attempt}-repair-{repairs}"
            result = self._dispatch(task, attempt, outbox, _repair_prompt(task, gate))
            handoff = handoff_module.normalize(
                handoff_module.HANDOFF, result.raw_handoff_path, task_dir
            )
            self._record_artifact(handoff, task, attempt)
            gate = handoff_module.gate(task, handoff)

        self._write_verification(task_dir, task, evidence, handoff)
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
    ) -> None:
        record = {
            "task_id": task.id,
            "output": handoff_module.merge_output(task, evidence.diff.to_output(), handoff),
            "differentials": [d.post_payload() for d in evidence.differentials],
        }
        (task_dir / "verification.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_prompt(self, task: Task, task_dir: Path) -> Path:
        """M1 의 프롬프트는 constitution + task 계약이다.

        계층형 컨텍스트 선택과 예산은 M4 다. 그전까지는 docs/02 가 정한 대로 task 계약에
        명시된 파일만 넣는다. constitution 은 **항상 메인 저장소에서** 읽는다 (docs/03).
        """
        sections = [_read(self.repo / HARNESS_DIR / "constitution.md"), _task_card(task)]
        for relative in task.context.get("files") or ():
            body = _read(self.repo / relative)
            if body:
                sections.append(f"## {relative}\n\n```\n{body}\n```")
        sections.append(
            "## 산출물\n\n"
            "claim 은 `$HARNESS_OUTBOX/result.json`, handoff 는 `$HARNESS_OUTBOX/handoff.json` 이다.\n"
            "둘 다 optional 이며, 하네스는 이 보고가 아니라 자기 관측으로 판정한다."
        )
        path = task_dir / "prompt.md"
        path.write_text("\n\n".join(s for s in sections if s) + "\n", encoding="utf-8")
        return path

    # ----------------------------------------------------------------- 보조

    def _adapter_for(self, task: Task) -> Any:
        if self._fixed_adapter is not None:
            return self._fixed_adapter
        name = task.agent or self.config.default_adapter
        if name not in self._adapters:
            entry = self.config.adapters[name]
            self._adapters[name] = build_adapter(name, entry.type, entry.options)
        return self._adapters[name]

    def _env(self, task: Task, outbox: Path, attempt: int) -> dict[str, str]:
        env = {name: os.environ[name] for name in ENV_PASSTHROUGH if name in os.environ}
        env["HARNESS_OUTBOX"] = str(outbox)
        env["HARNESS_TASK_ID"] = task.id
        env["HARNESS_ATTEMPT"] = str(attempt)
        return env


def _run_dir(repo: Path, run_id: str | None) -> Path:
    runs = repo / HARNESS_DIR / "runs"
    if run_id:
        return runs / run_id
    stamp = new_run_id()
    candidate, suffix = runs / stamp, 1
    while candidate.exists():
        suffix += 1
        candidate = runs / f"{stamp}-{suffix}"
    return candidate


def _denied(decision) -> bool:
    return decision is not None and decision.verdict is PolicyVerdict.DENY


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
