"""感知服务：把算法输出的三分类情绪按感知系统的真实节奏推给疏导仓。

用法（在仓库根目录执行）：
    python -m perception.serve                                   # 内置模拟，2s 一个窗口
    python -m perception.serve --scenario anxiety_escalation     # 换模拟演示
    python -m perception.serve --dump state.json                 # 同时按 state.json 形状落盘
    python -m perception.serve --backend replay --file out.jsonl --speed 2

对接疏导仓（backend/.env，改完重启后端）：
    SOURCE_MODE=external
    EXTERNAL_STATE_URL=ws://127.0.0.1:8765
    # 或直接读本服务/真实算法写出的文件：
    SOURCE_MODE=file
    STATE_FILE=...\\state.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

import websockets

from .backends import PerceptionBackend, build_backend

logger = logging.getLogger("perception.serve")

_REPORT_INTERVAL_S = 10.0
_SEND_TIMEOUT_S = 2.0
_LOG_RING = 50


class PerceptionServer:
    """WebSocket 服务器：接受疏导仓后端连接，每个新窗口推送一条结果。

    同时可选地把结果按 state.json 的形状原子落盘，供疏导仓以 file 模式直接读取，
    也方便与真实算法写出的文件逐字段对照。
    """

    def __init__(
        self,
        backend: PerceptionBackend,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        hz: float = 2.0,
        dump_path: str | None = None,
    ) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        # 检查新结果的频率上限：实际推送仍按"是否出现了新窗口"，默认 2s 一次
        self.hz = max(float(hz), 0.1)
        self.dump_path = Path(dump_path).expanduser().resolve() if dump_path else None
        self.clients: set = set()
        self.pushed = 0
        self.windows = 0
        self._last_frame = None
        self._last_emotion = ""
        self._logs: deque[dict[str, str]] = deque(maxlen=_LOG_RING)

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

    # ---- 新窗口处理 ------------------------------------------------------ #
    def _on_new_window(self, frame) -> None:
        self.windows += 1
        self._last_emotion = frame.emotion
        self._logs.appendleft(
            {
                "time": time.strftime("%H:%M:%S"),
                "message": (
                    f"Window#{self.windows} [{frame.window}] "
                    f"{frame.latency_ms}ms Emotion={frame.emotion}"
                ),
            }
        )
        logger.info(
            "Window#%d [%s] Emotion=%s confidence=%.3f",
            self.windows,
            frame.window,
            frame.emotion,
            frame.confidence,
        )
        if self.dump_path is not None:
            self._write_state_file(frame)

    def _write_state_file(self, frame) -> None:
        """按真实 state.json 的形状落盘（原子替换，避免读端读到半个文件）。"""
        state = frame.to_state()
        state.update(
            {
                "frame_count": int(frame.window_end * 5),
                "audio_samples": int(frame.window_end * 16000),
                "windows": self.windows,
                "logs": list(self._logs),
            }
        )
        assert self.dump_path is not None
        tmp = self.dump_path.with_name(self.dump_path.name + ".tmp")
        try:
            self.dump_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.dump_path)
        except OSError as exc:
            logger.warning("写 state.json 失败：%s", exc)

    # ---- 定频检查 -------------------------------------------------------- #
    async def _ticker(self) -> None:
        interval = 1.0 / self.hz
        next_at = time.monotonic()
        last_report = next_at
        while True:
            frame = self.backend.latest()
            # 只在新窗口出现时推送：同一窗口不重复发给疏导仓，否则会虚增触发计数
            if frame is not None and frame is not self._last_frame:
                self._last_frame = frame
                self._on_new_window(frame)
                if self.clients:
                    await self._broadcast(frame.to_message())
                self.pushed += 1
            now = time.monotonic()
            if now - last_report >= _REPORT_INTERVAL_S:
                logger.info(
                    "推送中：窗口 %d 个 ｜ 已推送 %d 次 ｜ 在线 %d ｜ 最新 %s",
                    self.windows,
                    self.pushed,
                    len(self.clients),
                    self._last_emotion or "—",
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
                "感知服务已启动：ws://%s:%d ｜ 后端=%s ｜ 最多 %.1fHz 检查新窗口%s",
                self.host,
                self.port,
                self.backend.name,
                self.hz,
                f" ｜ state.json -> {self.dump_path}" if self.dump_path else "",
            )
            ticker = asyncio.create_task(self._ticker(), name="perception-ticker")
            try:
                await ticker
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
                await self.backend.stop()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="疏导仓感知服务（窗口级情绪三分类）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument(
        "--hz",
        type=float,
        default=2.0,
        help="检查新结果的频率上限（默认 2Hz；实际推送按新窗口触发，约 0.5Hz）",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=2.0,
        help="感知窗口长度（秒），与 state.json 的 2s 窗口一致",
    )
    parser.add_argument(
        "--backend", default="simulator", choices=["simulator", "replay"], help="结果来源"
    )
    parser.add_argument(
        "--scenario",
        default="calm",
        choices=["calm", "anxiety_escalation", "recovery", "fluctuation"],
        help="simulator 模拟",
    )
    parser.add_argument("--file", help="replay 回放文件（JSONL，每行一个窗口）")
    parser.add_argument("--speed", type=float, default=1.0, help="replay 播放倍速")
    parser.add_argument("--no-loop", action="store_true", help="replay 播完停在最后一帧")
    parser.add_argument(
        "--dump",
        default="",
        help="可选：把结果按 state.json 形状写到该路径（每 2s 原子更新）",
    )
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
        window_seconds=args.window,
    )
    server = PerceptionServer(
        backend,
        host=args.host,
        port=args.port,
        hz=args.hz,
        dump_path=args.dump or None,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(server.run())


if __name__ == "__main__":
    main()
