"""Command Policy — allow / deny / require_approval.

docs/06 이 canonical 이다.

> Agent-authored commands are untrusted input. Harness must authorize them before execution.

**하네스가 서브프로세스를 띄우는 모든 지점이 이 모듈을 통과한다.** AC, precondition,
health 커맨드, verifier, eval fixture — 예외는 없다. 그래서 실행 함수도 여기 있다.
정책을 거치지 않고 커맨드를 실행할 수 있는 경로를 만들지 말 것.

정규식 매칭은 **보안 경계가 아니다.** 잘못된 planner 와 저장소 인젝션에 대한
가드레일이며, 진짜 경계는 `container` 프로파일뿐이다 (docs/05, docs/09).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from harness.errors import ConfigError


class PolicyVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


# 승격은 상향만 한다. shell 선언이 allow 를 require_approval 로 올리되 deny 는 낮추지 않는다.
_STRENGTH = {
    PolicyVerdict.ALLOW: 0,
    PolicyVerdict.REQUIRE_APPROVAL: 1,
    PolicyVerdict.DENY: 2,
}


def normalize(cmd: Sequence[str]) -> str:
    """docs/06 — 매칭 대상은 argv 를 공백으로 join 한 정규화 문자열이다."""
    if isinstance(cmd, str):
        raise TypeError("cmd 는 argv 리스트여야 한다 (docs/03)")
    return " ".join(cmd)


def approval_hash(cmd: Sequence[str]) -> str:
    """승인의 키. 인자가 하나라도 바뀌면 다른 해시가 된다."""
    return "sha256:" + hashlib.sha256(normalize(cmd).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Rule:
    match: str
    verdict: PolicyVerdict


@dataclass(frozen=True)
class Approval:
    cmd: tuple[str, ...]
    hash: str
    approver: str | None
    scope: str


@dataclass(frozen=True)
class Decision:
    cmd: tuple[str, ...]
    verdict: PolicyVerdict
    rule: str | None
    approver: str | None

    @property
    def may_execute(self) -> bool:
        if self.verdict is PolicyVerdict.ALLOW:
            return True
        if self.verdict is PolicyVerdict.REQUIRE_APPROVAL:
            return self.approver is not None
        return False

    def to_payload(self) -> dict[str, Any]:
        """docs/03 — command_policy_decision 의 payload."""
        return {
            "cmd": list(self.cmd),
            "verdict": str(self.verdict),
            "rule": self.rule,
            "approver": self.approver,
        }


@dataclass(frozen=True)
class CommandResult:
    """하네스가 커맨드에 대해 관측한 것 전부.

    `exit_code` 가 `None` 인 것은 종료 코드가 없었다는 뜻이다 — 정책이 실행을 막았거나,
    타임아웃으로 죽였거나, 프로세스를 띄우지 못했다. 없는 값을 지어내지 않는다.
    """

    decision: Decision
    executed: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class CommandPolicy:
    def __init__(
        self,
        default: PolicyVerdict,
        rules: Sequence[Rule],
        approvals: Sequence[Approval] = (),
    ) -> None:
        self.default = default
        self.rules = tuple(rules)
        self._by_hash = {a.hash: a for a in approvals}
        self._compiled = tuple((re.compile(r.match), r) for r in self.rules)

    @classmethod
    def from_config(
        cls,
        command_policy: Mapping[str, Any] | None,
        approvals: Sequence[Approval | Mapping[str, Any]] = (),
    ) -> CommandPolicy:
        """`command_policy` 키가 없으면 규칙이 없는 것이고, 따라서 전부 default 다."""
        data = command_policy or {}
        return cls(
            default=_verdict(data.get("default", PolicyVerdict.REQUIRE_APPROVAL), "default"),
            rules=[_rule(entry, index) for index, entry in enumerate(data.get("rules") or ())],
            approvals=[_approval(a) for a in approvals],
        )

    def decide(self, cmd: Sequence[str], shell: bool = False) -> Decision:
        """docs/06 의 판정 순서. 이벤트 기록은 호출자가 한다."""
        text = normalize(cmd)

        verdict, rule = self.default, None
        for pattern, entry in self._compiled:
            if pattern.search(text):
                verdict, rule = entry.verdict, entry.match
                break

        if shell and _STRENGTH[verdict] < _STRENGTH[PolicyVerdict.REQUIRE_APPROVAL]:
            # shell: true 선언 자체가 자동으로 require_approval 로 승격된다.
            verdict = PolicyVerdict.REQUIRE_APPROVAL

        approver = None
        if verdict is PolicyVerdict.REQUIRE_APPROVAL:
            approval = self._by_hash.get(approval_hash(cmd))
            approver = approval.approver if approval else None

        return Decision(cmd=tuple(cmd), verdict=verdict, rule=rule, approver=approver)

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path | str,
        timeout_s: int,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
    ) -> CommandResult:
        """정책을 통과한 커맨드만 실행한다. 기본은 argv 리스트 + `shell=False` 다."""
        decision = self.decide(cmd, shell=shell)
        if not decision.may_execute:
            return CommandResult(decision, False, None, "", "", False)

        target: Any = normalize(cmd) if shell else list(cmd)
        try:
            completed = subprocess.run(
                target,
                cwd=str(cwd),
                shell=shell,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                env=dict(env) if env is not None else None,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                decision, True, None, _text(exc.stdout), f"{timeout_s}초 타임아웃", True
            )
        except OSError as exc:
            return CommandResult(decision, True, None, "", str(exc), False)

        return CommandResult(
            decision,
            True,
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
            False,
        )


def load_approvals(path: Path | str) -> tuple[Approval, ...]:
    """`.harness/approved_commands.yaml` 을 읽는다. 항상 메인 저장소에서 읽는다."""
    path = Path(path)
    if not path.is_file():
        return ()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(_approval(entry) for entry in (data.get("approvals") or ()))


def _text(raw: Any) -> str:
    if raw is None:
        return ""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def _verdict(raw: Any, where: str) -> PolicyVerdict:
    try:
        return PolicyVerdict(raw)
    except ValueError as exc:
        raise ConfigError(f"command_policy.{where} 의 값 {raw!r} 은(는) 알 수 없는 verdict 다") from exc


def _rule(entry: Any, index: int) -> Rule:
    if not isinstance(entry, Mapping) or "match" not in entry:
        raise ConfigError(f"command_policy.rules[{index}] 에 match 가 없다")
    try:
        re.compile(entry["match"])
    except re.error as exc:
        raise ConfigError(f"command_policy.rules[{index}] 의 정규식이 잘못됐다: {exc}") from exc
    return Rule(match=entry["match"], verdict=_verdict(entry.get("verdict"), f"rules[{index}]"))


def _approval(entry: Approval | Mapping[str, Any]) -> Approval:
    if isinstance(entry, Approval):
        return entry
    return Approval(
        cmd=tuple(entry.get("cmd") or ()),
        hash=entry.get("hash", ""),
        approver=entry.get("approver"),
        scope=entry.get("scope", "run"),
    )
