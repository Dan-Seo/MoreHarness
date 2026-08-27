"""설정만으로 임의 agent CLI 를 구동하는 어댑터. docs/04 가 canonical 이다.

```yaml
adapters:
  my_cli:
    type: generic_cli
    command: ["mytool", "run", "--cwd", "{workspace}", "--prompt-file", "{prompt_file}"]
    prompt_delivery: file        # argv | stdin | file
    env_passthrough: [PATH, HOME, LANG]
    usage_from: none             # none | stdout_json:<path> | file:<path>
    timeout_grace_s: 10
```

> 어떤 agent CLI 를 이 설정으로 표현할 수 없다면, 그것은 그 CLI 의 문제가 아니라
> **이 프로토콜의 결함**이다.

여기서 띄우는 프로세스는 **config 가 지정한 agent CLI** 이며, planner agent 나 저장소
콘텐츠가 쓴 커맨드가 아니다. 그래서 Command Policy 의 적용 대상이 아니다 — docs/06 의
적용 대상 목록을 보라. `git.py` 가 같은 이유로 예외인 것과 같다.

이 어댑터는 판정하지 않는다. exit code 를 수집만 하고, 아티팩트는 **경로만** 돌려준다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.adapters.base import (
    AgentRequest,
    AgentResult,
    Capabilities,
    PreflightKind,
    PreflightReport,
    RuntimeFailure,
    Usage,
)

PLACEHOLDERS = ("workspace", "outbox", "prompt_file", "timeout_s", "task_id", "attempt")
PROMPT_DELIVERY = ("argv", "stdin", "file")
PROMPT_FILENAME = "prompt.txt"
CLAIM_FILENAME = "result.json"
HANDOFF_FILENAME = "handoff.json"
DEFAULT_GRACE_S = 10

# 하네스가 심는 변수는 env_passthrough 와 무관하게 항상 넘어간다. 이게 없으면 agent 가
# 산출물을 어디에 둘지 알 수 없다.
ALWAYS_PASSED = ("HARNESS_OUTBOX", "HARNESS_TASK_ID", "HARNESS_ATTEMPT")


class GenericCliAdapter:
    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.name = name
        self.command = tuple(options.get("command") or ())
        self.prompt_delivery = options.get("prompt_delivery", "file")
        self.env_passthrough = tuple(options.get("env_passthrough") or ())
        self.usage_from = options.get("usage_from", "none")
        self.timeout_grace_s = int(options.get("timeout_grace_s", DEFAULT_GRACE_S))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            reports_usage=self.usage_from != "none",
            supports_tool_allowlist=False,
            supports_session_reuse=False,
        )

    def preflight(self) -> PreflightReport:
        """설정 결함은 error, 준비물 부족은 blocked 로 갈린다 (docs/04, docs/10)."""
        if not self.command or not all(isinstance(part, str) for part in self.command):
            return PreflightReport(False, PreflightKind.MISCONFIGURED, "command 가 argv 리스트가 아니다")
        if self.prompt_delivery not in PROMPT_DELIVERY:
            return PreflightReport(
                False, PreflightKind.MISCONFIGURED, f"prompt_delivery: {self.prompt_delivery!r}"
            )
        unknown = _unknown_placeholders(self.command)
        if unknown:
            return PreflightReport(
                False, PreflightKind.MISCONFIGURED, f"해석할 수 없는 placeholder: {unknown}"
            )
        if shutil.which(self.command[0]) is None and not Path(self.command[0]).is_file():
            return PreflightReport(
                False, PreflightKind.MISSING_PREREQUISITE, f"{self.command[0]} 을(를) 찾을 수 없다"
            )
        return PreflightReport(True, PreflightKind.OK, " ".join(self.command))

    def execute(self, request: AgentRequest) -> AgentResult:
        request.outbox.mkdir(parents=True, exist_ok=True)
        prompt_file = request.outbox / PROMPT_FILENAME
        prompt_file.write_text(request.prompt, encoding="utf-8")

        argv = [_fill(part, request, prompt_file) for part in self.command]
        if self.prompt_delivery == "argv":
            argv.append(request.prompt)

        started = time.monotonic()
        stdout, stderr, exit_code, failure = self._spawn(argv, request)
        duration = time.monotonic() - started

        return AgentResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            raw_claim_path=_existing(request.outbox / CLAIM_FILENAME),
            raw_handoff_path=_existing(request.outbox / HANDOFF_FILENAME),
            duration_s=duration,
            usage=self._usage(stdout, request.outbox),
            transcript_path=None,
            runtime_failure=failure,
        )

    def _spawn(self, argv: list[str], request: AgentRequest):
        """timeout 을 강제하고, 어떤 경우에도 예외를 밖으로 던지지 않는다 (docs/04 conformance)."""
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(request.workspace),
                env=self._env(request),
                stdin=subprocess.PIPE if self.prompt_delivery == "stdin" else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return "", str(exc), None, RuntimeFailure.PROTOCOL_VIOLATION

        feed = request.prompt if self.prompt_delivery == "stdin" else None
        try:
            stdout, stderr = process.communicate(input=feed, timeout=request.timeout_s)
            return stdout or "", stderr or "", process.returncode, None
        except subprocess.TimeoutExpired:
            pass

        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=self.timeout_grace_s)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return stdout or "", stderr or "", process.returncode, RuntimeFailure.TIMEOUT

    def _env(self, request: AgentRequest) -> dict[str, str]:
        """request.env 를 좁힐 뿐 넓히지 않는다. 없는 변수를 자식에 넣지 않는다."""
        if not self.env_passthrough:
            return dict(request.env)
        keep = set(self.env_passthrough) | set(ALWAYS_PASSED)
        return {name: value for name, value in request.env.items() if name in keep}

    def _usage(self, stdout: str, outbox: Path) -> Usage | None:
        """벤더가 보고하지 않으면 `None` 이다. 추정하지 않는다 (docs/11)."""
        source, _, locator = str(self.usage_from).partition(":")
        if source == "stdout_json":
            return _usage_at(_load(stdout), locator)
        if source == "file":
            path = outbox / locator if not Path(locator).is_absolute() else Path(locator)
            return _usage_at(_load(_read(path)), "")
        return None


def build_generic_cli(name: str, options: Mapping[str, Any]) -> GenericCliAdapter:
    return GenericCliAdapter(name, options)


# placeholder 모양의 토큰만 본다. argv 에 JSON 같은 리터럴 중괄호가 오는 것은 정상이므로
# `str.format` 을 쓰지 않는다.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _fill(part: str, request: AgentRequest, prompt_file: Path) -> str:
    values = {
        "workspace": str(request.workspace),
        "outbox": str(request.outbox),
        "prompt_file": str(prompt_file),
        "timeout_s": str(request.timeout_s),
        "task_id": request.task_id,
        "attempt": str(request.attempt),
    }
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), part)


def _unknown_placeholders(command: Sequence[str]) -> tuple[str, ...]:
    found = {name for part in command for name in _PLACEHOLDER.findall(part)}
    return tuple(sorted(found - set(PLACEHOLDERS)))


def _existing(path: Path) -> Path | None:
    return path if path.is_file() else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _usage_at(data: Any, locator: str) -> Usage | None:
    for key in (part for part in locator.split(".") if part):
        if not isinstance(data, Mapping) or key not in data:
            return None
        data = data[key]
    if not isinstance(data, Mapping):
        return None
    return Usage(
        tokens_in=data.get("tokens_in"),
        tokens_out=data.get("tokens_out"),
        cost_usd=data.get("cost_usd"),
    )
