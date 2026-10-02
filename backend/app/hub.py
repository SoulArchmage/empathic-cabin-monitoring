"""WebSocket 连接管理与推送。

任务书硬性要求「WebSocket 数据流推送不卡顿」，为此采用三件套：
1. 每连接独立发送队列 + 独立发送协程 —— 慢客户端只拖慢自己；
2. drop-oldest 背压 —— 仅状态帧可丢（队列满时丢最旧帧）；控制类消息（干预/播报）
   单独入队且优先发送，不会被状态流拥塞顶掉；
3. 广播只做入队（O(客户端数) 的非阻塞操作），不做网络 IO。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque

from fastapi import WebSocket

from .bus import EventBus
from .config import Settings, settings as default_settings
from .schemas import Envelope, StateFrame

logger = logging.getLogger(__name__)
_ids = itertools.count(1)


class FrameRateMeter:
    """统计最近 5 秒的帧率，供 /api/health 展示推送性能。"""

    def __init__(self, window_s: float = 5.0) -> None:
        self.window_s = window_s
        self._ts: deque[float] = deque()

    def tick(self) -> None:
        now = time.monotonic()
        self._ts.append(now)
        while self._ts and now - self._ts[0] > self.window_s:
            self._ts.popleft()

    @property
    def rate(self) -> float:
        if len(self._ts) < 2:
            return 0.0
        span = self._ts[-1] - self._ts[0]
        return round((len(self._ts) - 1) / span, 2) if span > 0 else 0.0


class WSClient:
    """一条 WebSocket 连接：入队与发送分离，状态帧与控制消息分队列。"""

    def __init__(self, ws: WebSocket, queue_size: int) -> None:
        self.id = next(_ids)
        self.ws = ws
        self.state_queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=queue_size)
        self.ctrl_queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=queue_size)
        self.dropped = 0
        self.alive = True
        # 有新消息时唤醒发送协程。否则它可能正阻塞在空的 state_queue 上，
        # 干预卡片/播报要等下一帧状态才发出；数据源中断时会一直等下去。
        self._wake = asyncio.Event()

    def offer(self, env: Envelope, *, droppable: bool = False) -> None:
        """入队。``droppable=True``（状态帧）在队列满时丢弃最旧帧，保证实时性优先；
        控制类消息不因状态流拥塞被顶掉。"""
        queue = self.state_queue if droppable else self.ctrl_queue
        if queue.full():
            try:
                queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        try:
            queue.put_nowait(env)
        except asyncio.QueueFull:  # pragma: no cover
            self.dropped += 1
        self._wake.set()

    def _take_nowait(self) -> Envelope | None:
        # 控制消息（干预卡片/语音播报/pong）优先于状态帧
        for queue in (self.ctrl_queue, self.state_queue):
            if not queue.empty():
                return queue.get_nowait()
        return None

    async def sender(self) -> None:
        try:
            while self.alive:
                env = self._take_nowait()
                if env is None:
                    # clear 与复查之间没有 await，offer 不会丢唤醒
                    self._wake.clear()
                    env = self._take_nowait()
                    if env is None:
                        await self._wake.wait()
                        continue
                await self.ws.send_json(env.model_dump())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 客户端断开属正常情况，静默退出
            self.alive = False


class ConnectionHub:
    """全局连接注册表 + 状态流广播。"""

    def __init__(self, bus: EventBus, cfg: Settings | None = None) -> None:
        self.bus = bus
        self.cfg = cfg or default_settings
        self._clients: dict[int, WSClient] = {}
        self.frame_meter = FrameRateMeter()

    # ---- 连接生命周期 ---------------------------------------------------- #
    def add(self, ws: WebSocket) -> WSClient:
        client = WSClient(ws, self.cfg.ws_queue_size)
        self._clients[client.id] = client
        logger.info("WS 客户端接入 id=%s，当前在线 %d", client.id, len(self._clients))
        return client

    def remove(self, client: WSClient) -> None:
        client.alive = False
        self._clients.pop(client.id, None)
        logger.info(
            "WS 客户端断开 id=%s（丢弃帧 %d），当前在线 %d",
            client.id,
            client.dropped,
            len(self._clients),
        )

    @property
    def active_clients(self) -> int:
        return len(self._clients)

    # ---- 广播 ------------------------------------------------------------ #
    def broadcast(self, env: Envelope, *, droppable: bool = False) -> None:
        for client in self._clients.values():
            client.offer(env, droppable=droppable)

    async def run(self) -> None:
        """作为后台任务运行：消费 bus 上的状态帧并广播给所有客户端。"""
        q = self.bus.subscribe(maxsize=self.cfg.ws_queue_size)
        try:
            while True:
                frame: StateFrame = await q.get()
                self.frame_meter.tick()
                self.broadcast(Envelope.of("state", frame=frame.model_dump()), droppable=True)
        finally:
            self.bus.unsubscribe(q)
