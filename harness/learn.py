"""지식 카드의 제안·승격·폐기와 `uses` 집계. docs/08 이 canonical 이다.

**자동 승격은 없다.** 서로 다른 run 에서 반복 관측되는 것은 `candidate` 자격을 만들 뿐이고,
상태를 바꾸는 것은 사람의 명령(`promote`/`retire`)뿐이다.

이 모듈은 옵션이다 (docs/02). 없어도 커널은 동작하며, 카드가 없으면 컨텍스트에 L7 이
비는 것으로 끝난다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from harness.config import HARNESS_DIR, Config
from harness.errors import HarnessError
from harness.events import EventType
from harness.store import Journal

KNOWLEDGE_DIR = "knowledge"
RUNS_DIR = "runs"
NEEDS_CLAIM = "[NEEDS CLAIM]"

# 카드의 필드 순서. docs/08 의 예시 그대로 쓴다.
FIELD_ORDER = ("id", "kind", "rule", "scope", "claim", "evidence", "status", "uses")


@dataclass(frozen=True)
class LearnReport:
    proposed: tuple[str, ...]  # 새로 만든 카드
    updated: tuple[str, ...]  # evidence 가 늘어난 카드
    uses: Mapping[str, int]  # 카드별 갱신된 uses
    retire_candidates: tuple[str, ...]  # 오래 쓰이지 않은 promoted 카드


@dataclass
class _Card:
    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.data.get("id") or self.path.stem)


def learn(repo: Path | str, config: Config) -> LearnReport:
    """완료된 run 들을 읽어 후보를 제안하고, `uses` 를 갱신하고, 폐기 대상을 보고한다."""
    repo = Path(repo)
    runs = _runs(repo)
    cards = _cards(repo)

    proposed, updated = _propose(repo, cards, _observations(runs), config.knowledge.candidate_after)
    uses = _uses(runs, cards)
    retire_candidates = _unused(runs, cards, uses, config.knowledge.retire_after_unused_runs)

    for card in cards:
        _write(card)
    return LearnReport(proposed, updated, uses, retire_candidates)


def promote(repo: Path | str, card_id: str) -> Path:
    return _set_status(repo, card_id, "promoted")


def retire(repo: Path | str, card_id: str) -> Path:
    return _set_status(repo, card_id, "retired")


# ------------------------------------------------------------------ 후보 제안


def _propose(
    repo: Path, cards: list[_Card], observations: Mapping[str, list[dict]], threshold: int
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    by_rule = {str(card.data.get("rule")): card for card in cards if card.data.get("rule")}
    proposed: list[str] = []
    updated: list[str] = []

    for rule, seen in sorted(observations.items()):
        if len(seen) < threshold:
            continue
        card = by_rule.get(rule)
        if card is None:
            cards.append(_new_card(repo, cards, rule, seen))
            proposed.append(cards[-1].id)
            continue
        # 사람이 폐기한 카드를 하네스가 되살리지 않는다 (docs/08).
        if card.data.get("status") == "retired":
            continue
        if _add_evidence(card, seen):
            updated.append(card.id)

    return tuple(proposed), tuple(updated)


def _new_card(repo: Path, cards: Sequence[_Card], rule: str, seen: Sequence[dict]) -> _Card:
    card_id = _next_id(cards)
    directory = repo / HARNESS_DIR / KNOWLEDGE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return _Card(
        directory / f"{card_id}.yaml",
        {
            "id": card_id,
            "kind": "pitfall",  # 반복되는 지적은 패턴이 아니라 함정이다 (docs/08)
            "rule": rule,
            "scope": _scope([entry["file"] for entry in seen]),
            # learn 은 관측을 모을 뿐 문장을 지어내지 않는다.
            "claim": f"{NEEDS_CLAIM} {rule} 이(가) 서로 다른 run 에서 반복 지적되었다",
            "evidence": [{"run": entry["run"], "task": entry["task"]} for entry in seen],
            "status": "candidate",
            "uses": 0,
        },
    )


def _add_evidence(card: _Card, seen: Sequence[dict]) -> bool:
    evidence = list(card.data.get("evidence") or [])
    known = {(entry.get("run"), entry.get("task")) for entry in evidence}
    added = False
    for entry in seen:
        key = (entry["run"], entry["task"])
        if key in known:
            continue
        evidence.append({"run": entry["run"], "task": entry["task"]})
        known.add(key)
        added = True
    if added:
        card.data["evidence"] = evidence
    return added


def _observations(runs: Sequence[Path]) -> dict[str, list[dict]]:
    """rule 별 관측. **같은 run 안의 반복은 한 번으로 센다** (docs/08)."""
    per_rule: dict[str, list[dict]] = {}
    for run_dir in runs:
        journal = run_dir / "journal.jsonl"
        if not journal.is_file():
            continue
        seen: set[str] = set()
        for event in Journal(journal, run_dir.name).read():
            if event.type is not EventType.REVIEW_FINDING:
                continue
            rule = str(event.payload.get("rule"))
            if rule in seen:
                continue
            seen.add(rule)
            per_rule.setdefault(rule, []).append(
                {
                    "run": run_dir.name,
                    "task": event.task_id,
                    "file": str(event.payload.get("file") or ""),
                }
            )
    return per_rule


def _scope(files: Sequence[str]) -> str:
    """지적된 파일들의 공통 디렉토리. 공통이 없으면 저장소 전체다."""
    common: list[str] | None = None
    for file in files:
        parts = list(PurePosixPath(file.replace("\\", "/")).parent.parts)
        if common is None:
            common = parts
            continue
        keep = []
        for mine, theirs in zip(common, parts):
            if mine != theirs:
                break
            keep.append(mine)
        common = keep
    return f"{'/'.join(common)}/**" if common else "**"


# ------------------------------------------------------------------ uses 와 폐기


def _uses(runs: Sequence[Path], cards: Sequence[_Card]) -> dict[str, int]:
    """`context.manifest.json` 의 L7 기록에서 센다. 실행 경로에 카운터를 심지 않는다 (docs/11)."""
    counts = {card.id: 0 for card in cards}
    for run_dir in runs:
        for card_id in _included(run_dir):
            if card_id in counts:
                counts[card_id] += 1
    for card in cards:
        card.data["uses"] = counts[card.id]
    return counts


def _included(run_dir: Path) -> list[str]:
    """그 run 의 프롬프트에 실제로 들어간 카드 id. 예산으로 탈락한 것은 사용이 아니다."""
    included = []
    for manifest in sorted(run_dir.glob("tasks/*/context.manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for section in data.get("sections") or []:
            if section.get("layer") != "L7":
                continue
            if str(section.get("reason") or "").startswith("dropped"):
                continue
            included.append(str(section.get("source")))
    return included


def _unused(
    runs: Sequence[Path], cards: Sequence[_Card], uses: Mapping[str, int], threshold: int
) -> tuple[str, ...]:
    recent = runs[-threshold:] if threshold else []
    if len(recent) < threshold or not recent:
        return ()
    used = {card_id for run_dir in recent for card_id in _included(run_dir)}
    return tuple(
        card.id
        for card in cards
        if card.data.get("status") == "promoted" and card.id not in used
    )


# ------------------------------------------------------------------ 파일


def _runs(repo: Path) -> list[Path]:
    directory = repo / HARNESS_DIR / RUNS_DIR
    if not directory.is_dir():
        return []
    return sorted(run for run in directory.iterdir() if run.is_dir())


def _cards(repo: Path) -> list[_Card]:
    directory = repo / HARNESS_DIR / KNOWLEDGE_DIR
    if not directory.is_dir():
        return []
    cards = []
    for path in sorted(directory.glob("K-*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cards.append(_Card(path, data))
    return cards


def _next_id(cards: Sequence[_Card]) -> str:
    numbers = []
    for card in cards:
        try:
            numbers.append(int(card.id.split("-")[-1]))
        except ValueError:
            continue
    return f"K-{max(numbers, default=0) + 1:03d}"


def _write(card: _Card) -> None:
    ordered = {key: card.data[key] for key in FIELD_ORDER if key in card.data}
    ordered.update({k: v for k, v in card.data.items() if k not in ordered})
    card.path.write_text(
        yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _set_status(repo: Path | str, card_id: str, status: str) -> Path:
    path = Path(repo) / HARNESS_DIR / KNOWLEDGE_DIR / f"{card_id}.yaml"
    if not path.is_file():
        raise HarnessError(f"{card_id} 카드가 없다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HarnessError(f"{path} 를 읽을 수 없다")
    data["status"] = status
    card = _Card(path, data)
    _write(card)
    return path
