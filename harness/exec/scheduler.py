"""병렬 스케줄러 — ready-set 선정, path-conflict 직렬화, 머지 큐. docs/05 가 canonical 이다.

옵션 모듈이다 (docs/02). `max_parallel` 이 1 이면 커널의 sequential runner 만으로
동작하고, 2 이상이면 cli 가 이 모듈을 고른다. 커널은 이 모듈을 import 하지 않는다.

병렬화되는 것은 task 의 실행이지 상태 기록이 아니다. journal writer 는 여전히
오케스트레이터 프로세스 하나이고 (docs/03), append 는 Store 가 직렬화한다.
통합이 항상 직렬인 것은 Workspaces 의 머지 큐가 보장한다 (docs/05).
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.config import Config
from harness.dag import Dag
from harness.events import EventType
from harness.exec import runner as runner_module
from harness.models import ExecutionProfile, Task
from harness.store import Store

WILDCARDS = "*?"


def run_dag(
    repo: Path | str,
    config: Config,
    dag: Dag,
    *,
    adapter: Any = None,
    run_id: str | None = None,
    resume: bool = False,
    scratch: Path | str | None = None,
    context_builder: Any = None,
    review_stage: Any = None,
    tdd_stage: Any = None,
) -> Store:
    """`runner.run_dag` 와 같은 진입 절차로 ParallelRunner 를 돌린다."""
    return runner_module.run_dag(
        repo,
        config,
        dag,
        adapter=adapter,
        run_id=run_id,
        resume=resume,
        scratch=scratch,
        runner_cls=ParallelRunner,
        context_builder=context_builder,
        review_stage=review_stage,
        tdd_stage=tdd_stage,
    )


def conflicts(lhs: Task, rhs: Task, default_profile: ExecutionProfile) -> bool:
    """docs/05 의 겹침 판정 — safe 와 미선언은 전부와 겹치고, 나머지는 접두사 규칙이다."""
    for task in (lhs, rhs):
        if (task.profile or default_profile) is ExecutionProfile.SAFE:
            return True  # 메인 워크트리를 공유한다
        if not task.allowed_paths:
            return True  # 허용 범위에 제한이 없다
    return scopes_overlap(lhs.allowed_paths, rhs.allowed_paths)


def scopes_overlap(lhs: Sequence[str], rhs: Sequence[str]) -> bool:
    """선언된 두 allowed_paths 가 겹칠 수 있는가.

    docs/05 의 보수적 규칙 — 첫 와일드카드 앞까지의 리터럴 접두사가 서로 접두사
    관계면 겹친다고 본다. 과잉 직렬화는 병렬 기회를 잃을 뿐이지만 과소 판정은
    판정을 오염시키므로, 틀리려면 과잉 쪽으로만 틀린다.
    """
    return any(_pair_overlaps(p, q) for p in lhs for q in rhs)


def _pair_overlaps(lhs: str, rhs: str) -> bool:
    a, b = _literal_prefix(lhs), _literal_prefix(rhs)
    return a.startswith(b) or b.startswith(a)


def _literal_prefix(pattern: str) -> str:
    for index, char in enumerate(pattern):
        if char in WILDCARDS:
            return pattern[:index]
    return pattern


class ParallelRunner(runner_module.Runner):
    """워커 스레드 위에서 task 를 돌린다. attempt 실행·판정·통합은 Runner 그대로다."""

    def run(self, dag: Dag, *, resume: bool = False) -> Store:
        verified, remaining = self._begin(dag, resume)
        running: dict[str, Future] = {}

        with ThreadPoolExecutor(max_workers=self.config.max_parallel) as pool:
            while True:
                for task in self._selection(dag, verified, remaining, running):
                    remaining.discard(task.id)
                    running[task.id] = pool.submit(self._run_task, task)
                if not running:
                    break
                done = wait(running.values(), return_when=FIRST_COMPLETED).done
                for task_id in [t for t, future in running.items() if future in done]:
                    if running.pop(task_id).result():
                        verified.add(task_id)

        self.store.append(EventType.RUN_FINISHED, self._summary())
        return self.store

    def _selection(
        self,
        dag: Dag,
        verified: set[str],
        remaining: set[str],
        running: Mapping[str, Future],
    ) -> list[Task]:
        """docs/05 의 스케줄링 — 위상 정렬 순서로 훑고, 겹치면 건너뛰고, 상한에서 멈춘다."""
        default = self.config.default_profile
        taken = [dag.tasks[task_id] for task_id in running]
        chosen: list[Task] = []
        for task_id in dag.ready(verified, remaining):
            if len(taken) + len(chosen) >= self.config.max_parallel:
                break
            task = dag.tasks[task_id]
            if any(conflicts(task, other, default) for other in (*taken, *chosen)):
                continue
            chosen.append(task)
        return chosen
