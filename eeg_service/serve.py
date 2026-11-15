"""脑电负荷服务：把 4 级认知负荷按申报书的节奏推给疏导仓。

用法（在仓库根目录执行）：

    python -m eeg_service.serve                          # ws://127.0.0.1:8766，1s 一条
    python -m eeg_service.serve --level High             # 固定为"高"，用于演示触发
    python -m eeg_service.serve --speed 4                # 4 倍速，24s 走完一个完整循环
    python -m eeg_service.serve --dump load.json         # 同时按 JSON 落盘（file 模式用）
    python -m eeg_service.serve --port 8766 --window 4 --step 1

对接疏导仓（backend/.env，改完重启后端）：

    LOAD_SOURCE_MODE=external
    LOAD_EXTERNAL_URL=ws://127.0.0.1:8766
    # 或直接读本服务写出的文件：
    LOAD_SOURCE_MODE=file
    LOAD_STATE_FILE=...\\load.json

只开本服务、把负荷固定为 High，即可复现
"负荷爬升 → high_load 干预话术 → 冷却期抑制 → 等级继续恶化时打断冷却" 的完整闭环。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path

import websockets

from .protocol import LEVEL_ZH, LOAD_LEVELS
from .simulator import LoadSimulator

logger = logging.getLogger("eeg_service.serve")

_SEND_TIMEOUT_S = 2.0
_REPORT_INTERVAL_S = 10.0


class LoadServer:
    def __init__(
        self,
        simulator: LoadSimulator,
        *,
        host: str = "127.0.0.1",
        port: int = 8766,
        dump_path: str | None = None,
    ) -> None:
        self.simulator = simulator
        self.host = host
        self.port = port
        self.dump_path = Path(dump_path).expanduser().resolve() if dump_path else None
        self.clients: set = set()
        self.pushed = 0
        self._last_level = ""

    # ---- 连接 ------------------------------------------------------------ #
    async def _handler(self, websocket) -> None:
        peer = getattr(websocket, "remote_address", "unknown")
        self.clients.add(websocket)
        logger.info("客户端接入 %s（在线 %d）", peer, len(self.clients))
        with contextlib.suppress(Exception):
            await websocket.send(
                '{"type":"hello","payload":{"service":"eeg-load","levels":"'
                + "|".join(LOAD_LEVELS)
                + '"}}'
            )
            async for _ in websocket:  # 只读循环：用于感知断开
                pass
        self.clients.discard(websocket)
        logger.info("客户端断开 %s（在线 %d）", peer, len(self.clients))

    async def _broadcast(self, message: str) -> None:
        if not self.clients:
            return
        clients = list(self.clients)
        results = await asyncio.gather(
            *(self._send_one(ws, message) for ws in clients), return_exceptions=True
        )
        for ws, result in zip(clients, results):
            if isinstance(result, BaseException):
                self.clients.discard(ws)
                logger.warning("丢弃客户端连接：%s", result)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(), timeout=1.0)

    @staticmethod
    async def _send_one(websocket, message: str) -> None:
        await asyncio.wait_for(websocket.send(message), timeout=_SEND_TIMEOUT_S)

    # ---- 主循环 ---------------------------------------------------------- #
    async def _ticker(self) -> None:
        last_report = time.monotonic()
        while True:
            frame = self.simulator.next_frame()
            if self.dump_path is not None:
                self._dump(frame)
            if frame.level != self._last_level:
                logger.info(
                    "负荷等级切换 -> %s（%s，task=%s）",
                    frame.level,
                    LEVEL_ZH[frame.level],
                    frame.task,
                )
                self._last_level = frame.level
            if self.clients:
                await self._broadcast(frame.to_message())
            self.pushed += 1

            now = time.monotonic()
            if now - last_report >= _REPORT_INTERVAL_S:
                logger.info(
                    "推送中：已推送 %d 条 ｜ 在线 %d ｜ 最新 %s %.2f ｜ %s",
                    self.pushed,
                    len(self.clients),
                    frame.level,
                    frame.confidence,
                    frame.window,
                )
                last_report = now
            await asyncio.sleep(max(0.1, self.simulator.step_s / self.simulator.speed))

    def _dump(self, frame) -> None:
        """原子替换写 JSON，避免疏导仓 file 模式读到半个文件。"""
        assert self.dump_path is not None
        payload = {**frame.to_state(), "ts": round(time.time(), 3)}
        tmp = self.dump_path.with_name(self.dump_path.name + ".tmp")
        try:
            self.dump_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.dump_path)
        except OSError as exc:
            logger.warning("写负荷文件失败：%s", exc)

    async def run(self) -> None:
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info(
                "脑电负荷服务已启动：ws://%s:%d ｜ 窗口 %.1fs / 步长 %.1fs ｜ 倍速 %.1f%s",
                self.host,
                self.port,
                self.simulator.window_s,
                self.simulator.step_s,
                self.simulator.speed,
                f" ｜ JSON -> {self.dump_path}" if self.dump_path else "",
            )
            await self._ticker()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="脑电负荷服务（4 级认知负荷，4s 窗 1s 步）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8766, help="监听端口")
    parser.add_argument("--window", type=float, default=4.0, help="EEG 窗口长度（秒）")
    parser.add_argument("--step", type=float, default=1.0, help="滑动步长 / 推送间隔（秒）")
    parser.add_argument("--speed", type=float, default=1.0, help="时间倍速（演示时可调大）")
    parser.add_argument(
        "--level",
        default="",
        choices=["", *LOAD_LEVELS],
        help="固定负荷等级（空 = 按飞行任务的节奏自动爬升）",
    )
    parser.add_argument(
        "--dump",
        default="",
        help="可选：把最新结果写到该 JSON 路径（供疏导仓 LOAD_SOURCE_MODE=file 读取）",
    )
    parser.add_argument("--seed", type=int, default=None, help="随机种子（便于复现）")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    args = parse_args(argv)
    simulator = LoadSimulator(
        window_s=args.window,
        step_s=args.step,
        speed=args.speed,
        level_override=args.level or None,
        seed=args.seed,
    )
    server = LoadServer(
        simulator,
        host=args.host,
        port=args.port,
        dump_path=args.dump or None,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(server.run())


if __name__ == "__main__":
    main()
