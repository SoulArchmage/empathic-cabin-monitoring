"""脑电负荷等级报文解析（第二路数据源）。

EEG 监测服务按申报书的 4 级划分输出结果，本模块把各种可能的写法归一到
``CognitiveLoad(level / confidence / window)``：

    {"level": "High", "confidence": 0.92, "window": "12.0s - 16.0s"}
    {"type": "load", "payload": {"level": "极高", "confidence": 92, "window": "12-16"}}
    {"cognitive": {"level": "Medium", "score": 61.5}, "ts": 1794668035.9}
    {"load_level": "Low", "load_confidence": 0.81, "窗口": "0.0s - 4.0s"}

兼容点（都来自真实对接时的常见漂移）：
- 中英文等级名：低/中/高/极高、Low/Medium/High/VeryHigh、L/M/H/VH、1~4；
- 置信度 0~1 与百分数 0~100 混用（>1 自动除以 100）；
- 旧版感知服务的 3 级写法（Low/Medium/High）与 0~100 的 ``score``；
- 外层包装：裸 JSON、``{"type": "load", "payload": {...}}``、嵌在 state.json 的 ``load`` 字段。

解析失败返回 None 而不是抛异常：外部系统格式漂移不应拖垮实时链路。
"""

from __future__ import annotations

import json
from typing import Any

from ..schemas import CognitiveLoad, LoadLevel
from .parser import _pick, normalize_window

# 等级别名表：比较前先做 _compact（去空格/下划线/连字符、"负荷""等级"后缀）
_LEVEL_ALIASES: dict[str, LoadLevel] = {
    "low": "Low", "l": "Low", "低": "Low", "轻度": "Low", "低负荷": "Low",
    "medium": "Medium", "mid": "Medium", "m": "Medium", "中": "Medium",
    "中等": "Medium", "中度": "Medium", "中负荷": "Medium",
    "high": "High", "h": "High", "高": "High", "重度": "High", "高负荷": "High",
    "veryhigh": "VeryHigh", "vh": "VeryHigh", "极高": "VeryHigh",
    "很高": "VeryHigh", "超载": "VeryHigh", "极高负荷": "VeryHigh",
}
# 纯数字等级（1 最低、4 最高）：部分算法侧直接给类别编号
_NUMERIC_LEVELS: dict[int, LoadLevel] = {1: "Low", 2: "Medium", 3: "High", 4: "VeryHigh"}

# 嵌套节点的候选键：EEG 服务可能把负荷结果放在 load / cognitive / eeg 之下
_NODE_KEYS = ("load", "cognitive", "cognitive_load", "eeg", "workload")
_LEVEL_KEYS = ("level", "load_level", "cognitive_level", "grade", "等级", "负荷等级")
_CONF_KEYS = ("confidence", "load_confidence", "probability", "置信度", "score")
_WINDOW_KEYS = ("window", "window_range", "窗口", "脑电窗口")


def _compact(value: Any) -> str:
    """把等级写法压成别名表的 key：去空白与分隔符，去掉"负荷/等级"后缀。"""
    text = str(value).strip().lower()
    for token in (" ", "_", "-", "/", "\u3000"):
        text = text.replace(token, "")
    for suffix in ("负荷等级", "负荷", "等级"):
        text = text.replace(suffix, "")
    return text


def normalize_level(value: Any) -> LoadLevel | None:
    """把任意等级写法归一为 4 级之一；无法识别返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        return _NUMERIC_LEVELS.get(int(value))
    key = _compact(value)
    if key in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[key]
    if key.isdigit():
        return _NUMERIC_LEVELS.get(int(key))
    return None


def normalize_confidence(value: Any) -> float | None:
    """置信度统一到 0~1；百分数写法（92 / "92%"）自动换算。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if number > 1.0:
        number /= 100.0
    return round(min(1.0, max(0.0, number)), 4)


def _unwrap(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("type") not in (None, "state", "load"):
        # EEG 服务的 hello / 心跳等握手报文直接忽略
        return None
    payload = raw.get("payload")
    return payload if isinstance(payload, dict) else raw


def parse_load(raw: Any, source: str = "external") -> CognitiveLoad | None:
    """把一条 EEG 报文映射为 CognitiveLoad；等级缺失或无法识别时返回 None。"""
    data = _unwrap(raw)
    if data is None:
        return None

    base: dict[str, Any] = data
    for key in _NODE_KEYS:
        node = data.get(key)
        if isinstance(node, dict):
            base = node
            break
        if node is not None and not isinstance(node, dict):
            # 形如 {"load": "High", ...} 的扁平写法
            data = {**data, "load_level": node}
            break

    level = normalize_level(
        _pick(*(base.get(key) for key in _LEVEL_KEYS), data.get("load"), default=None)
    )
    if level is None:
        return None

    confidence = normalize_confidence(
        _pick(*(base.get(key) for key in _CONF_KEYS), default=None)
    )
    window = normalize_window(
        _pick(*(base.get(key) for key in _WINDOW_KEYS), default="")
    )
    return CognitiveLoad(
        level=level,
        confidence=confidence if confidence is not None else 0.0,
        window=window,
        source=source,
    )


def parse_load_json(text: str, source: str = "file") -> CognitiveLoad | None:
    """解析 JSON 文本（file 模式用）。"""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parse_load(raw, source=source)
