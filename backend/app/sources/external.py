"""外部数据源适配：直连感知系统的 WebSocket 推送。

感知系统每 2 秒推送一条 state.json 形态的结果：

    {"type": "state", "payload": {"emotion": "Neutral", "confidence": 0.7196,
                                  "window": "55.0s - 57.0s"}}

本系统只消费 emotion / confidence / window 三个标签（解析见 parser.py），
其余字段与日志一律忽略。断线后指数退避重连，保证系统对外部抖动具备韧性。

启用方式：.env 中设置 SOURCE_MODE=external 与 EXTERNAL_STATE_URL=ws://...
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..schemas import StateFrame
from .base import StateSource
from .parser import normalize_payload

logger = logging.getLogger(__name__)


class ExternalSource(StateSource):
    name = "external"

    def __init__(
        self,
        bus: EventBus,
        sample_hz: float = 0.5,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(bus, sample_hz)
        self.cfg = cfg or default_settings
        self.url = self.cfg.external_state_url
        self._backoff = 1.0
        self._last_window = ""

    async def run(self) -> None:
        if not self.url:
            raise RuntimeError("SOURCE_MODE=external 但未配置 EXTERNAL_STATE_URL")
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    logger.info("已接入感知系统 %s", self.url)
                    self._backoff = 1.0
                    async for message in ws:
                        frame = self._decode(message)
                        if frame is not None and self._is_new(frame):
                            self.bus.publish(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 外部依赖需整体兜底
                logger.warning(
                    "感知系统连接异常：%s，%.1fs 后重连", exc, self._backoff
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def _decode(self, message: str | bytes) -> StateFrame | None:
        try:
            raw = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("收到非 JSON 报文，已忽略")
            return None
        return normalize_payload(raw, self.session_id, source=self.name)

    def _is_new(self, frame: StateFrame) -> bool:
        """按 window 去重：对端若以高频重复推送同一窗口，不应重复计数/重复触发。"""
        if not frame.window:
            return True
        if frame.window == self._last_window:
            return False
        self._last_window = frame.window
        return True
