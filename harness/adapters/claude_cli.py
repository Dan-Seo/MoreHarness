"""claude CLI 벤더 어댑터. docs/04 가 canonical 이다.

`generic_cli` 위의 얇은 구성이다. 더 아는 것은 **usage 의 위치**(stdout JSON 의
`usage`/`total_cost_usd`)와 **도구 화이트리스트 플래그**뿐이다. 그 밖의 계약은
`generic_cli` 와 문자 그대로 같으며 conformance 스위트로 증명한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.adapters.base import AgentRequest, Capabilities, Usage
from harness.adapters.generic_cli import DEFAULT_GRACE_S, GenericCliAdapter

DEFAULT_BINARY = "claude"


def binary_prefix(options: Mapping[str, Any], default: str) -> list[str]:
    """`binary` 는 실행 파일 경로다. 래퍼로 구동해야 하는 환경(테스트의 가짜 CLI 포함)을
    위해 argv 접두 리스트도 허용한다 (docs/04)."""
    binary = options.get("binary", default)
    return list(binary) if isinstance(binary, (list, tuple)) else [str(binary)]


class ClaudeCliAdapter(GenericCliAdapter):
    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        command = [
            *binary_prefix(options, DEFAULT_BINARY),
            "-p",
            "--output-format",
            "json",
            *(options.get("extra_args") or ()),
        ]
        super().__init__(
            name,
            {
                "command": command,
                "prompt_delivery": "stdin",
                "env_passthrough": options.get("env_passthrough") or (),
                "timeout_grace_s": options.get("timeout_grace_s", DEFAULT_GRACE_S),
            },
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            reports_usage=True,
            supports_tool_allowlist=True,
            supports_session_reuse=False,  # task 단위 fresh context 가 원칙이다 (docs/00)
        )

    def _argv(self, request: AgentRequest) -> Sequence[str]:
        if not request.allowed_tools:
            return self.command
        return [*self.command, "--allowedTools", ",".join(request.allowed_tools)]

    def _usage(self, stdout: str, outbox: Path) -> Usage | None:
        """stdout 전체가 claude 의 결과 JSON 이다. 아니면 usage 없음 — 추정하지 않는다."""
        try:
            data = json.loads(stdout)
        except ValueError:
            return None
        if not isinstance(data, Mapping) or not isinstance(data.get("usage"), Mapping):
            return None
        usage = data["usage"]
        return Usage(
            tokens_in=usage.get("input_tokens"),
            tokens_out=usage.get("output_tokens"),
            cost_usd=data.get("total_cost_usd"),
        )


def build_claude_cli(name: str, options: Mapping[str, Any]) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(name, options)
