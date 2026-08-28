"""병렬 스케줄러 — ready-set 선정, path-conflict 직렬화, 머지 큐 (docs/05).

M3 완료 기준 셋을 여기서 증명한다 (docs/12) — 경로가 겹치지 않는 task 는 동시에,
겹치는 task 는 직렬로, journal writer 는 여전히 하나.
"""

import threading
import time

import pytest
from test_runner import commit, configure, out, write_task

from harness.adapters.base import AgentResult, Capabilities, PreflightKind, PreflightReport
from harness.config import load
from harness.dag import Dag, load_tasks
from harness.exec.scheduler import conflicts, run_dag, scopes_overlap
from harness.models import ExecutionProfile, State, Task, TaskKind
from harness.store import check_sequence, fold

# --------------------------------------------------------------------------- 도구


class ParallelAdapter:
    """task_id 별로 파일 하나를 쓰는, 워커 스레드에서 불려도 안전한 어댑터."""

    name = "parallel"

    def __init__(self, files, dies_on=None):
        self.files = files  # {task_id: 워크스페이스 안 상대 경로}
        self.dies_on = dies_on
        self.peak = 0
        self._active = 0
        self._lock = threading.Lock()

    def capabilities(self):
        return Capabilities(False, False, False)

    def preflight(self):
        return PreflightReport(True, PreflightKind.OK, "ok")

    def enter(self, request):
        pass

    def execute(self, request):
        if request.task_id == self.dies_on:
            raise RuntimeError("강제 종료")
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            self.enter(request)
            relative = self.files.get(request.task_id)
            if relative:
                target = request.workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(request.task_id + "\n", encoding="utf-8")
        finally:
            with self._lock:
                self._active -= 1
        return AgentResult(
            exit_code=0,
            stdout="",
            stderr="",
            raw_claim_path=None,
            raw_handoff_path=None,
            duration_s=0.0,
            usage=None,
            transcript_path=None,
            runtime_failure=None,
        )


class Meets(ParallelAdapter):
    """모든 party 가 동시에 execute 안에 있어야만 barrier 가 풀린다."""

    def __init__(self, files, parties):
        super().__init__(files)
        self.barrier = threading.Barrier(parties, timeout=30)

    def enter(self, request):
        self.barrier.wait()


class Lingers(ParallelAdapter):
    """겹치는 실행이 있으면 peak 에 드러나도록 잠깐 머무른다."""

    def enter(self, request):
        time.sleep(0.5)


class Watches(ParallelAdapter):
    """지정한 파일이 자기 워크스페이스에 보였는지를 task 별로 기록한다."""

    def __init__(self, files, looks_for):
        super().__init__(files)
        self.looks_for = looks_for
        self.seen = {}

    def enter(self, request):
        self.seen[request.task_id] = (request.workspace / self.looks_for).is_file()


def go(repo, adapter, resume=False):
    if not resume:
        commit(repo)
    return run_dag(
        repo,
        load(repo),
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        resume=resume,
        scratch=repo.parent / "scratch",
    )


def states(store):
    return {task_id: task.state for task_id, task in store.state.tasks.items()}


def implementation(task_id, allowed):
    return Task(id=task_id, name=task_id.lower(), kind=TaskKind.IMPLEMENTATION, allowed_paths=allowed)


# --------------------------------------------------------------------------- 겹침 판정 (docs/05)


@pytest.mark.parametrize(
    ("lhs", "rhs", "overlap"),
    [
        (("src/a/**",), ("src/b/**",), False),
        (("src/**",), ("src/api/**",), True),
        (("src/api/**",), ("src/apix/**",), False),
        (("*.py",), ("src/**",), True),  # 접두사가 비면 전부와 겹친다 — 보수적
        (("docs/a.md",), ("docs/b.md",), False),
        (("docs/a.md",), ("docs/**",), True),
        (("src/a/**", "docs/**"), ("src/b/**",), False),
        (("src/a/**", "docs/**"), ("docs/guide/**",), True),
    ],
)
def test_scope_overlap_is_conservative(lhs, rhs, overlap):
    assert scopes_overlap(lhs, rhs) is overlap
    assert scopes_overlap(rhs, lhs) is overlap


def test_disjoint_scopes_do_not_conflict():
    lhs = implementation("T-001", ("src/a/**",))
    rhs = implementation("T-002", ("src/b/**",))
    assert not conflicts(lhs, rhs, ExecutionProfile.WORKTREE)


def test_an_undeclared_scope_conflicts_with_everything():
    lhs = implementation("T-001", ())
    rhs = implementation("T-002", ("src/b/**",))
    assert conflicts(lhs, rhs, ExecutionProfile.WORKTREE)
    assert conflicts(rhs, lhs, ExecutionProfile.WORKTREE)


def test_a_safe_task_conflicts_with_everything():
    lhs = Task(
        id="T-001",
        name="a",
        kind=TaskKind.ANALYSIS,
        profile=ExecutionProfile.SAFE,
        allowed_paths=("src/a/**",),
    )
    rhs = implementation("T-002", ("src/b/**",))
    assert conflicts(lhs, rhs, ExecutionProfile.WORKTREE)
    assert conflicts(rhs, lhs, ExecutionProfile.WORKTREE)


# --------------------------------------------------------------------------- M3 완료 기준


