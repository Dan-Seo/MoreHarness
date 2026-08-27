"""docs/04 의 conformance 스위트 — 신규 어댑터의 합격 조건이다.

`mock` 과 `generic_cli` 가 **같은 스위트**를 통과한다. 어느 하나만 통과한다면 그것은
프로토콜이 벤더 중립이 아니라는 뜻이다.
"""

import json
import sys

import pytest

from harness.adapters import conformance
from harness.adapters.base import AgentResult, PreflightKind
from harness.adapters.conformance import ARTIFACTS, BROKEN, CRASH, QUIET, SLOW, failures, run_suite
from harness.adapters.generic_cli import GenericCliAdapter
from harness.adapters.mock import MockAdapter
from harness.adapters.registry import build, known_types

CLAIM = {"schema": "harness.claim/v1", "task_id": "T-001", "outcome_claim": "implemented"}
HANDOFF = {"schema": "harness.handoff/v1", "task_id": "T-001", "public_api": ["x"]}

WRITE_ARTIFACTS = (
    "import os, sys, json;"
    "outbox = sys.argv[1];"
    f"open(os.path.join(outbox, 'result.json'), 'w').write(json.dumps({CLAIM!r}));"
    f"open(os.path.join(outbox, 'handoff.json'), 'w').write(json.dumps({HANDOFF!r}))"
)
WRITE_BROKEN = (
    "import os, sys;"
    "open(os.path.join(sys.argv[1], 'result.json'), 'w').write('{ not json at all')"
)

CLI_SCRIPTS = {
    QUIET: "pass",
    ARTIFACTS: WRITE_ARTIFACTS,
    BROKEN: WRITE_BROKEN,
    SLOW: "import time; time.sleep(30)",
    CRASH: "raise SystemExit(3)",
}

MOCK_SCENARIOS = {
    QUIET: {},
    ARTIFACTS: {"default": {"claim": CLAIM, "handoff": HANDOFF}},
    BROKEN: {"default": {"claim": "{ not json at all"}},
    SLOW: {"default": {"delay_s": 30}},
    CRASH: {"default": {"exit_code": 3}},
}


def mock_for(scenario):
    return MockAdapter("mock", MOCK_SCENARIOS[scenario])


def cli_for(scenario):
    return GenericCliAdapter(
        "cli",
        {
            "command": [sys.executable, "-c", CLI_SCRIPTS[scenario], "{outbox}"],
            "prompt_delivery": "file",
            "timeout_grace_s": 2,
        },
    )


# --------------------------------------------------------------------------- 스위트


@pytest.mark.parametrize("adapter_for", [mock_for, cli_for], ids=["mock", "generic_cli"])
def test_the_adapter_passes_the_conformance_suite(adapter_for, tmp_path):
    checks = run_suite(adapter_for, tmp_path)
    assert failures(checks) == [], [(c.name, c.detail) for c in failures(checks)]


def test_the_suite_actually_catches_a_violation(tmp_path):
    """스위트가 아무것도 잡지 못한다면 통과는 의미가 없다."""

    class Judging(MockAdapter):
        def execute(self, request):
            result = super().execute(request)
            # 아티팩트를 어댑터가 고친다 — docs/04 가 금지하는 바로 그것
            if result.raw_claim_path:
                result.raw_claim_path.write_text(json.dumps(CLAIM), encoding="utf-8")
            return result

    checks = run_suite(lambda s: Judging("bad", MOCK_SCENARIOS[s]), tmp_path)
    assert "아티팩트 파손" in [check.name for check in failures(checks)]


def test_every_declared_scenario_is_exercised():
    assert set(conformance.SCENARIOS) == set(CLI_SCRIPTS) == set(MOCK_SCENARIOS)


# --------------------------------------------------------------------------- generic_cli


def test_generic_cli_is_registered():
    assert "generic_cli" in known_types()
    assert build("x", "generic_cli", {"command": ["echo"]}).name == "x"


