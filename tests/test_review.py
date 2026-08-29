"""리스크 티어, bounded review wave, fixer, 예산 상한 (docs/06).

M5 완료 기준을 여기서 증명한다 (docs/12) — `trivial` 선언 task 가 민감 경로를
변경하면 자동으로 티어가 상향되어 리뷰되고, `risk_escalated` 이벤트로 확인된다.
"""

import json

import pytest
import yaml
from test_runner import ScriptedAdapter, commit, configure, task_of, write_task

from harness.adapters import registry
from harness.adapters.base import AgentResult, Capabilities, PreflightKind, PreflightReport
from harness.config import load
from harness.dag import Dag, load_tasks
from harness.errors import ConfigError
from harness.events import EventType
from harness.exec.review import ReviewStage
from harness.exec.runner import run_dag
from harness.models import RiskLevel, State, Verdict
from harness.risk import assess, load_rules

CLEAN = {"schema": "harness.findings/v1", "task_id": "T-001", "findings": []}


def finding(severity="high", rule="hardcoded-secret"):
    return {
        "schema": "harness.findings/v1",
        "task_id": "T-001",
        "findings": [
            {"severity": severity, "rule": rule, "file": "src/plain.py", "line": 3, "message": "m"}
        ],
    }


def go(repo, config=None, adapter=None, resume=False):
    if not resume:
        commit(repo)
    config = config or load(repo)
    return run_dag(
        repo,
        config,
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        resume=resume,
        scratch=repo.parent / "scratch",
        review_stage=ReviewStage(repo, config),
    )


def events_of(store, type):
    return [event for event in store.journal.read() if event.type is type]


# --------------------------------------------------------------------------- effective_risk


def test_effective_risk_takes_the_max():
    floors = assess(RiskLevel.TRIVIAL, ("src/auth/login.py",), 10, 1, load_rules(()))
    assert floors.effective is RiskLevel.HIGH
    assert floors.escalated


def test_scale_alone_raises_to_medium():
    floors = assess(None, ("src/big.py",), 400, 1, load_rules(()))
    assert floors.effective is RiskLevel.MEDIUM


def test_a_declaration_cannot_lower_a_floor():
    floors = assess(RiskLevel.TRIVIAL, ("pyproject.toml",), 1, 1, load_rules(()))
    assert floors.effective is RiskLevel.HIGH


def test_declared_rules_extend_the_defaults():
    rules = load_rules(({"match": "src/payments/**", "floor": "critical"},))
    assert assess(None, ("src/payments/charge.py",), 1, 1, rules).effective is RiskLevel.CRITICAL
    assert assess(None, ("src/auth/x.py",), 1, 1, rules).effective is RiskLevel.HIGH


def test_a_plain_path_stays_declared():
    floors = assess(RiskLevel.LOW, ("src/plain.py",), 5, 1, load_rules(()))
    assert floors.effective is RiskLevel.LOW
    assert not floors.escalated


# --------------------------------------------------------------------------- M5 완료 기준


def test_a_trivial_task_on_a_sensitive_path_is_escalated_and_reviewed(repo):
    """M5 완료 기준 — trivial 선언 + 민감 경로 변경 → 자동 상향 + 리뷰 (docs/12)."""
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", risk="trivial", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [
            {"files": {"src/auth/login.py": "x = 1\n"}},
            {"findings": CLEAN},
            {"findings": CLEAN},
            {"findings": CLEAN},
        ]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.DONE
    escalated = events_of(store, EventType.RISK_ESCALATED)
    assert escalated and escalated[0].payload["declared"] == "trivial"
    assert escalated[0].payload["effective"] == "high"
    assert len(adapter.requests) == 4  # 구현자 1 + high 티어 리뷰어 3


