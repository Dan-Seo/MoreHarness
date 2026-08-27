"""예외 타입.

이 모듈은 아무것도 import 하지 않는다. `models` 와 같은 최하위 계층이다.
분류는 docs/10 의 실패 분류표를 따른다. verdict 로의 매핑은 이 모듈이 하지 않는다.
"""


class HarnessError(Exception):
    """하네스가 올리는 모든 예외의 뿌리."""


class ConfigError(HarnessError):
    """`.harness/config.yaml` 이 없거나 계약을 만족하지 않는다."""


class JournalCorruptionError(HarnessError):
    """journal 의 마지막 줄이 아닌 곳이 손상됐다. doctor 가 보고한다."""


class EventPayloadError(HarnessError):
    """이벤트 페이로드가 docs/03 의 이벤트 목록을 만족하지 않는다."""


class NotARepositoryError(HarnessError):
    """git 저장소가 아니다."""


class AdapterNotFoundError(HarnessError):
    """등록되지 않은 어댑터 타입이다."""


class TaskDefinitionError(HarnessError):
    """task 계약이 잘못됐다. docs/10 의 "task definition" 분류이며 재시도가 무의미하다."""
