"""docs/05 glob 방언의 경로 매칭. 아무것도 import 하지 않는다 (docs/02 의 최하위 계층).

gitignore 계열이며 저장소 루트 기준이다. `verify`(경로 스코프)와 `risk`(path_floor)가
같은 방언을 쓰므로 구현이 여기 하나만 있다.
"""

from __future__ import annotations

import re
from functools import lru_cache


def matches(path: str, pattern: str) -> bool:
    return _compiled(pattern).match(normalize(path)) is not None


def normalize(path: str) -> str:
    """저장소 루트 기준 POSIX 경로. 심볼릭 링크는 따라가지 않는다 (docs/05)."""
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    """glob 을 정규식으로 옮긴다. 방언 표는 docs/05 에 있다."""
    text = normalize(pattern)
    out, index, size = ["^"], 0, len(text)
    while index < size:
        if text.startswith("**/", index):
            out.append("(?:.*/)?")  # 0개 이상의 세그먼트
            index += 3
        elif text.startswith("/**", index) and index + 3 == size:
            out.append("(?:/.*)?")  # 앞 경로 자신과 그 아래 전부
            index += 3
        elif text.startswith("**", index):
            out.append(".*")
            index += 2
        elif text[index] == "*":
            out.append("[^/]*")
            index += 1
        elif text[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(text[index]))
            index += 1
    out.append("$")
    return re.compile("".join(out))