def test_a_trivial_task_off_sensitive_paths_skips_review(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", risk="trivial", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/plain.py": "x = 1\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.DONE
    assert len(adapter.requests) == 1
    assert not events_of(store, EventType.RISK_ESCALATED)


# --------------------------------------------------------------------------- bounded wave


def test_a_blocking_finding_dispatches_a_fixer_then_passes(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [
            {"files": {"src/plain.py": "x = 1\n"}},
            {"findings": finding()},
            {"files": {"src/plain.py": "x = 2\n"}},  # fixer
            {"findings": CLEAN},
        ]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.DONE
    fixer = events_of(store, EventType.FIXER_DISPATCHED)
    assert fixer and fixer[0].payload["scope"] == "code"
    found = events_of(store, EventType.REVIEW_FINDING)
    assert found and found[0].payload["blocking"] is True


def test_blocking_findings_after_the_wave_limit_reject(repo):
    """docs/10 의 review 행 — wave 한도 초과 후에도 blocking 이 남으면 rejected."""
    config = configure(repo, profile="worktree", max_attempts=1)
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [
            {"files": {"src/plain.py": "x = 1\n"}},
            {"findings": finding()},
            {"files": {}},  # fixer 가 아무것도 못 고쳤다
            {"findings": finding()},
        ]
    )

    store = go(repo, config, adapter)

    projection = task_of(store, "T-001")
    assert projection.verdict is Verdict.REJECTED
    assert projection.reason == "blocking_findings"
    assert projection.state is State.NEEDS_REPLAN


def test_a_low_severity_finding_does_not_block(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [{"files": {"src/plain.py": "x = 1\n"}}, {"findings": finding(severity="low", rule="nit")}]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.DONE
    assert len(adapter.requests) == 2  # fixer 없음
    assert events_of(store, EventType.REVIEW_FINDING)[0].payload["blocking"] is False


def test_a_constitution_critical_rule_blocks_regardless_of_severity(repo):
    (repo / ".harness" / "constitution.md").write_text(
        "# constitution\n\n## critical\n\n- no-todo\n", encoding="utf-8"
    )
    config = configure(repo, profile="worktree", max_review_waves=1, max_attempts=1)
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [{"files": {"src/plain.py": "x = 1\n"}}, {"findings": finding(severity="low", rule="no-todo")}]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.REJECTED
    assert events_of(store, EventType.REVIEW_FINDING)[0].payload["blocking"] is True


def test_a_silent_reviewer_is_an_error_not_a_pass(repo):
    """docs/06 — 리뷰어의 침묵을 통과로 해석하지 않는다."""
    config = configure(repo, profile="worktree", max_attempts=1)
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/plain.py": "x = 1\n"}}, {}])

    store = go(repo, config, adapter)

    projection = task_of(store, "T-001")
    assert projection.verdict is Verdict.ERROR
    assert projection.reason == "review_failed"


def test_the_fixers_change_faces_the_same_evidence(repo):
    """docs/06 — 리뷰가 만든 변경도 같은 증거 기준(경로 스코프)을 통과해야 한다."""
    config = configure(repo, profile="worktree", max_attempts=1)
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [
            {"files": {"src/plain.py": "x = 1\n"}},
            {"findings": finding()},
            {"files": {"elsewhere.py": "2\n"}},  # fixer 가 스코프 밖을 건드렸다
        ]
    )

    store = go(repo, config, adapter)

    projection = task_of(store, "T-001")
    assert projection.verdict is Verdict.REJECTED
    assert projection.reason == "path_violation"


def test_review_artifacts_are_preserved(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", risk="low", allowed_paths=["src/**"])
    adapter = ScriptedAdapter(
        [{"files": {"src/plain.py": "x = 1\n"}}, {"findings": CLEAN}]
    )

    store = go(repo, config, adapter)

    assert (store.run_dir / "tasks" / "T-001" / "review" / "wave-1" / "spec.json").is_file()


# --------------------------------------------------------------------------- 예산 상한


def test_the_run_stops_when_agent_calls_are_exhausted(repo):
    config = configure(repo, profile="worktree", budget={"max_agent_calls": 1})
    write_task(repo, "T-001", allowed_paths=["src/a/**"])
    write_task(repo, "T-002", allowed_paths=["src/b/**"])
    adapter = ScriptedAdapter(
        [{"files": {"src/a/one.py": "1\n"}}, {"files": {"src/b/two.py": "2\n"}}]
    )

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").state is State.DONE
    projection = task_of(store, "T-002")
    assert projection.verdict is Verdict.BUDGET_EXHAUSTED
    assert projection.state is State.HUMAN_REQUIRED
    assert events_of(store, EventType.BUDGET_CHECKPOINT)


def test_a_zero_wall_budget_stops_before_any_agent(repo):
    config = configure(repo, profile="worktree", budget={"max_wall_time_s": 0})
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/a.py": "1\n"}}])

    store = go(repo, config, adapter)

    assert task_of(store, "T-001").verdict is Verdict.BUDGET_EXHAUSTED
    assert adapter.requests == []


def test_no_budget_means_no_checkpoint_noise(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/a.py": "1\n"}}])

    store = go(repo, config, adapter)

    assert not events_of(store, EventType.BUDGET_CHECKPOINT)


# --------------------------------------------------------------------------- cross-adapter


class TaggedAdapter:
    """어느 어댑터가 그 리뷰어를 돌렸는지 findings 의 rule 로 드러내는 테스트용 어댑터."""

    def __init__(self, name, options):
        self.name = name
        self.tag = options["tag"]

    def capabilities(self):
        return Capabilities(False, False, False)

    def preflight(self):
        return PreflightReport(True, PreflightKind.OK, "ok")

    def execute(self, request):
        target = request.workspace / "src" / "plain.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")

        request.outbox.mkdir(parents=True, exist_ok=True)
        (request.outbox / "findings.json").write_text(
            json.dumps(
                {
                    "schema": "harness.findings/v1",
                    "task_id": request.task_id,
                    "findings": [
                        {
                            "severity": "low",
                            "rule": f"{self.tag}-note",
                            "file": "src/plain.py",
                            "line": 1,
                            "message": "m",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return AgentResult(0, "", "", None, None, 0.0, None, None, None)


@pytest.fixture
def tagged_type():
    registry.register("tagged", lambda name, options: TaggedAdapter(name, options))
    yield
    registry._FACTORIES.pop("tagged", None)


def critical_repo(repo, **extra):
    data = {
        "version": 1,
        "defaults": {"adapter": "impl", "profile": "safe", "max_parallel": 1},
        "adapters": {"impl": {"type": "tagged", "tag": "impl"}, "redteam": {"type": "tagged", "tag": "redteam"}},
        "command_policy": {"default": "allow"},
        **extra,
    }
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    write_task(repo, "T-001", risk="critical", acceptance=[])
    return load(repo)


def test_the_adversarial_reviewer_can_run_on_another_adapter(repo, tagged_type):
    config = critical_repo(repo, adversarial_adapter="redteam")

    store = go(repo, config=config)

    by_reviewer = {
        event.payload["reviewer"]: event.payload["rule"]
        for event in events_of(store, EventType.REVIEW_FINDING)
    }
    assert by_reviewer["adversarial"] == "redteam-note"
    # 나머지 리뷰어는 영향을 받지 않는다 (docs/06). 같은 (rule·file·line) 이므로 셋의
    # finding 은 하나로 병합되고 첫 리뷰어 이름으로 남는다.
    assert by_reviewer["spec"] == "impl-note"
    assert set(by_reviewer) == {"spec", "adversarial"}


def test_without_the_key_the_adversarial_reviewer_uses_the_task_adapter(repo, tagged_type):
    config = critical_repo(repo)

    store = go(repo, config=config)

    rules = {event.payload["rule"] for event in events_of(store, EventType.REVIEW_FINDING)}
    assert rules == {"impl-note"}


def test_an_unknown_adversarial_adapter_is_a_config_error(repo, tagged_type):
    with pytest.raises(ConfigError):
        critical_repo(repo, adversarial_adapter="nobody")
