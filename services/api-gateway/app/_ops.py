"""Internal operational state."""

import asyncio
import random
from typing import Any

_active: dict[str, dict[str, Any]] = {}
_leaked: list[bytes] = []


def start(scenario: str, config: dict[str, Any] | None = None) -> None:
    _active[scenario] = config or {}


def stop(scenario: str) -> None:
    _active.pop(scenario, None)
    if scenario == "memory_pressure":
        _leaked.clear()


def is_active(scenario: str) -> bool:
    return scenario in _active


def get_config(scenario: str) -> dict[str, Any]:
    return _active.get(scenario, {})


def get_status() -> dict[str, dict[str, Any]]:
    return dict(_active)


def clear() -> None:
    _active.clear()
    _leaked.clear()


async def apply_latency(scenario: str, default_ms: int = 5000) -> None:
    if scenario in _active:
        delay_ms = _active[scenario].get("delay_ms", default_ms)
        await asyncio.sleep(delay_ms / 1000.0)


def should_fail(scenario: str, default_rate: float = 1.0) -> bool:
    if scenario not in _active:
        return False
    rate = _active[scenario].get("failure_rate", default_rate)
    return random.random() < rate


async def run_memory_pressure() -> None:
    config = _active.get("memory_pressure", {})
    rate = config.get("leak_rate_mb_per_sec", 1.0)
    while is_active("memory_pressure"):
        _leaked.append(b"\x00" * (1024 * 1024))
        await asyncio.sleep(1.0 / max(rate, 0.1))


def run_cpu_pressure() -> None:
    while is_active("cpu_pressure"):
        pass
