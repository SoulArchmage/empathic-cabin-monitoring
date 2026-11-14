"""感知服务的输出契约：与疏导仓 state.json 完全一致的窗口级结果。

对外只暴露本系统真正需要的三个标签：

    {"emotion": "Neutral", "confidence": 0.7196, "window": "55.0s - 57.0s"}

情绪取值固定为 Neutral / Positive / Negative 三种。latency_ms / camera_ok 等字段
属于算法侧诊断信息，疏导仓会忽略，但保留在同一份 state.json 里便于自查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

EMOTION_LABELS = ("Positive", "Neutral", "Negative")


@dataclass(frozen=True, slots=True)
class Frame:
    """一个感知窗口的结果：情绪三分类 + 置信度 + 窗口区间。"""

    emotion: str        # Positive / Neutral / Negative
    confidence: float   # 0 ~ 1
    window_start: float  # 窗口起点（秒）
    window_end: float    # 窗口终点（秒）
    latency_ms: int = 0
    camera_ok: bool = True
    audio_ok: bool = True
    device: str = "cpu"
    gpu_name: str = ""
    precision: str = "fp32"

    def __post_init__(self) -> None:
        if self.emotion not in EMOTION_LABELS:
            raise ValueError(f"未知情绪标签：{self.emotion}")
        if self.window_end <= self.window_start:
            raise ValueError("窗口终点必须大于起点")
        # 越界值就地收敛，避免把非法帧推给疏导仓（对方会整帧丢弃）
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(self.confidence))))

    @property
    def window(self) -> str:
        return f"{self.window_start:.1f}s - {self.window_end:.1f}s"

    def to_state(self) -> dict:
        """state.json 的主体字段（不含 logs，logs 由服务端维护）。"""
        return {
            "emotion": self.emotion,
            "confidence": round(self.confidence, 4),
            "window": self.window,
            "latency_ms": int(self.latency_ms),
            "camera_ok": bool(self.camera_ok),
            "audio_ok": bool(self.audio_ok),
            "device": self.device,
            "gpu_name": self.gpu_name,
            "precision": self.precision,
        }

    def to_message(self) -> str:
        """序列化为疏导仓 external 数据源可直接消费的报文。"""
        return json.dumps(
            {"type": "state", "payload": self.to_state()},
            ensure_ascii=False,
        )
