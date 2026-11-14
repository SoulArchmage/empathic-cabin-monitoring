"""文件数据源：直接读取感知系统写出的 state.json。

适用于感知程序与疏导仓跑在同一台机器、且感知侧已经持续刷新 state.json 的场景：
本数据源每 file_poll_s 秒读取一次，检测到新的 window 就发布一帧，
不要求感知侧额外开 WebSocket，也不改动它的任何代码。

启用方式：.env 中设置

    SOURCE_MODE=file
    STATE_FILE=D:\\path\\to\\state.json
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..schemas import StateFrame
from .base import StateSource
from .parser import parse_state_json

logger = logging.getLogger(__name__)


class FileSource(StateSource):
    name = "file"

    def __init__(
        self,
        bus: EventBus,
        sample_hz: float = 0.5,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(bus, sample_hz)
        self.cfg = cfg or default_settings
        self.path = Path(self.cfg.state_file).expanduser() if self.cfg.state_file else None
        self.poll_s = max(0.1, float(self.cfg.file_poll_s))
        self._last_window = ""
        self._missing_logged = False

    async def run(self) -> None:
        if self.path is None:
            raise RuntimeError("SOURCE_MODE=file 但未配置 STATE_FILE")
        logger.info("文件数据源启动：轮询 %s（%.1fs/次）", self.path, self.poll_s)
        while True:
            frame = self._read()
            if frame is not None and self._is_new(frame):
                logger.debug("读取到新窗口 %s Emotion=%s", frame.window, frame.emotion.label)
                self.bus.publish(frame)
            await asyncio.sleep(self.poll_s)

    def _read(self) -> StateFrame | None:
        path = self.path
        assert path is not None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not self._missing_logged:
                logger.warning("state.json 尚未生成：%s（等待感知系统写出）", path)
                self._missing_logged = True
            return None
        except OSError as exc:
            logger.warning("读取 state.json 失败：%s", exc)
            return None

        self._missing_logged = False
        frame = parse_state_json(text, self.session_id, source=self.name)
        if frame is None:
            # 感知侧可能正在写入（文件被截断）：静默跳过，下一轮再读
            logger.debug("state.json 内容不完整或格式不符，本轮跳过")
        return frame

    def _is_new(self, frame: StateFrame) -> bool:
        if not frame.window:
            return True
        if frame.window == self._last_window:
            return False
        self._last_window = frame.window
        return True
