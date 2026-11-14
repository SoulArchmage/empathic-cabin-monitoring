"""状态输入契约测试：state.json 只取 emotion / confidence / window 三个标签。

用例直接使用感知系统真实写出的字段形态（含 latency_ms / camera_ok / logs 等），
验证"多余字段被忽略、三个标签被正确解析"。
"""

from __future__ import annotations

import json

from app.config import Settings
from app.sources.file import FileSource
from app.sources.parser import normalize_payload, normalize_window, parse_state_json
from app.sources.simulator import SimulatorSource

SESSION = "s-test"

# 与感知系统写出的 state.json 同形（logs 已截断）
STATE_JSON = {
    "emotion": "Neutral",
    "confidence": 0.7196246385574341,
    "window": "55.0s - 57.0s",
    "latency_ms": 2461,
    "camera_ok": True,
    "audio_ok": True,
    "device": "cpu",
    "gpu_name": "",
    "precision": "fp32",
    "frame_count": 300,
    "audio_samples": 1218816,
    "logs": [
        {"time": "15:05:46", "message": "Window#55 [55.0s - 57.0s] 2461ms Emotion=Neutral"},
        {"time": "15:04:44", "message": "正在加载情绪识别模型"},
    ],
}


def test_state_json_uses_only_three_labels() -> None:
    frame = parse_state_json(json.dumps(STATE_JSON, ensure_ascii=False), SESSION)
    assert frame is not None
    assert frame.emotion.label == "Neutral"
    assert abs(frame.emotion.confidence - 0.7196) < 1e-3
    assert frame.window == "55.0s - 57.0s"
    assert frame.source == "file"
    # 多余的感知侧诊断字段不会进入契约
    payload = frame.model_dump()
    assert set(payload) == {"ts", "session_id", "emotion", "window", "source"}


def test_websocket_envelope_is_unwrapped() -> None:
    frame = normalize_payload(
        {
            "type": "state",
            "payload": {"emotion": "Negative", "confidence": 0.86, "window": "1.0s - 3.0s"},
        },
        SESSION,
    )
    assert frame is not None
    assert frame.emotion.label == "Negative"
    assert frame.emotion.confidence == 0.86
    assert frame.source == "external"


def test_non_state_message_is_ignored() -> None:
    assert normalize_payload({"type": "hello", "payload": {}}, SESSION) is None


def test_chinese_and_legacy_emotion_keys() -> None:
    frame = normalize_payload(
        {"情绪": "消极", "置信度": 0.9, "window": "7.0s - 9.0s"}, SESSION
    )
    assert frame is not None
    assert frame.emotion.label == "Negative"

    legacy = normalize_payload(
        {"emotion": {"label": "Positive", "confidence": 0.8}, "window": "9.0s - 11.0s"},
        SESSION,
    )
    assert legacy is not None
    assert legacy.emotion.label == "Positive"


def test_zero_confidence_is_preserved() -> None:
    """0.0 是合法取值，不能被默认值顶替（回归：曾用 or 链把 0.0 误判为缺失）。"""
    frame = normalize_payload(
        {"emotion": "Positive", "confidence": 0.0, "window": "1.0s - 3.0s"}, SESSION
    )
    assert frame is not None
    assert frame.emotion.confidence == 0.0


def test_missing_fields_fall_back_to_neutral() -> None:
    frame = normalize_payload({}, SESSION)
    assert frame is not None
    assert frame.emotion.label == "Neutral"
    assert frame.emotion.confidence == 0.7
    assert frame.window == ""


def test_unparsable_confidence_returns_none() -> None:
    assert normalize_payload({"confidence": "不是数字"}, SESSION) is None


def test_window_variants_are_normalized() -> None:
    assert normalize_window("55.0s - 57.0s") == "55.0s - 57.0s"
    assert normalize_window("55-57") == "55.0s - 57.0s"
    assert normalize_window("55s~57s") == "55.0s - 57.0s"
    assert normalize_window({"start": 55, "end": 57}) == "55.0s - 57.0s"
    assert normalize_window("第 55 秒") == "第 55 秒"  # 无法识别时原样保留


def test_simulator_emits_state_json_shape() -> None:
    sim = SimulatorSource(bus=None, cfg=Settings(source_mode="simulator"))  # type: ignore[arg-type]
    frames = [sim.next_frame() for _ in range(5)]

    assert [f.window for f in frames] == [
        "0.0s - 2.0s",
        "2.0s - 4.0s",
        "4.0s - 6.0s",
        "6.0s - 8.0s",
        "8.0s - 10.0s",
    ]
    for frame in frames:
        assert frame.emotion.label in {"Positive", "Negative", "Neutral"}
        assert 0.5 <= frame.emotion.confidence <= 1.0
        assert frame.source == "simulator"


async def test_file_source_dedupes_by_window(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"emotion": "Neutral", "confidence": 0.72, "window": "0.0s - 2.0s"}),
        encoding="utf-8",
    )
    source = FileSource(
        bus=None,  # type: ignore[arg-type]
        cfg=Settings(source_mode="file", state_file=str(path)),
    )

    first = source._read()
    assert first is not None and first.emotion.label == "Neutral"
    assert source._is_new(first) is True

    # 同一个窗口的重复读取不应重复发布
    again = source._read()
    assert again is not None
    assert source._is_new(again) is False

    path.write_text(
        json.dumps({"emotion": "Negative", "confidence": 0.81, "window": "2.0s - 4.0s"}),
        encoding="utf-8",
    )
    third = source._read()
    assert third is not None
    assert third.emotion.label == "Negative"
    assert source._is_new(third) is True


async def test_file_source_tolerates_partial_write(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"emotion": "Neg', encoding="utf-8")  # 感知侧正在写入
    source = FileSource(
        bus=None,  # type: ignore[arg-type]
        cfg=Settings(source_mode="file", state_file=str(path)),
    )
    assert source._read() is None
