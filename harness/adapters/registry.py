"""이름 → 어댑터 해석.

registry 는 config 를 읽지 않는다. 어댑터 이름·타입·옵션을 받아 인스턴스를 만들 뿐이다.
docs/02 의 의존 방향 때문이다 — `adapters/*` 는 상위 계층을 import 하지 않는다.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from harness.adapters.base import AgentAdapter
from harness.adapters.claude_cli import build_claude_cli
from harness.adapters.codex_cli import build_codex_cli
from harness.adapters.generic_cli import build_generic_cli
from harness.adapters.mock import build_mock
from harness.errors import AdapterNotFoundError

AdapterFactory = Callable[[str, Mapping[str, Any]], AgentAdapter]

_FACTORIES: dict[str, AdapterFactory] = {
    "mock": build_mock,
    "generic_cli": build_generic_cli,
    "claude_cli": build_claude_cli,
    "codex_cli": build_codex_cli,
}


def register(type_name: str, factory: AdapterFactory) -> None:
    _FACTORIES[type_name] = factory


def known_types() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def build(name: str, type_name: str, options: Mapping[str, Any]) -> AgentAdapter:
    factory = _FACTORIES.get(type_name)
    if factory is None:
        raise AdapterNotFoundError(
            f"알 수 없는 어댑터 타입 {type_name!r}. 등록된 타입: {', '.join(known_types())}"
        )
    return factory(name, options)
