"""CLI — init·status·doctor (M0), run (M1), run --resume 과 doctor 복구 (M2).

docs/00, docs/10, docs/12.
"""

import json
import pkgutil
from pathlib import Path

import yaml

import harness
from harness.cli import REQUIRED_CONTROL_PLANE, main
from harness.exec.workspace import repo_scratch
from harness.config import load
from harness.events import EventType
from harness.models import RunState
from harness.policy import CommandPolicy, PolicyVerdict
from harness.store import Store, fold

RUN_ID = "run-20260827-1432"


def yaml_block(document: str, heading: str) -> dict:
    """문서의 특정 절 아래 첫 yaml 펜스를 계약으로 읽는다."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / document).read_text(encoding="utf-8")
    section = doc.split(heading, 1)[1]
    return yaml.safe_load(section.split("```yaml", 1)[1].split("```", 1)[0])


def check_ignore(repo: Path, relative: str) -> bool:
    import subprocess

    return (
        subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=repo, capture_output=True
        ).returncode
        == 0
    )


def seed_run(repo, run_id=RUN_ID):
    store = Store(repo / ".harness" / "runs" / run_id)
    store.append(
        EventType.RUN_STARTED,
        {
            "manifest": {"task_ids": ["T-001", "T-002"]},
            "profile": "worktree",
            "adapter": "mock",
            "max_parallel": 1,
        },
    )
    store.append(
        EventType.DEBT_OPENED,
        {"debt_id": "D-001", "cmd": ["npm", "test"], "origin_task": "T-001"},
        task_id="T-001",
        attempt=1,
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "verified", "attempt": 1, "reason": None, "next_state": "done"},
        task_id="T-001",
        attempt=1,
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "blocked", "attempt": 1, "reason": "prerequisite", "next_state": "human_required"},
        task_id="T-002",
        attempt=1,
    )
    return store


# --------------------------------------------------------------------------- 규약


def test_sys_exit_lives_only_in_cli():
    """docs/02 — sys.exit 는 cli.py 에만 존재한다."""
    package_dir = Path(harness.__file__).parent
    offenders = []
    for module in pkgutil.walk_packages([str(package_dir)], prefix="harness."):
        path = package_dir.joinpath(*module.name.split(".")[1:]).with_suffix(".py")
        if not path.exists() or module.name == "harness.cli":
            continue
        if "sys.exit" in path.read_text(encoding="utf-8"):
            offenders.append(module.name)
    assert offenders == []


def test_main_returns_an_exit_code_instead_of_exiting(repo):
    assert main(["status", "--repo", str(repo)]) == 0


# --------------------------------------------------------------------------- init


def test_init_creates_the_control_plane(plain_repo):
    """docs/03 의 파일 배치 — .harness/ 의 control-plane 항목을 만든다."""
    assert main(["init", "--repo", str(plain_repo)]) == 0
    for name in REQUIRED_CONTROL_PLANE:
        assert (plain_repo / ".harness" / name).exists(), name


def test_init_writes_a_config_that_loads(plain_repo):
    main(["init", "--repo", str(plain_repo)])
    config = load(plain_repo)
    assert config.default_adapter in config.adapters


def written_config(repo):
    return yaml.safe_load((repo / ".harness" / "config.yaml").read_text(encoding="utf-8"))


def test_init_writes_the_config_skeleton_documented_in_docs_03(plain_repo):
    """docs/03 의 `config.yaml — canonical` 이 최상위 뼈대의 계약이다."""
    main(["init", "--repo", str(plain_repo)])
    documented = yaml_block("03-DATA-MODEL.md", "### `config.yaml` — canonical")
    written = written_config(plain_repo)
    assert {k: written.get(k) for k in documented} == documented


def test_init_writes_the_command_policy_documented_in_docs_06(plain_repo):
    """docs/06 — init 이 이 규칙 목록을 기본 config 에 써 넣는다.

    06 소유 키이므로 03 의 뼈대에는 없고 06 이 canonical 이다.
    """
    main(["init", "--repo", str(plain_repo)])
    documented = yaml_block("06-VERIFICATION-REVIEW.md", "### 설정")
    assert written_config(plain_repo)["command_policy"] == documented["command_policy"]


def test_the_default_config_authorizes_nothing_dangerous(plain_repo):
    """fail-closed 가 기본이고, 파괴적 커맨드는 deny 다."""
    main(["init", "--repo", str(plain_repo)])
    policy = CommandPolicy.from_config(written_config(plain_repo)["command_policy"])
    assert policy.decide(["rm", "-rf", "/"]).verdict is PolicyVerdict.DENY
    assert policy.decide(["make", "release"]).verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert policy.decide(["pytest"]).verdict is PolicyVerdict.ALLOW


def test_doctor_passes_on_a_freshly_initialized_repo(plain_repo, capsys):
    """M0 완료 기준 (docs/12) — init 이 만든 저장소에서 doctor 가 지적 없이 통과한다."""
    main(["init", "--repo", str(plain_repo)])
    capsys.readouterr()
    assert main(["doctor", "--repo", str(plain_repo)]) == 0
    assert "[bad]" not in capsys.readouterr().out


def test_init_does_not_overwrite_existing_files(plain_repo):
    main(["init", "--repo", str(plain_repo)])
    constitution = plain_repo / ".harness" / "constitution.md"
    constitution.write_text("# 우리 프로젝트 규칙\n", encoding="utf-8")

    assert main(["init", "--repo", str(plain_repo)]) == 0
    assert constitution.read_text(encoding="utf-8") == "# 우리 프로젝트 규칙\n"


def test_init_keeps_run_artifacts_out_of_git(plain_repo):
    """docs/03 — 커밋되는 것은 사람이 쓴 입력뿐이다.

    `git check-ignore` 로 확인한다. 파일의 존재가 아니라 패턴이 실제로 먹는지가 계약이다.
    """
    main(["init", "--repo", str(plain_repo)])

    ignored = [
        ".harness/runs/run-1/journal.jsonl",
        ".harness/analyze.json",
        ".harness/analyze-report.md",
        ".harness/converge.json",
        ".harness/coverage.md",
        ".harness/ship-report.md",
    ]
    kept = [
        ".harness/config.yaml",
        ".harness/constitution.md",
        ".harness/approved_commands.yaml",
        ".harness/waivers.yaml",
        ".harness/knowledge/K-001.yaml",
    ]
    for relative in ignored:
        assert check_ignore(plain_repo, relative), f"{relative} 이 커밋 대상이 된다"
    for relative in kept:
        assert not check_ignore(plain_repo, relative), f"{relative} 이 커밋되지 않는다"


def test_init_does_not_overwrite_an_edited_gitignore(plain_repo):
    main(["init", "--repo", str(plain_repo)])
    ignore = plain_repo / ".harness" / ".gitignore"
    ignore.write_text("# 우리가 고친 것" + chr(10), encoding="utf-8")

    assert main(["init", "--repo", str(plain_repo)]) == 0
    assert ignore.read_text(encoding="utf-8") == "# 우리가 고친 것" + chr(10)


def test_init_restores_a_missing_directory(plain_repo):
    """runs/ 는 gitignore 대상이라 clone 뒤 사라진다. init 이 복구 경로다."""
    main(["init", "--repo", str(plain_repo)])
    (plain_repo / ".harness" / "runs").rmdir()

    assert main(["init", "--repo", str(plain_repo)]) == 0
    assert (plain_repo / ".harness" / "runs").is_dir()


def test_init_outside_a_git_repository_fails(tmp_path):
    """docs/00 — init 은 저장소에 .harness/ 를 만든다. 저장소가 없으면 만들 곳이 없다."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert main(["init", "--repo", str(outside)]) != 0
    assert not (outside / ".harness").exists()


