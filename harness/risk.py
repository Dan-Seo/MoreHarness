"""effective_risk = max(declared, path_floor, diff_floor). docs/06 이 canonical 이다.

옵션 모듈이다 (docs/02) — 없으면 declared_risk 를 그대로 쓴다. 상향만 가능하고,
하향은 기록되는 사람 waiver 뿐이므로 이 모듈에 하향 경로가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness.errors import ConfigError
from harness.models import RiskLevel
from harness.paths import matches

ORDER = (RiskLevel.TRIVIAL, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

# docs/06 — diff_floor 의 임계
DIFF_FLOOR_LINES = 400
DIFF_FLOOR_FILES = 20

# docs/06 의 내장 기본 목록. 선언된 risk_rules 는 여기에 **추가**된다 — 끌 수 없다.
DEFAULT_RULES: tuple[tuple[str, RiskLevel], ...] = tuple(
    (pattern, RiskLevel.HIGH)
    for pattern in (
        "**/auth/**",
        "**/*secret*",
        "**/*password*",
        "**/*credential*",
        "**/migrations/**",
        ".github/workflows/**",
        "**/Dockerfile*",
        "**/docker-compose*",
        "**/requirements*.txt",
        "**/pyproject.toml",
        "**/package.json",
        "**/go.mod",
        "**/Cargo.toml",
    )
)


@dataclass(frozen=True)
class Floors:
    declared: RiskLevel
    path_floor: RiskLevel
    diff_floor: RiskLevel

    @property
    def effective(self) -> RiskLevel:
        return highest((self.declared, self.path_floor, self.diff_floor))

    @property
    def escalated(self) -> bool:
        return ORDER.index(self.effective) > ORDER.index(self.declared)

    def to_payload(self) -> dict[str, str]:
        """docs/03 — risk_escalated."""
        return {
            "declared": str(self.declared),
            "path_floor": str(self.path_floor),
            "diff_floor": str(self.diff_floor),
            "effective": str(self.effective),
        }


def highest(levels: Sequence[RiskLevel]) -> RiskLevel:
    return max(levels, key=ORDER.index)


def load_rules(declared: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, RiskLevel], ...]:
    """내장 기본 + config 의 `risk_rules`. 형식 오류는 로드 시점에 거른다."""
    rules = list(DEFAULT_RULES)
    for entry in declared:
        if not isinstance(entry, Mapping) or "match" not in entry or "floor" not in entry:
            raise ConfigError(f"risk_rules 항목은 {{match, floor}} 여야 한다: {entry!r}")
        try:
            floor = RiskLevel(entry["floor"])
        except ValueError as exc:
            raise ConfigError(f"알 수 없는 floor: {entry['floor']!r}") from exc
        rules.append((str(entry["match"]), floor))
    return tuple(rules)


def path_floor(
    paths: Sequence[str], rules: Sequence[tuple[str, RiskLevel]]
) -> RiskLevel:
    """경로 패턴 floor 의 최대값. 사전에는 allowed_paths 에, 사후에는 diff 에 적용한다."""
    floor = RiskLevel.TRIVIAL
    for path in paths:
        for pattern, level in rules:
            if matches(path, pattern):
                floor = highest((floor, level))
    return floor


def diff_floor(total_lines: int, file_count: int) -> RiskLevel:
    """docs/06 — 변경 라인 합 400 이상 또는 파일 20개 이상이면 medium."""
    if total_lines >= DIFF_FLOOR_LINES or file_count >= DIFF_FLOOR_FILES:
        return RiskLevel.MEDIUM
    return RiskLevel.TRIVIAL


def assess(
    declared: RiskLevel | None,
    diff_paths: Sequence[str],
    total_lines: int,
    file_count: int,
    rules: Sequence[tuple[str, RiskLevel]],
) -> Floors:
    """사후 단계 — 실제 diff 를 본 뒤 리뷰 티어를 확정한다 (docs/06).

    risk 를 선언하지 않은 task 의 declared 는 trivial 로 본다. floor 가 안전망이다.
    """
    return Floors(
        declared=declared or RiskLevel.TRIVIAL,
        path_floor=path_floor(diff_paths, rules),
        diff_floor=diff_floor(total_lines, file_count),
    )
