"""outbox 아티팩트의 정규화와 required handoff 게이트. docs/06 이 canonical 이다.

> Invalid claim is an invalid report, not automatically an invalid implementation.
"""

import json

import yaml

from harness.dag import load_tasks
from harness.exec.handoff import CLAIM, HANDOFF, gate, merge_output, normalize

VALID_CLAIM = {"schema": "harness.claim/v1", "task_id": "T-001", "outcome_claim": "implemented"}
VALID_HANDOFF = {
    "schema": "harness.handoff/v1",
    "task_id": "T-001",
    "public_api": ["POST /api/users"],
    "decisions": ["인증은 미들웨어에서"],
}

HARNESS_FIELDS = {"changed_files": ["src/api.py"], "created_files": [], "diff_stat": {"files": 1}}


def outbox(tmp_path, filename, content):
    directory = tmp_path / "outbox"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def task_with(repo, required=(), optional=()):
    data = {"id": "T-001", "name": "t", "kind": "implementation"}
    if required or optional:
        data["outputs"] = {"required": list(required), "optional": list(optional)}
    directory = repo / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "T-001.task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_tasks(repo)["T-001"]


# --------------------------------------------------------------------------- 정규화


def test_an_absent_artifact_is_absent_not_invalid(tmp_path):
    """docs/04 — 아티팩트가 없으면 없는 것이다. 만들어내지 않는다."""
    artifact = normalize(CLAIM, None, tmp_path / "promoted")
    assert not artifact.valid
    assert artifact.error is None
    assert artifact.path is None


def test_a_valid_claim_is_promoted_under_its_canonical_name(tmp_path):
    """docs/03 파일 배치 — outbox 의 result.json 은 claim.json 으로 승격된다."""
    raw = outbox(tmp_path, "result.json", VALID_CLAIM)
    artifact = normalize(CLAIM, raw, tmp_path / "promoted")

    assert artifact.valid
    assert artifact.path.name == "claim.json"
    assert json.loads(artifact.path.read_text(encoding="utf-8")) == VALID_CLAIM


def test_a_valid_handoff_is_promoted(tmp_path):
    raw = outbox(tmp_path, "handoff.json", VALID_HANDOFF)
    artifact = normalize(HANDOFF, raw, tmp_path / "promoted")
    assert artifact.valid
    assert artifact.path.name == "handoff.json"


def test_unparsable_json_is_preserved_as_invalid(tmp_path):
    raw = outbox(tmp_path, "result.json", "{ this is not json")
    artifact = normalize(CLAIM, raw, tmp_path / "promoted")

    assert not artifact.valid
    assert artifact.error
    assert artifact.path.name == "claim.invalid.json"
    assert artifact.path.read_text(encoding="utf-8") == "{ this is not json"


def test_a_schema_violation_is_preserved_as_invalid(tmp_path):
    """docs/03 — schema 필드가 없으면 invalid 다."""
    raw = outbox(tmp_path, "result.json", {"task_id": "T-001"})
    artifact = normalize(CLAIM, raw, tmp_path / "promoted")

    assert not artifact.valid
    assert artifact.path.name == "claim.invalid.json"


def test_an_invalid_artifact_reads_as_none_for_judgement(tmp_path):
    """docs/06 — 이후 판정에서 None 으로 취급된다."""
    raw = outbox(tmp_path, "handoff.json", "broken")
    assert normalize(HANDOFF, raw, tmp_path / "promoted").data is None


def test_promotion_never_touches_the_outbox_original(tmp_path):
    """docs/04 — 하네스가 읽어 검증한 뒤에만 승격한다. agent 산출물은 그대로 둔다."""
    raw = outbox(tmp_path, "result.json", VALID_CLAIM)
    before = raw.read_bytes()
    normalize(CLAIM, raw, tmp_path / "promoted")
    assert raw.read_bytes() == before


def test_a_broken_claim_does_not_touch_the_handoff(tmp_path):
    """docs/03 — 별개 파일이므로 하나가 깨져도 다른 하나는 살아남는다."""
    promoted = tmp_path / "promoted"
    normalize(CLAIM, outbox(tmp_path, "result.json", "broken"), promoted)
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), promoted)
    assert handoff.valid


# --------------------------------------------------------------------------- required 게이트


def test_a_task_that_requires_nothing_passes_without_any_handoff(repo, tmp_path):
    """docs/06 — outputs.required 가 비어 있으면 handoff 유무는 판정에 영향이 없다."""
    result = gate(task_with(repo), normalize(HANDOFF, None, tmp_path))
    assert result.ok


def test_a_present_required_field_passes_the_gate(repo, tmp_path):
    task = task_with(repo, required=["public_api"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), tmp_path / "p")
    assert gate(task, handoff).ok


def test_a_missing_required_field_fails_the_gate(repo, tmp_path):
    task = task_with(repo, required=["public_api", "migration_notes"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), tmp_path / "p")

    result = gate(task, handoff)

    assert not result.ok
    assert result.missing == ("migration_notes",)


def test_an_invalid_handoff_fails_the_gate_when_something_is_required(repo, tmp_path):
    task = task_with(repo, required=["public_api"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", "broken"), tmp_path / "p")
    assert not gate(task, handoff).ok


def test_gate_payload_matches_the_event_contract(repo, tmp_path):
    """docs/03 — handoff_missing 의 payload 는 required·present 다."""
    task = task_with(repo, required=["public_api"])
    payload = gate(task, normalize(HANDOFF, None, tmp_path)).missing_payload()
    assert set(payload) == {"required", "present"}


def test_a_missing_optional_output_is_not_a_gate_failure(repo, tmp_path):
    """docs/06 — optional 이 없으면 다음 task 컨텍스트에서 제외할 뿐 아무 판정도 하지 않는다."""
    task = task_with(repo, optional=["decisions"])
    assert gate(task, normalize(HANDOFF, None, tmp_path)).ok


# --------------------------------------------------------------------------- TaskOutput 병합


def test_merged_output_separates_harness_facts_from_agent_claims(repo, tmp_path):
    """docs/03 — 병합 결과 안에서도 fact 와 untrusted 의 출처를 구분해 표기한다."""
    task = task_with(repo, required=["public_api"], optional=["decisions"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), tmp_path / "p")

    output = merge_output(task, HARNESS_FIELDS, handoff)

    assert output["harness"]["changed_files"] == ["src/api.py"]
    assert output["agent"]["public_api"] == ["POST /api/users"]
    assert set(output) == {"harness", "agent"}


def test_harness_fields_are_present_even_without_a_handoff(repo, tmp_path):
    """docs/03 — 하네스 산출 필드는 agent 협조가 필요 없으므로 항상 존재한다."""
    output = merge_output(task_with(repo), HARNESS_FIELDS, normalize(HANDOFF, None, tmp_path))
    assert output["harness"]["diff_stat"] == {"files": 1}
    assert output["agent"] == {}


def test_only_declared_output_fields_are_merged(repo, tmp_path):
    """task 계약이 outputs 로 선언한 것만 다음 task 로 간다."""
    task = task_with(repo, required=["public_api"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), tmp_path / "p")

    output = merge_output(task, HARNESS_FIELDS, handoff)

    assert "decisions" not in output["agent"]  # 선언되지 않았다
    assert "schema" not in output["agent"]


def test_an_absent_optional_field_is_simply_excluded(repo, tmp_path):
    task = task_with(repo, optional=["migration_notes"])
    handoff = normalize(HANDOFF, outbox(tmp_path, "handoff.json", VALID_HANDOFF), tmp_path / "p")
    assert merge_output(task, HARNESS_FIELDS, handoff)["agent"] == {}
