"""docs/04 의 벤더 어댑터 — `generic_cli` 위의 얇은 구성.

벤더 어댑터가 더 아는 것은 **usage 의 위치와 (claude 만) allowlist 플래그**뿐이다.
그 밖의 계약은 `generic_cli` 와 문자 그대로 같으며 conformance 스위트로 증명한다.
"""

import json
import sys
from pathlib import Path

import pytest

from harness.adapters.base import AgentRequest, PreflightKind, Usage
from harness.adapters.claude_cli import ClaudeCliAdapter
from harness.adapters.codex_cli import CodexCliAdapter
from harness.adapters.conformance import ARTIFACTS, BROKEN, CRASH, QUIET, SLOW, failures, run_suite
from harness.adapters.generic_cli import GenericCliAdapter
from harness.adapters.registry import build, known_types
from harness.models import ExecutionProfile

CLAIM = {"schema": "harness.claim/v1", "task_id": "T-001", "outcome_claim": "implemented"}

# argv 와 stdin 을 outbox 에 남기고 claude 모양의 usage JSON 을 찍는 가짜 CLI.
ECHO = """\
import json, os, sys
outbox = os.environ["HARNESS_OUTBOX"]
open(os.path.join(outbox, "argv.json"), "w").write(json.dumps(sys.argv[1:]))
open(os.path.join(outbox, "stdin.txt"), "w").write(sys.stdin.read())
print(json.dumps({"result": "done", "usage": {"input_tokens": 5, "output_tokens": 7},
                  "total_cost_usd": 0.01}))
"""

CODEX_ECHO = """\
import json, os, sys
outbox = os.environ["HARNESS_OUTBOX"]
open(os.path.join(outbox, "argv.json"), "w").write(json.dumps(sys.argv[1:]))
open(os.path.join(outbox, "stdin.txt"), "w").write(sys.stdin.read())
print("codex started")
print(json.dumps({"usage": {"input_tokens": 1, "output_tokens": 2}}))
print(json.dumps({"usage": {"input_tokens": 3, "output_tokens": 4}}))
"""


def script(tmp_path: Path, body: str, name: str = "fake_cli.py") -> list[str]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path)]


def request(tmp_path: Path, allowed_tools=None, prompt="do the thing") -> AgentRequest:
    workspace = tmp_path / "ws"
    outbox = tmp_path / "outbox" / "attempt-1"
    workspace.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    return AgentRequest(
        task_id="T-001",
        prompt=prompt,
        workspace=workspace,
        outbox=outbox,
        profile=ExecutionProfile.SAFE,
        allowed_tools=allowed_tools,
        timeout_s=30,
        env={"HARNESS_OUTBOX": str(outbox), "HARNESS_TASK_ID": "T-001", "HARNESS_ATTEMPT": "1"},
        attempt=1,
    )


