"""precondition 검사와 environment verifier.

docs/03 이 precondition 문법을, docs/06 이 blocked 판정에서의 쓰임을 정한다.
`blocked` 는 하네스 소유 증거로만 도달할 수 있다 — probe 가 그 증거다.
"""

import re
import sys

from harness.policy import CommandPolicy
from harness.probes import check, check_all, matching_blocked_signal

PYTHON = re.escape(sys.executable)


def allow_python():
    return CommandPolicy.from_config(
        {"default": "require_approval", "rules": [{"match": PYTHON, "verdict": "allow"}]}
    )


def run(precondition, tmp_path, policy=None, env=None):
    return check(precondition, policy or allow_python(), cwd=tmp_path, timeout_s=30, env=env)


# --------------------------------------------------------------------------- env_var


def test_env_var_probe_passes_when_the_variable_exists(tmp_path):
    result = run({"kind": "env_var", "name": "DATABASE_URL"}, tmp_path, env={"DATABASE_URL": "x"})
    assert result.ok
    assert result.name == "DATABASE_URL"


def test_env_var_probe_fails_when_the_variable_is_absent(tmp_path):
    assert not run({"kind": "env_var", "name": "DATABASE_URL"}, tmp_path, env={}).ok


def test_env_var_probe_never_records_the_value(tmp_path):
    """docs/03 — 존재 여부만 검사한다. 값은 기록하지 않는다."""
    secret = "postgres://user:hunter2@db/prod"
    result = run({"kind": "env_var", "name": "DATABASE_URL"}, tmp_path, env={"DATABASE_URL": secret})
    assert secret not in result.detail
    assert "hunter2" not in repr(result.to_payload())


# --------------------------------------------------------------------------- file


def test_file_probe_follows_the_workspace(tmp_path):
    (tmp_path / ".env.local").write_text("x", encoding="utf-8")
    assert run({"kind": "file", "path": ".env.local"}, tmp_path).ok


def test_file_probe_fails_when_the_file_is_absent(tmp_path):
    assert not run({"kind": "file", "path": ".env.local"}, tmp_path).ok


# --------------------------------------------------------------------------- command


def test_command_probe_passes_on_exit_zero(tmp_path):
    assert run({"kind": "command", "cmd": [sys.executable, "-c", "pass"]}, tmp_path).ok


def test_command_probe_fails_on_nonzero_exit(tmp_path):
    result = run({"kind": "command", "cmd": [sys.executable, "-c", "raise SystemExit(3)"]}, tmp_path)
    assert not result.ok


def test_command_probe_goes_through_command_policy(tmp_path):
    """docs/06 — kind: command precondition 은 Command Policy 적용 대상이다."""
    marker = tmp_path / "should-not-exist"
    code = f"open({str(marker)!r}, 'w').close()"
    fail_closed = CommandPolicy.from_config({})  # 규칙 없음 = 전부 require_approval

    result = run({"kind": "command", "cmd": [sys.executable, "-c", code]}, tmp_path, fail_closed)

    assert not result.ok
    assert not marker.exists()
    assert result.decision is not None


def test_command_probe_exposes_the_policy_decision(tmp_path):
    """deny 가 런타임에 도달했는지 호출자가 구분할 수 있어야 한다 (docs/03 needs_replan)."""
    deny = CommandPolicy.from_config({"default": "deny"})
    result = run({"kind": "command", "cmd": [sys.executable, "-c", "pass"]}, tmp_path, deny)
    assert str(result.decision.verdict) == "deny"


def test_a_non_command_probe_has_no_policy_decision(tmp_path):
    assert run({"kind": "file", "path": "x"}, tmp_path).decision is None


# --------------------------------------------------------------------------- 계약


def test_probe_payload_matches_the_event_contract(tmp_path):
    """docs/03 — precondition_checked 의 payload 는 kind·name·ok·detail 이다."""
    payload = run({"kind": "file", "path": "x"}, tmp_path).to_payload()
    assert set(payload) == {"kind", "name", "ok", "detail"}


def test_an_unknown_probe_kind_is_reported_not_raised(tmp_path):
    result = run({"kind": "moon_phase"}, tmp_path)
    assert not result.ok
    assert "moon_phase" in result.detail


def test_check_all_runs_every_precondition(tmp_path):
    results = check_all(
        [{"kind": "file", "path": "a"}, {"kind": "env_var", "name": "NOPE"}],
        allow_python(),
        cwd=tmp_path,
        timeout_s=30,
        env={},
    )
    assert [r.ok for r in results] == [False, False]


def test_check_all_on_no_preconditions_is_empty(tmp_path):
    assert check_all([], allow_python(), cwd=tmp_path, timeout_s=30) == []


# --------------------------------------------------------------------------- blocked_signals


def test_blocked_signals_match_against_harness_owned_stderr():
    """docs/06 — AC stderr 를 config 의 blocked_signals 패턴과 대조한다."""
    assert matching_blocked_signal("psql: connection refused", ["connection refused"]) == (
        "connection refused"
    )


def test_blocked_signals_default_to_matching_nothing():
    """기본이 빈 목록인 것은 의도다 (docs/06). 오탐의 결과는 잘못된 blocked 다."""
    assert matching_blocked_signal("connection refused", []) is None


def test_probes_do_not_import_higher_layers():
    from harness import probes as probes_module

    source = open(probes_module.__file__, encoding="utf-8").read()
    for forbidden in ("harness.exec", "harness.cli", "harness.eval", "harness.store"):
        assert forbidden not in source
