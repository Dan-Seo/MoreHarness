"""docs/03 의 journal/projection 계약과 docs/10 의 크래시 일관성을 검증한다.

M0 의 완료 기준이 이 파일에 있다.
  - state == fold(journal)
  - 임의 지점에서 프로세스를 죽인 뒤 journal 로 상태가 복원된다
"""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from harness.errors import JournalCorruptionError
from harness.events import EventType
from harness.models import RunState, State, Verdict
from harness.store import Journal, Store, check_sequence, fold

RUN_ID = "run-20260827-1432"

MANIFEST = {
    "run_id": RUN_ID,
    "created_at": "2026-08-27T14:32:00+09:00",
    "repo_root": "/repo",
    "adapter": "mock",
    "profile": "worktree",
    "max_parallel": 1,
    "task_ids": ["T-001", "T-002"],
}


def new_store(tmp_path):
    return Store(tmp_path / "runs" / RUN_ID)


def start_run(store):
    return store.append(
        EventType.RUN_STARTED,
        {"manifest": MANIFEST, "profile": "worktree", "adapter": "mock", "max_parallel": 1},
    )


# --------------------------------------------------------------------------- journal


def test_seq_starts_at_one_and_is_gapless(tmp_path):
    store = new_store(tmp_path)
    start_run(store)
    for _ in range(3):
        store.append(
            EventType.BUDGET_CHECKPOINT,
            {"tokens": 0, "cost_usd": None, "wall_time_s": 0.0, "remaining": None},
        )
    seqs = [e.seq for e in store.journal.read()]
    assert seqs == [1, 2, 3, 4]


def test_event_ids_follow_the_run_id(tmp_path):
    store = new_store(tmp_path)
    ev = start_run(store)
    assert ev.id == f"{RUN_ID}-000001"


def test_journal_is_append_only_jsonl(tmp_path):
    store = new_store(tmp_path)
    start_run(store)
    start = store.journal.path.read_bytes()
    store.append(
        EventType.BUDGET_CHECKPOINT,
        {"tokens": 1, "cost_usd": None, "wall_time_s": 0.1, "remaining": None},
    )
    after = store.journal.path.read_bytes()
    assert after.startswith(start)
    assert len(after.decode("utf-8").strip().splitlines()) == 2


def test_reopening_a_journal_continues_the_sequence(tmp_path):
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.BUDGET_CHECKPOINT,
        {"tokens": 1, "cost_usd": None, "wall_time_s": 0.1, "remaining": None},
    )
    reopened = new_store(tmp_path)
    ev = reopened.append(
        EventType.RUN_FINISHED, {"summary": {}, "open_debts": [], "human_required": []}
    )
    assert ev.seq == 3


def test_corrupt_trailing_line_is_dropped(tmp_path):
    """docs/10 — 부분 기록된 마지막 줄은 버린다. 앞선 이벤트는 유효하다."""
    store = new_store(tmp_path)
    start_run(store)
    with open(store.journal.path, "a", encoding="utf-8") as fh:
        fh.write('{"id": "run-2026082')  # 개행 없이 잘린 줄
    events = store.journal.read()
    assert len(events) == 1
    assert events[0].type is EventType.RUN_STARTED


def test_corruption_in_the_middle_raises(tmp_path):
    """마지막 줄이 아닌 곳의 손상은 버리지 않는다. doctor 가 보고하도록 올린다."""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.BUDGET_CHECKPOINT,
        {"tokens": 1, "cost_usd": None, "wall_time_s": 0.1, "remaining": None},
    )
    lines = store.journal.path.read_text(encoding="utf-8").splitlines()
    lines[0] = "{ this is not json"
    store.journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalCorruptionError):
        store.journal.read()


def test_check_sequence_reports_gaps_without_fixing_them(tmp_path):
    """docs/10 — seq 결번·역행은 보고만 한다."""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.BUDGET_CHECKPOINT,
        {"tokens": 1, "cost_usd": None, "wall_time_s": 0.1, "remaining": None},
    )
    events = store.journal.read()
    assert check_sequence(events) == []

    lines = store.journal.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["seq"] = 9
    lines[1] = json.dumps(record)
    store.journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    problems = check_sequence(store.journal.read())
    assert problems
    assert store.journal.path.read_text(encoding="utf-8").splitlines()[1] == lines[1]


def test_journal_events_are_immutable(tmp_path):
    store = new_store(tmp_path)
    ev = start_run(store)
    with pytest.raises(Exception):
        ev.seq = 99


# --------------------------------------------------------------------------- projection


def test_run_started_seeds_tasks_from_the_manifest(tmp_path):
    store = new_store(tmp_path)
    start_run(store)
    assert set(store.state.tasks) == {"T-001", "T-002"}
    assert all(t.state is State.PENDING for t in store.state.tasks.values())


