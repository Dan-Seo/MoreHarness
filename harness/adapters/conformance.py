"""모든 어댑터가 통과해야 하는 스위트. docs/04 가 canonical 이다.

**신규 어댑터의 합격 조건은 이 스위트 통과다.**

검사는 어댑터의 의무만 본다. agent 가 워크스페이스의 `allowed_paths` 안에 쓰는 것은
정상적인 작업이며 어댑터가 막을 일이 아니다 — 경로 스코프는 `exec/verify` 의 사후 diff
판정이 결정한다 (docs/04, docs/06).

이 검사는 **탐지**이지 OS 수준 강제가 아니다. 05 의 보장/미보장 표와 같은 표현 규약을
따른다.

어댑터마다 "느린 실행"이나 "깨진 아티팩트"를 만드는 방법이 다르므로, 호출자가 시나리오
이름을 받아 어댑터를 만들어 주는 팩토리를 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from harness.adapters.base import AgentRequest, AgentResult, RuntimeFailure
from harness.models import ExecutionProfile

# 팩토리가 만들 수 있어야 하는 어댑터의 모습들.
QUIET = "quiet"  # 아무 아티팩트도 남기지 않고 정상 종료한다
ARTIFACTS = "artifacts"  # claim 과 handoff 를 outbox 에 남긴다
BROKEN = "broken_artifact"  # JSON 이 아닌 claim 을 남긴다
SLOW = "slow"  # timeout_s 를 넘겨 실행된다
CRASH = "crash"  # 비정상 종료한다

SCENARIOS = (QUIET, ARTIFACTS, BROKEN, SLOW, CRASH)

AdapterFor = Callable[[str], object]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_suite(adapter_for: AdapterFor, root: Path, timeout_s: int = 2) -> list[Check]:
    """어댑터 하나를 계약 전체에 대해 검사한다. 실패는 예외가 아니라 결과로 돌아온다."""
    checks = [_no_judgement_fields()]
    for check in (
        _absent_artifacts_are_none,
        _artifacts_land_in_the_outbox,
        _a_broken_artifact_is_returned_unparsed,
        _writes_stay_inside_workspace_and_outbox,
        _each_attempt_gets_its_own_outbox,
        _a_timeout_is_reported_as_runtime_failure,
        _an_abnormal_exit_still_returns_a_result,
        _repeating_an_attempt_does_not_raise,
    ):
        checks.append(_guarded(check, adapter_for, root, timeout_s))
    return checks


def failures(checks: list[Check]) -> list[Check]:
    return [check for check in checks if not check.ok]


# --------------------------------------------------------------------------- 검사


def _no_judgement_fields() -> Check:
    """docs/04 — AgentResult 어디에도 성공·실패 해석이 없다."""
    import dataclasses

    names = {field.name for field in dataclasses.fields(AgentResult)}
    forbidden = names & {"success", "ok", "verdict", "passed", "failed"}
    return Check("판정 금지", not forbidden, f"금지된 필드: {sorted(forbidden)}" if forbidden else "")


def _absent_artifacts_are_none(adapter_for, root, timeout_s) -> Check:
    result = _run(adapter_for(QUIET), root, QUIET, timeout_s)
    ok = result.raw_claim_path is None and result.raw_handoff_path is None
    return Check("아티팩트 부재", ok, "" if ok else "없는 아티팩트의 경로를 만들어냈다")


def _artifacts_land_in_the_outbox(adapter_for, root, timeout_s) -> Check:
    request = _request(root, ARTIFACTS, timeout_s)
    result = adapter_for(ARTIFACTS).execute(request)
    paths = [p for p in (result.raw_claim_path, result.raw_handoff_path) if p]
    ok = bool(paths) and all(request.outbox in Path(p).parents for p in paths)
    return Check("결과는 outbox 에만", ok, "" if ok else f"outbox 밖의 경로: {paths}")


def _a_broken_artifact_is_returned_unparsed(adapter_for, root, timeout_s) -> Check:
    """docs/04 — 내용이 깨져 있어도 경로만 반환한다. 파싱하거나 고치지 않는다."""
    result = _run(adapter_for(BROKEN), root, BROKEN, timeout_s)
    if result.raw_claim_path is None:
        return Check("아티팩트 파손", False, "깨진 아티팩트를 지우거나 감췄다")
    raw = Path(result.raw_claim_path).read_text(encoding="utf-8")
    ok = not _is_json(raw)
    return Check("아티팩트 파손", ok, "" if ok else "어댑터가 아티팩트를 고쳤다")


def _writes_stay_inside_workspace_and_outbox(adapter_for, root, timeout_s) -> Check:
    """어댑터 자신의 쓰기 범위. agent 가 워크스페이스에 쓰는 것은 여기 해당하지 않는다."""
    sandbox = root / "scope"
    request = _request(sandbox, ARTIFACTS, timeout_s)
    outside = sandbox / "outside"
    outside.mkdir(parents=True, exist_ok=True)

    before = _snapshot(outside)
    adapter_for(ARTIFACTS).execute(request)
    after = _snapshot(outside)

    ok = before == after
    return Check("쓰기 범위", ok, "" if ok else f"밖에 쓴 것: {sorted(after - before)}")


def _each_attempt_gets_its_own_outbox(adapter_for, root, timeout_s) -> Check:
    """docs/04 — attempt 마다 새 디렉토리. 이전 산출물이 다음 판정에 섞이지 않는다."""
    adapter = adapter_for(ARTIFACTS)
    first = adapter.execute(_request(root / "a1", ARTIFACTS, timeout_s, attempt=1))
    second = adapter.execute(_request(root / "a2", ARTIFACTS, timeout_s, attempt=2))
    ok = first.raw_claim_path != second.raw_claim_path
    return Check("attempt 별 outbox", ok, "" if ok else "두 attempt 가 같은 경로를 썼다")


def _a_timeout_is_reported_as_runtime_failure(adapter_for, root, timeout_s) -> Check:
    result = _run(adapter_for(SLOW), root, SLOW, timeout_s)
    ok = result.runtime_failure is RuntimeFailure.TIMEOUT
    return Check("timeout", ok, "" if ok else f"runtime_failure={result.runtime_failure}")


def _an_abnormal_exit_still_returns_a_result(adapter_for, root, timeout_s) -> Check:
    result = _run(adapter_for(CRASH), root, CRASH, timeout_s)
    ok = isinstance(result, AgentResult)
    return Check("비정상 종료", ok, "" if ok else "AgentResult 를 돌려주지 않았다")


def _repeating_an_attempt_does_not_raise(adapter_for, root, timeout_s) -> Check:
    adapter = adapter_for(ARTIFACTS)
    request = _request(root / "twice", ARTIFACTS, timeout_s)
    adapter.execute(request)
    adapter.execute(request)
    return Check("멱등 정리", True, "")


# --------------------------------------------------------------------------- 보조


def _guarded(check, adapter_for, root, timeout_s) -> Check:
    name = check.__name__.strip("_")
    try:
        return check(adapter_for, root / name, timeout_s)
    except Exception as exc:  # 어댑터는 예외를 밖으로 던지지 않아야 한다
        return Check(name, False, f"예외가 밖으로 나왔다: {exc!r}")


def _request(root: Path, scenario: str, timeout_s: int, attempt: int = 1) -> AgentRequest:
    workspace = root / "workspace"
    outbox = root / "outbox" / f"attempt-{attempt}"
    workspace.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    return AgentRequest(
        task_id="T-001",
        prompt=f"conformance: {scenario}",
        workspace=workspace,
        outbox=outbox,
        profile=ExecutionProfile.SAFE,
        allowed_tools=None,
        timeout_s=timeout_s,
        env={"HARNESS_OUTBOX": str(outbox), "HARNESS_TASK_ID": "T-001", "HARNESS_ATTEMPT": "1"},
        attempt=attempt,
    )


def _run(adapter, root: Path, scenario: str, timeout_s: int) -> AgentResult:
    return adapter.execute(_request(root, scenario, timeout_s))


def _snapshot(directory: Path) -> set[str]:
    return {str(p.relative_to(directory)) for p in directory.rglob("*")}


def _is_json(raw: str) -> bool:
    import json

    try:
        json.loads(raw)
        return True
    except ValueError:
        return False
