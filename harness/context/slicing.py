"""앵커·심볼·R-### 단위 절취. **중간에서 자르지 않는다.** docs/07 이 canonical 이다."""

from __future__ import annotations

import re
from typing import Any, Collection, Mapping

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def slug(heading: str) -> str:
    """GitHub 계열 앵커 — 소문자, 공백은 하이픈, 영숫자·하이픈·언더스코어만 남긴다."""
    cleaned = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", cleaned)


def doc_slice(text: str, anchor: str) -> str | None:
    """헤딩부터 다음 동급(이상) 헤딩 전까지. 앵커가 없으면 `None` — 추측하지 않는다."""
    lines = text.splitlines()
    start = level = None
    for number, line in enumerate(lines):
        match = _HEADING.match(line)
        if match is None:
            continue
        if start is None:
            if slug(match.group(2)) == anchor:
                start, level = number, len(match.group(1))
        elif len(match.group(1)) <= level:
            return "\n".join(lines[start:number]).rstrip() + "\n"
    if start is None:
        return None
    return "\n".join(lines[start:]).rstrip() + "\n"


def spec_slice(spec: Mapping[str, Any], wanted: Collection[str]) -> str | None:
    """R-### 단위. task 의 `satisfies` 에 해당하는 요구사항만 절취한다."""
    entries = [
        requirement
        for requirement in spec.get("requirements") or ()
        if isinstance(requirement, Mapping) and requirement.get("id") in set(wanted)
    ]
    if not entries:
        return None

    lines = []
    for requirement in entries:
        lines.append(f"- {requirement['id']}: {requirement.get('statement', '')}")
        for key in ("rationale", "acceptance_hint"):
            if requirement.get(key):
                lines.append(f"  - {key}: {requirement[key]}")
    return "\n".join(lines) + "\n"


def python_block(text: str, symbol: str) -> str | None:
    """`def`/`class` 경계로 절취한다. 데코레이터를 포함하고, 경계를 못 찾으면 `None`."""
    lines = text.splitlines()
    opening = re.compile(rf"^(\s*)(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b")

    for number, line in enumerate(lines):
        match = opening.match(line)
        if match is None:
            continue
        indent = len(match.group(1))

        start = number
        while start > 0 and lines[start - 1].strip().startswith("@"):
            start -= 1

        end = len(lines)
        for later in range(number + 1, len(lines)):
            candidate = lines[later]
            if candidate.strip() and (len(candidate) - len(candidate.lstrip())) <= indent:
                end = later
                break
        return "\n".join(lines[start:end]).rstrip() + "\n"
    return None
