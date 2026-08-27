"""M0 의 CLI — init, status, doctor. (docs/00, docs/10, docs/12)"""

import json
import pkgutil
from pathlib import Path

import yaml

import harness
from harness.cli import REQUIRED_CONTROL_PLANE, main
from harness.config import load
from harness.events import EventType
from harness.models import RunState
from harness.store import Store, fold

RUN_ID = "run-20260827-1432"


def documented_config() -> str:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "03-DATA-MODEL.md").read_text(encoding="utf-8")
    after_heading = doc.split("### `config.yaml` — canonical", 1)[1]
    return after_heading.split("```yaml", 1)[1].split("```", 1)[0]


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


def test_init_writes_the_config_documented_in_docs_03(plain_repo):
    """docs/03 의 `config.yaml — canonical` 이 계약이다. init 은 그것을 그대로 쓴다."""
    main(["init", "--repo", str(plain_repo)])
    written = yaml.safe_load((plain_repo / ".harness" / "config.yaml").read_text(encoding="utf-8"))
    assert written == yaml.safe_load(documented_config())


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
