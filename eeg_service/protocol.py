"""脑电负荷服务的输出契约：与疏导仓 load 数据源的解析规则一一对应。

一条结果只暴露疏导仓真正需要的三个标签：

    {"level": "High", "confidence": 0.92, "window": "12.0s - 16.0s"}

等级固定为 Low / Medium / High / VeryHigh（低 / 中 / 高 / 极高），与申报书表 4.1 的
四类飞行任务一一对应。``task`` 是本服务附加的人工核对字段，疏导仓会忽略它。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

LOAD_LEVELS = ("Low", "Medium", "High", "VeryHigh")
LEVEL_ZH = {"Low": "低", "Medium": "中", "High": "高", "VeryHigh": "极高"}
# 申报书表 4.1：飞行任务 → 认知负荷等级
TASK_LEVELS = {
    "巡航": "Low",
    "水平转弯": "Medium",
    "起飞": "High",
    "着陆": "VeryHigh",
}


@dataclass(frozen=True, slots=True)
class LoadFrame:
    """一个脑电窗口的负荷结果。"""

    level: str            # Low / Medium / High / VeryHigh
    confidence: float     # 0 ~ 1
    window_start: float   # 窗口起点（秒）
    window_end: float     # 窗口终点（秒）
    task: str = ""        # 对应飞行任务（仅本服务用于自检/展示）
    source: str = "eeg"

    def __post_init__(self) -> None:
        if self.level not in LOAD_LEVELS:
            raise ValueError(f"未知负荷等级：{self.level}")
        if self.window_end <= self.window_start:
            raise ValueError("窗口终点必须大于起点")
        # 越界值就地收敛，避免把非法帧推给疏导仓（对方会整帧丢弃）
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(self.confidence))))

    @property
    def window(self) -> str:
        return f"{self.window_start:.1f}s - {self.window_end:.1f}s"

    def to_state(self) -> dict:
        return {
            "level": self.level,
            "confidence": round(self.confidence, 4),
            "window": self.window,
            "task": self.task,
            "source": self.source,
        }

    def to_message(self) -> str:
        """序列化为疏导仓 load 数据源可直接消费的报文。"""
        return json.dumps(
            {"type": "load", "payload": self.to_state()},
            ensure_ascii=False,
        )
