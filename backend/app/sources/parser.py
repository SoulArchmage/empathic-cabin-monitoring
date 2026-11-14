"""state.json 报文解析：本系统只理解其中的三个标签。

感知系统写出的 state.json 形如：

    {"emotion": "Neutral", "confidence": 0.7196, "window": "55.0s - 57.0s",
     "latency_ms": 2461, "camera_ok": true, "audio_ok": true,
     "device": "cpu", "gpu_name": "", "precision": "fp32",
     "frame_count": 300, "audio_samples": 1218816, "logs": [...]}

本模块只取 emotion / confidence / window，其余字段（含 logs）全部忽略。
同时兼容两种外层包装，便于直连感知服务或直读文件：

    裸 state.json                        {"emotion": ..., "confidence": ..., "window": ...}
    WebSocket 报文                        {"type": "state", "payload": {...}}
    历史嵌套形态（早期版本）               {"emotion": {"label": ..., "confidence": ...}, ...}

解析失败时返回 None 而不是抛异常：外部系统格式漂移不应拖垮实时链路。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import Emotion, EmotionLabel, StateFrame

# "55.0s - 57.0s" / "55.0 - 57.0" / "55s~57s" 都能识别，统一规范成前者
_WINDOW_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[-~－—至到]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?",
    re.IGNORECASE,
)

# 三分类的别名表：感知系统标准输出是英文三分类，别名只为兼容不同来源的写法
_EMOTION_ALIASES = {
    "positive": "Positive", "pos": "Positive", "积极": "Positive", "正向": "Positive",
    "negative": "Negative", "neg": "Negative", "消极": "Negative", "负向": "Negative",
    "neutral": "Neutral", "neu": "Neutral", "中性": "Neutral", "平静": "Neutral",
}

DEFAULT_CONFIDENCE = 0.7


def _pick(*candidates: Any, default: Any) -> Any:
    """按顺序取第一个"有值"的候选。

    必须用 ``is not None`` / ``""`` 判断而非 ``or``：置信度 0.0 是合法取值，
    用 ``or`` 会把它误判为缺失并替换成默认值。
    """
    for value in candidates:
        if value is not None and value != "":
            return value
    return default


def normalize_window(value: Any) -> str:
    """把各种窗口写法规范成 "55.0s - 57.0s"；无法识别时原样保留供前端展示。"""
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
        if start is not None and end is not None:
            try:
                return f"{float(start):.1f}s - {float(end):.1f}s"
            except (TypeError, ValueError):
                return ""
        return ""
    text = str(value).strip()
    match = _WINDOW_RE.search(text)
    if match:
        return f"{float(match.group(1)):.1f}s - {float(match.group(2)):.1f}s"
    return text


def unwrap(raw: Any) -> dict[str, Any] | None:
    """剥掉 {"type": "state", "payload": {...}} 外层；非状态报文返回 None。"""
    if not isinstance(raw, dict):
        return None
    if raw.get("type") not in (None, "state"):
        # perception 服务的 hello 等握手报文直接忽略
        return None
    payload = raw.get("payload")
    if isinstance(payload, dict):
        return payload
    return raw


def normalize_payload(
    raw: Any, session_id: str, source: str = "external"
) -> StateFrame | None:
    """把一条 state.json / 报文映射为 StateFrame；只读取三个标签。"""
    data = unwrap(raw)
    if data is None:
        return None

    emotion_raw = data.get("emotion")
    if isinstance(emotion_raw, dict):
        label = _pick(emotion_raw.get("label"), emotion_raw.get("emotion"), default=None)
        conf = _pick(emotion_raw.get("confidence"), default=None)
    else:
        # 兼容 "emotion_label" / 中文键等平面写法
        label = _pick(emotion_raw, data.get("emotion_label"), data.get("label"), data.get("情绪"), default=None)
        conf = _pick(data.get("confidence"), data.get("置信度"), default=None)

    raw_label = str(label).strip() if label is not None else ""
    norm_label: EmotionLabel = _EMOTION_ALIASES.get(
        raw_label.lower(), "Neutral"
    )
    # 已经是标准三分类时别名表也能命中（lower 后 key 一致）
    try:
        confidence = float(conf) if conf is not None else DEFAULT_CONFIDENCE
    except (TypeError, ValueError):
        return None

    window = normalize_window(
        _pick(data.get("window"), data.get("window_range"), data.get("窗口"), default="")
    )

    try:
        return StateFrame(
            session_id=session_id,
            emotion=Emotion(label=norm_label, confidence=confidence),
            window=window,
            source=source,
        )
    except (TypeError, ValueError):
        return None


def parse_state_json(text: str, session_id: str, source: str = "file") -> StateFrame | None:
    """直接解析 state.json 文本（文件模式用）。"""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return normalize_payload(raw, session_id, source=source)
