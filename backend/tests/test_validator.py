"""抗幻觉校验单元测试：JSON 容错、禁词、超长、高危安全出口、兜底库覆盖。"""

from __future__ import annotations

from app.decision.validator import (
    FALLBACK_LIBRARY,
    SAFE_REFERRAL_SPEECH,
    parse_json_loose,
    validate,
)
from app.schemas import Severity, TriggerEvent, WindowStats

STATS = WindowStats(
    window_size=15,
    negative_ratio=0.8,
    high_load_ratio=0.2,
    consec_abnormal=12,
    avg_cognitive_score=58.0,
    duration_s=1.2,
)


def make_event(kind: str = "negative_emotion", severity: Severity = "mild") -> TriggerEvent:
    return TriggerEvent(
        ts=1.0, session_id="s-test", kind=kind, severity=severity,
        reason="测试触发", stats=STATS,
    )


def test_parse_strips_code_fence_and_noise() -> None:
    assert parse_json_loose('```json\n{"speech": "hi"}\n```') == {"speech": "hi"}
    assert parse_json_loose('好的，结果是：{"speech": "hi"} 以上') == {"speech": "hi"}
    assert parse_json_loose("完全不是 JSON") is None
    assert parse_json_loose("[1,2,3]") is None


def test_valid_output_passes() -> None:
    result = validate(
        '{"speech": "我陪着你，我们先慢慢呼吸。", "strategy": "呼吸引导", "risk": "medium"}',
        make_event(),
    )
    assert result.degraded is False
    assert result.strategy == "呼吸引导"
    assert result.risk == "medium"


def test_invalid_json_falls_back() -> None:
    result = validate("抱歉，我不想用JSON回答", make_event())
    assert result.degraded is True
    assert result.speech == FALLBACK_LIBRARY[("negative_emotion", "mild")][0]


def test_diagnostic_words_are_rejected() -> None:
    """模型擅自下诊断结论是典型幻觉，必须被拦截并降级。"""
    for banned in ("你可能有抑郁症", "建议你吃点药就好了", "你这是焦虑症的表现"):
        result = validate(f'{{"speech": "{banned}"}}', make_event())
        assert result.degraded is True, banned


def test_self_harm_words_are_rejected() -> None:
    result = validate('{"speech": "你是不是想过自杀？"}', make_event())
    assert result.degraded is True


def test_overlong_speech_is_clamped_at_sentence() -> None:
    long_text = "我注意到你现在很累。" + "我们先放慢呼吸，" * 12
    result = validate(f'{{"speech": "{long_text}"}}', make_event())
    assert result.degraded is False
    assert len(result.speech) <= 80
    assert result.speech.endswith(("。", "，"))


def test_severe_state_forces_safety_exit() -> None:
    """高危状态不让模型自由发挥，直接使用确定性转介模板。"""
    result = validate(
        '{"speech": "我们聊点开心的吧", "strategy": "轻量提问", "risk": "low"}',
        make_event(kind="combined", severity="severe"),
    )
    assert result.speech == SAFE_REFERRAL_SPEECH
    assert result.risk == "high"
    assert result.degraded is False


def test_severe_never_marked_low_risk() -> None:
    result = validate('{"speech": "慢慢来，我在。", "risk": "low"}', make_event(severity="severe"))
    assert result.risk != "low"


def test_fallback_library_covers_all_paths() -> None:
    """任何可能触发的组合都必须有确定性兜底话术，否则降级时会无话可说。"""
    required = [
        (kind, severity)
        for kind in ("negative_emotion", "high_cognitive_load", "combined")
        for severity in ("mild", "severe")
    ] + [("recovered", "normal")]

    for key in required:
        assert key in FALLBACK_LIBRARY, f"缺少兜底话术：{key}"
        speech, _ = FALLBACK_LIBRARY[key]
        assert 0 < len(speech) <= 80
