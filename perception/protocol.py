"""感知服务的输出契约：与疏导仓后端 StateFrame 对齐的标签帧。

字段定义与 backend/app/schemas.py 保持一致，因此疏导仓侧切换到
SOURCE_MODE=external 后无需任何代码改动即可直接消费。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

EMOTION_LABELS = ("Positive", "Neutral", "Negative")
COGNITIVE_LEVELS = ("Low", "Medium", "High")


@dataclass(frozen=True, slots=True)
class Frame:
    """一帧感知结果：情绪标签 + 认知负荷等级。"""

    label: str          # Positive / Neutral / Negative
    confidence: float   # 0 ~ 1
    level: str          # Low / Medium / High
    score: float        # 0 ~ 100

    def __post_init__(self) -> None:
        if self.label not in EMOTION_LABELS:
            raise ValueError(f"未知情绪标签：{self.label}")
        if self.level not in COGNITIVE_LEVELS:
            raise ValueError(f"未知负荷等级：{self.level}")
        # 越界值就地收敛，避免把非法帧推给疏导仓（对方会整帧丢弃）
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(self.confidence))))
        object.__setattr__(self, "score", min(100.0, max(0.0, float(self.score))))

    def to_message(self) -> str:
        """序列化为疏导仓 external 数据源可直接消费的报文。"""
        return json.dumps(
            {
                "type": "state",
                "payload": {
                    "emotion": {
                        "label": self.label,
                        "confidence": round(self.confidence, 3),
                    },
                    "cognitive": {
                        "level": self.level,
                        "score": round(self.score, 2),
                    },
                },
            },
            ensure_ascii=False,
        )