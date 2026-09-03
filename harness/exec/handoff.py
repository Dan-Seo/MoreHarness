"""outbox 아티팩트의 정규화, required handoff 게이트, TaskOutput 병합.

docs/06 이 canonical 이다.

> **Invalid claim is an invalid report, not automatically an invalid implementation.**

정규화는 판정이 아니다. 여기서 verdict 가 결정되는 경로는 없다. 검증에 실패한 것은
버리지 않고 `*.invalid.json` 으로 보존하며, 이후 판정에서 `None` 으로 취급된다.

구현 판정과 handoff 게이트는 분리되어 있다. required handoff 가 없다고 해서 통과한
구현을 되돌리지 않는다 — 코드는 그대로 두고 handoff artifact 만 다시 만든다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from harness.models import Task
from harness.schemas import first_error

CLAIM = "claim"
HANDOFF = "handoff"


# 프롬프트가 알려주는 산출물 계약. envelope 의 canonical 정의는 docs/03 이다.
_OUTPUT_CONTRACT = """claim 은 `$HARNESS_OUTBOX/result.json`, handoff 는 `$HARNESS_OUTBOX/handoff.json` 이다.
둘 다 optional 이며, 하네스는 이 보고가 아니라 자기 관측으로 판정한다.
형식은 다음이고, 벗어나면 보고가 버려진다.
`{{"schema": "harness.claim/v1", "task_id": "{task_id}", "outcome_claim": "implemented|blocked|infeasible", "blocked_hint": "..."}}`
`{{"schema": "harness.handoff/v1", "task_id": "{task_id}", "<필드>": ...}}`"""


def output_contract(task_id: str) -> str:
    """경로와 **envelope 형태**를 함께 알려준다 (docs/04 Outbox 규약).

    형태를 모르는 agent 는 자기 방식대로 쓰고, 그 결과는 `*.invalid.json` 이 된다.
    커널의 프롬프트와 계층형 컨텍스트(docs/07)가 같은 문장을 써야 하므로 여기 한 곳에만 둔다.
    """
    return _OUTPUT_CONTRACT.format(task_id=task_id)


@dataclass(frozen=True)
class Artifact:
    """정규화 결과.

    `data` 가 `None` 인 경우는 둘이다 — 아티팩트가 없었거나(`error` 도 `None`),
    검증에 실패했거나(`error` 가 사유). 판정에서는 둘 다 `None` 으로 취급한다.
    """

    kind: str
    data: Mapping[str, Any] | None
    path: Path | None
    error: str | None

    @property
    def valid(self) -> bool:
        return self.data is not None


@dataclass(frozen=True)
class HandoffGate:
    ok: bool
    required: tuple[str, ...]
    present: tuple[str, ...]
    missing: tuple[str, ...]

    def missing_payload(self) -> dict[str, Any]:
        """docs/03 — handoff_missing."""
        return {"required": list(self.required), "present": list(self.present)}


def normalize(kind: str, raw_path: Path | None, dest_dir: Path | str) -> Artifact:
    """outbox 의 raw 아티팩트를 검증해 control-plane 으로 승격한다.

    agent 산출물이 `.harness/` 에 직접 들어가는 경로는 없다. 원본은 건드리지 않는다.
    """
    if raw_path is None or not Path(raw_path).is_file():
        return Artifact(kind, None, None, None)

    raw = Path(raw_path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return _preserve(kind, dest_dir, raw, f"JSON 이 아니다: {exc}")

    error = first_error(kind, data)
    if error:
        return _preserve(kind, dest_dir, raw, error)

    path = _write(dest_dir, f"{kind}.json", raw)
    return Artifact(kind, data, path, None)


def gate(task: Task, handoff: Artifact) -> HandoffGate:
    """docs/06 의 required handoff 게이트. 구현 판정과 분리되어 있다."""
    fields = handoff.data or {}
    present = tuple(name for name in task.required_outputs if name in fields)
    missing = tuple(name for name in task.required_outputs if name not in fields)
    return HandoffGate(not missing, task.required_outputs, present, missing)


def merge_output(
    task: Task,
    harness_fields: Mapping[str, Any],
    handoff: Artifact,
) -> dict[str, Any]:
    """docs/03 의 TaskOutput. 병합과 기록은 하네스가 한다.

    출처를 구분해 표기한다. `harness` 는 하네스가 계산한 사실이고, `agent` 는 검증을
    통과했을 뿐 신뢰되지는 않는 보고다 (docs/07).
    """
    fields = handoff.data or {}
    declared = (*task.required_outputs, *task.optional_outputs)
    return {
        "harness": dict(harness_fields),
        "agent": {name: fields[name] for name in declared if name in fields},
    }


def _preserve(kind: str, dest_dir: Path | str, raw: str, error: str) -> Artifact:
    return Artifact(kind, None, _write(dest_dir, f"{kind}.invalid.json", raw), error)


def _write(dest_dir: Path | str, filename: str, text: str) -> Path:
    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path
