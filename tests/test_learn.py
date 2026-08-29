"""지식 카드의 제안·승격·폐기와 `uses` 집계 (docs/08).

`learn`은 옵션 레이어다 (docs/02). **자동 승격은 없다** — 반복 관측은 후보 자격을 만들 뿐이고,
상태를 바꾸는 것은 사람의 명령이다.
"""

import json

import pytest
import yaml
from test_runner import configure

from harness.errors import HarnessError
from harness.events import EventType
from harness.learn import learn, promote, retire
from harness.store import Store

KNOWLEDGE = ".harness/knowledge"


def finding_event(store, task_id, rule, file, wave=1, reviewer="quality"):
    store.append(
        EventType.REVIEW_FINDING,
        {
            "wave": wave,
            "reviewer": reviewer,
            "severity": "high",
            "rule": rule,
            "file": file,
            "line": 3,
            "blocking": True,
        },
        task_id,
        1,
    )


def journal_with(repo, run_id, findings):
    """`findings` 는 (task_id, rule, file) 목록이다."""
    run_dir = repo / ".harness" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    store = Store(run_dir)
    for task_id, rule, file in findings:
        finding_event(store, task_id, rule, file)
    return store


def manifest(repo, run_id, task_id, sections):
    directory = repo / ".harness" / "runs" / run_id / "tasks" / task_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "context.manifest.json").write_text(
        json.dumps({"task_id": task_id, "sections": sections}), encoding="utf-8"
    )


def card(repo, card_id):
    return yaml.safe_load((repo / KNOWLEDGE / f"{card_id}.yaml").read_text(encoding="utf-8"))


def write_card(repo, card_id, **fields):
    directory = repo / KNOWLEDGE
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "id": card_id,
        "kind": "pitfall",
        "scope": "src/**",
        "claim": "c",
        "evidence": [],
        "status": "candidate",
        "uses": 0,
        **fields,
    }
    (directory / f"{card_id}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


# --------------------------------------------------------------------------- 후보 제안


def test_a_rule_repeated_across_runs_becomes_a_candidate(repo):
    config = configure(repo)
    journal_with(repo, "run-1", [("T-001", "hardcoded-secret", "src/api/db.py")])
    journal_with(repo, "run-2", [("T-004", "hardcoded-secret", "src/api/users.py")])

    report = learn(repo, config)

    assert report.proposed == ("K-001",)
    data = card(repo, "K-001")
    assert data["status"] == "candidate"
    assert data["kind"] == "pitfall"
    assert data["scope"] == "src/api/**"
    assert data["claim"].startswith("[NEEDS CLAIM]")
    assert data["evidence"] == [
        {"run": "run-1", "task": "T-001"},
        {"run": "run-2", "task": "T-004"},
    ]


def test_repetition_inside_one_run_is_one_observation(repo):
    config = configure(repo)
    journal_with(
        repo,
        "run-1",
        [("T-001", "hardcoded-secret", "src/a.py"), ("T-002", "hardcoded-secret", "src/b.py")],
    )

    report = learn(repo, config)

    assert report.proposed == ()
    assert list((repo / KNOWLEDGE).glob("K-*.yaml")) == []


def test_an_existing_card_gains_evidence_instead_of_a_duplicate(repo):
    config = configure(repo)
    journal_with(repo, "run-1", [("T-001", "hardcoded-secret", "src/api/db.py")])
    journal_with(repo, "run-2", [("T-004", "hardcoded-secret", "src/api/users.py")])
    learn(repo, config)

    journal_with(repo, "run-3", [("T-007", "hardcoded-secret", "src/api/keys.py")])
    report = learn(repo, config)

    assert report.proposed == ()
    assert report.updated == ("K-001",)
    assert len(list((repo / KNOWLEDGE).glob("K-*.yaml"))) == 1
    assert card(repo, "K-001")["evidence"][-1] == {"run": "run-3", "task": "T-007"}


def test_a_retired_rule_does_not_come_back(repo):
    config = configure(repo)
    write_card(repo, "K-001", status="retired", rule="hardcoded-secret")
    journal_with(repo, "run-1", [("T-001", "hardcoded-secret", "src/a.py")])
    journal_with(repo, "run-2", [("T-002", "hardcoded-secret", "src/b.py")])

    report = learn(repo, config)

    assert report.proposed == ()
    assert report.updated == ()
    assert card(repo, "K-001")["status"] == "retired"


def test_learn_never_promotes_by_itself(repo):
    config = configure(repo)
    for index in range(5):
        journal_with(repo, f"run-{index}", [("T-001", "hardcoded-secret", "src/a.py")])

    learn(repo, config)

    assert card(repo, "K-001")["status"] == "candidate"


# --------------------------------------------------------------------------- uses


def test_uses_counts_only_sections_that_reached_the_prompt(repo):
    config = configure(repo)
    write_card(repo, "K-001")
    manifest(
        repo,
        "run-1",
        "T-001",
        [
            {"layer": "L7", "source": "K-001", "trust": "untrusted", "tokens": 40, "reason": "knowledge"},
            {"layer": "L0", "source": "constitution", "trust": "trusted", "tokens": 10, "reason": "always"},
        ],
    )
    manifest(
        repo,
        "run-2",
        "T-001",
        [{"layer": "L7", "source": "K-001", "trust": "untrusted", "tokens": 0, "reason": "dropped: budget"}],
    )

    report = learn(repo, config)

    assert report.uses["K-001"] == 1
    assert card(repo, "K-001")["uses"] == 1


def test_a_promoted_card_left_unused_is_reported_for_retirement(repo):
    config = configure(repo, knowledge={"retire_after_unused_runs": 2})
    write_card(repo, "K-001", status="promoted")
    journal_with(repo, "run-1", [])
    journal_with(repo, "run-2", [])

    report = learn(repo, config)

    assert report.retire_candidates == ("K-001",)
    # 보고일 뿐 상태는 그대로다 — 폐기도 사람이 한다.
    assert card(repo, "K-001")["status"] == "promoted"


def test_a_used_card_is_not_reported(repo):
    config = configure(repo, knowledge={"retire_after_unused_runs": 1})
    write_card(repo, "K-001", status="promoted")
    journal_with(repo, "run-1", [])
    manifest(
        repo,
        "run-1",
        "T-001",
        [{"layer": "L7", "source": "K-001", "trust": "untrusted", "tokens": 40, "reason": "knowledge"}],
    )

    assert learn(repo, config).retire_candidates == ()


# --------------------------------------------------------------------------- 사람의 명령


def test_promote_and_retire_record_the_human_decision(repo):
    configure(repo)
    write_card(repo, "K-001")

    promote(repo, "K-001")
    assert card(repo, "K-001")["status"] == "promoted"

    retire(repo, "K-001")
    assert card(repo, "K-001")["status"] == "retired"


def test_an_unknown_card_is_an_error(repo):
    configure(repo)
    with pytest.raises(HarnessError):
        promote(repo, "K-404")
