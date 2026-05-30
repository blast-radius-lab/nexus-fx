import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth.jwt_handler import decode_token

router = APIRouter()
logger = logging.getLogger(__name__)

_price_http = None


def init_router(http_client):
    global _price_http
    _price_http = http_client


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active.remove(websocket)

    async def broadcast(self, message: str):
        for ws in self.active[:]:
            try:
                await ws.send_text(message)
            except Exception:
                self.active.remove(ws)


manager = ConnectionManager()


@router.websocket("/ws/prices")
async def price_stream(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await manager.connect(websocket)
    try:
        while True:
            try:
                resp = await _price_http.get("/prices/current", params={"instruments": ""})
                prices = resp.json()
                await websocket.send_text(json.dumps(prices))
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception:
                logger.exception("Price fetch for WS failed")
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
