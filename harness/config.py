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
from harness.models import ExecutionProfile

CONFIG_VERSION = 1
HARNESS_DIR = ".harness"


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
        path=path,
    )


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
