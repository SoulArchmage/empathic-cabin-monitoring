"""内置状态模拟器：按感知系统的真实节奏构造模拟数据。

与旧版"10Hz 连续生理信号"不同，这里严格复刻 state.json 的形态：
**每 2 秒输出一个滑动窗口的三分类情绪结果**，字段只有 emotion / confidence / window，
情绪取值固定为 Neutral / Positive / Negative 三种，例如：

    {"emotion": "Neutral", "confidence": 0.7196, "window": "55.0s - 57.0s"}

一个隐变量 valence ∈ [-1, 1] 驱动全部输出：
  valence > +0.25 → Positive；valence < -0.25 → Negative；其余为 Neutral。
置信度随"离分类边界的距离"增大而升高（0.55 ~ 0.97），因此 Neutral 的置信度通常
在 0.7 左右、明确 Negative 时更高——与真实算法给出的分布一致。

模拟通过改变 valence 的目标值 + 一阶惯性跟随 + 高斯噪声，产生自然的情绪演变，
并支持突变（焦虑升级），从而可复现地演示"平静 → 负向 → 共情 → 恢复"闭环。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..schemas import Emotion, EmotionLabel, StateFrame
from .base import StateSource

logger = logging.getLogger(__name__)

_POSITIVE_BOUNDARY = 0.25
_NEGATIVE_BOUNDARY = -0.25

# 每种模拟的目标 valence：输入其运行秒数
_SCENARIO_TARGETS = {
    # 平静常态：略偏正向但落在 Neutral 区间，与真实录制数据"以 Neutral 为主"一致
    "calm": lambda t: 0.10,
    # 负向升级：约 4.5 秒内从常态跌到明确负向，触发"先陪伴、后转介"
    "anxiety_escalation": lambda t: max(-0.95, 0.10 - t / 4.5 * 1.05),
    # 情绪恢复：目标直接回到正向区间，约 3 个窗口（6s）内脱离 Negative
    "recovery": lambda t: 0.40,
    # 情绪波动：每 10 秒一个约 1.5 秒的负向尖峰；由于尖峰短于一个 2s 窗口，
    # 至多命中一个感知窗口，用于验证"单窗口负向不触发"的去抖设计
    "fluctuation": lambda t: 0.10 - (0.95 if (t % 10.0) < 1.5 else 0.0),
}


def _classify(valence: float) -> tuple[EmotionLabel, float]:
    """把连续 valence 映射为三分类 + 置信度（置信度与离边界距离正相关）。"""
    if valence >= _POSITIVE_BOUNDARY:
        label: EmotionLabel = "Positive"
        distance = min(1.0, (valence - _POSITIVE_BOUNDARY) / 0.75)
    elif valence <= _NEGATIVE_BOUNDARY:
        label = "Negative"
        distance = min(1.0, (_NEGATIVE_BOUNDARY - valence) / 0.75)
    else:
        label = "Neutral"
        # 区间中心（0.0）距离边界最远，置信度最高
        distance = min(_POSITIVE_BOUNDARY - valence, valence - _NEGATIVE_BOUNDARY) / _POSITIVE_BOUNDARY

    confidence = 0.58 + 0.30 * distance + random.gauss(0, 0.015)
    return label, round(min(0.97, max(0.55, confidence)), 4)


class SimulatorSource(StateSource):
    name = "simulator"

    def __init__(
        self,
        bus: EventBus,
        sample_hz: float = 0.5,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(bus, sample_hz)
        self.cfg = cfg or default_settings
        self.window_seconds = max(0.5, float(self.cfg.window_seconds))
        self._valence = 0.10
        self._window_start = 0.0
        self._scenario_started = time.monotonic()

    def _on_scenario_changed(self, name: str) -> None:
        # 切换模拟只重置"目标曲线"的起点，窗口时间轴继续前进
        self._scenario_started = time.monotonic()

    def reset(self) -> None:
        self._valence = 0.10
        self._window_start = 0.0
        self._scenario_started = time.monotonic()

    def next_frame(self) -> StateFrame:
        """生成一个窗口的结果（同步，便于单元测试直接调用）。"""
        start = self._window_start
        end = start + self.window_seconds
        self._window_start = end

        target = _SCENARIO_TARGETS[self._scenario](time.monotonic() - self._scenario_started)
        # 一阶惯性跟随 + 高斯噪声：曲线平滑，又不会机械重复
        self._valence += (target - self._valence) * 0.65
        self._valence = min(1.0, max(-1.0, self._valence + random.gauss(0, 0.02)))

        label, confidence = _classify(self._valence)
        return StateFrame(
            session_id=self.session_id,
            emotion=Emotion(label=label, confidence=confidence),
            window=f"{start:.1f}s - {end:.1f}s",
            source=self.name,
        )

    async def run(self) -> None:
        logger.info(
            "模拟器启动：窗口 %.1fs（%.1fHz），形态与感知系统 state.json 一致",
            self.window_seconds,
            self.sample_hz,
        )
        while True:
            self.bus.publish(self.next_frame())
            await asyncio.sleep(self.window_seconds)
