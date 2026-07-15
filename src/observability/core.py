from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import logging
import math
from threading import RLock
from types import MappingProxyType
from typing import Mapping, final


_LOGGER = logging.getLogger("vless_agent.observability")
_LATENCY_BUCKETS = (
    ("le_0.01", 0.01),
    ("le_0.1", 0.1),
    ("le_1", 1.0),
    ("le_5", 5.0),
    ("le_inf", math.inf),
)


class EventCode(StrEnum):
    READINESS_READY = "readiness_ready"
    READINESS_NOT_READY = "readiness_not_ready"
    READINESS_RECOVERY_READY = "readiness_recovery_ready"
    REVISION_DRIFT = "revision_drift"
    APPLY_SUCCESS = "apply_success"
    APPLY_FAILURE = "apply_failure"
    REVISION_CONFLICT = "revision_conflict"
    SNAPSHOT_OVERFLOW = "snapshot_overflow"
    AUTH_FAILURE = "auth_failure"
    INCOMPATIBLE_CONTRACT = "incompatible_contract"
    STARTUP_RESTORE_FAILURE = "startup_restore_failure"
    XRAY_TIMEOUT = "xray_timeout"
    XRAY_UNAVAILABLE = "xray_unavailable"
    XRAY_PROTOCOL_FAILURE = "xray_protocol_failure"


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class MetricsSnapshot:
    counters: Mapping[str, int]
    apply_latency_buckets: Mapping[str, int]
    apply_latency_count: int
    apply_latency_seconds_sum: float

    def as_dict(self) -> dict[str, object]:
        return {
            "counters": dict(self.counters),
            "apply_latency_buckets": dict(self.apply_latency_buckets),
            "apply_latency_count": self.apply_latency_count,
            "apply_latency_seconds_sum": self.apply_latency_seconds_sum,
        }


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class Observability:
    _lock: RLock = field(default_factory=RLock)
    _counters: dict[EventCode, int] = field(
        default_factory=lambda: {code: 0 for code in EventCode}
    )
    _apply_latency_buckets: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name, _ in _LATENCY_BUCKETS}
    )
    _apply_latency_count: list[int] = field(default_factory=lambda: [0])
    _apply_latency_seconds_sum: list[float] = field(default_factory=lambda: [0.0])

    def record(self, code: EventCode) -> None:
        if not isinstance(code, EventCode):
            raise TypeError("code must be an EventCode")
        with self._lock:
            self._counters[code] += 1
        self._log(code=code)

    def observe_apply(self, *, succeeded: bool, latency_seconds: float) -> None:
        if not isinstance(succeeded, bool):
            raise TypeError("succeeded must be a bool")
        if isinstance(latency_seconds, bool) or not isinstance(latency_seconds, (int, float)):
            raise TypeError("latency_seconds must be numeric")
        latency = float(latency_seconds)
        if not math.isfinite(latency) or latency < 0:
            raise ValueError("latency_seconds must be finite and non-negative")
        code = EventCode.APPLY_SUCCESS if succeeded else EventCode.APPLY_FAILURE
        with self._lock:
            self._counters[code] += 1
            self._apply_latency_count[0] += 1
            self._apply_latency_seconds_sum[0] += latency
            for name, upper_bound in _LATENCY_BUCKETS:
                if latency <= upper_bound:
                    self._apply_latency_buckets[name] += 1
        self._log(code=code, latency_seconds=latency)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = {code.value: self._counters[code] for code in EventCode}
            latency_buckets = {
                name: self._apply_latency_buckets[name]
                for name, _ in _LATENCY_BUCKETS
            }
            return MetricsSnapshot(
                counters=MappingProxyType(counters),
                apply_latency_buckets=MappingProxyType(latency_buckets),
                apply_latency_count=self._apply_latency_count[0],
                apply_latency_seconds_sum=self._apply_latency_seconds_sum[0],
            )

    def _log(self, *, code: EventCode, latency_seconds: float | None = None) -> None:
        event: dict[str, str | float] = {
            "event": "agent_observation",
            "code": code.value,
        }
        if latency_seconds is not None:
            event["latency_seconds"] = latency_seconds
        _LOGGER.info(json.dumps(event, separators=(",", ":"), sort_keys=True))


__all__ = ("EventCode", "MetricsSnapshot", "Observability")