def argv_seen(result_request: AgentRequest) -> list[str]:
    return json.loads((result_request.outbox / "argv.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- registry


def test_vendor_types_are_registered():
    assert "claude_cli" in known_types()
    assert "codex_cli" in known_types()
    assert build("c", "claude_cli", {}).name == "c"
    assert build("x", "codex_cli", {}).name == "x"


# --------------------------------------------------------------------------- claude_cli


def test_claude_builds_the_documented_argv_and_feeds_stdin(tmp_path):
    """docs/04 — `<binary> -p --output-format json <extra_args...> --add-dir <outbox>`,
    프롬프트는 stdin. outbox 는 워크스페이스 밖이라 `--add-dir` 없이는 claude 가 쓰지 못한다."""
    adapter = ClaudeCliAdapter(
        "claude", {"binary": script(tmp_path, ECHO), "extra_args": ["--model", "opus"]}
    )
    req = request(tmp_path, prompt="hello agent")
    adapter.execute(req)
    assert argv_seen(req) == [
        "-p", "--output-format", "json", "--model", "opus", "--add-dir", str(req.outbox)
    ]
    assert (req.outbox / "stdin.txt").read_text(encoding="utf-8") == "hello agent"


def test_claude_appends_the_tool_allowlist_per_request(tmp_path):
    adapter = ClaudeCliAdapter("claude", {"binary": script(tmp_path, ECHO)})
    req = request(tmp_path, allowed_tools=["Bash", "Edit"])
    adapter.execute(req)
    assert argv_seen(req)[-2:] == ["--allowedTools", "Bash,Edit"]


def test_claude_reports_usage_from_stdout_json(tmp_path):
    adapter = ClaudeCliAdapter("claude", {"binary": script(tmp_path, ECHO)})
    result = adapter.execute(request(tmp_path))
    assert result.usage == Usage(tokens_in=5, tokens_out=7, cost_usd=0.01)


def test_claude_usage_is_none_when_stdout_is_not_its_json(tmp_path):
    """추정하지 않는다 (docs/11). 파싱 실패는 usage 없음이지 오류가 아니다."""
    adapter = ClaudeCliAdapter(
        "claude", {"binary": script(tmp_path, "import sys; sys.stdin.read(); print('plain text')")}
    )
    result = adapter.execute(request(tmp_path))
    assert result.runtime_failure is None
    assert result.usage is None


def test_claude_capabilities():
    caps = ClaudeCliAdapter("claude", {}).capabilities()
    assert caps.reports_usage
    assert caps.supports_tool_allowlist
    assert not caps.supports_session_reuse


# --------------------------------------------------------------------------- codex_cli


def test_codex_builds_the_documented_argv_and_feeds_stdin(tmp_path):
    """docs/04 — `<binary> exec --json <extra_args...> -`, 프롬프트는 stdin."""
    adapter = CodexCliAdapter(
        "codex", {"binary": script(tmp_path, CODEX_ECHO), "extra_args": ["--sandbox", "off"]}
    )
    req = request(tmp_path, prompt="hi")
    adapter.execute(req)
    assert argv_seen(req) == ["exec", "--json", "--sandbox", "off", "-"]
    assert (req.outbox / "stdin.txt").read_text(encoding="utf-8") == "hi"


def test_codex_reads_usage_from_the_last_matching_json_line(tmp_path):
    """cost 는 벤더가 보고하지 않으므로 None 이다 (docs/04)."""
    adapter = CodexCliAdapter("codex", {"binary": script(tmp_path, CODEX_ECHO)})
    result = adapter.execute(request(tmp_path))
    assert result.usage == Usage(tokens_in=3, tokens_out=4, cost_usd=None)


def test_codex_usage_is_none_without_a_usage_line(tmp_path):
    adapter = CodexCliAdapter(
        "codex", {"binary": script(tmp_path, "import sys; sys.stdin.read(); print('{}')")}
    )
    assert adapter.execute(request(tmp_path)).usage is None


def test_codex_capabilities():
    caps = CodexCliAdapter("codex", {}).capabilities()
    assert caps.reports_usage
    assert not caps.supports_tool_allowlist
    assert not caps.supports_session_reuse


# --------------------------------------------------------------------------- preflight


@pytest.mark.parametrize("adapter_cls", [ClaudeCliAdapter, CodexCliAdapter])
def test_a_missing_binary_is_a_missing_prerequisite(adapter_cls):
    """docs/04 — CLI 미설치는 blocked 로 가는 prerequisite 부족이다."""
    report = adapter_cls("v", {"binary": "definitely-not-installed-a7f3"}).preflight()
    assert not report.ok
    assert report.kind is PreflightKind.MISSING_PREREQUISITE


# --------------------------------------------------------------------------- conformance

SCENARIO_BODIES = {
    QUIET: "import sys; sys.stdin.read()",
    ARTIFACTS: (
        "import json, os, sys; sys.stdin.read();"
        "outbox = os.environ['HARNESS_OUTBOX'];"
        f"open(os.path.join(outbox, 'result.json'), 'w').write(json.dumps({CLAIM!r}));"
        "open(os.path.join(outbox, 'handoff.json'), 'w').write("
        "json.dumps({'schema': 'harness.handoff/v1', 'task_id': 'T-001', 'public_api': ['x']}))"
    ),
    BROKEN: (
        "import os, sys; sys.stdin.read();"
        "open(os.path.join(os.environ['HARNESS_OUTBOX'], 'result.json'), 'w')"
        ".write('{ not json at all')"
    ),
    SLOW: "import time; time.sleep(30)",
    CRASH: "raise SystemExit(3)",
}


@pytest.mark.parametrize("adapter_cls", [ClaudeCliAdapter, CodexCliAdapter], ids=["claude", "codex"])
def test_the_vendor_adapter_passes_the_conformance_suite(adapter_cls, tmp_path):
    """docs/04 — 신규 어댑터의 합격 조건은 이 스위트 통과다."""

    def adapter_for(scenario):
        binary = script(tmp_path, SCENARIO_BODIES[scenario], name=f"cli-{scenario}.py")
        return adapter_cls("vendor", {"binary": binary, "timeout_grace_s": 2})

    checks = run_suite(adapter_for, tmp_path)
    assert failures(checks) == [], [(c.name, c.detail) for c in failures(checks)]


def test_the_same_cli_is_expressible_as_generic_cli(tmp_path):
    """docs/04 의 vendor-neutrality — 동등한 generic_cli 설정이 항상 존재한다."""
    binary = script(tmp_path, ECHO)
    generic = GenericCliAdapter(
        "g",
        {
            "command": [*binary, "-p", "--output-format", "json"],
            "prompt_delivery": "stdin",
            "usage_from": "stdout_json:usage",
        },
    )
    result = generic.execute(request(tmp_path))
    assert result.runtime_failure is None and result.exit_code == 0
    assert result.raw_claim_path is None  # ECHO 는 claim 을 만들지 않는다
    # 프로세스 계약은 동일하다. usage 의 형태 해석만이 벤더 어댑터가 더 아는 것이라
    # generic_cli 는 claude 의 키 이름(input_tokens)을 하네스 필드로 번역하지 못한다.
    assert result.usage.tokens_in is None and result.usage.tokens_out is None
