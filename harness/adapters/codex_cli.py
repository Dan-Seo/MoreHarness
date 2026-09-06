"""codex CLI 벤더 어댑터. docs/04 가 canonical 이다.

`generic_cli` 위의 얇은 구성이다. 더 아는 것은 **usage 의 위치** — stdout 의 JSON
라인들 중 `usage` 객체를 담은 마지막 라인 — 와 outbox 접근 플래그다.
cost 는 벤더가 보고하지 않으므로 `None` 이다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from harness.adapters.base import Capabilities, Usage
from harness.adapters.generic_cli import DEFAULT_GRACE_S, GenericCliAdapter, binary_prefix

DEFAULT_BINARY = "codex"


class CodexCliAdapter(GenericCliAdapter):
    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        command = [
            *binary_prefix(options, DEFAULT_BINARY),
            "exec",
            "--json",
            *(options.get("extra_args") or ()),
            "--add-dir",
            "{outbox}",  # 결과 디렉토리는 워크스페이스 밖이다 (docs/04)
            "-",  # 프롬프트는 stdin 이다
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
            supports_tool_allowlist=False,
            supports_session_reuse=False,
        )

    def _usage(self, stdout: str, outbox: Path) -> Usage | None:
        for line in reversed(stdout.splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, Mapping) and isinstance(data.get("usage"), Mapping):
                usage = data["usage"]
                return Usage(
                    tokens_in=usage.get("input_tokens"),
                    tokens_out=usage.get("output_tokens"),
                    cost_usd=None,
                )
        return None


def build_codex_cli(name: str, options: Mapping[str, Any]) -> CodexCliAdapter:
    return CodexCliAdapter(name, options)
