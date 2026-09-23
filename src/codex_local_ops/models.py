from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Result:
    status: str = "OK"
    data: Any = None
    reason: str | None = None
    platform: str | None = None
    feature: str | None = None
    suggested_setup: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != {}}


def unavailable(
    reason: str,
    platform: str,
    suggested_setup: str | None = None,
    *,
    feature: str | None = None,
) -> dict[str, Any]:
    return Result(
        status="CAPABILITY_UNAVAILABLE",
        reason=reason,
        platform=platform,
        feature=feature or "generic",
        suggested_setup=suggested_setup,
    ).as_dict()


def denied(reason: str, platform: str | None = None) -> dict[str, Any]:
    return Result(status="PERMISSION_DENIED", reason=reason, platform=platform).as_dict()


def failed(reason: str, platform: str | None = None, data: Any = None) -> dict[str, Any]:
    return Result(status="FAILED", reason=reason, platform=platform, data=data).as_dict()
