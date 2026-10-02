"""外部数据源适配测试：报文归一化、中文键别名与 0 值保真。"""

from __future__ import annotations

from app.sources.external import normalize_payload

SESSION = "s-test"


def test_nested_payload_is_normalized() -> None:
    frame = normalize_payload(
        {
            "emotion": {"label": "Negative", "confidence": 0.86},
            "cognitive": {"level": "High", "score": 82},
        },
        SESSION,
    )
    assert frame is not None
    assert frame.emotion.label == "Negative"
    assert frame.emotion.confidence == 0.86
    assert frame.cognitive.level == "High"
    assert frame.cognitive.score == 82
    assert frame.source == "external"


def test_flat_payload_with_chinese_keys() -> None:
    frame = normalize_payload(
        {"情绪": "消极", "置信度": 0.9, "认知负荷": "高负荷", "认知负荷分值": 91},
        SESSION,
    )
    assert frame is not None
    assert frame.emotion.label == "Negative"
    assert frame.cognitive.level == "High"
    assert frame.cognitive.score == 91


def test_zero_values_are_preserved() -> None:
    """0.0 是合法取值，不能被默认值顶替（回归：曾用 or 链把 0.0 误判为缺失）。"""
    frame = normalize_payload(
        {
            "emotion": {"label": "Positive", "confidence": 0.0},
            "cognitive": {"level": "Low", "score": 0},
        },
        SESSION,
    )
    assert frame is not None
    assert frame.emotion.confidence == 0.0
    assert frame.cognitive.score == 0.0


def test_unknown_fields_fall_back_to_defaults() -> None:
    frame = normalize_payload({}, SESSION)
    assert frame is not None
    assert frame.emotion.label == "Neutral"
    assert frame.emotion.confidence == 0.7
    assert frame.cognitive.level == "Medium"
    assert frame.cognitive.score == 50.0


def test_unparsable_payload_returns_none() -> None:
    assert normalize_payload({"confidence": "不是数字"}, SESSION) is None
