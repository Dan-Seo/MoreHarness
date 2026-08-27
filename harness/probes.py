"""precondition 검사와 environment verifier.

probe 는 **하네스가 직접 만든 관측치**다. docs/06 이 `blocked` 를 하네스 소유 증거로만
허용하는 근거가 이것이고, agent 의 `blocked_hint` 는 어떤 probe 를 먼저 돌릴지 고르는
트리거일 뿐 결론이 아니다.

`kind: command` precondition 은 Command Policy 적용 대상이므로 여기서도 커맨드는
`policy.run` 을 통해서만 실행된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from harness.policy import CommandPolicy, Decision, normalize


@dataclass(frozen=True)
class ProbeResult:
    kind: str
    name: str
    ok: bool
    detail: str
    decision: Decision | None = None  # kind: command 일 때만 있다

    def to_payload(self) -> dict[str, Any]:
        """docs/03 — precondition_checked 의 payload. 값은 여기 들어가지 않는다."""
        return {"kind": self.kind, "name": self.name, "ok": self.ok, "detail": self.detail}


def check(
    precondition: Mapping[str, Any],
    policy: CommandPolicy,
    cwd: Path | str,
    timeout_s: int,
    env: Mapping[str, str] | None = None,
) -> ProbeResult:
    kind = precondition.get("kind")

    if kind == "env_var":
        name = precondition.get("name", "")
        present = name in (env if env is not None else _ambient())
        # 존재 여부만 말한다. 값은 detail 에도 payload 에도 들어가지 않는다.
        return ProbeResult(kind, name, present, "있음" if present else "없음")

    if kind == "file":
        path = precondition.get("path", "")
        exists = (Path(cwd) / path).exists()
        return ProbeResult(kind, path, exists, "있음" if exists else "없음")

    if kind == "command":
        cmd = list(precondition.get("cmd") or ())
        result = policy.run(cmd, cwd=cwd, timeout_s=timeout_s, env=env)
        if not result.executed:
            detail = f"Command Policy 가 실행을 승인하지 않았다 ({result.decision.verdict})"
            return ProbeResult(kind, normalize(cmd), False, detail, result.decision)
        ok = result.exit_code == 0
        detail = f"exit={result.exit_code}" + (" (타임아웃)" if result.timed_out else "")
        return ProbeResult(kind, normalize(cmd), ok, detail, result.decision)

    return ProbeResult(str(kind), "", False, f"알 수 없는 precondition kind: {kind!r}")


def check_all(
    preconditions: Iterable[Mapping[str, Any]],
    policy: CommandPolicy,
    cwd: Path | str,
    timeout_s: int,
    env: Mapping[str, str] | None = None,
) -> list[ProbeResult]:
    return [check(p, policy, cwd, timeout_s, env) for p in preconditions]


def matching_blocked_signal(text: str, patterns: Sequence[str]) -> str | None:
    """docs/06 — AC stderr 를 config 의 blocked_signals 와 대조한다.

    기본이 빈 목록이므로 아무 패턴도 없으면 아무것도 매치하지 않는다. 오탐의 결과가
    잘못된 `blocked` 이기 때문에 미리 심어 두지 않는다.
    """
    for pattern in patterns:
        if re.search(pattern, text):
            return pattern
    return None


def _ambient() -> Mapping[str, str]:
    import os

    return os.environ
