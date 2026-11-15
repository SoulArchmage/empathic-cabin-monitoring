"""数据库模型：状态采样（情绪 + 脑电负荷快照）与干预记录两张表。

设计取舍：感知系统每 2 秒给出一个窗口，但状态表只按 persist_hz（默认 1Hz）聚合落库，
既保证曲线形态，又避免 SQLite 长时间运行后膨胀。落库保留 window 字段，
使历史曲线上的每个点都能追溯到感知系统当时给出的窗口区间，便于答辩举证。

脑电负荷是第二路数据源（独立 EEG 服务），按"最新快照"随情绪帧一起落库：
load_level 为空串表示当时未接入 EEG 服务或尚无负荷数据。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StateFrameRecord(Base):
    """情绪窗口采样点（默认 1Hz 聚合）。"""

    __tablename__ = "state_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    emotion_label: Mapped[str] = mapped_column(String(16))
    emotion_confidence: Mapped[float] = mapped_column(Float)
    window: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="simulator")
    # 脑电负荷快照（第二路数据源）；缺省空串/0.0 = 当时没有负荷数据
    load_level: Mapped[str] = mapped_column(String(16), default="")
    load_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_state_session_ts", "session_id", "ts"),)


class InterventionRecord(Base):
    """系统干预日志：一次共情话术的完整决策上下文。"""

    __tablename__ = "interventions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    prompt_digest: Mapped[str] = mapped_column(Text, default="")
    speech: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(String(16))
    risk: Mapped[str] = mapped_column(String(16), default="low")
    degraded: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    audio_available: Mapped[int] = mapped_column(Integer, default=0)
    stats_json: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_intervention_session_ts", "session_id", "ts"),)
