"""arm 별 실행과 채점. docs/11 이 canonical 이다.

**M1 이 만드는 것은 회귀 eval 러너뿐이다.** `mock` 어댑터로 돌고, 완전히 결정론적이며,
비용이 0 이고, CI 에서 매번 돈다. `raw` 와 능력 eval 은 M7 이다 — 섞으면 CI 가
비결정론적이 되거나 능력 측정이 mock 시나리오의 재확인이 된다.

회귀 eval 이 묻는 것은 "오케스트레이션이 규약대로 동작했는가" 이므로, 채점 대상은
산출된 코드가 아니라 journal 이 만든 최종 state 다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from harness.config import load as load_config
from harness.dag import Dag, load_tasks
from harness.eval.fixtures import Fixture, materialize
from harness.exec.runner import run_dag
from harness.models import RunState

REGRESSION_ARM = "harness-full"


@dataclass(frozen=True)
class ArmResult:
    fixture: str
    arm: str
    repo: Path
    state: RunState
    mismatches: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches


def run_regression(fixture: Fixture, workdir: Path | str) -> ArmResult:
    repo = materialize(fixture, Path(workdir) / fixture.name)
    config = load_config(repo)
    # 워크스페이스는 저장소 밖이어야 한다. 작업 디렉토리 옆에 두면 eval 이 자기 뒷정리를 한다.
    store = run_dag(
        repo, config, Dag(load_tasks(repo)), scratch=Path(workdir) / f"{fixture.name}-scratch"
    )
    return ArmResult(
        fixture=fixture.name,
        arm=REGRESSION_ARM,
        repo=repo,
        state=store.state,
        mismatches=grade(store.state, fixture.expected),
    )


def grade(state: RunState, expected: Mapping[str, Any]) -> tuple[str, ...]:
    """docs/11 의 `expected.yaml`. 적히지 않은 키는 채점하지 않는다."""
    mismatches: list[str] = []

    for task_id, claim in (expected.get("tasks") or {}).items():
        task = state.tasks.get(task_id)
        if task is None:
            mismatches.append(f"{task_id}: run 에 없다")
            continue
        observed = {"verdict": _text(task.verdict), "state": _text(task.state)}
        for key, want in claim.items():
            if observed.get(key) != want:
                mismatches.append(f"{task_id}.{key}: {observed.get(key)!r} ≠ {want!r}")

    if "open_debts" in expected:
        observed = sorted(" ".join(debt.cmd) for debt in state.open_debts.values())
        want = sorted(expected["open_debts"] or ())
        if observed != want:
            mismatches.append(f"open_debts: {observed} ≠ {want}")

    if "human_required" in expected:
        observed = sorted(state.human_required)
        want = sorted(expected["human_required"] or ())
        if observed != want:
            mismatches.append(f"human_required: {observed} ≠ {want}")

    return tuple(mismatches)


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None
