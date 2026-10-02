"""数据库模型：状态曲线 + 干预记录两张表。

设计取舍：状态帧以 10Hz 采集，但只按 1Hz 聚合落库。原因是任务书要求"记录状态变化曲线"，
而 10Hz 原始数据长时间运行会让 SQLite 迅速膨胀，1Hz 聚合已足够刻画曲线形态。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StateFrameRecord(Base):
    """状态曲线采样点（1Hz 聚合）。"""

    __tablename__ = "state_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    emotion_label: Mapped[str] = mapped_column(String(16))
    emotion_confidence: Mapped[float] = mapped_column(Float)
    cognitive_level: Mapped[str] = mapped_column(String(16))
    cognitive_score: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="simulator")

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
