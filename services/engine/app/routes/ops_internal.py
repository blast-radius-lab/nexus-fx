"""Internal operational control."""

import asyncio
import threading
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import _ops

router = APIRouter(prefix="/internal/ops", tags=["ops-internal"])


class OpsRequest(BaseModel):
    config: dict[str, Any] = {}


@router.post("/{scenario}/start")
async def start_scenario(scenario: str, req: OpsRequest | None = None):
    config = (req or OpsRequest()).config
    _ops.start(scenario, config)

    if scenario == "memory_pressure":
        asyncio.create_task(_ops.run_memory_pressure())
    elif scenario == "cpu_pressure":
        threads = config.get("spin_threads", 1)
        for _ in range(threads):
            threading.Thread(target=_ops.run_cpu_pressure, daemon=True).start()

    return {"status": "started", "scenario": scenario, "config": config}


@router.post("/{scenario}/stop")
async def stop_scenario(scenario: str):
    _ops.stop(scenario)
    return {"status": "stopped", "scenario": scenario}


@router.get("/status")
async def get_status():
    return {"active": _ops.get_status()}