def test_lifecycle_states_follow_the_state_machine(tmp_path):
    store = new_store(tmp_path)
    start_run(store)

    store.append(
        EventType.TASK_DISPATCHED,
        {"context_manifest_ref": None, "prompt_ref": None, "effective_risk": "medium"},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.PRECHECK

    store.append(
        EventType.AGENT_STARTED,
        {"adapter": "mock", "workspace": "/w", "outbox": "/o"},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.RUNNING

    store.append(
        EventType.AGENT_FINISHED,
        {"exit_code": 0, "duration_s": 1.0, "usage": None, "runtime_failure": None},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.EXECUTED

    store.append(
        EventType.AC_POST_EXECUTED,
        {"cmd": ["pytest"], "exit_code": 0, "classification": "green", "differential": "green_green"},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.VERIFYING

    store.append(
        EventType.REVIEW_FINDING,
        {
            "wave": 1,
            "reviewer": "r1",
            "severity": "low",
            "rule": "style",
            "file": "a.py",
            "line": 1,
            "blocking": False,
        },
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.REVIEWING

    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "verified", "attempt": 1, "reason": None, "next_state": "done"},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.DONE
    assert store.state.tasks["T-001"].verdict is Verdict.VERIFIED


def test_handoff_missing_moves_to_repairing_without_a_verdict(tmp_path):
    """docs/03 — required handoff 누락은 verdict 없이 state 만 갖는다."""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.HANDOFF_MISSING,
        {"required": ["public_api"], "present": []},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is State.REPAIRING
    assert store.state.tasks["T-001"].verdict is None


def test_final_verdict_is_the_one_with_the_largest_attempt(tmp_path):
    """docs/03 — task 의 최종 verdict 는 가장 큰 attempt 의 verdict_assigned 다."""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "rejected", "attempt": 1, "reason": "regression", "next_state": "ready"},
        task_id="T-001",
        attempt=1,
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": "verified", "attempt": 2, "reason": None, "next_state": "done"},
        task_id="T-001",
        attempt=2,
    )
    assert store.state.tasks["T-001"].verdict is Verdict.VERIFIED
    assert store.state.tasks["T-001"].verdict_attempt == 2
    assert store.state.tasks["T-001"].attempt == 2


def test_debts_open_and_close(tmp_path):
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.DEBT_OPENED,
        {"debt_id": "D-001", "cmd": ["npm", "test"], "origin_task": "T-001"},
        task_id="T-001",
        attempt=1,
    )
    assert "D-001" in store.state.open_debts
    store.append(
        EventType.DEBT_CLOSED,
        {"debt_id": "D-001", "closed_by": "T-002"},
        task_id="T-002",
        attempt=1,
    )
    assert store.state.open_debts == {}


def test_evidence_only_events_do_not_change_state(tmp_path):
    """exit code 는 증거일 뿐 판정이 아니다. (docs/06)"""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.AGENT_FINISHED,
        {"exit_code": 1, "duration_s": 1.0, "usage": None, "runtime_failure": None},
        task_id="T-001",
        attempt=1,
    )
    before = store.state.tasks["T-001"].state
    store.append(
        EventType.AGENT_EXIT_NONZERO,
        {"exit_code": 1, "stderr_tail": "boom"},
        task_id="T-001",
        attempt=1,
    )
    assert store.state.tasks["T-001"].state is before
    assert store.state.tasks["T-001"].verdict is None


def test_events_for_unknown_tasks_are_still_projected(tmp_path):
    """manifest 에 없던 task 의 이벤트도 projection 에 나타난다."""
    store = new_store(tmp_path)
    start_run(store)
    store.append(
        EventType.TASK_DISPATCHED,
        {"context_manifest_ref": None, "prompt_ref": None, "effective_risk": "low"},
        task_id="T-099",
        attempt=1,
    )
    assert store.state.tasks["T-099"].state is State.PRECHECK


# --------------------------------------------------------------------------- state == fold(journal)


