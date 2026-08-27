"""어댑터 프로토콜. docs/04 가 canonical 이다.

어댑터는 프로세스를 띄우고 결과의 **경로**를 돌려주는 것까지만 한다. 파싱도 판정도
하지 않는다. `AgentResult` 에 성공을 뜻하는 필드가 없는 것은 의도다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Protocol

from harness.models import ExecutionProfile


class RuntimeFailure(StrEnum):
    """실행 자체가 유효하게 성립하지 않은 경우. 어댑터가 하는 유일한 해석이다."""

    TIMEOUT = "timeout"
    KILLED = "killed"
    PROTOCOL_VIOLATION = "protocol_violation"


class PreflightKind(StrEnum):
    """docs/04 — 어느 kind 가 어느 실패 분류에 대응하는지는 docs/10 의 표를 따른다."""

    OK = "ok"
    MISSING_PREREQUISITE = "missing_prerequisite"
    MISCONFIGURED = "misconfigured"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class Usage:
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class Capabilities:
    reports_usage: bool
    supports_tool_allowlist: bool
    supports_session_reuse: bool


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    kind: PreflightKind
    detail: str


@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    prompt: str
    workspace: Path  # agent 의 cwd — 저장소 밖 워크트리
    outbox: Path  # 워크스페이스 밖. result.json / handoff.json 을 여기에.
    profile: ExecutionProfile
    allowed_tools: list[str] | None
    timeout_s: int
    env: Mapping[str, str]  # 화이트리스트. HARNESS_OUTBOX 를 포함한다.
    attempt: int


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    stdout: str
    stderr: str
    raw_claim_path: Path | None  # 파싱·검증은 하네스가 한다
    raw_handoff_path: Path | None
    duration_s: float
    usage: Usage | None  # 벤더가 보고하지 않으면 None
    transcript_path: Path | None
    runtime_failure: RuntimeFailure | None


class AgentAdapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...

    def preflight(self) -> PreflightReport: ...

    def execute(self, request: AgentRequest) -> AgentResult: ...
