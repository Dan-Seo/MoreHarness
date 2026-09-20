"""Small, conservative redaction boundary for persisted harness artifacts."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_ENV_NAME = re.compile(
    r"^(?:[A-Z0-9]+_)*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|ACCESS_TOKEN|AUTH_TOKEN|AUTHORIZATION|CREDENTIAL|PRIVATE_KEY|CLIENT_SECRET|REFRESH_TOKEN)$",
    re.IGNORECASE,
)
_CREDENTIAL_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|auth(?:orization)?|client[_-]?secret|credential|password|passwd|private[_-]?key|refresh[_-]?token|secret|token)$",
    re.IGNORECASE,
)
_BUILTIN_PATTERNS = (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",
    r"\b(?:sk|rk)-[A-Za-z0-9_-]{8,}\b",
    r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token|authorization|credential)\b\s*[:=]\s*[\"']?[^\s,;\"']+",
    r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@",
)


class Redactor:
    """Mask configured, recognizable, and secret-valued text at persistence boundaries."""

    def __init__(self, patterns=()) -> None:
        self._patterns = tuple(
            re.compile(pattern) for pattern in (*_BUILTIN_PATTERNS, *patterns)
        )
        self._environment_values = tuple(
            sorted(
                {
                    value
                    for name, value in os.environ.items()
                    if _ENV_NAME.search(name) and value
                },
                key=len,
                reverse=True,
            )
        )

    def text(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("Redactor.text expects a string")
        for secret in self._environment_values:
            value = value.replace(secret, REDACTED)
        for pattern in self._patterns:
            value = pattern.sub(REDACTED, value)
        return value

    def data(self, value: Any) -> Any:
        return self._data(value)

    def _data(self, value: Any, credential: bool = False) -> Any:
        if isinstance(value, str):
            return REDACTED if credential else self.text(value)
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                safe_key = self.text(key) if isinstance(key, str) else key
                is_credential = isinstance(key, str) and _CREDENTIAL_KEY.fullmatch(key)
                result[safe_key] = self._data(item, credential=bool(is_credential))
            return result
        if isinstance(value, list):
            return [self._data(item, credential=credential) for item in value]
        if isinstance(value, tuple):
            return tuple(self._data(item, credential=credential) for item in value)
        return value

    def payload(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("Redactor.payload expects a mapping")
        copied = dict(value)
        command = copied.get("cmd")
        identity = None
        if isinstance(command, (list, tuple)) and all(isinstance(part, str) for part in command):
            from harness.policy import approval_hash

            identity = approval_hash(command)
        safe = self.data(copied)
        if identity is not None:
            safe["cmd_identity"] = identity
        return safe
