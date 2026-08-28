"""스펙 파이프라인 — spec/clarify/plan/tasks 스캐폴드, analyze, converge, ship (docs/08).

M6 완료 기준 셋을 여기서 증명한다 (docs/12) —
미커버 요구사항 → converge 실패 · 미해소 debt → ship 실패 · deny 커맨드 → analyze 실패.
"""

import sys

import yaml
from test_runner import ScriptedAdapter, commit, configure, exits, task_of, write_task

from harness.cli import main
from harness.config import load
from harness.converge import converge, ship
from harness.dag import Dag, load_tasks
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.models import State
from harness.spec import open_questions, resolve_question, spec_hash
from harness.store import Store

PY = sys.executable

REQ1 = {"id": "R-001", "statement": "파일이 생긴다"}
REQ2 = {"id": "R-002", "statement": "두 번째 요구사항"}


def write_spec(repo, requirements, slug="feature", open_qs=()):
    data = {"slug": slug, "intent": "테스트", "requirements": list(requirements)}
    if open_qs:
        data["open_questions"] = list(open_qs)
    path = repo / "specs" / slug / "spec.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def green():
    return {"cmd": [PY, "-c", "raise SystemExit(0)"]}


def proves(path):
    """red→green 증명 — 파일이 생겨야 통과한다."""
    code = f"import pathlib, sys; sys.exit(0 if pathlib.Path('{path}').exists() else 1)"
    return {"cmd": [PY, "-c", code], "expect_fail_before": True}


def run1(repo, config, adapter):
    commit(repo)
    return run_dag(
        repo,
        config,
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        scratch=repo.parent / "scratch",
    )


# --------------------------------------------------------------------------- 저작 단계


def test_spec_scaffolds_and_refuses_overwrite(repo, capsys):
    assert main(["spec", "user api", "--repo", str(repo)]) == 0
    path = repo / "specs" / "user-api" / "spec.yaml"
    assert path.is_file()
    assert "[NEEDS CLARIFICATION]" in path.read_text(encoding="utf-8")

    assert main(["spec", "user api", "--repo", str(repo)]) == 1  # 덮어쓰지 않는다


def test_clarify_lists_then_passes_after_resolution(repo, capsys):
    write_spec(repo, [REQ1], open_qs=["[NEEDS CLARIFICATION] 삭제는 소프트인가"])
    assert main(["clarify", "--repo", str(repo)]) == 1  # 캡처된 stdin 은 TTY 가 아니다

    ((path, question),) = open_questions(repo)
    resolve_question(path, question, "소프트 삭제다")

    assert main(["clarify", "--repo", str(repo)]) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["open_questions"] == []
    assert data["clarifications"][0]["answer"] == "소프트 삭제다"


def test_plan_scaffolds_per_spec(repo):
    write_spec(repo, [REQ1, REQ2])
    assert main(["plan", "--repo", str(repo)]) == 0
    plan = repo / "specs" / "feature" / "plan.md"
    assert plan.is_file()
    assert "R-002" in plan.read_text(encoding="utf-8")


def test_tasks_scaffolds_unsatisfied_requirements(repo):
    spec_path = write_spec(repo, [REQ1, REQ2])
    write_task(repo, "T-001", satisfies=["R-001"])

    assert main(["tasks", "--repo", str(repo)]) == 0

    created = repo / "tasks" / "T-002.task.yaml"
    assert created.is_file()
    data = yaml.safe_load(created.read_text(encoding="utf-8"))
    assert data["satisfies"] == ["R-002"]
    assert data["spec_hash"] == spec_hash(spec_path)  # 하네스가 찍는다 (docs/08)


# --------------------------------------------------------------------------- analyze


def test_analyze_blocks_a_deny_command_and_then_run(repo, capsys):
    """M6 완료 기준 ③ — deny 커맨드가 포함된 task 는 analyze 가 막는다."""
    configure(
        repo,
        command_policy={
            "default": "require_approval",
            "rules": [{"match": "danger", "verdict": "deny"}],
        },
    )
    write_task(repo, "T-001", acceptance=[{"cmd": [PY, "-c", "danger"]}])

    assert main(["analyze", "--repo", str(repo)]) == 1
    assert "deny" in capsys.readouterr().out

    assert main(["run", "--repo", str(repo)]) == 1  # 게이트가 run 을 막는다
    assert "analyze" in capsys.readouterr().out


def test_analyze_passes_a_clean_setup_and_run_proceeds(repo):
    configure(
        repo,
        scenario={"tasks": {"T-001": {"files": {"src/a.py": "1\n"}}}},
        profile="worktree",
    )
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    commit(repo)

    assert main(["analyze", "--repo", str(repo)]) == 0
    assert main(["run", "--repo", str(repo), "--run-id", "run-1"]) == 0


def test_analyze_fails_on_unknown_requirement(repo):
    configure(repo)
    write_spec(repo, [REQ1])
    write_task(repo, "T-001", satisfies=["R-999"], acceptance=[green()])
    assert main(["analyze", "--repo", str(repo)]) == 1


def test_analyze_fails_on_an_unassigned_requirement(repo, capsys):
    configure(repo)
    write_spec(repo, [REQ1, REQ2])
    write_task(repo, "T-001", satisfies=["R-001"], acceptance=[green()])
    assert main(["analyze", "--repo", str(repo)]) == 1
    assert "R-002" in capsys.readouterr().out


def test_analyze_fails_when_an_implementation_task_has_no_ac(repo):
    configure(repo)
    write_task(repo, "T-001")
    assert main(["analyze", "--repo", str(repo)]) == 1


