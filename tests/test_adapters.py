"""docs/04 의 어댑터 계약을 검증한다."""

import dataclasses
import json
import re
from pathlib import Path

import pytest

from harness.adapters import base as base_module
from harness.adapters import mock as mock_module
from harness.adapters import registry as registry_module
from harness.adapters.base import AgentRequest, AgentResult, RuntimeFailure
from harness.adapters.mock import MockAdapter
from harness.adapters.registry import build, known_types
from harness.errors import AdapterNotFoundError
from harness.models import ExecutionProfile


def make_request(tmp_path, task_id="T-001", attempt=1):
    workspace = tmp_path / "worktree"
    outbox = tmp_path / "outbox" / f"attempt-{attempt}"
    workspace.mkdir(parents=True, exist_ok=True)
    return AgentRequest(
        task_id=task_id,
        prompt="do the thing",
        workspace=workspace,
        outbox=outbox,
        profile=ExecutionProfile.WORKTREE,
        allowed_tools=None,
        timeout_s=60,
        env={"HARNESS_OUTBOX": str(outbox)},
        attempt=attempt,
    )


# --------------------------------------------------------------------------- 프로토콜


def test_agent_result_has_no_success_field():
    """docs/04 — 어댑터는 성공을 판단하지 않는다."""
    names = {f.name for f in dataclasses.fields(AgentResult)}
    assert "success" not in names
    assert "verdict" not in names
    assert "ok" not in names


def test_agent_result_fields_match_the_protocol():
    assert {f.name for f in dataclasses.fields(AgentResult)} == {
        "exit_code",
        "stdout",
        "stderr",
        "raw_claim_path",
        "raw_handoff_path",
        "duration_s",
        "usage",
        "transcript_path",
        "runtime_failure",
    }


def test_agent_request_fields_match_the_protocol():
    assert {f.name for f in dataclasses.fields(AgentRequest)} == {
        "task_id",
        "prompt",
        "workspace",
        "outbox",
        "profile",
        "allowed_tools",
        "timeout_s",
        "env",
        "attempt",
        "container",
    }


def test_runtime_failure_values():
    assert {f.value for f in RuntimeFailure} == {"timeout", "killed", "protocol_violation"}


def test_preflight_kinds():
    from harness.adapters.base import PreflightKind

    assert {k.value for k in PreflightKind} == {
        "ok",
        "missing_prerequisite",
        "misconfigured",
        "internal_error",
    }


def test_requests_and_results_are_frozen(tmp_path):
    request = make_request(tmp_path)
    with pytest.raises(Exception):
        request.task_id = "T-999"


def test_adapters_depend_only_on_models_and_errors():
    """docs/02 — adapters/* 는 models 와 errors 에만 의존한다. 형제 모듈은 같은 층이다."""
    for module in (base_module, registry_module, mock_module):
        source = open(module.__file__, encoding="utf-8").read()
        imported = set(re.findall(r"^(?:from|import) harness\.([A-Za-z_.]+)", source, re.MULTILINE))
        outside = {name for name in imported if not name.startswith("adapters")}
        assert outside <= {"models", "errors"}, (module.__name__, outside)


def test_no_vendor_sdk_is_imported():
    for module in (base_module, registry_module, mock_module):
        source = open(module.__file__, encoding="utf-8").read()
        for vendor in ("anthropic", "openai"):
            assert vendor not in source


# --------------------------------------------------------------------------- registry


def test_registry_knows_mock():
    assert "mock" in known_types()


def test_registry_builds_a_named_adapter():
    adapter = build(name="default", type_name="mock", options={})
    assert adapter.name == "default"


def test_registry_rejects_an_unknown_type():
    with pytest.raises(AdapterNotFoundError):
        build(name="x", type_name="nope", options={})


# --------------------------------------------------------------------------- mock


def test_mock_preflight_is_ok():
    report = MockAdapter("default", {}).preflight()
    assert report.ok
    assert report.kind == "ok"


def test_mock_runs_without_network_or_llm(tmp_path):
    """M0 완료 기준 — LLM 도 네트워크도 없이 동작한다."""
    adapter = MockAdapter("default", {"default": {"exit_code": 0}})
    result = adapter.execute(make_request(tmp_path))
    assert isinstance(result, AgentResult)
    assert result.exit_code == 0


def test_mock_is_deterministic(tmp_path):
    scenario = {"tasks": {"T-001": {"exit_code": 3, "stdout": "hi", "delay_s": 0}}}
    first = MockAdapter("default", scenario).execute(make_request(tmp_path / "a"))
    second = MockAdapter("default", scenario).execute(make_request(tmp_path / "b"))
    assert (first.exit_code, first.stdout, first.duration_s) == (
        second.exit_code,
        second.stdout,
        second.duration_s,
    )
    assert first.exit_code == 3


