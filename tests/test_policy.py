"""Command Policy — docs/06 이 canonical 이다.

핵심 계약: 하네스가 서브프로세스를 띄우는 모든 지점이 이 모듈을 통과한다.
정규식 매칭은 보안 경계가 아니라 잘못된 planner 와 저장소 인젝션에 대한 가드레일이다.
"""

import re
import sys

import pytest
import yaml

from harness.policy import (
    CommandPolicy,
    PolicyVerdict,
    approval_hash,
    load_approvals,
    normalize,
)

DOC_RULES = yaml.safe_load(
    r"""
    default: require_approval
    rules:
      - {match: '^(npm|pnpm|yarn) (test|run (build|lint|typecheck))$', verdict: allow}
      - {match: '^(pytest|python -m pytest)', verdict: allow}
      - {match: '^git (status|diff|log)', verdict: allow}
      - {match: 'rm\s+-rf|git\s+push\s+--force|git\s+reset\s+--hard|^sudo|DROP\s+DATABASE|id_rsa', verdict: deny}
      - {match: '^(curl|wget|npm install|pip install|terraform apply|kubectl apply)', verdict: require_approval}
    """
)


def doc_policy(approvals=()):
    return CommandPolicy.from_config(DOC_RULES, approvals)


PYTHON = re.escape(sys.executable)


def python_policy():
    """테스트가 실제로 실행할 커맨드만 allow 하는 최소 정책."""
    return CommandPolicy.from_config(
        {"default": "require_approval", "rules": [{"match": PYTHON, "verdict": "allow"}]}
    )


def py(code):
    return [sys.executable, "-c", code]


# --------------------------------------------------------------------------- 정규화


def test_normalization_joins_argv_with_spaces():
    """docs/06 — 매칭 대상은 argv 를 공백으로 join 한 정규화 문자열이다."""
    assert normalize(["npm", "run", "build"]) == "npm run build"


def test_a_string_command_is_rejected():
    """cmd 는 argv 리스트다 (docs/03). 문자열을 받아 쪼개는 편의를 제공하지 않는다."""
    with pytest.raises(TypeError):
        normalize("npm run build")


# --------------------------------------------------------------------------- 판정 순서


def test_first_matching_rule_wins():
    decision = doc_policy().decide(["git", "status"])
    assert decision.verdict is PolicyVerdict.ALLOW
    assert decision.rule == "^git (status|diff|log)"


def test_no_match_falls_back_to_the_default():
    decision = doc_policy().decide(["make", "release"])
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert decision.rule is None


def test_the_default_is_fail_closed():
    """docs/06 — 기본은 require_approval 이다. 규칙이 없어도 무엇도 자동 실행되지 않는다."""
    empty = CommandPolicy.from_config({})
    assert empty.decide(["anything"]).verdict is PolicyVerdict.REQUIRE_APPROVAL


def test_an_absent_command_policy_key_is_still_fail_closed():
    """docs/06 — 키의 부재에도 fail-closed 가 그대로 적용된다."""
    decision = CommandPolicy.from_config(None).decide(["npm", "test"])
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL


def test_denied_commands_are_denied():
    for cmd in (["rm", "-rf", "/"], ["sudo", "reboot"], ["git", "push", "--force"]):
        assert doc_policy().decide(cmd).verdict is PolicyVerdict.DENY, cmd


def test_decision_payload_matches_the_event_contract():
    """docs/03 — command_policy_decision 의 payload 는 cmd·verdict·rule·approver 다."""
    payload = doc_policy().decide(["npm", "test"]).to_payload()
    assert set(payload) == {"cmd", "verdict", "rule", "approver"}


# --------------------------------------------------------------------------- shell


def test_declaring_shell_promotes_an_allowed_command_to_require_approval():
    """docs/06 — shell: true 선언 자체가 자동으로 require_approval 로 승격된다."""
    policy = doc_policy()
    assert policy.decide(["npm", "test"]).verdict is PolicyVerdict.ALLOW
    assert policy.decide(["npm", "test"], shell=True).verdict is PolicyVerdict.REQUIRE_APPROVAL


def test_shell_promotion_never_weakens_a_deny():
    """승격은 상향이다. deny 를 require_approval 로 낮추지 않는다."""
    assert doc_policy().decide(["sudo", "reboot"], shell=True).verdict is PolicyVerdict.DENY


# --------------------------------------------------------------------------- 승인


