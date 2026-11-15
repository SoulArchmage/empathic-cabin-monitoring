"""内置脑电负荷模拟：按申报书四类飞行任务的负荷节奏产出 4 级结果。

默认一个完整循环 96s，节奏与表 4.1 一致：

    0-24s   巡航      低
    24-42s  水平转弯  中
    42-60s  起飞      高
    60-74s  着陆      极高
    74-96s  落地滑行  低

每 1 秒产出一个 4s 窗口（4s 窗、1s 步长，与申报书的 EEG 处理参数一致）。
等级刚切换的前 3 秒置信度偏低，模拟"状态正在过渡、模型还不太确定"，
避免演示时出现"等级一跳变、置信度立刻 100%"的假象。
"""

from __future__ import annotations

import random

from .protocol import LoadFrame

# (起始秒, 等级, 飞行任务)
PHASES: tuple[tuple[float, str, str], ...] = (
    (0.0, "Low", "巡航"),
    (24.0, "Medium", "水平转弯"),
    (42.0, "High", "起飞"),
    (60.0, "VeryHigh", "着陆"),
    (74.0, "Low", "落地滑行"),
)
CYCLE_S = 96.0


class LoadSimulator:
    def __init__(
        self,
        *,
        window_s: float = 4.0,
        step_s: float = 1.0,
        speed: float = 1.0,
        level_override: str | None = None,
        seed: int | None = None,
    ) -> None:
        if window_s <= 0 or step_s <= 0:
            raise ValueError("窗口与步长必须为正数")
        self.window_s = float(window_s)
        self.step_s = float(step_s)
        self.speed = max(0.1, float(speed))
        self.level_override = level_override
        self._rng = random.Random(seed)
        self._elapsed = 0.0

    def next_frame(self) -> LoadFrame:
        start = self._elapsed
        self._elapsed += self.step_s * self.speed

        level, task, phase_start = "Low", "巡航", 0.0
        for offset, name, task_name in PHASES:
            if start % CYCLE_S >= offset:
                level, task, phase_start = name, task_name, offset
        if self.level_override:
            level = self.level_override
            task = f"固定 {level}"

        transition = (start % CYCLE_S - phase_start) < 3.0
        base = 0.66 if transition else 0.88
        confidence = min(0.98, max(0.55, self._rng.gauss(base, 0.035)))
        return LoadFrame(
            level=level,
            confidence=round(confidence, 4),
            window_start=start,
            window_end=start + self.window_s,
            task=task,
        )