# --------------------------------------------------------------------------- status


def test_status_reports_no_runs(repo, capsys):
    assert main(["status", "--repo", str(repo)]) == 0
    assert "no runs" in capsys.readouterr().out.lower()


def test_status_shows_tasks_verdicts_debts_and_human_required(repo, capsys):
    seed_run(repo)
    assert main(["status", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert RUN_ID in out
    assert "T-001" in out and "verified" in out and "done" in out
    assert "T-002" in out and "blocked" in out and "human_required" in out
    assert "D-001" in out


def test_status_picks_the_latest_run_by_default(repo, capsys):
    seed_run(repo, "run-20260101-0000")
    seed_run(repo, "run-20260827-1432")
    main(["status", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "run-20260827-1432" in out
    assert "run-20260101-0000" not in out


def test_status_can_select_a_run(repo, capsys):
    seed_run(repo, "run-20260101-0000")
    seed_run(repo, "run-20260827-1432")
    main(["status", "--repo", str(repo), "--run", "run-20260101-0000"])
    assert "run-20260101-0000" in capsys.readouterr().out


def test_status_on_an_unknown_run_fails(repo, capsys):
    seed_run(repo)
    assert main(["status", "--repo", str(repo), "--run", "run-nope"]) != 0


def test_status_rebuilds_state_from_the_journal(repo, capsys):
    """state 는 캐시다. 없어도 status 가 동작해야 한다."""
    store = seed_run(repo)
    store.state_path.unlink()
    assert main(["status", "--repo", str(repo)]) == 0
    assert "D-001" in capsys.readouterr().out


# --------------------------------------------------------------------------- doctor


def test_doctor_passes_on_a_healthy_repo(repo, capsys):
    seed_run(repo)
    assert main(["doctor", "--repo", str(repo)]) == 0
    assert "state == fold(journal)" in capsys.readouterr().out


def test_doctor_rebuilds_a_stale_state_snapshot(repo, capsys):
    """docs/10 — 불일치 시 journal 기준으로 state 를 재구성한다."""
    store = seed_run(repo)
    expected = store.state
    stale = json.loads(store.state_path.read_text(encoding="utf-8"))
    stale["tasks"] = {}
    stale["open_debts"] = {}
    stale["last_applied_seq"] = 1
    store.state_path.write_text(json.dumps(stale), encoding="utf-8")

    assert main(["doctor", "--repo", str(repo)]) != 0
    repaired = RunState.from_dict(json.loads(store.state_path.read_text(encoding="utf-8")))
    assert repaired == expected
    assert "재구성" in capsys.readouterr().out


def test_doctor_never_modifies_the_journal(repo):
    """docs/10 — doctor 는 journal 을 수정하지 않는다. journal 은 불변이다."""
    store = seed_run(repo)
    before = store.journal.path.read_bytes()
    store.state_path.write_text("{ corrupt", encoding="utf-8")
    main(["doctor", "--repo", str(repo)])
    assert store.journal.path.read_bytes() == before


def test_doctor_reports_sequence_gaps_without_fixing_them(repo, capsys):
    store = seed_run(repo)
    lines = store.journal.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["seq"] = 99
    lines[-1] = json.dumps(record)
    store.journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["doctor", "--repo", str(repo)]) != 0
    out = capsys.readouterr().out
    assert "seq" in out
    assert json.loads(store.journal.path.read_text(encoding="utf-8").splitlines()[-1])["seq"] == 99


def test_doctor_reports_missing_control_plane_files(repo, capsys):
    (repo / ".harness" / "constitution.md").unlink()
    assert main(["doctor", "--repo", str(repo)]) != 0
    assert "constitution.md" in capsys.readouterr().out


def test_doctor_reports_a_missing_harness_directory(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["doctor", "--repo", str(plain)]) != 0


def test_doctor_runs_adapter_preflight(repo, capsys):
    """docs/10 — 등록된 모든 어댑터에 preflight 를 실행하고 분류표 기준으로 보고한다."""
    seed_run(repo)
    main(["doctor", "--repo", str(repo)])
    assert "preflight" in capsys.readouterr().out


def test_doctor_reports_a_bad_config_without_crashing(repo, capsys):
    (repo / ".harness" / "config.yaml").write_text("version: 99\n", encoding="utf-8")
    assert main(["doctor", "--repo", str(repo)]) != 0
    assert "config" in capsys.readouterr().out.lower()


def test_doctor_reports_journal_corruption_instead_of_crashing(repo, capsys):
    store = seed_run(repo)
    lines = store.journal.path.read_text(encoding="utf-8").splitlines()
    lines[0] = "{ broken"
    store.journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["doctor", "--repo", str(repo)]) != 0
    assert "journal" in capsys.readouterr().out.lower()


def test_doctor_is_safe_to_run_twice(repo):
    store = seed_run(repo)
    expected = fold(store.journal.read(), RUN_ID)
    main(["doctor", "--repo", str(repo)])
    main(["doctor", "--repo", str(repo)])
    assert RunState.from_dict(json.loads(store.state_path.read_text(encoding="utf-8"))) == expected


# --------------------------------------------------------------------------- 인자


def test_unknown_command_fails(repo):
    assert main(["nope", "--repo", str(repo)]) != 0


def test_no_command_fails(repo):
    assert main([]) != 0


# --------------------------------------------------------------------------- run


def write_runnable_task(repo, task_id="T-001", **fields):
    scenario = repo / "mock-scenario.yaml"
    scenario.write_text(
        yaml.safe_dump({"default": {"files": {"made.py": "x\n"}}}), encoding="utf-8"
    )
    config = yaml.safe_load((repo / ".harness" / "config.yaml").read_text(encoding="utf-8"))
    config["defaults"]["profile"] = "safe"
    config["adapters"]["mock"]["scenario"] = str(scenario)
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    directory = repo / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    data = {"id": task_id, "name": "t", "kind": "implementation", **fields}
    (directory / f"{task_id}.task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_run_executes_the_task_dag(repo, capsys):
    write_runnable_task(repo)
    assert main(["run", "--repo", str(repo), "--run-id", "run-1"]) == 0
    out = capsys.readouterr().out
    assert "T-001" in out and "verified" in out


def test_run_leaves_a_journal_that_status_can_read(repo, capsys):
    write_runnable_task(repo)
    main(["run", "--repo", str(repo), "--run-id", "run-1"])
    capsys.readouterr()

    assert main(["status", "--repo", str(repo)]) == 0
    assert "run-1" in capsys.readouterr().out


def test_doctor_passes_after_a_run(repo, capsys):
    """docs/03 — state == fold(journal) 은 run 이 끝난 뒤에도 성립한다."""
    write_runnable_task(repo)
    main(["run", "--repo", str(repo), "--run-id", "run-1"])
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0
    assert "[bad]" not in capsys.readouterr().out


def test_run_reports_a_nonzero_code_when_something_needs_a_human(repo, capsys):
    write_runnable_task(repo, preconditions=[{"kind": "file", "path": "absent.txt"}])
    assert main(["run", "--repo", str(repo), "--run-id", "run-1"]) != 0
    assert "blocked" in capsys.readouterr().out


def test_run_refuses_a_profile_it_cannot_provide(repo, capsys):
    """docs/05 — container 는 M8 이다. 제공한다고 말하지 않고 사유를 출력한다."""
    write_runnable_task(repo)
    config = yaml.safe_load((repo / ".harness" / "config.yaml").read_text(encoding="utf-8"))
    config["defaults"]["profile"] = "container"
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    assert main(["run", "--repo", str(repo)]) != 0
    assert "container" in capsys.readouterr().out


def test_run_reports_a_broken_task_definition_instead_of_crashing(repo, capsys):
    write_runnable_task(repo)
    (repo / "tasks" / "T-002.task.yaml").write_text(
        "id: T-002\nname: b\nkind: analysis\ndepends_on: [T-404]\n", encoding="utf-8"
    )
    assert main(["run", "--repo", str(repo)]) != 0
    assert "T-404" in capsys.readouterr().out


# --------------------------------------------------------------------------- 재개와 복구 (M2)


def test_run_resume_reports_an_unknown_run(repo, capsys):
    write_runnable_task(repo)
    assert main(["run", "--repo", str(repo), "--resume", "run-404"]) != 0
    assert "재개할 run" in capsys.readouterr().out


def test_run_resume_picks_up_the_named_run(repo, capsys):
    """docs/10 — 같은 run-id 로 다시 돌려도 결과가 같다."""
    write_runnable_task(repo)
    main(["run", "--repo", str(repo), "--run-id", "run-1"])
    capsys.readouterr()

    assert main(["run", "--repo", str(repo), "--resume", "run-1"]) == 0
    assert "run-1" in capsys.readouterr().out


def test_doctor_removes_an_orphan_worktree(repo, capsys):
    """docs/10 — 어느 run 에도 속하지 않는 워크스페이스는 지운다."""
    orphan = repo_scratch(repo) / "run-404" / "T-001" / "worktree"
    orphan.mkdir(parents=True)

    assert main(["doctor", "--repo", str(repo)]) != 0
    assert "고아 워크트리" in capsys.readouterr().out
    assert not orphan.parent.parent.exists()


def test_doctor_keeps_the_workspace_of_a_run_it_knows(repo, capsys):
    (repo / ".harness" / "runs" / "run-1").mkdir(parents=True)
    workspace = repo_scratch(repo) / "run-1" / "T-001" / "worktree"
    workspace.mkdir(parents=True)

    main(["doctor", "--repo", str(repo)])
    assert workspace.exists()


def test_doctor_clears_the_outbox_of_a_finished_task(repo, capsys):
    """docs/10 — 승격이 끝난 attempt 디렉토리는 남겨 둘 이유가 없다."""
    write_runnable_task(repo)
    main(["run", "--repo", str(repo), "--run-id", "run-1"])
    outbox = repo_scratch(repo) / "run-1" / "T-001" / "outbox"
    assert outbox.is_dir()

    main(["doctor", "--repo", str(repo)])
    assert not outbox.exists()


# --------------------------------------------------------------------------- learn (M8)


def test_learn_proposes_and_records_the_human_decision(repo, capsys):
    """docs/08 — 하네스는 제안하고, 승격은 사람의 명령으로만 일어난다."""
    for index, task in enumerate(("T-001", "T-004"), start=1):
        store = Store(repo / ".harness" / "runs" / f"run-{index}")
        store.append(
            EventType.REVIEW_FINDING,
            {
                "wave": 1,
                "reviewer": "quality",
                "severity": "high",
                "rule": "hardcoded-secret",
                "file": "src/api/db.py",
                "line": 3,
                "blocking": True,
            },
            task,
            1,
        )

    assert main(["learn", "--repo", str(repo)]) == 0
    assert "K-001" in capsys.readouterr().out

    card = repo / ".harness" / "knowledge" / "K-001.yaml"
    assert yaml.safe_load(card.read_text(encoding="utf-8"))["status"] == "candidate"

    assert main(["learn", "promote", "K-001", "--repo", str(repo)]) == 0
    assert yaml.safe_load(card.read_text(encoding="utf-8"))["status"] == "promoted"


def test_learn_promote_needs_a_card_id(repo, capsys):
    assert main(["learn", "promote", "--repo", str(repo)]) == 2
    assert "카드 id" in capsys.readouterr().out
