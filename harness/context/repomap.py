"""저장소 구조 요약 — stdlib 만 쓴다. docs/07 이 canonical 이다.

`.gitignore` 존중은 직접 구현하지 않는다. 워크스페이스는 git 트리이므로 git 이 이미
알고 있고, `ls-files` 가 추적 파일과 무시되지 않은 미추적 파일을 돌려준다.
tree-sitter 계열 정밀 심볼 그래프는 옵션이며 커널에도 여기에도 넣지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.context.slicing import python_block
from harness.git import git

MAX_DEPTH = 4

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}

# 정규식 기반 최상위 심볼. 정밀하지 않아도 된다 — 지도의 목적은 위치 감각이다.
_SYMBOLS = {
    "python": re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE),
    "javascript": re.compile(r"^(?:export\s+)?(?:function|class|const)\s+(\w+)", re.MULTILINE),
    "typescript": re.compile(
        r"^(?:export\s+)?(?:function|class|interface|type|const)\s+(\w+)", re.MULTILINE
    ),
    "go": re.compile(r"^(?:func|type)\s+(\w+)", re.MULTILINE),
    "rust": re.compile(r"^(?:pub\s+)?(?:fn|struct|enum|trait)\s+(\w+)", re.MULTILINE),
}


def tracked_files(root: Path) -> list[str]:
    result = git(["ls-files", "--cached", "--others", "--exclude-standard"], cwd=root)
    if result.exit_code != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def render_map(root: Path, max_depth: int = MAX_DEPTH) -> str:
    """파일별 크기·언어·최상위 심볼. 깊은 경로는 세지 않고 생략을 표시한다."""
    lines: list[str] = []
    skipped = 0
    for relative in sorted(tracked_files(root)):
        if relative.count("/") >= max_depth:
            skipped += 1
            continue
        path = root / relative
        if not path.is_file():
            continue
        language = LANGUAGES.get(path.suffix, "")
        note = f"{path.stat().st_size}B" + (f", {language}" if language else "")
        symbols = top_symbols(path)
        tail = f" — {', '.join(symbols[:8])}" if symbols else ""
        lines.append(f"{relative}  ({note}){tail}")
    if skipped:
        lines.append(f"... 깊이 {max_depth} 초과 {skipped}개 생략")
    return "\n".join(lines) + ("\n" if lines else "")


def top_symbols(path: Path) -> list[str]:
    pattern = _SYMBOLS.get(LANGUAGES.get(path.suffix, ""))
    if pattern is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return list(dict.fromkeys(pattern.findall(text)))


def symbol_block(root: Path, symbol: str) -> tuple[str, str] | None:
    """심볼을 가진 파일을 찾아 함수·클래스 경계로 절취한다. 못 찾으면 `None`."""
    for relative in sorted(tracked_files(root)):
        path = root / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        if symbol not in top_symbols(path):
            continue
        try:
            block = python_block(path.read_text(encoding="utf-8"), symbol)
        except (OSError, UnicodeDecodeError):
            continue
        if block:
            return relative, block
    return None