def build_full_journal(store):
    start_run(store)
    for task_id in ("T-001", "T-002"):
        store.append(
            EventType.TASK_DISPATCHED,
            {"context_manifest_ref": None, "prompt_ref": None, "effective_risk": "medium"},
            task_id=task_id,
            attempt=1,
        )
        store.append(
            EventType.AGENT_STARTED,
            {"adapter": "mock", "workspace": "/w", "outbox": "/o"},
            task_id=task_id,
            attempt=1,
        )
        store.append(
            EventType.AGENT_FINISHED,
            {"exit_code": 0, "duration_s": 0.5, "usage": None, "runtime_failure": None},
            task_id=task_id,
            attempt=1,
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
    store.append(
        EventType.RUN_FINISHED,
        {"summary": {"verified": 1}, "open_debts": ["D-001"], "human_required": ["T-002"]},
    )


def test_state_equals_fold_of_journal(tmp_path):
    store = new_store(tmp_path)
    build_full_journal(store)
    assert store.state == fold(store.journal.read(), RUN_ID)


def test_state_file_on_disk_equals_fold_of_journal(tmp_path):
    store = new_store(tmp_path)
    build_full_journal(store)
    on_disk = RunState.from_dict(json.loads(store.state_path.read_text(encoding="utf-8")))
    assert on_disk == fold(store.journal.read(), RUN_ID)
    assert on_disk.last_applied_seq == store.journal.read()[-1].seq


def test_state_is_rebuilt_when_the_snapshot_is_missing(tmp_path):
    store = new_store(tmp_path)
    build_full_journal(store)
    expected = store.state
    store.state_path.unlink()

    reopened = new_store(tmp_path)
    assert reopened.state == expected


def test_state_is_rebuilt_when_the_snapshot_is_corrupt(tmp_path):
    """docs/10 — state 는 캐시이므로 언제든 버릴 수 있다."""
    store = new_store(tmp_path)
    build_full_journal(store)
    expected = store.state
    store.state_path.write_text("{ not json", encoding="utf-8")

    reopened = new_store(tmp_path)
    assert reopened.state == expected


def test_journal_ahead_of_state_is_recovered(tmp_path):
    """docs/10 — 2 와 3 사이에서 죽으면 journal 이 앞서 있다. 재개 시 복원된다."""
    store = new_store(tmp_path)
    build_full_journal(store)
    expected = store.state

    stale = json.loads(store.state_path.read_text(encoding="utf-8"))
    stale["last_applied_seq"] = 1
    stale["tasks"] = {}
    stale["open_debts"] = {}
    store.state_path.write_text(json.dumps(stale), encoding="utf-8")

    reopened = new_store(tmp_path)
    assert reopened.state.last_applied_seq == expected.last_applied_seq
    assert reopened.state == expected


def test_append_writes_the_journal_before_the_state(tmp_path, monkeypatch):
    """docs/03 — 쓰기 순서는 journal append + fsync -> state 갱신이다."""
    store = new_store(tmp_path)
    start_run(store)
    seq_before = store.journal.read()[-1].seq

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_state", boom)
    with pytest.raises(OSError):
        store.append(
            EventType.BUDGET_CHECKPOINT,
            {"tokens": 1, "cost_usd": None, "wall_time_s": 0.1, "remaining": None},
        )

    monkeypatch.undo()
    assert store.journal.read()[-1].seq == seq_before + 1
    snapshot = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert snapshot["last_applied_seq"] == seq_before

    reopened = new_store(tmp_path)
    assert reopened.state.last_applied_seq == seq_before + 1


def test_fold_is_pure_and_repeatable(tmp_path):
    store = new_store(tmp_path)
    build_full_journal(store)
    events = store.journal.read()
    assert fold(events, RUN_ID) == fold(events, RUN_ID)


# --------------------------------------------------------------------------- 강제 종료


KILL_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from harness.events import EventType
    from harness.store import Store

    store = Store(Path(sys.argv[1]))
    store.append(
        EventType.RUN_STARTED,
        {"manifest": {"task_ids": ["T-001"]}, "profile": "safe", "adapter": "mock", "max_parallel": 1},
    )
    while True:
        store.append(
            EventType.BUDGET_CHECKPOINT,
            {"tokens": 1, "cost_usd": None, "wall_time_s": 0.0, "remaining": None},
        )
        time.sleep(0.002)
    """
)


def test_state_is_recoverable_after_the_process_is_killed(tmp_path):
    """M0 완료 기준 — 임의 지점에서 프로세스를 죽인 뒤 journal 로 상태가 복원된다."""
    run_dir = tmp_path / "runs" / RUN_ID
    script = tmp_path / "writer.py"
    script.write_text(KILL_SCRIPT, encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    proc = subprocess.Popen([sys.executable, str(script), str(run_dir)], env=env)
    try:
        journal_path = run_dir / "journal.jsonl"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if journal_path.exists() and journal_path.read_bytes().count(b"\n") >= 20:
                break
            time.sleep(0.01)
        else:  # pragma: no cover - 환경 문제일 때만
            pytest.fail("writer 프로세스가 journal 을 쓰지 못했다")
        proc.kill()
    finally:
        proc.wait(timeout=10)

    reopened = Store(run_dir)
    events = reopened.journal.read()
    assert len(events) >= 20
    assert check_sequence(events) == []
    assert reopened.state == fold(events, RUN_ID)
    assert reopened.state.last_applied_seq == events[-1].seq


def test_a_state_only_outcome_is_recorded_with_a_null_verdict(tmp_path):
    """docs/03 — handoff 누락·머지 충돌·task 정의 결함은 verdict 없이 next_state 만 갖는다."""
    store = Store(tmp_path / "runs" / "run-1")
    store.append(
        EventType.RUN_STARTED,
        {"manifest": {"task_ids": ["T-001"]}, "profile": "safe", "adapter": "mock", "max_parallel": 1},
    )
    store.append(
        EventType.VERDICT_ASSIGNED,
        {"verdict": None, "attempt": 1, "reason": "no_op_detected", "next_state": "needs_replan"},
        task_id="T-001",
        attempt=1,
    )
    task = store.state.tasks["T-001"]
    assert task.verdict is None
    assert task.state is State.NEEDS_REPLAN
    assert task.reason == "no_op_detected"
    assert store.rebuild().tasks["T-001"].state is State.NEEDS_REPLAN
