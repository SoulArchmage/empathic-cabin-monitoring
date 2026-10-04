"""感知服务：把算法输出的"情绪 / 认知负荷"标签按固定频率推给疏导仓。

用法（在仓库根目录执行）：
    python -m perception.serve                                   # 内置模拟，10Hz
    python -m perception.serve --scenario anxiety_escalation     # 换演示剧本
    python -m perception.serve --backend replay --file out.jsonl --speed 2

对接疏导仓（backend/.env，改完重启后端）：
    SOURCE_MODE=external
    EXTERNAL_STATE_URL=ws://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import time

import websockets

from .backends import PerceptionBackend, build_backend

logger = logging.getLogger("perception.serve")

_REPORT_INTERVAL_S = 10.0
_SEND_TIMEOUT_S = 2.0


class PerceptionServer:
    """WebSocket 服务器：接受疏导仓后端连接，按固定频率推送最新结果。"""

    def __init__(
        self,
        backend: PerceptionBackend,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        hz: float = 10.0,
    ) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.hz = max(float(hz), 0.1)
        self.clients: set = set()
        self.pushed = 0

    # ---- 连接管理 -------------------------------------------------------- #
    async def _handler(self, websocket) -> None:
        peer = getattr(websocket, "remote_address", "unknown")
        self.clients.add(websocket)
        logger.info("客户端接入 %s（在线 %d）", peer, len(self.clients))
        with contextlib.suppress(Exception):
            # 握手帧只用于人工调试；疏导仓后端会自动忽略非 state 报文
            await websocket.send(
                '{"type":"hello","payload":{"service":"perception","backend":"'
                + self.backend.name
                + '"}}'
            )
            async for _ in websocket:  # 只读循环：用于感知断开
                pass
        self.clients.discard(websocket)
        logger.info("客户端断开 %s（在线 %d）", peer, len(self.clients))

    async def _broadcast(self, message: str) -> None:
        """并发发送 + 单连接超时：慢客户端只丢自己，不拖住推送循环。"""
        clients = list(self.clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(self._send_one(websocket, message) for websocket in clients),
            return_exceptions=True,
        )
        for websocket, result in zip(clients, results):
            if isinstance(result, BaseException):
                await self._drop(websocket, result)

    @staticmethod
    async def _send_one(websocket, message: str) -> None:
        await asyncio.wait_for(websocket.send(message), timeout=_SEND_TIMEOUT_S)

    async def _drop(self, websocket, reason: object) -> None:
        self.clients.discard(websocket)
        logger.warning("丢弃客户端连接：%s", reason)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(), timeout=1.0)

    # ---- 定频推送 -------------------------------------------------------- #
    async def _ticker(self) -> None:
        interval = 1.0 / self.hz
        next_at = time.monotonic()
        last_report = next_at
        while True:
            if self.clients:
                frame = self.backend.latest()
                if frame is not None:
                    await self._broadcast(frame.to_message())
                    self.pushed += 1
            now = time.monotonic()
            if now - last_report >= _REPORT_INTERVAL_S:
                logger.info(
                    "推送中：目标 %.0fHz ｜ 累计 %d 帧 ｜ 在线 %d",
                    self.hz,
                    self.pushed,
                    len(self.clients),
                )
                last_report = now
            next_at += interval
            if next_at <= now:
                # 已落后一个周期以上（广播耗时/事件循环卡顿）：重同步时钟，避免连续补发
                next_at = now + interval
            await asyncio.sleep(next_at - now)

    async def run(self) -> None:
        await self.backend.start()
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info(
                "感知服务已启动：ws://%s:%d ｜ 后端=%s ｜ 推送=%.1fHz",
                self.host,
                self.port,
                self.backend.name,
                self.hz,
            )
            ticker = asyncio.create_task(self._ticker(), name="perception-ticker")
            try:
                await ticker
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
                await self.backend.stop()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="疏导仓感知服务（骨架）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument(
        "--hz", type=float, default=10.0, help="对外推送频率，与疏导仓采样率一致（默认 10Hz）"
    )
    parser.add_argument(
        "--backend", default="simulator", choices=["simulator", "replay"], help="结果来源"
    )
    parser.add_argument(
        "--scenario",
        default="calm",
        choices=["calm", "anxiety_escalation", "recovery", "high_load"],
        help="simulator 剧本",
    )
    parser.add_argument("--file", help="replay 回放文件（JSONL）")
    parser.add_argument("--speed", type=float, default=1.0, help="replay 播放倍速")
    parser.add_argument("--no-loop", action="store_true", help="replay 播完停在最后一帧")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    args = parse_args(argv)
    backend = build_backend(
        args.backend,
        scenario=args.scenario,
        file=args.file,
        speed=args.speed,
        loop=not args.no_loop,
    )
    server = PerceptionServer(backend, host=args.host, port=args.port, hz=args.hz)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(server.run())


if __name__ == "__main__":
    main()