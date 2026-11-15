"""第二路数据源（脑电认知负荷等级）测试：解析、快照合并、触发与兜底。

覆盖三条最容易出错的边界：
1. 外部写法漂移（中文等级、百分数置信度、3 级旧写法、各种外层包装）都能归一；
2. 负荷是"状态量"：只保留最新值，贴在情绪帧上时不得覆盖帧自带的负荷；
3. 高/极高负荷能触发干预，但只走 mild + 呼吸引导，绝不触发 severe 的安全出口（12356 热线）。
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.decision.trigger import TriggerEngine
from app.decision.validator import fallback_for
from app.load_store import LoadStore
from app.schemas import CognitiveLoad, Emotion, StateFrame
from app.service.pipeline import aggregate_frames
from app.sources.load_parser import normalize_confidence, normalize_level, parse_load
from app.sources.load_source import LoadFileSource, LoadSimulatorSource

SESSION = "s-load-test"


def make_frame(index: int, level: str | None = "High", confidence: float = 0.9) -> StateFrame:
    load = (
        CognitiveLoad(level=level, confidence=confidence, window=f"{index}.0s - {index + 4}.0s")
        if level is not None
        else None
    )
    return StateFrame(
        ts=round(index * 2.0, 3),
        session_id=SESSION,
        emotion=Emotion(label="Neutral", confidence=0.7),
        load=load,
        window=f"{index * 2.0:.1f}s - {(index + 1) * 2.0:.1f}s",
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
        load_trigger_high_consec=2,
        load_trigger_min_confidence=0.6,
        load_trigger_cooldown_s=90.0,
    )
    base.update(overrides)
    return TriggerEngine(Settings(**base))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Low", "Low"),
        ("低", "Low"),
        ("中等", "Medium"),
        ("High", "High"),
        ("极高", "VeryHigh"),
        ("very high", "VeryHigh"),
        ("VH", "VeryHigh"),
        (4, "VeryHigh"),
        (2, "Medium"),
        ("3", "High"),
    ],
)
def test_level_aliases(raw, expected) -> None:
    assert normalize_level(raw) == expected


def test_unknown_level_is_rejected() -> None:
    assert normalize_level("崩溃") is None
    assert parse_load({"level": "unknown"}) is None


def test_percentage_and_score_confidence() -> None:
    assert normalize_confidence("92%") == 0.92
    assert normalize_confidence(0.86) == 0.86
    assert normalize_confidence(61.5) == 0.615
    # score（0~100 连续分）作为置信度的兜底来源
    load = parse_load({"cognitive": {"level": "Medium", "score": 61.5}})
    assert load is not None and load.confidence == 0.615


def test_wrapped_flat_and_nested_payloads() -> None:
    wrapped = parse_load(
        {
            "type": "load",
            "payload": {"level": "High", "confidence": 0.91, "window": "12-16"},
        }
    )
    assert wrapped is not None
    assert wrapped.level == "High" and wrapped.window == "12.0s - 16.0s"

    flat = parse_load({"load_level": "VeryHigh", "load_confidence": 0.88, "窗口": "20s~24s"})
    assert flat is not None
    assert flat.level == "VeryHigh" and flat.window == "20.0s - 24.0s"

    # 旧的感知服务 3 级写法（嵌在 state.json 里）
    legacy = parse_load({"type": "state", "payload": {"cognitive": {"level": "High"}}})
    assert legacy is not None and legacy.level == "High"

    # 握手/心跳报文直接忽略
    assert parse_load({"type": "hello", "payload": {"level": "High"}}) is None


# --------------------------------------------------------------------------- #
# 快照合并
# --------------------------------------------------------------------------- #
def test_store_attaches_latest_load_and_notifies() -> None:
    received: list[CognitiveLoad] = []
    store = LoadStore()
    store.add_listener(received.append)

    load = CognitiveLoad(level="High", confidence=0.9)
    store.update(load)
    assert received == [load]

    frame = make_frame(0, level=None)
    assert frame.load is None
    store.attach(frame)
    assert frame.load is load
    assert store.level == "High"


def test_store_does_not_override_frame_own_load() -> None:
    store = LoadStore()
    store.update(CognitiveLoad(level="Low", confidence=0.7))
    own = CognitiveLoad(level="VeryHigh", confidence=0.95)
    frame = make_frame(0, level=None)
    frame.load = own

    store.attach(frame)
    assert frame.load is own, "报文自带的负荷优先于全局快照"


def test_aggregate_keeps_last_load() -> None:
    frames = [make_frame(0, level="Medium", confidence=0.7), make_frame(1, level="High")]
    point = aggregate_frames(frames)
    assert point.load is not None and point.load.level == "High"

    without = aggregate_frames([make_frame(0, level=None)])
    assert without.load is None


# --------------------------------------------------------------------------- #
# 内置模拟与文件源
# --------------------------------------------------------------------------- #
def test_simulator_climbs_through_four_levels() -> None:
    sim = LoadSimulatorSource(LoadStore(), Settings(load_source_mode="simulator"))
    seen: list[str] = []
    for elapsed in (0.0, 30.0, 50.0, 65.0):
        sim._elapsed = elapsed  # 直接拨到指定相位，避免测试依赖真实时钟
        seen.append(sim.next_load().level)
    assert seen == ["Low", "Medium", "High", "VeryHigh"]


def test_simulator_windows_follow_eeg_rhythm() -> None:
    sim = LoadSimulatorSource(LoadStore(), Settings(load_window_s=4.0, load_step_s=1.0))
    first = sim.next_load()
    second = sim.next_load()
    assert first.window == "0.0s - 4.0s"
    assert second.window == "1.0s - 5.0s"


def test_file_source_reads_dumped_json(tmp_path) -> None:
    path = tmp_path / "load.json"
    path.write_text(
        json.dumps({"level": "极高", "confidence": 0.93, "window": "8.0s - 12.0s"}),
        encoding="utf-8",
    )
    source = LoadFileSource(
        LoadStore(), Settings(load_source_mode="file", load_state_file=str(path))
    )
    load = source._read()
    assert load is not None
    assert load.level == "VeryHigh" and load.confidence == 0.93

    assert source._is_new(load) is True
    assert source._is_new(load) is False  # 同一窗口重复推送不重复更新

    path.write_text('{"level": "Hi', encoding="utf-8")  # EEG 侧正在写入
    assert source._read() is None


# --------------------------------------------------------------------------- #
# 触发
# --------------------------------------------------------------------------- #
def test_high_load_triggers_after_two_windows() -> None:
    engine = make_engine()
    assert engine.evaluate(make_frame(0)) is None  # 连续 1/2，继续观察
    event = engine.evaluate(make_frame(1))
    assert event is not None
    assert event.kind == "high_load"
    assert event.severity == "mild"
    assert "脑电认知负荷" in event.reason
    assert event.stats is not None
    assert event.stats.load_level == "High"
    assert event.stats.load_high_ratio == 1.0


def test_low_confidence_or_low_level_never_triggers() -> None:
    engine = make_engine()
    for index in range(10):
        assert engine.evaluate(make_frame(index, level="High", confidence=0.4)) is None
        assert engine.evaluate(make_frame(index, level="Medium", confidence=0.99)) is None


def test_load_cooldown_suppressed_until_level_escalates() -> None:
    engine = make_engine()
    assert engine.evaluate(make_frame(0)) is None
    first = engine.evaluate(make_frame(1))
    assert first is not None

    # 冷却期内维持同一等级：不再重复打扰
    for index in range(2, 8):
        assert engine.evaluate(make_frame(index, level="High")) is None

    # 高 → 极高 属于恶化，立刻打断冷却
    assert engine.evaluate(make_frame(8, level="VeryHigh")) is None
    escalated = engine.evaluate(make_frame(9, level="VeryHigh"))
    assert escalated is not None and escalated.stats is not None
    assert escalated.stats.load_level == "VeryHigh"


def test_load_trigger_never_uses_safety_exit() -> None:
    """负荷过高是"累"，不是心理危机：兜底话术不能出现 12356 热线。"""
    engine = make_engine()
    engine.evaluate(make_frame(0, level="VeryHigh"))
    event = engine.evaluate(make_frame(1, level="VeryHigh"))
    assert event is not None
    fallback = fallback_for(event)
    assert "12356" not in fallback.speech
    assert fallback.strategy == "呼吸引导"


def test_snapshot_event_reports_high_load_when_emotion_is_calm() -> None:
    engine = make_engine()
    for index in range(4):
        engine.evaluate(make_frame(index, level="High"))
    snapshot = engine.snapshot_event()
    assert snapshot.kind == "high_load"
    assert snapshot.severity == "mild"
    assert snapshot.stats is not None and snapshot.stats.load_level == "High"
