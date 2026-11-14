"""触发引擎单元测试：去抖、置信度门槛、冷却、升级打断、恢复重置、聚合降频。

时间语义：一个窗口 = 2 秒，窗口 4 个 ≈ 8 秒，连续 2 个 ≈ 4 秒。
"""

from __future__ import annotations

from app.config import Settings
from app.decision.trigger import TriggerEngine
from app.schemas import Emotion, StateFrame
from app.service.pipeline import aggregate_frames

SESSION = "s-test"
STEP = 2.0


def make_frame(
    index: int,
    label: str = "Negative",
    confidence: float = 0.9,
) -> StateFrame:
    return StateFrame(
        ts=round(index * STEP, 3),
        session_id=SESSION,
        emotion=Emotion(label=label, confidence=confidence),  # type: ignore[arg-type]
        window=f"{index * STEP:.1f}s - {(index + 1) * STEP:.1f}s",
    )


def make_engine(**overrides) -> TriggerEngine:
    base = dict(
        trigger_window=4,
        trigger_min_consec=2,
        trigger_ratio=0.5,
        trigger_min_confidence=0.55,
        trigger_cooldown_s=60.0,
        trigger_recovery_frames=2,
        trigger_escalate_after_s=6.0,
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


def test_debounce_waits_for_consecutive_windows() -> None:
    engine = make_engine()
    assert feed(engine, 0, 4, label="Neutral") == []
    # 第 3、4 个窗口开始出现 Negative（占比 50%），但连续不足 2 个窗口，不应触发
    assert feed(engine, 4, 2, label="Negative") == []

    events = feed(engine, 6, 1, label="Negative")
    assert len(events) == 1
    assert events[0].kind == "negative_emotion"
    assert events[0].severity == "mild"
    assert events[0].stats is not None and events[0].stats.consec_abnormal == 2


def test_isolated_negative_window_does_not_trigger() -> None:
    """偶发的一个负向窗口不应触发干预——这是避免"幻觉式异常判定"的关键。"""
    engine = make_engine()
    events = []
    for index in range(30):
        label = "Negative" if index % 5 == 0 else "Neutral"
        event = engine.evaluate(make_frame(index, label=label))
        if event:
            events.append(event)
    assert events == []


def test_cooldown_suppresses_repeat_trigger() -> None:
    engine = make_engine()
    # 负向/中性交替：窗口占比稳定在 50%，只会得到 mild，不会因升级打断冷却
    events = []
    for index in range(20):
        label = "Negative" if index % 2 == 0 else "Neutral"
        event = engine.evaluate(make_frame(index, label=label, confidence=0.8))
        if event:
            events.append(event)
    assert len(events) == 1
    assert events[0].severity == "mild"


def test_escalation_breaks_cooldown() -> None:
    """异常持续恶化时，严重度升级应立刻打断冷却期再次干预。"""
    engine = make_engine(trigger_escalate_after_s=2.0)
    pattern = ["Negative", "Neutral", "Negative", "Neutral", "Negative"]
    first = []
    for index, label in enumerate(pattern):
        event = engine.evaluate(make_frame(index, label=label, confidence=0.9))
        if event:
            first.append(event)
    assert len(first) == 1 and first[0].severity == "mild"

    events = feed(engine, 5, 4, label="Negative", confidence=0.9)
    assert any(e.severity == "severe" for e in events), "持续恶化未升级为 severe"


def test_recovery_resets_state_machine() -> None:
    engine = make_engine()
    feed(engine, 0, 4, label="Neutral")
    assert len(feed(engine, 4, 3, label="Negative")) == 1

    events = feed(engine, 7, 6, label="Positive")
    recovered = [e for e in events if e.kind == "recovered"]
    assert len(recovered) == 1
    assert recovered[0].severity == "normal"

    # 恢复后再次异常应能重新触发（证明状态机已重置）
    assert feed(engine, 13, 4, label="Neutral") == []
    assert len(feed(engine, 17, 4, label="Negative")) >= 1


def test_low_confidence_negative_is_not_trusted() -> None:
    """算法自己都不确定的负向（置信度低于门槛）不作为干预依据。"""
    engine = make_engine()
    assert feed(engine, 0, 12, label="Negative", confidence=0.4) == []

    # 置信度恢复后立即可以触发
    events = feed(engine, 12, 2, label="Negative", confidence=0.9)
    assert len(events) == 1
    assert events[0].kind == "negative_emotion"


def test_snapshot_event_for_voice_context() -> None:
    engine = make_engine()
    feed(engine, 0, 4, label="Negative", confidence=0.85)
    event = engine.snapshot_event()
    assert event.kind == "negative_emotion"
    assert event.stats is not None
    assert event.stats.negative_ratio == 1.0
    assert event.stats.negative_confidence == 0.85

    stable_engine = make_engine()
    feed(stable_engine, 0, 4, label="Neutral", confidence=0.7)
    stable = stable_engine.snapshot_event()
    assert stable.kind == "stable"
    assert stable.severity == "normal"


def test_aggregate_frames_downsamples() -> None:
    frames = [
        make_frame(0, label="Negative", confidence=0.8),
        make_frame(1, label="Negative", confidence=0.9),
        make_frame(2, label="Positive", confidence=0.7),
    ]
    point = aggregate_frames(frames)
    assert point.ts == frames[-1].ts
    assert point.emotion.label == "Negative"          # 多数表决
    assert point.emotion.confidence == round((0.8 + 0.9 + 0.7) / 3, 3)
    assert point.window == frames[-1].window
