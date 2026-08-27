"""journal append + state projection + 재구성.

docs/03 — journal 이 canonical 이고 `state.json` 은 그것을 fold 한 캐시다.
docs/10 — 쓰기 순서는 journal append + fsync -> state 갱신이며 뒤집지 않는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from harness.errors import JournalCorruptionError
from harness.events import Event, EventType, make_event
from harness.models import Debt, RunState, State, TaskProjection, Verdict

# 이벤트가 직접 결정하는 state. 여기 없는 이벤트는 증거일 뿐 워크플로 위치를 바꾸지 않는다.
_STATE_BY_EVENT: dict[EventType, State] = {
    EventType.TASK_DISPATCHED: State.PRECHECK,
    EventType.AGENT_STARTED: State.RUNNING,
    EventType.AGENT_FINISHED: State.EXECUTED,
    EventType.CLAIM_RECEIVED: State.VERIFYING,
    EventType.CLAIM_REJECTED: State.VERIFYING,
    EventType.CLAIM_UNCORROBORATED: State.VERIFYING,
    EventType.HANDOFF_RECEIVED: State.VERIFYING,
    EventType.HANDOFF_REJECTED: State.VERIFYING,
    EventType.AC_POST_EXECUTED: State.VERIFYING,
    EventType.PATH_VIOLATION: State.VERIFYING,
    EventType.REVIEW_FINDING: State.REVIEWING,
    EventType.HANDOFF_MISSING: State.REPAIRING,
}


class Journal:
    """append-only 이벤트 로그. writer 는 오케스트레이터 프로세스 하나뿐이다."""

    def __init__(self, path: Path | str, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._last_seq: int | None = None

    @property
    def last_seq(self) -> int:
        if self._last_seq is None:
            events = self.read()
            self._last_seq = events[-1].seq if events else 0
        return self._last_seq

    def append(
        self,
        type: EventType,
        payload: Mapping[str, Any],
        task_id: str | None = None,
        attempt: int | None = None,
    ) -> Event:
        event = make_event(self.run_id, self.last_seq + 1, type, payload, task_id, attempt)
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._last_seq = event.seq
        return event

    def read(self) -> list[Event]:
        """journal 을 처음부터 읽는다.

        개행 없이 끊긴 마지막 줄은 쓰기 도중 죽은 흔적이므로 버린다 (docs/10).
        그 밖의 손상은 버리지 않고 올린다 — doctor 가 보고할 일이지 조용히
        지나갈 일이 아니다.
        """
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8")
        if not raw:
            return []
        lines = raw.split("\n")
        lines.pop()  # 개행 뒤의 빈 문자열이거나, 개행 없이 끊긴 부분 줄이다.

        events = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                events.append(Event.from_dict(json.loads(line)))
            except (ValueError, KeyError) as exc:
                raise JournalCorruptionError(f"{self.path}:{number} 손상: {exc}") from exc
        return events


def check_sequence(events: Iterable[Event]) -> list[str]:
    """seq 의 결번·역행을 보고한다. 고치지 않는다 (docs/10)."""
    problems = []
    expected = 1
    for event in events:
        if event.seq != expected:
            problems.append(f"seq {expected} 이(가) 와야 하는 자리에 {event.seq} 이(가) 있다")
            expected = event.seq
        expected += 1
    return problems


def apply(state: RunState, event: Event) -> RunState:
    """이벤트 하나를 projection 에 반영한다."""
    state.last_applied_seq = event.seq

    if event.type is EventType.RUN_STARTED:
        state.started_at = event.ts
        manifest = event.payload.get("manifest") or {}
        state.manifest = manifest
        for task_id in manifest.get("task_ids") or ():
            state.tasks.setdefault(task_id, TaskProjection(task_id=task_id))
        return state

    if event.type is EventType.RUN_FINISHED:
        state.finished_at = event.ts
        return state

    if event.type is EventType.DEBT_OPENED:
        debt_id = event.payload["debt_id"]
        state.open_debts[debt_id] = Debt(
            debt_id=debt_id,
            cmd=tuple(event.payload.get("cmd") or ()),
            origin_task=event.payload.get("origin_task"),
        )
        return state

    if event.type is EventType.DEBT_CLOSED:
        state.open_debts.pop(event.payload["debt_id"], None)
        return state

    if event.task_id is None:
        return state

    task = state.tasks.setdefault(event.task_id, TaskProjection(task_id=event.task_id))
    if event.attempt is not None and event.attempt > task.attempt:
        task.attempt = event.attempt

    if event.type is EventType.VERDICT_ASSIGNED:
        _apply_verdict(task, event)
        return state

    if event.type is EventType.FIXER_DISPATCHED:
        scope = event.payload.get("scope")
        task.state = State.REPAIRING if scope == "handoff" else State.REVIEWING
        return state

    next_state = _STATE_BY_EVENT.get(event.type)
    if next_state is not None:
        task.state = next_state
    return state


def _apply_verdict(task: TaskProjection, event: Event) -> None:
    """docs/03 — task 의 최종 verdict 는 가장 큰 attempt 의 verdict_assigned 다."""
    attempt = event.attempt if event.attempt is not None else event.payload.get("attempt")
    if task.verdict_attempt is not None and attempt is not None and attempt < task.verdict_attempt:
        return
    task.verdict = Verdict(event.payload["verdict"])
    task.verdict_attempt = attempt
    task.reason = event.payload.get("reason")
    task.state = State(event.payload["next_state"])


def fold(events: Iterable[Event], run_id: str) -> RunState:
    state = RunState(run_id=run_id)
    for event in events:
        apply(state, event)
    return state


class Store:
    """한 run 의 journal 과 그 projection."""

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = self.run_dir.name
        self.journal = Journal(self.run_dir / "journal.jsonl", self.run_id)
        self.state_path = self.run_dir / "state.json"
        self._state = self._load_or_fold()

    @property
    def state(self) -> RunState:
        return self._state

    def append(
        self,
        type: EventType,
        payload: Mapping[str, Any],
        task_id: str | None = None,
        attempt: int | None = None,
    ) -> Event:
        event = self.journal.append(type, payload, task_id, attempt)  # 1) append + fsync
        apply(self._state, event)  # 2) projection
        self._write_state()  # 3) 스냅샷
        return event

    def rebuild(self) -> RunState:
        """journal 을 기준으로 state 를 재구성하고 스냅샷을 다시 쓴다."""
        self._state = fold(self.journal.read(), self.run_id)
        self._write_state()
        return self._state

    def _load_or_fold(self) -> RunState:
        """스냅샷이 journal 과 어긋나 있으면 버리고 fold 한다. state 는 캐시다."""
        events = self.journal.read()
        last_seq = events[-1].seq if events else 0
        snapshot = self._read_state()
        if snapshot is not None and snapshot.last_applied_seq == last_seq:
            return snapshot
        return fold(events, self.run_id)

    def _read_state(self) -> RunState | None:
        if not self.state_path.exists():
            return None
        try:
            return RunState.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (ValueError, KeyError):
            return None

    def _write_state(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.run_dir / "state.json.tmp"
        temp_path.write_text(
            json.dumps(self._state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.state_path)
