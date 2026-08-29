"""arm 별 실행과 채점. docs/11 이 canonical 이다.

회귀 eval 은 `mock` 으로 돌고 journal 의 최종 state 를 채점한다. 능력 eval 은 실제
어댑터로 돌고 **hidden grader 가 최종 트리를 채점한다** — 채점자는 하네스 밖이어야
`raw` 와의 비교가 성립하기 때문이다. grader 의 AC 는 task acceptance 에도 컨텍스트에도
절대 들어가지 않는다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.adapters.base import AgentRequest
from harness.adapters.registry import build as build_adapter
from harness.config import HARNESS_DIR, Config, load as load_config
from harness.dag import Dag, load_tasks
from harness.errors import HarnessError
from harness.eval.fixtures import GRADER_DIR, GRADER_PLACEHOLDER, Fixture, materialize
from harness.events import EventType
from harness.exec.runner import ENV_PASSTHROUGH, run_dag
from harness.exec.verify import GREEN, RED
from harness.exec.workspace import integration_branch
from harness.git import git
from harness.models import RunState, Verdict
from harness.policy import CommandPolicy, load_approvals
from harness.store import Store

REGRESSION_ARM = "harness-full"

# 능력 eval 의 arm (docs/11). ablation:<feature> 는 harness-full 에서 하나만 뺀다.
ABLATION_PREFIX = "ablation:"
ABLATABLE = ("context", "review", "parallel")


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


# --------------------------------------------------------------------------- hidden grader


@dataclass(frozen=True)
class GraderCheck:
    cmd: tuple[str, ...]
    exit_code: int | None  # None 이면 실행 자체가 없었다 — 정책 차단 또는 타임아웃
    green: bool
    detail: str


@dataclass(frozen=True)
class GraderResult:
    checks: tuple[GraderCheck, ...]

    @property
    def success(self) -> bool:
        return all(check.green for check in self.checks)

    @property
    def green(self) -> int:
        return sum(1 for check in self.checks if check.green)

    @property
    def total(self) -> int:
        return len(self.checks)


def grade_in(fixture: Fixture, repo: Path, tree: Path) -> GraderResult:
    """hidden AC 를 `tree` 에서 실행한다. 정책은 materialize 된 저장소의 config 기준이다.

    run journal 은 이미 닫혔으므로 여기서는 journal 에 쓰지 않는다 — grader 의 결과와
    정책 판정은 eval.json 이 갖는다 (docs/11).
    """
    config = load_config(repo)
    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )
    checks = []
    for cmd, shell in _hidden_acceptance(fixture):
        result = policy.run(cmd, tree, config.ac_timeout_s, shell=shell)
        if not result.executed:
            checks.append(
                GraderCheck(cmd, None, False, f"정책이 커맨드를 막았다: {result.decision.verdict}")
            )
        elif result.timed_out:
            checks.append(GraderCheck(cmd, None, False, result.stderr))
        else:
            green = result.exit_code == 0
            detail = "" if green else (result.stderr or result.stdout).strip()[-400:]
            checks.append(GraderCheck(cmd, result.exit_code, green, detail))
    return GraderResult(tuple(checks))


def _hidden_acceptance(fixture: Fixture) -> list[tuple[tuple[str, ...], bool]]:
    """`{grader}` 치환은 여기서만 일어난다 — 컨텍스트 조립에는 `grader/` 가 없다 (docs/11)."""
    path = fixture.root / GRADER_DIR / "hidden_ac.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    entries = (data or {}).get("acceptance") or ()
    grader = str(fixture.root / GRADER_DIR)
    return [
        (
            tuple(
                str(arg).replace(GRADER_PLACEHOLDER, grader) for arg in entry.get("cmd") or ()
            ),
            bool(entry.get("shell", False)),
        )
        for entry in entries
        if isinstance(entry, dict)
    ]


# --------------------------------------------------------------------------- 능력 eval


@dataclass(frozen=True)
class CapabilityResult:
    fixture: str
    arm: str
    repo: Path
    run_id: str
    harness: str | None  # "verified" | "rejected_or_blocked" | "other" — raw 는 None
    grader: GraderResult


def run_capability(
    fixture: Fixture, arm: str, workdir: Path | str, *, index: int = 0
) -> CapabilityResult:
    workdir = Path(workdir)
    label = f"{fixture.name}-{arm.replace(':', '-')}-{index + 1}"
    repo = materialize(fixture, workdir / label)
    config = load_config(repo)
    run_id = f"eval-{label}"
    scratch = workdir / f"{label}-scratch"

    if arm == "raw":
        _run_raw(repo, config, run_id, scratch)
        harness_call = None
        tree, cleanup = repo, _noop
    else:
        store = _run_harness(repo, config, arm, run_id, scratch)
        harness_call = _classify(store.state)
        tree, cleanup = _graded_tree(repo, run_id, workdir)

    try:
        graded = grade_in(fixture, repo, tree)
    finally:
        cleanup()
    return CapabilityResult(fixture.name, arm, repo, run_id, harness_call, graded)


def run_matrix(
    fixtures: list[Fixture], arms: list[str], repeat: int, workdir: Path | str
) -> list[CapabilityResult]:
    for arm in arms:
        if arm != "raw":
            _features(arm)  # 알 수 없는 arm 은 매트릭스 도중이 아니라 시작 전에 거른다
    for fixture in fixtures:
        if not _hidden_acceptance(fixture):
            raise HarnessError(
                f"{fixture.name}: grader/hidden_ac.yaml 이 없거나 비어 있다 — "
                "채점 기준 없는 능력 eval 은 공허하게 성공할 뿐이다 (docs/11)"
            )
    results = []
    for fixture in fixtures:
        for arm in arms:
            for index in range(repeat):
                results.append(run_capability(fixture, arm, workdir, index=index))
    return results


def _features(arm: str) -> frozenset[str]:
    if arm == "harness-lite":
        return frozenset()
    if arm == REGRESSION_ARM:
        return frozenset(ABLATABLE)
    if arm.startswith(ABLATION_PREFIX):
        removed = arm[len(ABLATION_PREFIX) :]
        if removed not in ABLATABLE:
            raise HarnessError(f"ablation 대상이 아니다: {removed!r} (가능: {ABLATABLE})")
        return frozenset(ABLATABLE) - {removed}
    raise HarnessError(f"알 수 없는 arm: {arm!r}")


def _run_harness(repo: Path, config: Config, arm: str, run_id: str, scratch: Path) -> Store:
    """docs/11 의 arm 조립표. lite 는 커널만이고, full 은 `harness run` 과 같다."""
    features = _features(arm)
    builder = review = None
    if "context" in features:
        from harness.context.builder import ContextBuilder

        builder = ContextBuilder(repo, config)
    if "review" in features:
        from harness.exec.review import ReviewStage

        review = ReviewStage(repo, config)
    execute = run_dag
    if "parallel" in features and config.max_parallel > 1:
        from harness.exec import scheduler

        execute = scheduler.run_dag
    return execute(
        repo,
        config,
        Dag(load_tasks(repo)),
        run_id=run_id,
        scratch=scratch,
        context_builder=builder,
        review_stage=review,
    )


def _classify(state: RunState) -> str:
    verdicts = [projection.verdict for projection in state.tasks.values()]
    if verdicts and all(verdict is Verdict.VERIFIED for verdict in verdicts):
        return "verified"
    if any(verdict in (Verdict.REJECTED, Verdict.BLOCKED) for verdict in verdicts):
        return "rejected_or_blocked"
    return "other"


def _noop() -> None:
    return None


def _graded_tree(repo: Path, run_id: str, workdir: Path):
    """docs/11 — 채점 대상은 run 이 끝난 뒤 사용자가 갖게 되는 트리다.

    기준은 프로파일 선언이 아니라 **integration 브랜치의 존재**다. task 별 프로파일
    오버라이드가 섞여 있어도 ship 이 머지할 그 트리를 채점한다.
    """
    branch = integration_branch(run_id)
    if git(["rev-parse", "--verify", "--quiet", branch], cwd=repo).exit_code != 0:
        return repo, _noop  # 통합 브랜치가 없다 — 사용자 트리는 그대로다

    path = workdir / f"graded-{run_id}"
    result = git(["worktree", "add", "--detach", str(path), branch], cwd=repo)
    if result.exit_code != 0:
        raise HarnessError(f"채점용 워크트리를 만들 수 없다: {result.stderr.strip()}")

    def cleanup() -> None:
        git(["worktree", "remove", "--force", str(path)], cwd=repo)
        shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], cwd=repo)

    return path, cleanup


# --------------------------------------------------------------------------- raw arm


def _run_raw(repo: Path, config: Config, run_id: str, scratch: Path) -> Store:
    """측정 전용 래퍼 (docs/11). 판정하지 않는다 — 스펙 전문을 한 번에 투입하고,
    측정 이벤트만 journal 에 남긴다."""
    run_dir = repo / HARNESS_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    store = Store(run_dir)
    store.append(
        EventType.RUN_STARTED,
        {
            "manifest": {"arm": "raw"},
            "profile": str(config.default_profile),
            "adapter": config.default_adapter,
            "max_parallel": 1,
        },
    )

    entry = config.adapters[config.default_adapter]
    adapter = build_adapter(config.default_adapter, entry.type, entry.options)
    outbox = scratch / "outbox" / "attempt-1"
    outbox.mkdir(parents=True, exist_ok=True)
    env = {name: os.environ[name] for name in ENV_PASSTHROUGH if name in os.environ}
    env.update(
        {"HARNESS_OUTBOX": str(outbox), "HARNESS_TASK_ID": "RAW", "HARNESS_ATTEMPT": "1"}
    )
    request = AgentRequest(
        task_id="RAW",
        prompt=_spec_text(repo),
        workspace=repo,
        outbox=outbox,
        profile=config.default_profile,
        allowed_tools=None,
        timeout_s=config.agent_timeout_s,
        env=env,
        attempt=1,
    )
    store.append(
        EventType.AGENT_STARTED,
        {"adapter": adapter.name, "workspace": str(repo), "outbox": str(outbox)},
        task_id="RAW",
        attempt=1,
    )
    result = adapter.execute(request)
    usage = result.usage
    store.append(
        EventType.AGENT_FINISHED,
        {
            "exit_code": result.exit_code,
            "duration_s": result.duration_s,
            "usage": None
            if usage is None
            else {
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "cost_usd": usage.cost_usd,
            },
            "runtime_failure": str(result.runtime_failure) if result.runtime_failure else None,
        },
        task_id="RAW",
        attempt=1,
    )

    _post_acceptance(repo, config, store)
    store.append(
        EventType.RUN_FINISHED, {"summary": {"arm": "raw"}, "open_debts": [], "human_required": []}
    )
    return store


def _post_acceptance(repo: Path, config: Config, store: Store) -> None:
    """보이는 AC 합집합의 사후 실행 — 지표 원천일 뿐 판정이 아니다 (docs/11)."""
    if not (repo / "tasks").is_dir():
        return
    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )
    seen = set()
    for task in load_tasks(repo).values():
        for criterion in task.acceptance:
            key = (criterion.cmd, criterion.shell)
            if key in seen:
                continue
            seen.add(key)
            result = policy.run(criterion.cmd, repo, config.ac_timeout_s, shell=criterion.shell)
            store.append(EventType.COMMAND_POLICY_DECISION, result.decision.to_payload())
            if not result.executed:
                continue
            store.append(
                EventType.AC_POST_EXECUTED,
                {
                    "cmd": list(criterion.cmd),
                    "exit_code": result.exit_code,
                    "classification": GREEN if result.exit_code == 0 else RED,
                    "differential": None,  # baseline 이 없으므로 차등 판정도 없다
                },
                task_id="RAW",
                attempt=1,
            )


def _spec_text(repo: Path) -> str:
    parts = [
        path.read_text(encoding="utf-8") for path in sorted(repo.glob("specs/*/spec.yaml"))
    ]
    return "\n\n---\n\n".join(parts) or "스펙이 없다."