def test_mock_writes_artifacts_into_the_outbox_only(tmp_path):
    scenario = {
        "tasks": {
            "T-001": {
                "claim": {"schema": "harness.claim/v1", "task_id": "T-001", "outcome_claim": "implemented"},
                "handoff": {"schema": "harness.handoff/v1", "task_id": "T-001", "public_api": ["x"]},
            }
        }
    }
    request = make_request(tmp_path)
    result = MockAdapter("default", scenario).execute(request)

    assert result.raw_claim_path == request.outbox / "result.json"
    assert result.raw_handoff_path == request.outbox / "handoff.json"
    assert json.loads(result.raw_claim_path.read_text(encoding="utf-8"))["task_id"] == "T-001"
    assert list(request.workspace.iterdir()) == []


def test_mock_returns_none_when_there_are_no_artifacts(tmp_path):
    """docs/04 — 아티팩트가 없으면 None 을 반환한다. 만들어내지 않는다."""
    result = MockAdapter("default", {"tasks": {"T-001": {"exit_code": 0}}}).execute(
        make_request(tmp_path)
    )
    assert result.raw_claim_path is None
    assert result.raw_handoff_path is None


def test_mock_returns_a_path_for_a_broken_artifact_without_parsing_it(tmp_path):
    """docs/04 — 내용이 깨져 있어도 경로만 반환한다. 파싱하거나 고치지 않는다."""
    scenario = {"tasks": {"T-001": {"claim": "{ this is not json"}}}
    request = make_request(tmp_path)
    result = MockAdapter("default", scenario).execute(request)
    assert result.raw_claim_path == request.outbox / "result.json"
    assert result.raw_claim_path.read_text(encoding="utf-8") == "{ this is not json"


def test_mock_writes_workspace_files_when_the_scenario_says_so(tmp_path):
    """agent 가 워크스페이스에 쓰는 것은 정상적인 작업이다. (docs/04)"""
    scenario = {"tasks": {"T-001": {"files": {"src/api.py": "print('x')\n"}}}}
    request = make_request(tmp_path)
    MockAdapter("default", scenario).execute(request)
    assert (request.workspace / "src" / "api.py").read_text(encoding="utf-8") == "print('x')\n"


def test_mock_uses_a_new_outbox_per_attempt(tmp_path):
    """docs/04 — attempt 마다 새 디렉토리."""
    scenario = {"default": {"claim": {"schema": "harness.claim/v1", "task_id": "T-001"}}}
    adapter = MockAdapter("default", scenario)
    first = adapter.execute(make_request(tmp_path, attempt=1))
    second = adapter.execute(make_request(tmp_path, attempt=2))
    assert first.raw_claim_path != second.raw_claim_path
    assert first.raw_claim_path.exists() and second.raw_claim_path.exists()


def test_mock_reports_runtime_failure_when_the_scenario_declares_one(tmp_path):
    scenario = {"tasks": {"T-001": {"exit_code": -9, "runtime_failure": "timeout"}}}
    result = MockAdapter("default", scenario).execute(make_request(tmp_path))
    assert result.runtime_failure is RuntimeFailure.TIMEOUT


def test_mock_reports_no_usage_by_default(tmp_path):
    """벤더가 보고하지 않으면 None 이다. 추정하지 않는다. (docs/11)"""
    result = MockAdapter("default", {}).execute(make_request(tmp_path))
    assert result.usage is None
    assert MockAdapter("default", {}).capabilities().reports_usage is False


def test_mock_never_raises_on_an_unknown_task(tmp_path):
    result = MockAdapter("default", {"tasks": {}}).execute(make_request(tmp_path, task_id="T-777"))
    assert result.exit_code == 0


def test_mock_loads_a_scenario_file_through_the_registry(tmp_path):
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("tasks:\n  T-001:\n    exit_code: 7\n", encoding="utf-8")
    adapter = build(name="default", type_name="mock", options={"scenario": str(scenario_path)})
    assert adapter.execute(make_request(tmp_path)).exit_code == 7


def test_mock_accepts_a_scenario_written_directly_in_the_options():
    """fixture 는 실행 시점에 위치가 정해지므로 절대 경로를 미리 쓸 수 없다 (docs/04)."""
    adapter = build(name="default", type_name="mock", options={"tasks": {"T-001": {"exit_code": 4}}})
    assert adapter.execute(make_request(Path(__file__).parent / "..")).exit_code == 4