def test_placeholders_resolve_from_the_request(tmp_path):
    """docs/04 — {workspace} {outbox} {prompt_file} {timeout_s} {task_id} {attempt}"""
    script = "import sys; print('|'.join(sys.argv[1:]))"
    adapter = GenericCliAdapter(
        "cli",
        {
            "command": [
                sys.executable,
                "-c",
                script,
                "{workspace}",
                "{outbox}",
                "{prompt_file}",
                "{timeout_s}",
                "{task_id}",
                "{attempt}",
            ]
        },
    )
    request = conformance._request(tmp_path, "x", timeout_s=30)
    result = adapter.execute(request)

    assert str(request.workspace) in result.stdout
    assert str(request.outbox) in result.stdout
    assert "T-001" in result.stdout


def test_an_unknown_placeholder_is_a_misconfiguration(tmp_path):
    """docs/10 — 코드·설정 결함은 error 이지 blocked 가 아니다."""
    report = GenericCliAdapter("cli", {"command": ["echo", "{nope}"]}).preflight()
    assert report.kind is PreflightKind.MISCONFIGURED


def test_a_missing_executable_is_a_missing_prerequisite():
    """docs/10 — 설치하면 해결되는 것은 blocked 다."""
    report = GenericCliAdapter("cli", {"command": ["no-such-binary-xyz"]}).preflight()
    assert report.kind is PreflightKind.MISSING_PREREQUISITE


def test_an_unknown_prompt_delivery_is_a_misconfiguration():
    report = GenericCliAdapter("cli", {"command": ["echo"], "prompt_delivery": "telepathy"}).preflight()
    assert report.kind is PreflightKind.MISCONFIGURED


def test_the_prompt_can_be_delivered_on_stdin(tmp_path):
    adapter = GenericCliAdapter(
        "cli",
        {
            "command": [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
            "prompt_delivery": "stdin",
        },
    )
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))
    assert "conformance: x" in result.stdout


def test_the_prompt_can_be_delivered_in_argv(tmp_path):
    adapter = GenericCliAdapter(
        "cli",
        {
            "command": [sys.executable, "-c", "import sys; print(sys.argv[1])"],
            "prompt_delivery": "argv",
        },
    )
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))
    assert "conformance: x" in result.stdout


def test_the_prompt_file_lands_in_the_outbox_not_the_workspace(tmp_path):
    """프롬프트가 워크스페이스에 떨어지면 그것이 diff 로 잡혀 판정을 오염시킨다."""
    adapter = GenericCliAdapter("cli", {"command": [sys.executable, "-c", "pass"]})
    request = conformance._request(tmp_path, "x", timeout_s=30)
    adapter.execute(request)

    assert (request.outbox / "prompt.txt").is_file()
    assert list(request.workspace.iterdir()) == []


def test_env_passthrough_narrows_but_never_widens(tmp_path):
    """docs/04 — request.env 에 없는 변수를 자식 프로세스에 넣지 않는다."""
    script = "import os; print(sorted(k for k in os.environ if k.startswith('HARNESS') or k == 'SECRET'))"
    adapter = GenericCliAdapter(
        "cli", {"command": [sys.executable, "-c", script], "env_passthrough": ["PATH"]}
    )
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))

    assert "SECRET" not in result.stdout
    assert "HARNESS_OUTBOX" in result.stdout  # 없으면 agent 가 산출물을 둘 곳을 모른다


def test_usage_is_none_when_the_vendor_does_not_report_it(tmp_path):
    """docs/11 — 미보고 시 null 이다. 추정 모델을 만들지 않는다."""
    adapter = GenericCliAdapter("cli", {"command": [sys.executable, "-c", "pass"]})
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))
    assert result.usage is None
    assert adapter.capabilities().reports_usage is False


def test_usage_is_read_from_stdout_json_when_configured(tmp_path):
    payload = {"usage": {"tokens_in": 10, "tokens_out": 5, "cost_usd": 0.01}}
    adapter = GenericCliAdapter(
        "cli",
        {
            "command": [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"],
            "usage_from": "stdout_json:usage",
        },
    )
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))
    assert result.usage.tokens_in == 10
    assert result.usage.cost_usd == 0.01


def test_a_nonzero_exit_is_collected_without_interpretation(tmp_path):
    """docs/04 — 어댑터는 exit code 를 수집만 한다."""
    adapter = GenericCliAdapter("cli", {"command": [sys.executable, "-c", "raise SystemExit(7)"]})
    result = adapter.execute(conformance._request(tmp_path, "x", timeout_s=30))
    assert isinstance(result, AgentResult)
    assert result.exit_code == 7
    assert result.runtime_failure is None
