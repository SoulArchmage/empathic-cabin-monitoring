"""触发引擎单元测试：去抖、冷却、升级打断、恢复重置、聚合降频。"""

from __future__ import annotations

from app.config import Settings
from app.decision.trigger import TriggerEngine
from app.schemas import Cognitive, Emotion, StateFrame
from app.service.pipeline import aggregate_frames

SESSION = "s-test"
STEP = 0.1


def make_frame(
    index: int,
    label: str = "Negative",
    level: str = "High",
    score: float = 85.0,
) -> StateFrame:
    return StateFrame(
        ts=round(index * STEP, 3),
        session_id=SESSION,
        emotion=Emotion(label=label, confidence=0.9),  # type: ignore[arg-type]
        cognitive=Cognitive(level=level, score=score),  # type: ignore[arg-type]
    )


def make_engine(**overrides) -> TriggerEngine:
    base = dict(
        trigger_window=5,
        trigger_min_consec=3,
        trigger_ratio=0.6,
        trigger_cooldown_s=30.0,
        trigger_recovery_frames=4,
        trigger_escalate_after_s=8.0,
    )
    base.update(overrides)
    return TriggerEngine(Settings(**base))  # type: ignore[arg-type]


def feed(engine: TriggerEngine, start: int, count: int, **kwargs) -> list:
    events = []
    for index in range(start, start + count):
        event = engine.evaluate(make_frame(index, **kwargs))
        if event is not None:
            events.append(event)
    return events


def test_debounce_waits_for_consecutive_frames() -> None:
    engine = make_engine()
    # 只让情绪通道异常（认知负荷正常），隔离验证单路判定
    kwargs = dict(label="Negative", level="Low", score=30.0)
    # 窗口 5 帧 + 连续 3 帧 ⇒ 第 7 帧才应触发
    assert feed(engine, 0, 6, **kwargs) == []

    events = feed(engine, 6, 1, **kwargs)
    assert len(events) == 1
    assert events[0].kind == "negative_emotion"
    assert events[0].severity == "mild"
    assert events[0].stats is not None and events[0].stats.consec_abnormal == 3


def test_single_abnormal_frame_does_not_trigger() -> None:
    """偶发噪声不应触发干预——这是避免"幻觉式异常判定"的关键。"""
    engine = make_engine()
    events = []
    for index in range(30):
        label = "Negative" if index % 4 == 0 else "Positive"
        level = "High" if index % 4 == 0 else "Low"
        event = engine.evaluate(make_frame(index, label=label, level=level, score=20.0))
        if event:
            events.append(event)
    assert events == []


def test_cooldown_suppresses_repeat_trigger() -> None:
    engine = make_engine()
    first = feed(engine, 0, 7)
    assert len(first) == 1

    # 冷却期内即使异常持续也不应再次触发
    assert feed(engine, 7, 40) == []


def test_escalation_breaks_cooldown() -> None:
    """异常持续恶化时，严重度升级应立刻打断冷却期再次干预。"""
    engine = make_engine(trigger_escalate_after_s=0.5)
    first = feed(engine, 0, 7)
    assert len(first) == 1 and first[0].severity == "mild"

    events = feed(engine, 7, 10)
    assert any(e.severity == "severe" for e in events), "持续恶化未升级为 severe"


def test_recovery_resets_state_machine() -> None:
    engine = make_engine()
    assert len(feed(engine, 0, 7)) == 1

    events = feed(engine, 7, 20, label="Positive", level="Low", score=20.0)
    recovered = [e for e in events if e.kind == "recovered"]
    assert len(recovered) == 1
    assert recovered[0].severity == "normal"

    # 恢复后再次异常应能重新触发（证明状态机已重置）
    assert len(feed(engine, 27, 7)) == 1


def test_high_load_only_classification() -> None:
    engine = make_engine()
    events = feed(engine, 0, 7, label="Positive", level="High", score=88.0)
    assert len(events) == 1
    assert events[0].kind == "high_cognitive_load"


def test_aggregate_frames_downsamples() -> None:
    frames = [
        make_frame(0, label="Negative", level="High", score=80.0),
        make_frame(1, label="Negative", level="High", score=90.0),
        make_frame(2, label="Positive", level="Low", score=20.0),
    ]
    point = aggregate_frames(frames)
    assert point.ts == frames[-1].ts
    assert point.emotion.label == "Negative"          # 多数表决
    assert point.cognitive.score == round((80 + 90 + 20) / 3, 2)
    assert point.cognitive.level == "High"
