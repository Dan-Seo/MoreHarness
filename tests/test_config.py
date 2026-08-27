"""`.harness/config.yaml` 로드·검증. 하네스는 config 를 항상 메인 저장소에서 읽는다. (docs/03, docs/05)"""

import textwrap

import pytest

from harness import config as config_module
from harness.config import load
from harness.errors import ConfigError
from harness.models import ExecutionProfile


def write_config(repo, body):
    (repo / ".harness" / "config.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def test_loads_the_default_config(repo):
    cfg = load(repo)
    assert cfg.version == 1
    assert cfg.default_adapter == "mock"
    assert cfg.default_profile is ExecutionProfile.WORKTREE
    assert cfg.max_parallel == 1
    assert cfg.adapters["mock"].type == "mock"


def test_config_path_points_at_the_main_repository(repo):
    assert load(repo).path == repo / ".harness" / "config.yaml"


def test_missing_config_raises(repo):
    (repo / ".harness" / "config.yaml").unlink()
    with pytest.raises(ConfigError):
        load(repo)


def test_unparsable_config_raises(repo):
    write_config(repo, "version: [1,\n")
    with pytest.raises(ConfigError):
        load(repo)


def test_unknown_version_is_rejected(repo):
    write_config(
        repo,
        """
        version: 2
        defaults: {adapter: mock, profile: safe, max_parallel: 1}
        adapters: {mock: {type: mock}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_default_adapter_must_be_declared(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: claude, profile: safe, max_parallel: 1}
        adapters: {mock: {type: mock}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_unknown_profile_is_rejected(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: yolo, max_parallel: 1}
        adapters: {mock: {type: mock}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_max_parallel_must_be_at_least_one(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: safe, max_parallel: 0}
        adapters: {mock: {type: mock}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_unsafe_is_never_the_default_without_explicit_allowance(repo):
    """docs/05, docs/09 — unsafe 는 절대 기본값이 아니다."""
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: unsafe, max_parallel: 1}
        adapters: {mock: {type: mock}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_unsafe_requires_explicit_config_allowance(repo):
    write_config(
        repo,
        """
        version: 1
        allow_unsafe: true
        defaults: {adapter: mock, profile: unsafe, max_parallel: 1}
        adapters: {mock: {type: mock}}
        """,
    )
    cfg = load(repo)
    assert cfg.allow_unsafe is True
    assert cfg.default_profile is ExecutionProfile.UNSAFE


def test_allow_unsafe_defaults_to_false(repo):
    assert load(repo).allow_unsafe is False


def test_adapter_options_are_preserved(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: safe, max_parallel: 1}
        adapters:
          mock:
            type: mock
            scenario: evals/scenarios/basic.yaml
        """,
    )
    assert load(repo).adapters["mock"].options == {"scenario": "evals/scenarios/basic.yaml"}


def test_unknown_top_level_keys_are_tolerated(repo):
    """뒤 마일스톤이 자기 키를 더한다. M0 의 로더가 그것을 막지 않는다."""
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: safe, max_parallel: 1}
        adapters: {mock: {type: mock}}
        command_policy:
          default: require_approval
        """,
    )
    assert load(repo).version == 1


def test_adapter_entry_must_declare_a_type(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock, profile: safe, max_parallel: 1}
        adapters: {mock: {scenario: x.yaml}}
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_config_does_not_import_higher_layers():
    """docs/02 의 의존 방향."""
    source = open(config_module.__file__, encoding="utf-8").read()
    for forbidden in ("harness.store", "harness.cli", "harness.adapters"):
        assert forbidden not in source


# --------------------------------------------------------------------------- 06 소유 키


def test_the_keys_owned_by_docs_06_have_documented_defaults(repo):
    """docs/06 의 "06이 소유하는 config 키" 가 canonical 이다."""
    cfg = load(repo)
    assert cfg.ac_timeout_s == 300
    assert cfg.max_attempts == 2
    assert cfg.max_handoff_repairs == 1
    assert cfg.blocked_signals == ()
    assert cfg.command_policy == {}


def test_the_keys_owned_by_docs_06_are_overridable(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock}
        adapters: {mock: {type: mock}}
        ac_timeout_s: 30
        max_attempts: 5
        max_handoff_repairs: 0
        blocked_signals: ["connection refused"]
        command_policy: {default: allow}
        """,
    )
    cfg = load(repo)
    assert (cfg.ac_timeout_s, cfg.max_attempts, cfg.max_handoff_repairs) == (30, 5, 0)
    assert cfg.blocked_signals == ("connection refused",)
    assert cfg.command_policy == {"default": "allow"}


def test_max_attempts_must_allow_at_least_one_attempt(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock}
        adapters: {mock: {type: mock}}
        max_attempts: 0
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_blocked_signals_must_be_a_list_of_strings(repo):
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock}
        adapters: {mock: {type: mock}}
        blocked_signals: "connection refused"
        """,
    )
    with pytest.raises(ConfigError):
        load(repo)


def test_command_policy_shape_is_not_validated_by_config(repo):
    """형태 검증은 policy 가 한다 (docs/06). config 는 원본을 그대로 전달한다."""
    write_config(
        repo,
        """
        version: 1
        defaults: {adapter: mock}
        adapters: {mock: {type: mock}}
        command_policy: {default: allow, rules: [{match: "^x", verdict: deny}]}
        """,
    )
    assert load(repo).command_policy["rules"][0]["match"] == "^x"