def test_analyze_fails_on_clarification_markers(repo):
    configure(repo)
    write_spec(repo, [REQ1], open_qs=["[NEEDS CLARIFICATION] x"])
    write_task(repo, "T-001", satisfies=["R-001"], acceptance=[green()])
    assert main(["analyze", "--repo", str(repo)]) == 1


def test_analyze_fails_on_spec_hash_drift(repo, capsys):
    configure(repo)
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo, "T-001", satisfies=["R-001"], acceptance=[green()], spec_hash=spec_hash(spec_path)
    )
    write_spec(repo, [{"id": "R-001", "statement": "구현 중에 바뀐 문장"}])

    assert main(["analyze", "--repo", str(repo)]) == 1
    assert "드리프트" in capsys.readouterr().out


def test_analyze_warnings_do_not_block(repo, capsys):
    configure(repo)
    write_spec(repo, [REQ1])
    write_task(repo, "T-001", satisfies=["R-001"], acceptance=[green()])

    assert main(["analyze", "--repo", str(repo)]) == 0  # 검증력 의심·spec_hash 없음은 경고다
    assert "[경고]" in capsys.readouterr().out


def test_a_stale_analyze_blocks_run(repo, capsys):
    configure(repo, scenario={"tasks": {"T-001": {"files": {"src/a.py": "1\n"}}}})
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    assert main(["analyze", "--repo", str(repo)]) == 0

    write_task(repo, "T-001", satisfies=["R-001"], acceptance=[green()])  # analyze 이후 수정

    assert main(["run", "--repo", str(repo)]) == 1
    assert "다시 실행" in capsys.readouterr().out


# --------------------------------------------------------------------------- converge · ship


def test_an_uncovered_requirement_fails_converge(repo):
    """M6 완료 기준 ① — 미커버 요구사항은 converge 를 막는다."""
    config = configure(repo, profile="worktree")
    spec_path = write_spec(repo, [REQ1, REQ2])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    run1(repo, config, ScriptedAdapter([{"files": {"src/a.py": "1\n"}}]))

    report = converge(repo, config, "run-1")

    assert not report.ok
    assert report.coverage == {"R-001": "covered", "R-002": "uncovered"}
    assert "uncovered" in (repo / ".harness" / "coverage.md").read_text(encoding="utf-8")


def test_converge_passes_when_covered_and_green(repo):
    config = configure(repo, profile="worktree")
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    run1(repo, config, ScriptedAdapter([{"files": {"src/a.py": "1\n"}}]))

    report = converge(repo, config, "run-1")

    assert report.ok, report.failures
    assert report.coverage == {"R-001": "covered"}


def test_an_open_debt_blocks_ship_until_waived(repo):
    """M6 완료 기준 ② — 미해소 debt 는 ship 을 막고, 예외는 기록되는 waiver 뿐이다."""
    config = configure(repo, profile="worktree")
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py"), exits(1)],  # exits(1) 은 pre-existing 실패 — debt
        spec_hash=spec_hash(spec_path),
    )
    store = run1(repo, config, ScriptedAdapter([{"files": {"src/a.py": "1\n"}}]))
    assert task_of(store, "T-001").state is State.DONE  # 차등 판정 — debt 는 task 를 막지 않는다
    (debt_id,) = store.state.open_debts

    report = converge(repo, config, "run-1")
    assert report.ok, report.failures  # debt 는 converge 가 아니라 ship 의 조건이다 (docs/08)
    assert report.open_debts == (debt_id,)

    shipped = ship(repo, config, "run-1")
    assert not shipped.ok
    assert debt_id in shipped.detail

    (repo / ".harness" / "waivers.yaml").write_text(
        yaml.safe_dump(
            {"waivers": [{"debt_id": debt_id, "approver": "tester", "reason": "레거시"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    shipped = ship(repo, config, "run-1")
    assert shipped.ok
    assert shipped.waived == (debt_id,)
    assert (repo / "src" / "a.py").is_file()  # 통합 브랜치가 사용자 브랜치로 머지되었다

    waived_events = [
        event
        for event in Store(repo / ".harness" / "runs" / "run-1").journal.read()
        if event.type is EventType.DEBT_WAIVED
    ]
    assert waived_events and waived_events[0].payload["debt_id"] == debt_id


def test_ship_requires_a_fresh_converge(repo):
    config = configure(repo, profile="worktree")
    write_spec(repo, [REQ1])
    spec_path = repo / "specs" / "feature" / "spec.yaml"
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    run1(repo, config, ScriptedAdapter([{"files": {"src/a.py": "1\n"}}]))

    shipped = ship(repo, config, "run-1")

    assert not shipped.ok
    assert "converge" in shipped.detail


def test_the_cli_converges_and_ships(repo):
    configure(
        repo,
        scenario={"tasks": {"T-001": {"files": {"src/a.py": "1\n"}}}},
        profile="worktree",
    )
    spec_path = write_spec(repo, [REQ1])
    write_task(
        repo,
        "T-001",
        satisfies=["R-001"],
        allowed_paths=["src/**"],
        acceptance=[proves("src/a.py")],
        spec_hash=spec_hash(spec_path),
    )
    commit(repo)
    assert main(["run", "--repo", str(repo), "--run-id", "run-1"]) == 0
    assert main(["converge", "--repo", str(repo), "--run", "run-1"]) == 0
    assert main(["ship", "--repo", str(repo), "--run", "run-1"]) == 0
    assert (repo / ".harness" / "ship-report.md").is_file()
