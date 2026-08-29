"""`.harness/config.yaml` 로드·검증.

docs/03, docs/05 — 하네스는 config 를 항상 메인 저장소에서 읽는다. 워크트리 안의
사본은 agent 가 고칠 수 있는 저장소 콘텐츠이므로 control-plane 입력이 아니다.

뒤 마일스톤이 자기 키(`command_policy`, `risk_rules`, `ac_timeout_s` 등)를 이 파일에
더한다. 모르는 최상위 키를 거부하지 않는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.errors import ConfigError
from harness.models import ContainerSpec, ExecutionProfile

CONFIG_VERSION = 1
HARNESS_DIR = ".harness"

# docs/06 이 소유하는 키의 기본값. 그 문서가 canonical 이다.
DEFAULT_AC_TIMEOUT_S = 300
DEFAULT_AGENT_TIMEOUT_S = 1800
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_HANDOFF_REPAIRS = 1

# docs/05 — control-plane 은 이 목록에서 뺄 수 없다. 설정에서 지워도 하네스가 다시 넣는다.
ALWAYS_FORBIDDEN = (".harness/**",)


@dataclass(frozen=True)
class BudgetConfig:
    """docs/06 이 소유하는 `budget` 키. null 은 무제한이다."""

    max_wall_time_s: int | None = None
    max_agent_calls: int | None = None
    max_cost_usd: float | None = None

    @property
    def enabled(self) -> bool:
        return any(
            limit is not None
            for limit in (self.max_wall_time_s, self.max_agent_calls, self.max_cost_usd)
        )


@dataclass(frozen=True)
class ContextConfig:
    """docs/07 이 소유하는 `context` 키. 기본값도 07 의 예산 표가 canonical 이다."""

    budget_tokens: int = 60000
    reserve_for_output: int = 8000
    slice_max_tokens: int = 4000


@dataclass(frozen=True)
class KnowledgeConfig:
    """docs/08 이 소유하는 `knowledge` 키. 승격·폐기는 사람이 하고, 이 값들은 제안 기준이다."""

    candidate_after: int = 2
    retire_after_unused_runs: int = 10


@dataclass(frozen=True)
class AdapterConfig:
    name: str
    type: str
    options: Mapping[str, Any]


@dataclass(frozen=True)
class Config:
    version: int
    default_adapter: str
    default_profile: ExecutionProfile
    max_parallel: int
    allow_unsafe: bool
    adapters: Mapping[str, AdapterConfig]
    ac_timeout_s: int
    agent_timeout_s: int
    max_attempts: int
    max_handoff_repairs: int
    blocked_signals: tuple[str, ...]
    max_review_waves: int  # docs/06 — bounded review wave 의 한도
    adversarial_adapter: str | None  # docs/06 — adversarial 리뷰어만 쓰는 어댑터
    risk_rules: tuple[Mapping[str, Any], ...]  # docs/06 — 항목 검증은 risk 가 한다
    budget: BudgetConfig  # docs/06 — 예산 상한
    health_commands: tuple[tuple[str, ...], ...]  # docs/08 — converge 의 health 커맨드
    forbidden_paths: tuple[str, ...]  # docs/05 — 전역 금지 목록
    command_policy: Mapping[str, Any]  # 형태 검증은 policy 가 한다
    container: ContainerSpec | None  # docs/05 — container 프로파일의 실행 계약
    context: ContextConfig  # docs/07 — 계층형 컨텍스트 예산
    knowledge: KnowledgeConfig  # docs/08 — 지식 카드 제안 기준
    path: Path


def config_path(repo_root: Path | str) -> Path:
    return Path(repo_root) / HARNESS_DIR / "config.yaml"


def load(repo_root: Path | str) -> Config:
    path = config_path(repo_root)
    if not path.is_file():
        raise ConfigError(f"{path} 이(가) 없다")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} 을(를) 읽을 수 없다: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 의 최상위는 매핑이어야 한다")

    version = data.get("version")
    if version != CONFIG_VERSION:
        raise ConfigError(f"지원하지 않는 config version: {version!r}")

    adapters = _load_adapters(data.get("adapters"))
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("defaults 는 매핑이어야 한다")

    default_adapter = defaults.get("adapter")
    if default_adapter not in adapters:
        raise ConfigError(f"defaults.adapter {default_adapter!r} 가 adapters 에 없다")

    try:
        profile = ExecutionProfile(defaults.get("profile", ExecutionProfile.WORKTREE))
    except ValueError as exc:
        raise ConfigError(f"알 수 없는 프로파일: {defaults.get('profile')!r}") from exc

    adversarial = data.get("adversarial_adapter")
    if adversarial is not None and adversarial not in adapters:
        raise ConfigError(f"adversarial_adapter {adversarial!r} 가 adapters 에 없다")

    max_parallel = defaults.get("max_parallel", 1)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        raise ConfigError(f"defaults.max_parallel 은 1 이상의 정수여야 한다: {max_parallel!r}")

    allow_unsafe = bool(data.get("allow_unsafe", False))
    if profile is ExecutionProfile.UNSAFE and not allow_unsafe:
        # docs/05, docs/09 — unsafe 는 절대 기본값이 아니다. config 의 명시적 허용과
        # CLI 의 명시적 플래그가 둘 다 있어야 한다. 여기서는 앞의 절반을 강제한다.
        raise ConfigError("unsafe 프로파일은 allow_unsafe: true 없이 쓸 수 없다")

    return Config(
        version=version,
        default_adapter=default_adapter,
        default_profile=profile,
        max_parallel=max_parallel,
        allow_unsafe=allow_unsafe,
        adapters=adapters,
        ac_timeout_s=_bounded_int(data, "ac_timeout_s", DEFAULT_AC_TIMEOUT_S, minimum=1),
        agent_timeout_s=_bounded_int(
            data, "agent_timeout_s", DEFAULT_AGENT_TIMEOUT_S, minimum=1
        ),
        max_attempts=_bounded_int(data, "max_attempts", DEFAULT_MAX_ATTEMPTS, minimum=1),
        max_handoff_repairs=_bounded_int(
            data, "max_handoff_repairs", DEFAULT_MAX_HANDOFF_REPAIRS, minimum=0
        ),
        blocked_signals=_string_list(data, "blocked_signals"),
        max_review_waves=_bounded_int(data, "max_review_waves", 2, minimum=1),
        adversarial_adapter=adversarial,
        risk_rules=tuple(data.get("risk_rules") or ()),
        budget=_load_budget(data.get("budget")),
        health_commands=_argv_list(data, "health_commands"),
        forbidden_paths=tuple(
            dict.fromkeys((*ALWAYS_FORBIDDEN, *_string_list(data, "forbidden_paths")))
        ),
        command_policy=data.get("command_policy") or {},
        container=_load_container(data.get("container")),
        context=_load_context(data.get("context")),
        knowledge=_load_knowledge(data.get("knowledge")),
        path=path,
    )


def _argv_list(data: Mapping[str, Any], key: str) -> tuple[tuple[str, ...], ...]:
    raw = data.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{key} 는 목록이어야 한다")
    out = []
    for entry in raw:
        if (
            not isinstance(entry, list)
            or not entry
            or not all(isinstance(part, str) for part in entry)
        ):
            raise ConfigError(f"{key} 항목은 argv 문자열 리스트여야 한다: {entry!r}")
        out.append(tuple(entry))
    return tuple(out)


def _load_budget(raw: Any) -> BudgetConfig:
    if raw is None:
        return BudgetConfig()
    if not isinstance(raw, dict):
        raise ConfigError("budget 은 매핑이어야 한다")
    fields: dict[str, Any] = {}
    for key, kinds in (
        ("max_wall_time_s", (int,)),
        ("max_agent_calls", (int,)),
        ("max_cost_usd", (int, float)),
    ):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, kinds) or value < 0:
            raise ConfigError(f"budget.{key} 는 0 이상의 수여야 한다: {value!r}")
        fields[key] = value
    return BudgetConfig(**fields)


def _load_knowledge(raw: Any) -> KnowledgeConfig:
    """docs/08 — 두 값 모두 제안 기준이다. 자동 승격을 만드는 키는 없다."""
    if raw is None:
        return KnowledgeConfig()
    if not isinstance(raw, dict):
        raise ConfigError("knowledge 는 매핑이어야 한다")
    fields = {}
    for key in ("candidate_after", "retire_after_unused_runs"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"knowledge.{key} 는 1 이상의 정수여야 한다: {value!r}")
        fields[key] = value
    return KnowledgeConfig(**fields)


def _load_container(raw: Any) -> ContainerSpec | None:
    """docs/05 가 소유하는 `container` 키. `image` 없는 선언은 설정 결함이다."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("container 는 매핑이어야 한다")

    image = raw.get("image")
    if not isinstance(image, str) or not image:
        raise ConfigError("container.image 는 필수다")

    runtime = raw.get("runtime", "docker")
    if not isinstance(runtime, str) or not runtime:
        raise ConfigError(f"container.runtime 은 실행 파일 이름이어야 한다: {runtime!r}")

    network = raw.get("network")
    if network is not None and not isinstance(network, str):
        raise ConfigError(f"container.network 는 문자열이거나 null 이다: {network!r}")

    mounts = raw.get("mounts") or []
    if not isinstance(mounts, list) or not all(isinstance(m, str) for m in mounts):
        raise ConfigError("container.mounts 는 문자열 목록이어야 한다")

    return ContainerSpec(image=image, runtime=runtime, network=network, mounts=tuple(mounts))


def _load_context(raw: Any) -> ContextConfig:
    if raw is None:
        return ContextConfig()
    if not isinstance(raw, dict):
        raise ConfigError("context 는 매핑이어야 한다")
    fields = {}
    for key in ("budget_tokens", "reserve_for_output", "slice_max_tokens"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"context.{key} 는 0 이상의 정수여야 한다: {value!r}")
        fields[key] = value
    return ContextConfig(**fields)


def _load_adapters(raw: Any) -> dict[str, AdapterConfig]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("adapters 에 최소 하나의 어댑터가 있어야 한다")

    adapters = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"adapters.{name} 은(는) 매핑이어야 한다")
        type_name = entry.get("type")
        if not isinstance(type_name, str) or not type_name:
            raise ConfigError(f"adapters.{name} 에 type 이 없다")
        options = {k: v for k, v in entry.items() if k != "type"}
        adapters[name] = AdapterConfig(name=name, type=type_name, options=options)
    return adapters


def _bounded_int(data: Mapping[str, Any], key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{key} 은(는) {minimum} 이상의 정수여야 한다: {value!r}")
    return value


def _string_list(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} 은(는) 문자열 목록이어야 한다: {value!r}")
    return tuple(value)
