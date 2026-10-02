"""内置状态模拟器：在没有真实硬件/外部系统时提供可控的多模态数据流。

两个隐变量驱动全部输出：
  stress ∈ [0,1]  —— 情绪压力（音视频情绪标签的来源）
  load   ∈ [0,100] —— 认知负荷（脑电认知负荷等级的来源）
剧本通过改变这两个隐变量的目标值 + 平滑跟随 + 高斯噪声，产生自然的状态曲线，
并支持突变（焦虑升级），从而可复现地演示"平静 → 异常 → 共情 → 恢复"闭环。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..schemas import Cognitive, CognitiveLevel, Emotion, EmotionLabel, StateFrame
from .base import StateSource

logger = logging.getLogger(__name__)

# 每个剧本的目标值函数：输入剧本已运行秒数，返回 (stress_target, load_target)
_SCENARIO_TARGETS = {
    "calm": lambda t: (0.14, 24.0),
    "high_load": lambda t: (0.45, 86.0),
    "anxiety_escalation": lambda t: (
        min(0.92, 0.14 + t / 12.0 * 0.78),
        min(88.0, 24.0 + t / 12.0 * 64.0),
    ),
    "recovery": lambda t: (
        max(0.14, 0.92 - t / 15.0 * 0.78),
        max(24.0, 88.0 - t / 15.0 * 64.0),
    ),
}


def _classify_emotion(stress: float) -> EmotionLabel:
    if stress >= 0.55:
        return "Negative"
    if stress <= 0.32:
        return "Positive"
    return "Neutral"


def _classify_load(load: float) -> CognitiveLevel:
    if load >= 70:
        return "High"
    if load >= 40:
        return "Medium"
    return "Low"


class SimulatorSource(StateSource):
    name = "simulator"

    def __init__(
        self,
        bus: EventBus,
        sample_hz: float = 10.0,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(bus, sample_hz)
        self.cfg = cfg or default_settings
        self._stress = 0.14
        self._load = 24.0
        self._scenario_started = time.monotonic()

    def _on_scenario_changed(self, name: str) -> None:
        self._scenario_started = time.monotonic()

    def reset(self) -> None:
        self._stress, self._load = 0.14, 24.0
        self._scenario_started = time.monotonic()

    def next_frame(self) -> StateFrame:
        """生成一帧（同步，便于单元测试直接调用）。"""
        elapsed = time.monotonic() - self._scenario_started
        target_stress, target_load = _SCENARIO_TARGETS[self._scenario](elapsed)

        # 一阶惯性跟随 + 高斯噪声，曲线平滑且不呆板
        self._stress += (target_stress - self._stress) * 0.18
        self._stress += random.gauss(0, 0.02)
        self._stress = min(1.0, max(0.0, self._stress))

        self._load += (target_load - self._load) * 0.18
        self._load += random.gauss(0, 1.8)
        self._load = min(100.0, max(0.0, self._load))

        label = _classify_emotion(self._stress)
        # 离分类边界越远，置信度越高
        distance = min(abs(self._stress - 0.55), abs(self._stress - 0.32))
        confidence = round(min(0.98, 0.62 + distance * 1.1), 3)

        return StateFrame(
            session_id=self.session_id,
            emotion=Emotion(label=label, confidence=confidence),
            cognitive=Cognitive(
                level=_classify_load(self._load), score=round(self._load, 2)
            ),
            source=self.name,
        )

    async def run(self) -> None:
        interval = 1.0 / max(self.sample_hz, 0.1)
        logger.info("模拟器启动，采样率 %.1f Hz", self.sample_hz)
        while True:
            self.bus.publish(self.next_frame())
            await asyncio.sleep(interval)
