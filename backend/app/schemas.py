"""数据契约（Single Source of Truth）。

本系统接入两路外部数据：

1. **情绪通道**（感知系统）：每 2 秒一个滑动窗口的三分类结果，与 state.json 对齐。
   本系统只消费三个标签：

       {"emotion": "Neutral", "confidence": 0.7196, "window": "55.0s - 57.0s"}

2. **脑电负荷通道**（独立 EEG 监测服务）：按申报书的 4 级脑电负荷等级输出等级与置信度：

       {"level": "High", "confidence": 0.92, "window": "12.0s - 16.0s"}

   等级固定为 Low / Medium / High / VeryHigh（低 / 中 / 高 / 极高）四种。

两路数据在 StateFrame 上汇合：情绪帧携带"最新的脑电负荷快照"（``load``），因此
WebSocket 下行、REST 查询、数据库落库、CSV 导出与前端 types/contract.ts 都只需要
同步一套字段。``load`` 缺省为 None —— 未接入 EEG 服务时契约与旧版本完全一致。

state.json 里的 latency_ms / camera_ok / audio_ok / device / precision / frame_count /
audio_samples / logs 属于感知侧诊断信息，本系统一律忽略。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# 情绪只有三类，顺序与感知系统一致：Positive / Neutral / Negative
EmotionLabel = Literal["Positive", "Negative", "Neutral"]
# 脑电认知负荷等级：与申报书的 4 级划分一致（低 / 中 / 高 / 极高）
LoadLevel = Literal["Low", "Medium", "High", "VeryHigh"]
LOAD_LEVELS: tuple[LoadLevel, ...] = ("Low", "Medium", "High", "VeryHigh")
# 会触发干预的负荷等级：高与极高（中/低只展示与记录）
HIGH_LOAD_LEVELS: tuple[LoadLevel, ...] = ("High", "VeryHigh")
LOAD_LEVEL_RANK: dict[str, int] = {"Low": 0, "Medium": 1, "High": 2, "VeryHigh": 3}
LOAD_LEVEL_ZH: dict[str, str] = {"Low": "低", "Medium": "中", "High": "高", "VeryHigh": "极高"}
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


class CognitiveLoad(BaseModel):
    """一路脑电负荷结果：等级 + 置信度 + EEG 侧窗口区间。

    ``window`` 与情绪窗口相互独立：申报书里的 EEG 系统是 4s 窗、1s 步长，
    而情绪感知是 2s 窗，因此这里单独保存，前端才能说明"这条负荷数据对应哪段脑电"。
    """

    level: LoadLevel
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    window: str = ""
    source: str = "external"


class StateFrame(BaseModel):
    """一个感知窗口的结果：情绪标签 + 置信度 + 窗口区间（与 state.json 对齐）。

    ``load`` 是该时刻最新的一条脑电负荷结果（由 LoadStore 合并进来），
    没有接入 EEG 服务时为 None。
    """

    ts: float = Field(default_factory=now_ts)
    session_id: str = Field(default_factory=new_session_id)
    emotion: Emotion
    load: CognitiveLoad | None = None
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
    # 脑电负荷通道的判据：最新等级/置信度，以及窗口内"高及以上"的占比
    load_level: str = ""
    load_confidence: float = 0.0
    load_high_ratio: float = 0.0


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
    "load",        # 脑电负荷等级更新（独立于情绪窗口的实时推送）
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
    # 脑电负荷快照（未接入 EEG 服务时为空串 / 0.0，历史页显示 "—"）
    load_level: str = ""
    load_confidence: float = 0.0


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
