"""下行状态流 WebSocket：向监测大屏推送状态帧、干预事件与播报指令。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..hub import ConnectionHub
from ..schemas import Envelope
from .system import collect_providers

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/monitor")
async def monitor(ws: WebSocket) -> None:
    app = ws.app
    hub: ConnectionHub = app.state.hub
    await ws.accept()

    client = hub.add(ws)
    client.offer(
        Envelope.of(
            "meta",
            session_id=app.state.source.session_id,
            scenario=app.state.source.current_scenario,
            providers={
                k: v.model_dump() for k, v in collect_providers(app.state).items()
            },
        )
    )

    sender = asyncio.create_task(client.sender(), name=f"ws-sender-{client.id}")
    try:
        while True:
            raw = await ws.receive_text()
            if raw == "ping":  # 客户端心跳，用于探测半开连接
                client.offer(Envelope.of("meta", pong=True))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("WS 连接异常结束：%s", exc)
    finally:
        client.alive = False
        sender.cancel()
        hub.remove(client)
