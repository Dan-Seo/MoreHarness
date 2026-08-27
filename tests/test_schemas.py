"""schemas/ 가 docs/03 의 예제를 그대로 통과시키는지 검증한다."""

import json

import pytest
import yaml
from jsonschema import Draft202012Validator

SCHEMA_FILES = [
    "claim.schema.json",
    "handoff.schema.json",
    "task.schema.json",
    "spec.schema.json",
    "event.schema.json",
]


def load(schemas_dir, name):
    return json.loads((schemas_dir / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_schema_files_exist_and_are_valid_jsonschema(schemas_dir, name):
    Draft202012Validator.check_schema(load(schemas_dir, name))


def validator(schemas_dir, name):
    return Draft202012Validator(load(schemas_dir, name))


# --------------------------------------------------------------------------- claim


DOC_CLAIM = {
    "schema": "harness.claim/v1",
    "task_id": "T-003",
    "outcome_claim": "implemented",
    "commands_run": [{"cmd": "npm test", "exit_code": 0}],
    "blocked_hint": "DATABASE_URL 이 없어 보임",
}


def test_doc_claim_example_validates(schemas_dir):
    validator(schemas_dir, "claim.schema.json").validate(DOC_CLAIM)


def test_claim_requires_the_schema_marker(schemas_dir):
    """docs/03 — schema 필드가 없으면 invalid 다."""
    bad = {k: v for k, v in DOC_CLAIM.items() if k != "schema"}
    assert not validator(schemas_dir, "claim.schema.json").is_valid(bad)


def test_claim_outcome_is_one_of_three(schemas_dir):
    v = validator(schemas_dir, "claim.schema.json")
    for outcome in ("implemented", "blocked", "infeasible"):
        assert v.is_valid({**DOC_CLAIM, "outcome_claim": outcome})
    assert not v.is_valid({**DOC_CLAIM, "outcome_claim": "verified"})


def test_claim_has_no_blocked_reason_field(schemas_dir):
    """docs/03 — 필드 이름은 blocked_reason 이 아니라 blocked_hint 다."""
    schema = load(schemas_dir, "claim.schema.json")
    assert "blocked_hint" in schema["properties"]
    assert "blocked_reason" not in schema["properties"]


def test_claim_is_optional_shaped(schemas_dir):
    """claim 은 힌트다. schema 와 task_id 외에는 아무것도 요구하지 않는다."""
    assert validator(schemas_dir, "claim.schema.json").is_valid(
        {"schema": "harness.claim/v1", "task_id": "T-003"}
    )


# --------------------------------------------------------------------------- handoff


DOC_HANDOFF = {
    "schema": "harness.handoff/v1",
    "task_id": "T-003",
    "public_api": ["POST /api/users", "GET /api/users/:id"],
    "decisions": ["인증은 미들웨어에서 처리하고 라우트 핸들러는 인증을 가정한다"],
}


def test_doc_handoff_example_validates(schemas_dir):
    validator(schemas_dir, "handoff.schema.json").validate(DOC_HANDOFF)


def test_handoff_requires_the_schema_marker(schemas_dir):
    bad = {k: v for k, v in DOC_HANDOFF.items() if k != "schema"}
    assert not validator(schemas_dir, "handoff.schema.json").is_valid(bad)


def test_handoff_accepts_project_defined_fields(schemas_dir):
    """handoff 의 필드 집합은 task 계약이 정한다. schema 가 미리 고정하지 않는다."""
    assert validator(schemas_dir, "handoff.schema.json").is_valid(
        {"schema": "harness.handoff/v1", "task_id": "T-003", "migration_notes": ["x"]}
    )


# --------------------------------------------------------------------------- task


DOC_TASK = yaml.safe_load(
    """
    id: T-003
    name: api-layer
    kind: implementation
    satisfies: [R-002, R-005]
    depends_on: [T-001]
    risk: medium
    agent: default
    profile: worktree
    allowed_paths:   ["src/api/**", "tests/api/**"]
    forbidden_paths: []
    preconditions:
      - {kind: env_var, name: DATABASE_URL}
      - {kind: command, cmd: ["docker", "info"]}
      - {kind: file, path: ".env.local"}
    context:
      docs:    ["docs/ARCHITECTURE.md#api-layer"]
      files:   ["src/types/user.ts"]
      symbols: ["UserRepository"]
    acceptance:
      - cmd: ["npm", "run", "build"]
      - cmd: ["npm", "test", "--", "tests/api"]
        expect_fail_before: true
    outputs:
      required: [public_api]
      optional: [decisions]
    spec_hash: "sha256:abc"
    """
)


def test_doc_task_example_validates(schemas_dir):
    validator(schemas_dir, "task.schema.json").validate(DOC_TASK)


def test_task_minimal_form_validates(schemas_dir):
    assert validator(schemas_dir, "task.schema.json").is_valid(
        {"id": "T-001", "name": "seed", "kind": "analysis"}
    )


def test_task_acceptance_cmd_must_be_an_argv_list(schemas_dir):
    """docs/03 — cmd 는 문자열이 아니라 argv 리스트다."""
    bad = {**DOC_TASK, "acceptance": [{"cmd": "npm run build"}]}
    assert not validator(schemas_dir, "task.schema.json").is_valid(bad)


def test_task_precondition_cmd_must_be_an_argv_list(schemas_dir):
    bad = {**DOC_TASK, "preconditions": [{"kind": "command", "cmd": "docker info"}]}
    assert not validator(schemas_dir, "task.schema.json").is_valid(bad)


def test_task_rejects_unknown_kind_and_profile(schemas_dir):
    v = validator(schemas_dir, "task.schema.json")
    assert not v.is_valid({**DOC_TASK, "kind": "refactor"})
    assert not v.is_valid({**DOC_TASK, "profile": "yolo"})


def test_task_id_format_is_enforced(schemas_dir):
    v = validator(schemas_dir, "task.schema.json")
    assert not v.is_valid({**DOC_TASK, "id": "task-3"})
    assert not v.is_valid({**DOC_TASK, "satisfies": ["REQ-2"]})


# --------------------------------------------------------------------------- spec


DOC_SPEC = yaml.safe_load(
    """
    slug: user-api
    intent: "사용자 CRUD API 를 만든다"
    requirements:
      - id: R-001
        statement: "POST /api/users 로 사용자를 생성할 수 있다"
        rationale: "가입 플로우의 전제"
        acceptance_hint: "생성 후 201 과 id 를 반환"
      - id: R-002
        statement: "이메일 중복은 409 로 거절한다"
    open_questions:
      - "[NEEDS CLARIFICATION] 소프트 삭제인가 하드 삭제인가"
    """
)


def test_doc_spec_example_validates(schemas_dir):
    validator(schemas_dir, "spec.schema.json").validate(DOC_SPEC)


def test_spec_requirement_id_format_is_enforced(schemas_dir):
    bad = {**DOC_SPEC, "requirements": [{"id": "R1", "statement": "x"}]}
    assert not validator(schemas_dir, "spec.schema.json").is_valid(bad)


# --------------------------------------------------------------------------- event


DOC_EVENT = {
    "id": "run-20260827-1432-000042",
    "seq": 42,
    "ts": "2026-08-27T14:35:02.113+09:00",
    "type": "ac_post_executed",
    "run_id": "run-20260827-1432",
    "task_id": "T-003",
    "attempt": 1,
    "payload": {},
}


def test_doc_event_example_validates(schemas_dir):
    validator(schemas_dir, "event.schema.json").validate(DOC_EVENT)


def test_event_allows_null_task_id_and_attempt(schemas_dir):
    run_level = {**DOC_EVENT, "type": "run_started", "task_id": None, "attempt": None}
    assert validator(schemas_dir, "event.schema.json").is_valid(run_level)


def test_event_type_enum_matches_the_catalog(schemas_dir):
    from harness.events import EventType

    schema = load(schemas_dir, "event.schema.json")
    assert set(schema["properties"]["type"]["enum"]) == {e.value for e in EventType}


def test_event_rejects_unknown_type_and_zero_seq(schemas_dir):
    v = validator(schemas_dir, "event.schema.json")
    assert not v.is_valid({**DOC_EVENT, "type": "task_completed"})
    assert not v.is_valid({**DOC_EVENT, "seq": 0})


def test_every_event_written_by_the_store_validates(schemas_dir, tmp_path):
    from harness.events import EventType
    from harness.store import Store

    v = validator(schemas_dir, "event.schema.json")
    store = Store(tmp_path / "runs" / "run-1")
    store.append(
        EventType.RUN_STARTED,
        {"manifest": {"task_ids": ["T-001"]}, "profile": "safe", "adapter": "mock", "max_parallel": 1},
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "verified", "attempt": 1, "reason": None, "next_state": "done"},
        task_id="T-001",
        attempt=1,
    )
    for line in store.journal.path.read_text(encoding="utf-8").splitlines():
        v.validate(json.loads(line))
