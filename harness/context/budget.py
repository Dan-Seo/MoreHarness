"""토큰 예산 — 추정, 고정 계층 확보, 낮은 우선순위부터 탈락. docs/07 이 canonical 이다."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from harness.config import ContextConfig

# docs/07 — L0~L4 와 L8 은 고정 확보한다. L5~L7 이 남은 예산을 채운다.
FIXED_LAYERS = ("L0", "L1", "L2", "L3", "L4", "L8")
OPTIONAL_ORDER = ("L5", "L6", "L7")

# slice_max_tokens 가 적용되는 슬라이스 계층. L0·L1·L4·L8 은 자르지도 탈락시키지도 않는다.
CAPPED_LAYERS = ("L2", "L3", "L5", "L6", "L7")

TRUSTED = "trusted"
FACT = "trusted (fact)"
UNTRUSTED = "untrusted"


def estimate_tokens(text: str) -> int:
    """docs/07 — 문자 수를 4로 나눠 올림한 값. 정확도보다 재현성이다."""
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class Section:
    """조립 후보 하나. 슬라이스거나 계층 전체거나, 어느 쪽이든 통째로 들어가거나 빠진다."""

    layer: str
    source: str
    trust: str  # trusted | trusted (fact) | untrusted
    text: str
    reason: str  # 왜 포함 후보가 되었는가

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class FitResult:
    kept: tuple[Section, ...]
    entries: tuple[dict, ...]  # manifest 의 sections — 탈락한 것도 사유와 함께 남는다
    total_tokens: int
    warnings: tuple[str, ...]


def fit(sections: Sequence[Section], config: ContextConfig) -> FitResult:
    """docs/07 의 예산 알고리즘.

    계층 내부를 임의로 자르지 않는다 — 슬라이스는 통째로 들어가거나 통째로 빠진다.
    """
    available = config.budget_tokens - config.reserve_for_output
    dropped: dict[int, str] = {}

    # 1) 단일 슬라이스 상한 — 자르지 않고 통째로 탈락시킨다
    for index, section in enumerate(sections):
        if section.layer in CAPPED_LAYERS and section.tokens > config.slice_max_tokens:
            dropped[index] = "dropped: slice_max_tokens"

    # 2) 고정 계층 확보
    fixed_cost = sum(
        section.tokens
        for index, section in enumerate(sections)
        if index not in dropped and section.layer in FIXED_LAYERS
    )
    warnings: list[str] = []
    if fixed_cost > available:
        # docs/07 — constitution 이 너무 크다는 신호다. 자르지 않고 경고한다.
        warnings.append(f"고정 계층 {fixed_cost} 토큰이 가용 예산 {available} 을 넘는다")
    remaining = available - fixed_cost

    # 3) 옵션 계층 전체가 남은 예산에 맞을 때까지 **낮은 우선순위부터** 통째로 탈락
    groups = {
        layer: [
            (index, section)
            for index, section in enumerate(sections)
            if index not in dropped and section.layer == layer
        ]
        for layer in OPTIONAL_ORDER
    }
    included = [layer for layer in OPTIONAL_ORDER if groups[layer]]

    def optional_cost() -> int:
        return sum(section.tokens for layer in included for _, section in groups[layer])

    while included and optional_cost() > remaining:
        lowest = included.pop()  # OPTIONAL_ORDER 의 뒤쪽이 낮은 우선순위다
        for index, _ in groups[lowest]:
            dropped[index] = "dropped: budget"

    kept = tuple(s for i, s in enumerate(sections) if i not in dropped)
    entries = tuple(
        {
            "layer": section.layer,
            "source": section.source,
            "trust": section.trust,
            "tokens": 0 if index in dropped else section.tokens,
            "reason": dropped.get(index, section.reason),
        }
        for index, section in enumerate(sections)
    )
    return FitResult(kept, entries, sum(s.tokens for s in kept), tuple(warnings))