def test_an_approval_is_keyed_by_the_hash_of_the_normalized_string():
    cmd = ["npm", "install"]
    approvals = [{"cmd": cmd, "hash": approval_hash(cmd), "approver": "me", "scope": "project"}]
    decision = doc_policy(approvals).decide(cmd)
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert decision.approver == "me"
    assert decision.may_execute


def test_changing_one_argument_invalidates_the_approval():
    """docs/06 — 해시가 키이므로 인자가 하나라도 바뀌면 승인이 재사용되지 않는다."""
    cmd = ["npm", "install"]
    approvals = [{"cmd": cmd, "hash": approval_hash(cmd), "approver": "me"}]
    decision = doc_policy(approvals).decide(["npm", "install", "--force"])
    assert decision.approver is None
    assert not decision.may_execute


def test_an_unapproved_command_may_not_execute():
    """무인 실행에서 미승인이면 실행하지 않는다. verdict blocked 는 호출자가 부여한다."""
    assert not doc_policy().decide(["curl", "https://example.com"]).may_execute


def test_an_approval_cannot_rescue_a_denied_command():
    cmd = ["sudo", "reboot"]
    approvals = [{"cmd": cmd, "hash": approval_hash(cmd), "approver": "me"}]
    assert not doc_policy(approvals).decide(cmd).may_execute


def test_approvals_load_from_the_control_plane_file(repo):
    path = repo / ".harness" / "approved_commands.yaml"
    path.write_text(
        yaml.safe_dump({"approvals": [{"cmd": ["npm", "install"], "hash": "sha256:x"}]}),
        encoding="utf-8",
    )
    assert load_approvals(path)[0].hash == "sha256:x"


def test_loading_approvals_from_a_missing_file_is_empty(tmp_path):
    assert load_approvals(tmp_path / "nope.yaml") == ()


# --------------------------------------------------------------------------- 실행


def test_an_allowed_command_runs_and_reports_what_the_harness_observed(tmp_path):
    result = python_policy().run(py("print('hi')"), cwd=tmp_path, timeout_s=30)
    assert result.executed
    assert result.exit_code == 0
    assert "hi" in result.stdout


def test_a_denied_command_is_never_executed(tmp_path):
    """M1 완료 기준 ③ — deny 커맨드가 실행되지 않는다."""
    marker = tmp_path / "should-not-exist"
    policy = CommandPolicy.from_config(
        {"default": "allow", "rules": [{"match": PYTHON, "verdict": "deny"}]}
    )
    result = policy.run(py(f"open({str(marker)!r}, 'w').close()"), cwd=tmp_path, timeout_s=30)

    assert not result.executed
    assert result.exit_code is None
    assert result.decision.verdict is PolicyVerdict.DENY
    assert not marker.exists()


def test_an_unapproved_command_is_never_executed(tmp_path):
    marker = tmp_path / "should-not-exist"
    policy = CommandPolicy.from_config({})  # 규칙 없음 = 전부 require_approval
    result = policy.run(py(f"open({str(marker)!r}, 'w').close()"), cwd=tmp_path, timeout_s=30)

    assert not result.executed
    assert not marker.exists()


def test_execution_does_not_go_through_a_shell(tmp_path):
    """docs/06 — 기본 실행은 argv 리스트 + shell=False 다. 메타문자가 해석되지 않는다."""
    marker = tmp_path / "pwned"
    result = python_policy().run(
        py("print('x')") + [";", f"touch {marker}"], cwd=tmp_path, timeout_s=30
    )
    assert result.executed
    assert not marker.exists()


def test_a_timeout_is_reported_not_raised(tmp_path):
    result = python_policy().run(py("import time; time.sleep(30)"), cwd=tmp_path, timeout_s=1)
    assert result.timed_out
    assert result.exit_code != 0


def test_a_missing_executable_is_reported_not_raised(tmp_path):
    policy = CommandPolicy.from_config({"default": "allow"})
    result = policy.run(["no-such-binary-xyz"], cwd=tmp_path, timeout_s=5)
    assert result.executed
    assert result.exit_code != 0
    assert result.stderr


def test_policy_does_not_import_higher_layers():
    """docs/02 — policy 는 exec·cli·eval 을 import 하지 않는다."""
    from harness import policy as policy_module

    source = open(policy_module.__file__, encoding="utf-8").read()
    for forbidden in ("harness.exec", "harness.cli", "harness.eval", "harness.store"):
        assert forbidden not in source
