"""Operational control endpoints."""

import asyncio
import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import _ops

router = APIRouter(tags=["ops"])

OPS_TOKEN = os.getenv("OPS_TOKEN", "br-labs-ops-7f3a2b")

_price_http = None
_engine_http = None


def init_router(price_client, engine_client):
    global _price_http, _engine_http
    _price_http = price_client
    _engine_http = engine_client


SCENARIO_TARGETS = {
    "price_stopped": "price",
    "price_latency": "price",
    "stale_prices": "price",
    "db_write_fail": "engine",
    "db_write_delay": "engine",
    "memory_pressure": None,
    "cpu_pressure": None,
    "generic_errors": "gateway",
}


class OpsStartRequest(BaseModel):
    config: dict[str, Any] = {}
    target: str = "gateway"


def _validate_token(token: str):
    if token != OPS_TOKEN:
        raise HTTPException(status_code=404)


def _resolve_target(scenario: str, requested_target: str) -> str:
    fixed = SCENARIO_TARGETS.get(scenario)
    if fixed is not None:
        return fixed
    return requested_target


def _get_client(target: str):
    if target == "price":
        return _price_http
    if target == "engine":
        return _engine_http
    return None


async def _forward_start(client, scenario: str, config: dict[str, Any]):
    resp = await client.post(
        f"/internal/ops/{scenario}/start", json={"config": config}
    )
    return resp.json()


async def _forward_stop(client, scenario: str):
    resp = await client.post(f"/internal/ops/{scenario}/stop")
    return resp.json()


@router.post("/ops/{token}/{scenario}/start")
async def start_scenario(
    token: str, scenario: str, req: OpsStartRequest | None = None
):
    _validate_token(token)
    if scenario not in SCENARIO_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario: {scenario}")

    body = req or OpsStartRequest()
    target = _resolve_target(scenario, body.target)
    config = body.config

    client = _get_client(target)
    if client:
        return await _forward_start(client, scenario, config)

    _ops.start(scenario, config)
    if scenario == "memory_pressure":
        asyncio.create_task(_ops.run_memory_pressure())
    elif scenario == "cpu_pressure":
        threads = config.get("spin_threads", 1)
        for _ in range(threads):
            threading.Thread(target=_ops.run_cpu_pressure, daemon=True).start()

    return {"status": "started", "scenario": scenario, "target": target, "config": config}


@router.post("/ops/{token}/{scenario}/stop")
async def stop_scenario(token: str, scenario: str, target: str = "gateway"):
    _validate_token(token)
    if scenario not in SCENARIO_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario: {scenario}")

    resolved = _resolve_target(scenario, target)
    client = _get_client(resolved)
    if client:
        return await _forward_stop(client, scenario)

    _ops.stop(scenario)
    return {"status": "stopped", "scenario": scenario, "target": resolved}


@router.get("/ops/{token}/status")
async def get_status(token: str):
    _validate_token(token)

    result = {"gateway": _ops.get_status()}

    try:
        resp = await _engine_http.get("/internal/ops/status")
        result["engine"] = resp.json().get("active", {})
    except Exception:
        result["engine"] = {"error": "unreachable"}

    try:
        resp = await _price_http.get("/internal/ops/status")
        result["price-service"] = resp.json().get("active", {})
    except Exception:
        result["price-service"] = {"error": "unreachable"}

    return result
