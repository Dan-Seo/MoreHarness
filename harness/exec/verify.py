"""AC 실행, diff 관측, 차등 판정. docs/06 이 canonical 이다.

> Agents propose. Harness verifies. **Evidence decides.**

이 모듈에는 agent 가 만든 것이 하나도 들어오지 않는다. AC 는 하네스가 직접 실행하고,
diff 는 하네스가 직접 읽는다. agent 의 exit code 도 자기 보고도 판정의 입력이 아니다.

**여기서 다루는 것은 docs/06 의 verified 조건 1·2·3이다.** 조건 4(리뷰 blocking
finding)는 M5 가 이 자리에 더한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.git import git
from harness.models import State, Task, TaskKind, Verdict
from harness.paths import matches as _matches
from harness.policy import CommandPolicy, Decision

GREEN_BEFORE = "green_before"
RED_BEFORE = "red_before"
GREEN = "green"
RED = "red"

STDERR_TAIL_CHARS = 2000


@dataclass(frozen=True)
class AcObservation:
    """AC 하나에 대해 하네스가 관측한 것.

    `exit_code` 가 `None` 이면 종료 코드가 없었다는 뜻이다 — 정책이 막았거나 타임아웃이거나
    프로세스를 띄우지 못했다. AC 는 하네스가 실행하므로 여기서 exit code 는 곧 관측치다.
    """

    cmd: tuple[str, ...]
    expect_fail_before: bool
    exit_code: int | None
    classification: str
    timed_out: bool
    stderr_tail: str
    decision: Decision | None = None

    @property
    def green(self) -> bool:
        return self.classification in (GREEN, GREEN_BEFORE)

    def baseline_payload(self) -> dict[str, Any]:
        """docs/03 — ac_baseline_executed."""
        return {
            "cmd": list(self.cmd),
            "exit_code": self.exit_code,
            "classification": self.classification,
            "expect_fail_before": self.expect_fail_before,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> AcObservation:
        """journal 의 `ac_baseline_executed` 에서 복원한다 (docs/10 의 재개).

        차등 판정에 필요한 것은 분류와 선언뿐이다. 정책 판정과 stderr 는 이미 journal 에
        기록되어 있으므로 여기서 다시 나르지 않는다.
        """
        return cls(
            cmd=tuple(payload["cmd"]),
            expect_fail_before=bool(payload["expect_fail_before"]),
            exit_code=payload["exit_code"],
            classification=payload["classification"],
            timed_out=False,
            stderr_tail="",
        )


@dataclass(frozen=True)
class Baseline:
    observations: tuple[AcObservation, ...]

    @property
    def not_discriminating(self) -> tuple[AcObservation, ...]:
        """expect_fail_before 인데 이미 통과한다. 무엇을 바꾸든 통과하므로 검증력이 없다."""
        return tuple(o for o in self.observations if o.expect_fail_before and o.green)

    @property
    def pre_existing(self) -> tuple[AcObservation, ...]:
        """이 task 의 책임이 아닌 기존 실패. debt 원장에 올라간다."""
        return tuple(o for o in self.observations if not o.expect_fail_before and not o.green)


@dataclass(frozen=True)
class Differential:
    """docs/06 의 차등 판정표 한 줄. task 는 자기가 바꾼 것으로만 평가된다."""

    before: AcObservation
    after: AcObservation
    outcome: str

    @property
    def rejects(self) -> bool:
        return self.outcome in ("regression", "unmet")

    def post_payload(self) -> dict[str, Any]:
        """docs/03 — ac_post_executed."""
        return {
            "cmd": list(self.after.cmd),
            "exit_code": self.after.exit_code,
            "classification": self.after.classification,
            "differential": self.outcome,
        }


@dataclass(frozen=True)
class DiffObservation:
    """docs/03 의 하네스 산출 필드. agent 협조가 필요 없으므로 항상 존재한다."""

    changed_files: tuple[str, ...]
    created_files: tuple[str, ...]
    diff_stat: Mapping[str, int]

    @property
    def is_empty(self) -> bool:
        return not self.changed_files and not self.created_files

    def to_output(self) -> dict[str, Any]:
        return {
            "changed_files": list(self.changed_files),
            "created_files": list(self.created_files),
            "diff_stat": dict(self.diff_stat),
        }

    def without(self, earlier: DiffObservation) -> DiffObservation:
        """앞선 관측에 이미 있던 변경을 뺀다.

        `safe` 프로파일에는 워크트리 분리가 없어서 한 저장소에 변경이 쌓인다. 그러면 diff 를
        어느 task 에 귀속시킬지가 모호해지고 판정이 무의미해진다. `worktree` 프로파일은
        task 마다 깨끗한 워크트리에서 시작하므로 뺄 것이 없다.
        """
        return DiffObservation(
            tuple(p for p in self.changed_files if p not in earlier.changed_files),
            tuple(p for p in self.created_files if p not in earlier.created_files),
            {k: max(0, v - earlier.diff_stat.get(k, 0)) for k, v in self.diff_stat.items()},
        )


@dataclass(frozen=True)
class PathViolation:
    """허용 범위를 벗어난 변경. docs/05 — 판정은 사후이며 **탐지**이지 OS 강제가 아니다."""

    rule: str  # allowed_paths | forbidden_paths
    paths: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        """docs/03 — path_violation."""
        return {"paths": list(self.paths), "rule": self.rule}


@dataclass(frozen=True)
class Evidence:
    """증거 조건의 결과.

    `verdict` 가 `None` 이고 `next_state` 도 `None` 이면 증거는 전부 통과했다는 뜻이며,
    그다음은 handoff 게이트다. **`verified` 는 여기서 기록되지 않는다** (docs/06).
    """

    diff: DiffObservation
    differentials: tuple[Differential, ...]
    verdict: Verdict | None = None
    reason: str | None = None
    next_state: State | None = None


def run_baseline(task: Task, policy: CommandPolicy, cwd: Path | str, timeout_s: int) -> Baseline:
    """agent 실행 전, 하네스가 직접 실행한다."""
    return Baseline(
        tuple(
            _observe(criterion, policy, cwd, timeout_s, GREEN_BEFORE, RED_BEFORE)
            for criterion in task.acceptance
        )
    )


def run_post(
    task: Task,
    baseline: Baseline,
    policy: CommandPolicy,
    cwd: Path | str,
    timeout_s: int,
) -> tuple[Differential, ...]:
    """agent 실행 후 모든 AC 를 다시 실행하고 baseline 과 짝지어 차등 판정한다."""
    differentials = []
    for criterion, before in zip(task.acceptance, baseline.observations):
        after = _observe(criterion, policy, cwd, timeout_s, GREEN, RED)
        differentials.append(Differential(before, after, _outcome(before, after)))
    return tuple(differentials)


def observe_diff(cwd: Path | str, exclude: Sequence[str] = ()) -> DiffObservation:
    """작업 트리에서 하네스가 직접 읽은 변경. agent 의 보고를 쓰지 않는다.

    `exclude` 는 **하네스 자신이 쓴 경로**다. `safe` 프로파일에서는 하네스와 agent 가
    한 디렉토리를 쓰므로, 하네스가 남긴 journal 과 아티팩트를 task 의 변경으로 세면
    모든 task 가 control-plane 을 건드린 것이 된다.
    """
    status = git(["status", "--porcelain", "--untracked-files=all"], cwd=cwd)
    changed, created = [], []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], _status_path(line[3:])
        if _any_match(path, exclude):
            continue
        (created if code in ("??", "A ", "AM") else changed).append(path)

    return DiffObservation(tuple(changed), tuple(created), _numstat(cwd, len(created)))


def matches(path: str, pattern: str) -> bool:
    """docs/05 의 glob 방언. 구현은 `paths` 가 갖는다 — `risk` 도 같은 방언을 쓴다."""
    return _matches(path, pattern)


def check_paths(
    diff: DiffObservation,
    allowed: Sequence[str],
    forbidden: Sequence[str],
) -> tuple[PathViolation, ...]:
    """docs/05 의 경로 스코프 판정. `allowed` 가 비어 있으면 허용 범위에 제한이 없다."""
    changed = (*diff.changed_files, *diff.created_files)

    outside = tuple(p for p in changed if allowed and not _any_match(p, allowed))
    banned = tuple(p for p in changed if _any_match(p, forbidden))

    violations = []
    if banned:
        violations.append(PathViolation("forbidden_paths", banned))
    if outside:
        violations.append(PathViolation("allowed_paths", outside))
    return tuple(violations)


def judge(
    task: Task,
    diff: DiffObservation,
    differentials: Sequence[Differential],
    violations: Sequence[PathViolation] = (),
) -> Evidence:
    """docs/06 의 verified 조건. 통과해도 verdict 를 기록하지 않는다 — terminal 이 남았다."""
    differentials = tuple(differentials)
    rejecting = [d for d in differentials if d.rejects]

    if task.kind is TaskKind.IMPLEMENTATION and diff.is_empty:
        if not rejecting:
            # 바꾼 것이 없는데 AC 가 전부 통과한다. AC 에 검증력이 없다는 신호다.
            return Evidence(diff, differentials, None, "no_op_detected", State.NEEDS_REPLAN)
        return Evidence(diff, differentials, Verdict.REJECTED, rejecting[0].outcome)

    if task.kind is TaskKind.READONLY and not diff.is_empty:
        return Evidence(diff, differentials, Verdict.REJECTED, "unexpected_diff")

    if violations:
        return Evidence(diff, differentials, Verdict.REJECTED, "path_violation")

    if rejecting:
        return Evidence(diff, differentials, Verdict.REJECTED, rejecting[0].outcome)

    return Evidence(diff, differentials)


def _observe(
    criterion,
    policy: CommandPolicy,
    cwd: Path | str,
    timeout_s: int,
    green: str,
    red: str,
) -> AcObservation:
    result = policy.run(criterion.cmd, cwd=cwd, timeout_s=timeout_s, shell=criterion.shell)
    passed = result.executed and result.exit_code == 0
    stderr = result.stderr if result.executed else f"Command Policy: {result.decision.verdict}"
    return AcObservation(
        cmd=tuple(criterion.cmd),
        expect_fail_before=criterion.expect_fail_before,
        exit_code=result.exit_code,
        classification=green if passed else red,
        timed_out=result.timed_out,
        stderr_tail=stderr[-STDERR_TAIL_CHARS:],
        decision=result.decision,
    )


def _outcome(before: AcObservation, after: AcObservation) -> str:
    """docs/06 의 차등 판정표."""
    if before.green:
        return "ok" if after.green else "regression"
    if before.expect_fail_before:
        return "proven" if after.green else "unmet"
    # 이 task 가 만들지 않은 실패다. 판정에 영향을 주지 않는다.
    return "debt_closed" if after.green else "debt_kept"


def _any_match(path: str, patterns: Sequence[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def _status_path(raw: str) -> str:
    """`git status --porcelain` 의 경로. 이름이 바뀐 항목은 도착 경로를 쓴다."""
    path = raw.split(" -> ")[-1].strip()
    return path.strip('"')


def _numstat(cwd: Path | str, created_count: int) -> dict[str, int]:
    """추적 중인 파일의 삽입·삭제 줄 수. 새 파일은 numstat 에 없으므로 개수만 더한다."""
    result = git(["diff", "--numstat"], cwd=cwd)
    files = insertions = deletions = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        insertions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files + created_count, "insertions": insertions, "deletions": deletions}
