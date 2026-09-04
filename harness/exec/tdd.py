"""TDD 강제 모드 — test-author / red gate / implementation. docs/06 이 canonical 이다.

옵션 모듈이다 (docs/02). 커널은 이 모듈을 import 하지 않고, cli 가 조립해 주입한다.
스테이지가 없는 설치본에서 `development.mode: tdd` 인 task 는 커널이 fail-closed 로
verdict `error` 를 준다 — 검증할 수 없는 모드를 조용히 표준 모드로 돌리지 않는다.

여기서 새로 판정하는 것은 하나도 없다. 하는 일은 **기존 판정을 언제 어느 기준으로
돌릴지 정하는 것**뿐이다.

- red gate 는 `expect_fail_before` AC 의 baseline 을 테스트가 존재하는 시점에 재는 것이고,
  이미 통과하면 기존 `ac_not_discriminating` 이 그대로 걸린다.
- 단계 스코프는 기존 `check_paths` 에 다른 `allowed`·`forbidden` 을 넣는 것이다.
- green gate 는 기존 차등 판정이다. 이 모듈에 green gate 코드가 없는 이유다.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from harness.config import Config
from harness.events import EventType
from harness.exec import verify as verify_module
from harness.exec.runner import AttemptOutcome, ResumePoint, Runner
from harness.exec.workspace import Workspace
from harness.models import State, Task, Verdict

TEST_AUTHOR = "test_author"
RED_GATE = "red_gate"
IMPLEMENTATION = "implementation"

EMPTY = verify_module.DiffObservation((), (), {})
NOTHING = (None, None)  # agent 가 아무것도 내놓지 않았다


@dataclass(frozen=True)
class Phased:
    """단계 실행의 결과. 커널의 단일 디스패치 자리를 그대로 대신한다."""

    baseline: verify_module.Baseline | None = None
    before: verify_module.DiffObservation = EMPTY
    outbox: tuple[Path | None, Path | None] = NOTHING
    stop: AttemptOutcome | None = None


class TddStage:
    def __init__(self, repo: Path | str, config: Config) -> None:
        self.repo = Path(repo)
        self.config = config

    # ----------------------------------------------------------------- 진입점

    def run(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        task_dir: Path,
        base: str | None,
        prompt: str,
        resume: ResumePoint,
    ) -> Phased:
        """docs/06 의 다섯 단계. [5] post 는 커널이 돌려받은 baseline 으로 한다."""
        if workspace.branch is None:
            # 단계 경계를 커밋으로 고정할 수 없으면 red 증거를 고정할 수 없다 (docs/06).
            return Phased(stop=AttemptOutcome(Verdict.ERROR, "tdd_requires_worktree"))

        red_criteria = [c for c in task.acceptance if c.expect_fail_before]
        if not red_criteria:
            return Phased(
                stop=AttemptOutcome(None, "ac_not_discriminating", State.NEEDS_REPLAN)
            )

        if resume.from_implementation:
            outbox, stop = self._implementation(
                runner, task, attempt, workspace, task_dir, prompt
            )
            self._record(
                runner,
                task,
                attempt,
                IMPLEMENTATION,
                self._red_base(runner, task, attempt),
                stop,
            )
            return Phased(
                runner._baseline_from_journal(task, attempt), EMPTY, outbox, stop
            )

        # [1] baseline — expect_fail_before 가 아닌 AC 만. 나머지는 red gate 에서 잰다.
        baseline, stop = runner._baseline(
            task,
            attempt,
            workspace.path,
            criteria=[c for c in task.acceptance if not c.expect_fail_before],
        )
        if stop:
            return Phased(baseline, stop=stop)

        before = verify_module.observe_diff(
            workspace.path, runner._harness_paths(workspace.path), base=base
        )

        # [2] test-author
        authoring = self._test_author_prompt(task, prompt)
        self._write_prompt(task_dir, TEST_AUTHOR, authoring)
        result = runner._dispatch(
            task,
            attempt,
            workspace,
            authoring,
            outbox=self._outbox(workspace, attempt, TEST_AUTHOR),
        )
        if result.runtime_failure is not None:
            return Phased(baseline, before, stop=_runtime_error(result))

        stop = self._check_test_author(runner, task, attempt, workspace, base, before)
        red_base = None
        if stop is None:
            red_base = runner.workspaces.checkpoint(
                workspace, f"harness: {task.id} — tests (red)"
            )
            if red_base is None:
                stop = AttemptOutcome(Verdict.ERROR, "tdd_checkpoint_failed")
        self._record(runner, task, attempt, TEST_AUTHOR, red_base, stop)
        if stop:
            return Phased(baseline, before, stop=stop)

        # [3] red gate — expect_fail_before AC 의 baseline 을 여기서 잰다
        red, stop = runner._baseline(
            task, attempt, workspace.path, criteria=red_criteria
        )
        stop = stop or _unexecuted(red)
        self._record(runner, task, attempt, RED_GATE, red_base, stop)
        baseline = _merged(task, baseline, red)
        if stop:
            return Phased(baseline, before, stop=stop)

        # [4] implementation
        outbox, stop = self._implementation(
            runner, task, attempt, workspace, task_dir, prompt
        )
        self._record(runner, task, attempt, IMPLEMENTATION, red_base, stop)
        return Phased(baseline, before, outbox, stop)

    def resumes_at_implementation(
        self, runner: Runner, task: Task, attempt: int
    ) -> bool:
        """docs/10 의 TDD 예외 — red 증거와 baseline 이 journal 에 온전히 있어야 한다."""
        return (
            self._red_base(runner, task, attempt) is not None
            and runner._baseline_from_journal(task, attempt) is not None
        )

    def implementation_completed(
        self, runner: Runner, task: Task, attempt: int
    ) -> bool:
        """구현 agent 가 정상 반환했고 완료 이벤트까지 durable 하게 남았는지.

        초기 구현은 완료 이벤트를 agent 호출 *전*에 기록했다. 그 journal 과도 안전하게
        호환하려면 이벤트 존재뿐 아니라 red gate 뒤의 agent_finished 가 완료 이벤트보다
        앞서는 순서까지 확인해야 한다.
        """
        red = self._phase_event(runner, task, attempt, RED_GATE)
        completed = self._phase_event(runner, task, attempt, IMPLEMENTATION)
        if (
            red is None
            or completed is None
            or runner._baseline_from_journal(task, attempt) is None
        ):
            return False
        return any(
            event.type is EventType.AGENT_FINISHED
            and event.task_id == task.id
            and event.attempt == attempt
            and red.seq < event.seq < completed.seq
            for event in runner.store.journal.read()
        )

    def phase_violations(
        self, runner: Runner, task: Task, attempt: int, workspace: Workspace
    ) -> tuple[verify_module.PathViolation, ...]:
        """implementation 단계의 스코프 판정 (docs/06).

        관측 기준이 red gate 커밋이므로 테스트의 수정·삭제가 여기서 드러난다. agent 가
        커밋으로 감춰도 같은 diff 로 관측된다.
        """
        red_base = self._red_base(runner, task, attempt)
        if red_base is None:
            return ()  # red gate 전에 끝난 attempt — 볼 단계가 없다
        diff = verify_module.observe_diff(
            workspace.path, runner._harness_paths(workspace.path), base=red_base
        )
        return verify_module.check_paths(
            diff,
            task.development.implementation_paths,
            (*runner._forbidden(task), *task.development.test_paths),
        )

    # ----------------------------------------------------------------- 단계

    def _implementation(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        task_dir: Path,
        prompt: str,
    ):
        """둘째 디스패치. outbox 는 기본 이름이라 재개가 어댑터 없이 산출물을 찾는다."""
        implementing = self._implementation_prompt(task, prompt)
        self._write_prompt(task_dir, IMPLEMENTATION, implementing)
        result = runner._dispatch(task, attempt, workspace, implementing)
        stop = _runtime_error(result) if result.runtime_failure is not None else None
        return (result.raw_claim_path, result.raw_handoff_path), stop

    def _check_test_author(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        base: str | None,
        before: verify_module.DiffObservation,
    ) -> AttemptOutcome | None:
        """1단계의 스코프 판정 — 구현 경로는 금지다 (docs/06)."""
        diff = verify_module.observe_diff(
            workspace.path, runner._harness_paths(workspace.path), base=base
        ).without(before)
        if diff.is_empty:
            return AttemptOutcome(Verdict.REJECTED, "tdd_no_tests")

        violations = verify_module.check_paths(
            diff,
            task.development.test_paths,
            (*runner._forbidden(task), *task.development.implementation_paths),
        )
        for violation in violations:
            runner.store.append(
                EventType.PATH_VIOLATION, violation.to_payload(), task.id, attempt
            )
        return (
            AttemptOutcome(Verdict.REJECTED, "path_violation") if violations else None
        )

    # ----------------------------------------------------------------- 기록

    def _record(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        phase: str,
        base: str | None,
        stop: AttemptOutcome | None,
    ) -> None:
        """docs/03 — tdd_phase_completed. state 를 바꾸지 않는 증거 이벤트다."""
        runner.store.append(
            EventType.TDD_PHASE_COMPLETED,
            {
                "phase": phase,
                "base": base,
                "ok": stop is None,
                "detail": stop.reason if stop else None,
            },
            task.id,
            attempt,
        )

    def _red_base(self, runner: Runner, task: Task, attempt: int) -> str | None:
        """통과한 red gate 가 남긴 관측 기준. 없으면 이 attempt 는 거기까지 가지 않았다."""
        event = self._phase_event(runner, task, attempt, RED_GATE)
        return event.payload["base"] if event is not None else None

    @staticmethod
    def _phase_event(runner: Runner, task: Task, attempt: int, phase: str):
        """성공한 phase 의 최신 이벤트. 실패 이벤트는 재개 증거가 아니다."""
        for event in reversed(runner.store.journal.read()):
            if (
                event.type is EventType.TDD_PHASE_COMPLETED
                and event.task_id == task.id
                and event.attempt == attempt
                and event.payload["phase"] == phase
                and event.payload["ok"]
            ):
                return event
        return None

    @staticmethod
    def _outbox(workspace: Workspace, attempt: int, phase: str) -> Path:
        """단계마다 다른 outbox 다. 같으면 transcript 이름이 겹쳐 무엇을 보는지 알 수 없다."""
        return (
            workspace.root / "outbox" / f"attempt-{attempt}-{phase.replace('_', '-')}"
        )

    @staticmethod
    def _write_prompt(task_dir: Path, phase: str, prompt: str) -> None:
        (task_dir / f"prompt.{phase.replace('_', '-')}.md").write_text(
            prompt, encoding="utf-8"
        )

    # ----------------------------------------------------------------- 프롬프트

    def _test_author_prompt(self, task: Task, prompt: str) -> str:
        return prompt + (
            "\n## TDD — 1단계: 테스트만 쓴다\n\n"
            "이 디스패치에서는 **테스트만** 쓴다. 구현을 건드리면 하네스가 diff 로 탐지하고 "
            "이 attempt 는 rejected 된다. 프롬프트가 아니라 증거가 판정한다.\n\n"
            f"- 쓸 수 있는 경로: {_join(task.development.test_paths)}\n"
            f"- 건드리면 안 되는 경로: {_join(task.development.implementation_paths)}\n"
            "- 이 단계가 끝나면 하네스가 다음 커맨드를 직접 실행하고 **전부 실패해야** "
            "구현 단계로 간다:\n"
            f"{_commands(task)}\n"
            "- 이미 통과하는 테스트는 아무것도 증명하지 못한다. 실패하는 테스트를 쓴다.\n"
        )

    def _implementation_prompt(self, task: Task, prompt: str) -> str:
        return prompt + (
            "\n## TDD — 2단계: 구현으로 테스트를 통과시킨다\n\n"
            "1단계의 테스트는 커밋되어 고정되었고, 하네스는 그 커밋 대비로 diff 를 본다. "
            "**테스트를 고치거나 지우면** 커밋해서 감춰도 탐지되고 이 attempt 는 rejected 된다. "
            "구현으로 통과시킨다.\n\n"
            f"- 쓸 수 있는 경로: {_join(task.development.implementation_paths)}\n"
            f"- 건드리면 안 되는 경로: {_join(task.development.test_paths)}\n"
            "- 하네스가 다시 실행할 커맨드:\n"
            f"{_commands(task)}\n"
        )


def _merged(task: Task, *parts: verify_module.Baseline) -> verify_module.Baseline:
    """조각난 baseline 을 AC 순서로 되돌린다. 합집합은 AC 하나당 관측 하나다 (docs/06)."""
    observed = defaultdict(deque)
    for part in parts:
        for observation in part.observations:
            observed[(observation.cmd, observation.expect_fail_before)].append(
                observation
            )
    return verify_module.Baseline(
        tuple(
            observed[(tuple(criterion.cmd), criterion.expect_fail_before)].popleft()
            for criterion in task.acceptance
        )
    )


def _unexecuted(red: verify_module.Baseline) -> AttemptOutcome | None:
    """실행되지 않은 커맨드는 red 증거가 아니다 (docs/06).

    분류는 docs/10 의 경계를 따른다 — 승인만 하면 풀리는 것은 `blocked`, 실행 자체가
    성립하지 않은 것은 `error` 다.
    """
    for observed in red.observations:
        if observed.exit_code is not None:
            continue
        name = " ".join(observed.cmd)
        if observed.decision is not None and not observed.decision.may_execute:
            return AttemptOutcome(Verdict.BLOCKED, f"unapproved_command: {name}")
        return AttemptOutcome(Verdict.ERROR, f"tdd_red_gate_unexecuted: {name}")
    return None


def _runtime_error(result: Any) -> AttemptOutcome:
    return AttemptOutcome(Verdict.ERROR, str(result.runtime_failure))


def _join(patterns: Sequence[str]) -> str:
    return ", ".join(patterns) or "(없음)"


def _commands(task: Task) -> str:
    return "\n".join(
        f"    {' '.join(criterion.cmd)}"
        for criterion in task.acceptance
        if criterion.expect_fail_before
    )
