"""지표 — 전부 journal 의 projection 이다. docs/11 이 canonical 이다.

지표를 위해 코드에 카운터를 심지 않는다. 여기 없는 지표가 필요하면 먼저 필요한
이벤트가 journal 에 있는지 본다. 벤더가 보고하지 않은 값은 `None` 이며 추정하지
않는다 — `None` 은 집계에서 제외되고 리포트에 `n/a` 로 표시된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence

from harness.config import HARNESS_DIR
from harness.events import Event, EventType
from harness.store import Store


@dataclass(frozen=True)
class RunMetrics:
    """실행 1회의 비용·속도 지표 (docs/11)."""

    wall_time_s: float | None
    agent_time_s: float | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    retry_count: int
    first_pass: bool | None  # 판정 이벤트가 없는 run(raw)은 None
    review_waves: int
    human_interventions: int
    context_tokens: int | None


def of_run(repo: Path | str, run_id: str) -> RunMetrics:
    run_dir = Path(repo) / HARNESS_DIR / "runs" / run_id
    store = Store(run_dir)
    events = store.journal.read()

    finished = [e for e in events if e.type is EventType.AGENT_FINISHED]
    usages = [e.payload.get("usage") or {} for e in finished]
    verdicts = [e for e in events if e.type is EventType.VERDICT_ASSIGNED]

    return RunMetrics(
        wall_time_s=_wall(events),
        agent_time_s=_sum(e.payload.get("duration_s") for e in finished),
        tokens_in=_sum(u.get("tokens_in") for u in usages),
        tokens_out=_sum(u.get("tokens_out") for u in usages),
        cost_usd=_sum(u.get("cost_usd") for u in usages),
        retry_count=max((e.attempt or 0 for e in verdicts), default=0),
        first_pass=_first_pass(verdicts),
        review_waves=sum(1 for e in events if e.type is EventType.FIXER_DISPATCHED),
        # docs/11 — human_required 로 **간 횟수**다. 현재 상태가 아니라 전이를 센다.
        human_interventions=sum(
            1 for e in verdicts if e.payload.get("next_state") == "human_required"
        ),
        context_tokens=_context_tokens(run_dir),
    )


def median_range(values: Iterable[float | None]) -> dict[str, float] | None:
    """docs/11 — 연속 지표는 중앙값과 [min, max]. null 은 제외하고 전부 null 이면 None."""
    known = [value for value in values if value is not None]
    if not known:
        return None
    return {"median": median(known), "min": min(known), "max": max(known)}


def rates(results: Sequence[Any]) -> dict[str, float | None]:
    """docs/11 의 외부 결과·보정 지표. 분모가 0 이면 null 이다."""
    total = len(results)
    checks = sum(r.grader.total for r in results)
    verified = [r for r in results if r.harness == "verified"]
    blocked = [r for r in results if r.harness == "rejected_or_blocked"]
    return {
        "grader_success_rate": _ratio(sum(1 for r in results if r.grader.success), total),
        "hidden_ac_pass_rate": _ratio(sum(r.grader.green for r in results), checks),
        "escape_rate": _ratio(sum(1 for r in verified if not r.grader.success), len(verified)),
        "false_block_rate": _ratio(sum(1 for r in blocked if r.grader.success), len(blocked)),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _sum(values: Iterable[Any]) -> Any:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _wall(events: list[Event]) -> float | None:
    started = next((e for e in events if e.type is EventType.RUN_STARTED), None)
    ended = next((e for e in reversed(events) if e.type is EventType.RUN_FINISHED), None)
    if started is None or ended is None:
        return None
    try:
        delta = datetime.fromisoformat(ended.ts) - datetime.fromisoformat(started.ts)
    except ValueError:
        return None
    return delta.total_seconds()


def _first_pass(verdicts: list[Event]) -> bool | None:
    if not verdicts:
        return None
    final: dict[str, Event] = {e.task_id: e for e in verdicts if e.task_id}
    return all(
        e.payload.get("verdict") == "verified" and (e.attempt or 0) == 1 for e in final.values()
    )


def _context_tokens(run_dir: Path) -> int | None:
    totals = []
    for manifest in run_dir.glob("tasks/*/context.manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(data.get("total_tokens"), int):
            totals.append(data["total_tokens"])
    return sum(totals) if totals else None