def test_disjoint_tasks_run_concurrently(repo):
    """M3 ① — 두 task 가 동시에 execute 안에 있어야만 barrier 가 풀린다."""
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", allowed_paths=["src/a/**"])
    write_task(repo, "T-002", allowed_paths=["src/b/**"])
    adapter = Meets({"T-001": "src/a/one.py", "T-002": "src/b/two.py"}, parties=2)

    store = go(repo, adapter)

    assert states(store) == {"T-001": State.DONE, "T-002": State.DONE}


def test_overlapping_tasks_are_serialized(repo):
    """M3 ② — 겹치면 직렬이 유일한 규칙이다 (docs/05)."""
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", allowed_paths=["src/**"])
    write_task(repo, "T-002", allowed_paths=["src/api/**"])
    adapter = Lingers({"T-001": "src/one.py", "T-002": "src/api/two.py"})

    store = go(repo, adapter)

    assert adapter.peak == 1
    assert states(store) == {"T-001": State.DONE, "T-002": State.DONE}


def test_the_journal_writer_stays_single(repo):
    """M3 ③ — 병렬로 돌아도 seq 는 결번 없이 단조 증가하고 state == fold(journal) 이다."""
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", allowed_paths=["src/a/**"])
    write_task(repo, "T-002", allowed_paths=["src/b/**"])
    adapter = Meets({"T-001": "src/a/one.py", "T-002": "src/b/two.py"}, parties=2)

    store = go(repo, adapter)

    events = store.journal.read()
    assert check_sequence(events) == []
    assert fold(events, store.run_id).to_dict() == store.state.to_dict()


# --------------------------------------------------------------------------- 스케줄링 규칙


def test_max_parallel_caps_concurrency(repo):
    configure(repo, profile="worktree", max_parallel=2)
    files = {}
    for number, letter in ((1, "a"), (2, "b"), (3, "c")):
        write_task(repo, f"T-00{number}", allowed_paths=[f"src/{letter}/**"])
        files[f"T-00{number}"] = f"src/{letter}/f.py"
    adapter = Lingers(files)

    store = go(repo, adapter)

    assert adapter.peak == 2  # 셋 다 분리돼 있어도 상한은 2 다
    assert set(states(store).values()) == {State.DONE}


def test_a_safe_task_serializes_with_the_run(repo):
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", kind="analysis", profile="safe")
    write_task(repo, "T-002", allowed_paths=["src/b/**"])
    adapter = Lingers({"T-002": "src/b/two.py"})

    store = go(repo, adapter)

    assert adapter.peak == 1
    assert states(store) == {"T-001": State.DONE, "T-002": State.DONE}


def test_a_serialized_task_sees_the_earlier_merge(repo):
    """미뤄진 task 는 그 시점의 통합 tip 에서 분기하므로 앞선 결과를 본다 (docs/05)."""
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", allowed_paths=["src/**"])
    write_task(repo, "T-002", allowed_paths=["src/api/**"])  # T-001 과 겹친다
    adapter = Watches(
        {"T-001": "src/one.py", "T-002": "src/api/two.py"}, looks_for="src/one.py"
    )

    store = go(repo, adapter)

    assert states(store) == {"T-001": State.DONE, "T-002": State.DONE}
    assert adapter.seen["T-002"] is True


def test_the_merge_queue_lands_every_task(repo):
    configure(repo, profile="worktree", max_parallel=3)
    files = {}
    for number, letter in ((1, "a"), (2, "b"), (3, "c")):
        write_task(repo, f"T-00{number}", allowed_paths=[f"src/{letter}/**"])
        files[f"T-00{number}"] = f"src/{letter}/f.py"
    adapter = Meets(files, parties=3)

    go(repo, adapter)

    tree = out(repo, "ls-tree", "-r", "--name-only", "harness/run-1/integration")
    assert set(files.values()) <= set(tree.splitlines())


# --------------------------------------------------------------------------- 재개 (docs/10)


def test_a_parallel_run_resumes_after_a_crash(repo):
    """병렬에서도 재개 규칙은 같다 — 처리는 task 의 state 로만 결정된다."""
    configure(repo, profile="worktree", max_parallel=2)
    write_task(repo, "T-001", allowed_paths=["src/a/**"])
    write_task(repo, "T-002", allowed_paths=["src/b/**"])
    files = {"T-001": "src/a/one.py", "T-002": "src/b/two.py"}

    with pytest.raises(RuntimeError):
        go(repo, ParallelAdapter(files, dies_on="T-002"))

    store = go(repo, ParallelAdapter(files), resume=True)
    assert states(store) == {"T-001": State.DONE, "T-002": State.DONE}


# --------------------------------------------------------------------------- cli 디스패치


def test_the_cli_hands_a_parallel_config_to_the_scheduler(repo, monkeypatch, capsys):
    import harness.exec.scheduler as scheduler_module
    from harness.cli import main

    real, calls = scheduler_module.run_dag, []

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(scheduler_module, "run_dag", spy)
    configure(
        repo,
        scenario={"tasks": {"T-001": {"files": {"a.py": "1\n"}}}},
        profile="worktree",
        max_parallel=2,
    )
    write_task(repo, "T-001")
    commit(repo)

    assert main(["run", "--repo", str(repo)]) == 0
    assert calls
