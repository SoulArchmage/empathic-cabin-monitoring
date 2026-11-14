"""数据契约（Single Source of Truth）。

本系统的输入只有一个感知通道：感知系统每 2 秒产出一个滑动窗口的三分类情绪结果，
与它写出的 state.json 完全对齐。其中本系统只消费三个标签：

    {"emotion": "Neutral", "confidence": 0.7196, "window": "55.0s - 57.0s"}

情绪取值固定为 Neutral / Positive / Negative 三种。state.json 里的
latency_ms / camera_ok / audio_ok / device / precision / frame_count / audio_samples / logs
属于感知侧诊断信息，本系统一律忽略，避免把"算法运行细节"混进业务契约。

WebSocket 下行、REST 查询、数据库落库、前端 types/contract.ts 四处共用同一套字段定义，
避免字段漂移。任何新增字段先改这里，再同步前端类型。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# 情绪只有三类，顺序与感知系统一致：Positive / Neutral / Negative
EmotionLabel = Literal["Positive", "Negative", "Neutral"]
Severity = Literal["normal", "mild", "severe"]
Strategy = Literal["呼吸引导", "共情复述", "环境调节", "轻量提问"]
Risk = Literal["low", "medium", "high"]
ScenarioName = Literal["calm", "anxiety_escalation", "recovery", "fluctuation"]


def now_ts() -> float:
    return round(time.time(), 3)


def new_session_id() -> str:
    return f"s-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------- #
# 状态帧
# --------------------------------------------------------------------------- #
class Emotion(BaseModel):
    label: EmotionLabel
    confidence: float = Field(ge=0.0, le=1.0)


class StateFrame(BaseModel):
    """一个感知窗口的结果：情绪标签 + 置信度 + 窗口区间（与 state.json 对齐）。"""

    ts: float = Field(default_factory=now_ts)
    session_id: str = Field(default_factory=new_session_id)
    emotion: Emotion
    window: str = ""            # 形如 "55.0s - 57.0s"，感知系统给什么就原样展示什么
    source: str = "simulator"


# --------------------------------------------------------------------------- #
# 决策与干预
# --------------------------------------------------------------------------- #
class WindowStats(BaseModel):
    """触发判定依据，写入干预记录便于事后复核与答辩举证。"""

    window: str = ""              # 触发时最新的感知窗口
    window_size: int = 0          # 参与统计的窗口数
    negative_ratio: float = 0.0
    positive_ratio: float = 0.0
    neutral_ratio: float = 0.0
    avg_confidence: float = 0.0
    negative_confidence: float = 0.0  # 窗口内 Negative 片段的平均置信度
    consec_abnormal: int = 0
    duration_s: float = 0.0


class TriggerEvent(BaseModel):
    ts: float = Field(default_factory=now_ts)
    session_id: str
    kind: str                      # negative_emotion / recovered / stable
    severity: Severity
    reason: str                    # 人类可读的触发原因
    stats: WindowStats


class Intervention(BaseModel):
    """一次干预记录 = 一条共情话术 + 其决策上下文。"""

    id: int | None = None
    ts: float = Field(default_factory=now_ts)
    session_id: str
    kind: str
    severity: Severity
    reason: str
    prompt_digest: str = ""        # Prompt 摘要（脱敏、截断），用于审计
    speech: str
    strategy: Strategy
    risk: Risk = "low"
    degraded: bool = False         # True = 走了规则兜底（LLM 不可用/校验未通过）
    provider: str = "mock"
    latency_ms: int = 0
    audio_available: bool = False
    stats: WindowStats | None = None


# --------------------------------------------------------------------------- #
# WebSocket 统一信封
# --------------------------------------------------------------------------- #
EnvelopeType = Literal[
    "meta",        # 连接建立后的元信息（会话号、provider 状态）
    "state",       # 状态帧
    "pending",     # 干预生成中（占位交互，掩盖 LLM 延迟）
    "intervention",
    "speak",       # TTS 结果：audio_b64 或 speak_text
    "asr_final",
    "scenario",    # 当前模拟
    "error",
]


class Envelope(BaseModel):
    type: EnvelopeType
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, type_: EnvelopeType, **payload: Any) -> "Envelope":
        return cls(type=type_, payload=payload)


class ScenarioRequest(BaseModel):
    name: ScenarioName


class ProviderStatus(BaseModel):
    name: str
    mode: Literal["mock", "real"]
    ready: bool
    # 最近一次真实调用的结果：ready 只代表"配置齐不齐"，
    # 额度耗尽 / Key 失效 / 上游 402 只能靠实际调用暴露
    last_ok: bool | None = None
    last_detail: str = ""
    last_at: float = 0.0  # epoch 秒


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    frame_rate_hz: float = 0.0
    active_clients: int = 0
    current_scenario: str = "calm"
    providers: dict[str, ProviderStatus] = Field(default_factory=dict)


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# 历史查询响应（REST）
# --------------------------------------------------------------------------- #
class SessionSummary(BaseModel):
    session_id: str
    started_at: float
    ended_at: float
    state_points: int
    interventions: int


class StatePoint(BaseModel):
    ts: float
    session_id: str
    emotion_label: EmotionLabel
    emotion_confidence: float
    window: str = ""
    source: str


class InterventionItem(BaseModel):
    id: int | None = None
    ts: float
    session_id: str
    kind: str
    severity: Severity
    reason: str
    prompt_digest: str = ""
    speech: str
    strategy: Strategy
    risk: Risk
    degraded: bool = False
    provider: str = "mock"
    latency_ms: int = 0
    audio_available: bool = False
    stats: WindowStats | None = None


class StatePage(PageMeta):
    items: list[StatePoint]


class InterventionPage(PageMeta):
    items: list[InterventionItem]


class SessionList(BaseModel):
    items: list[SessionSummary]
