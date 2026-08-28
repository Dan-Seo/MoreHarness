"""`schemas/` 의 jsonschema 정의를 읽어 검증기를 만든다.

docs/02 의 「배포」에 따라 `resources/schemas/` 는 패키지 안에 있다. 이 모듈은 그 위치를
아는 유일한 곳이며, 그것 말고는 아무 판단도 하지 않는다. 검증 실패를 무엇으로
분류할지는 각 호출자가 자기 문서의 계약에 따라 정한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "resources" / "schemas"


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    """`name` 은 `claim` · `handoff` · `task` · `spec` · `event` 중 하나다."""
    path = SCHEMA_DIR / f"{name}.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def first_error(name: str, data: object) -> str | None:
    """검증 실패 사유 한 줄. 통과하면 `None`."""
    errors = sorted(validator(name).iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    error = errors[0]
    where = "/".join(str(part) for part in error.absolute_path)
    return f"{where}: {error.message}" if where else error.message
