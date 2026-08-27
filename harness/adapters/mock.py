"""결정론적 어댑터. 회귀 eval 과 CI 의 기본값이며 LLM 도 네트워크도 필요 없다.

시나리오 파일이 exit code, 산출 파일, 지연을 지정한다 (docs/04).

```yaml
default:                      # tasks 에 없는 task 에 적용된다
  exit_code: 0
tasks:
  T-001:
    exit_code: 0
    delay_s: 0
    stdout: ""
    stderr: ""
    runtime_failure: null     # timeout | killed | protocol_violation
    files:                    # 워크스페이스에 쓸 파일
      "src/api.py": "print('x')\n"
    claim:                    # 매핑이면 JSON 으로, 문자열이면 그대로 쓴다
      schema: harness.claim/v1
      task_id: T-001
    handoff:
      schema: harness.handoff/v1
      task_id: T-001
```
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.adapters.base import (
    AgentRequest,
    AgentResult,
    Capabilities,
    PreflightKind,
    PreflightReport,
    RuntimeFailure,
)


class MockAdapter:
    def __init__(self, name: str, scenario: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self._scenario = dict(scenario or {})

    def capabilities(self) -> Capabilities:
        return Capabilities(
            reports_usage=False,
            supports_tool_allowlist=False,
            supports_session_reuse=False,
        )

    def preflight(self) -> PreflightReport:
        return PreflightReport(ok=True, kind=PreflightKind.OK, detail="mock 은 항상 사용 가능하다")

    def execute(self, request: AgentRequest) -> AgentResult:
        entry = self._entry(request.task_id)

        delay = float(entry.get("delay_s", 0.0))
        if delay:
            time.sleep(delay)

        self._write_workspace_files(request.workspace, entry.get("files") or {})
        claim_path = self._write_artifact(request.outbox, "result.json", entry.get("claim"))
        handoff_path = self._write_artifact(request.outbox, "handoff.json", entry.get("handoff"))

        failure = entry.get("runtime_failure")
        return AgentResult(
            exit_code=int(entry.get("exit_code", 0)),
            stdout=str(entry.get("stdout", "")),
            stderr=str(entry.get("stderr", "")),
            raw_claim_path=claim_path,
            raw_handoff_path=handoff_path,
            duration_s=delay,  # 측정값이 아니라 시나리오값이다. 결정론을 위해서다.
            usage=None,
            transcript_path=None,
            runtime_failure=RuntimeFailure(failure) if failure else None,
        )

    def _entry(self, task_id: str) -> Mapping[str, Any]:
        tasks = self._scenario.get("tasks") or {}
        return tasks.get(task_id) or self._scenario.get("default") or {}

    @staticmethod
    def _write_workspace_files(workspace: Path, files: Mapping[str, str]) -> None:
        for relative, content in files.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_artifact(outbox: Path, filename: str, content: Any) -> Path | None:
        if content is None:
            return None
        outbox.mkdir(parents=True, exist_ok=True)
        target = outbox / filename
        # 문자열은 그대로 쓴다. 깨진 아티팩트를 만들 수 있어야 하네스의 정규화를 시험할 수 있다.
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        target.write_text(text, encoding="utf-8")
        return target


def build_mock(name: str, options: Mapping[str, Any]) -> MockAdapter:
    scenario_path = options.get("scenario")
    scenario: Mapping[str, Any] = {}
    if scenario_path:
        scenario = yaml.safe_load(Path(scenario_path).read_text(encoding="utf-8")) or {}
    return MockAdapter(name, scenario)
