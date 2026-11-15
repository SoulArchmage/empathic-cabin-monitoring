"""脑电负荷数据源：独立 EEG 监测服务的三种接法。

第二路数据源与情绪通道解耦——EEG 系统可以是独立进程、独立机器，甚至换一家算法，
只要它按 ``level / confidence / window`` 的报文推送即可（见 load_parser.py）。三种实现：

    LoadSimulatorSource  内置 4 级模拟，无需任何外部进程即可演示（默认）
    LoadExternalSource   直连 EEG 服务的 WebSocket（LOAD_SOURCE_MODE=external）
    LoadFileSource       轮询 EEG 服务写出的 JSON（LOAD_SOURCE_MODE=file）

数据只做一件事：``store.update(load)``。往 WebSocket 广播、贴到情绪帧上、落库，
全部由 LoadStore 的订阅者与 pipeline 完成，数据源本身不关心业务。
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import random
import time
from pathlib import Path

import websockets

from ..config import Settings, settings as default_settings
from ..load_store import LoadStore
from ..schemas import CognitiveLoad, LoadLevel
from .load_parser import parse_load, parse_load_json

logger = logging.getLogger(__name__)

# 模拟节奏直接照搬申报书的四类飞行任务：巡航(低) → 水平转弯(中) → 起飞(高) → 着陆(极高)
# 一个完整循环 96s，便于答辩时在两分钟内展示"负荷爬升 → 触发干预 → 回落"。
_PHASES: tuple[tuple[float, LoadLevel, str], ...] = (
    (0.0, "Low", "巡航"),
    (24.0, "Medium", "水平转弯"),
    (42.0, "High", "起飞"),
    (60.0, "VeryHigh", "着陆"),
    (74.0, "Low", "落地滑行"),
)
_CYCLE_S = 96.0


class LoadSource(abc.ABC):
    """脑电负荷数据源接口。"""

    name: str = "base"
    mode: str = "mock"  # mock = 内置模拟；real = 真实外部服务

    def __init__(self, store: LoadStore, cfg: Settings | None = None) -> None:
        self.store = store
        self.cfg = cfg or default_settings
        self._last_key = ""

    @abc.abstractmethod
    async def run(self) -> None:
        """持续产出负荷结果并写入 store，直到被取消。"""

    def _is_new(self, load: CognitiveLoad) -> bool:
        """按"窗口 + 等级"去重：对端高频重复推送同一结果时不应重复更新/重复触发。"""
        key = f"{load.window}|{load.level}"
        if key == self._last_key:
            return False
        self._last_key = key
        return True


class LoadSimulatorSource(LoadSource):
    """内置 4 级负荷模拟：4s 窗、1s 步，等级按飞行任务节奏爬升。"""

    name = "simulator"

    def __init__(self, store: LoadStore, cfg: Settings | None = None) -> None:
        super().__init__(store, cfg)
        self.window_s = max(1.0, float(self.cfg.load_window_s))
        self.step_s = max(0.2, float(self.cfg.load_step_s))
        self._started = time.monotonic()
        self._elapsed = 0.0

    def reset(self) -> None:
        self._started = time.monotonic()
        self._elapsed = 0.0

    def next_load(self) -> CognitiveLoad:
        """生成一条负荷结果（同步，便于单元测试直接调用）。"""
        start = self._elapsed
        self._elapsed += self.step_s

        phase_at = start % _CYCLE_S
        level: LoadLevel = "Low"
        phase_start = 0.0
        for offset, name, _task in _PHASES:
            if phase_at >= offset:
                level, phase_start = name, offset
        # 切换后的前 3 秒置信度偏低：模拟"状态正在过渡、模型还不太确定"
        transition = (phase_at - phase_start) < 3.0
        base = 0.66 if transition else 0.88
        confidence = min(0.98, max(0.55, random.gauss(base, 0.035)))

        return CognitiveLoad(
            level=level,
            confidence=round(confidence, 4),
            window=f"{start:.1f}s - {start + self.window_s:.1f}s",
            source=self.name,
        )

    async def run(self) -> None:
        logger.info(
            "脑电负荷模拟器启动：%.0fs 一个完整循环（4s 窗 / %.1fs 步）",
            _CYCLE_S,
            self.step_s,
        )
        while True:
            load = self.next_load()
            self.store.update(load)
            logger.debug(
                "负荷模拟 level=%s confidence=%.2f window=%s",
                load.level,
                load.confidence,
                load.window,
            )
            await asyncio.sleep(self.step_s)


class LoadExternalSource(LoadSource):
    """直连 EEG 服务的 WebSocket。断线后指数退避重连，保证对端重启自愈。"""

    name = "external"
    mode = "real"

    def __init__(self, store: LoadStore, cfg: Settings | None = None) -> None:
        super().__init__(store, cfg)
        self.url = self.cfg.load_external_url
        self._backoff = 1.0

    async def run(self) -> None:
        if not self.url:
            raise RuntimeError("LOAD_SOURCE_MODE=external 但未配置 LOAD_EXTERNAL_URL")
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    logger.info("已接入 EEG 负荷服务 %s", self.url)
                    self._backoff = 1.0
                    async for message in ws:
                        load = self._decode(message)
                        if load is not None and self._is_new(load):
                            self.store.update(load)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 外部依赖需整体兜底
                logger.warning(
                    "EEG 负荷服务连接异常：%s，%.1fs 后重连", exc, self._backoff
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def _decode(self, message: str | bytes) -> CognitiveLoad | None:
        try:
            raw = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("收到非 JSON 的负荷报文，已忽略")
            return None
        return parse_load(raw, source=self.name)


class LoadFileSource(LoadSource):
    """轮询 EEG 服务写出的 JSON 文件（无需对端开 WebSocket）。"""

    name = "file"
    mode = "real"

    def __init__(self, store: LoadStore, cfg: Settings | None = None) -> None:
        super().__init__(store, cfg)
        path = self.cfg.load_state_file
        self.path = Path(path).expanduser() if path else None
        self.poll_s = max(0.1, float(self.cfg.load_file_poll_s))
        self._missing_logged = False

    async def run(self) -> None:
        if self.path is None:
            raise RuntimeError("LOAD_SOURCE_MODE=file 但未配置 LOAD_STATE_FILE")
        logger.info("EEG 负荷文件源启动：轮询 %s（%.1fs/次）", self.path, self.poll_s)
        while True:
            load = self._read()
            if load is not None and self._is_new(load):
                self.store.update(load)
            await asyncio.sleep(self.poll_s)

    def _read(self) -> CognitiveLoad | None:
        path = self.path
        assert path is not None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not self._missing_logged:
                logger.warning("负荷文件尚未生成：%s（等待 EEG 服务写出）", path)
                self._missing_logged = True
            return None
        except OSError as exc:
            logger.warning("读取负荷文件失败：%s", exc)
            return None

        self._missing_logged = False
        load = parse_load_json(text, source=self.name)
        if load is None:
            # EEG 侧可能正在写入（文件被截断）：静默跳过，下一轮再读
            logger.debug("负荷文件内容不完整或格式不符，本轮跳过")
        return load
