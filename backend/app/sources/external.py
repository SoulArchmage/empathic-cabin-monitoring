"""外部数据源适配：对接真实的"情绪识别系统"与"认知负荷监测系统"。

当前工作区无对接目标，故此处提供通用实现骨架：
从 EXTERNAL_STATE_URL 建立 WebSocket 长连接，把外部报文规整为统一的 StateFrame。
断线后指数退避重连，保证系统对外部抖动具备韧性。

启用方式：.env 中设置 SOURCE_MODE=external 与 EXTERNAL_STATE_URL=ws://...
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..schemas import Cognitive, CognitiveLevel, Emotion, EmotionLabel, StateFrame
from .base import StateSource

logger = logging.getLogger(__name__)

_EMOTION_ALIASES = {
    "positive": "Positive", "pos": "Positive", "正向": "Positive", "积极": "Positive",
    "negative": "Negative", "neg": "Negative", "负向": "Negative", "消极": "Negative",
    "neutral": "Neutral", "neu": "Neutral", "中性": "Neutral",
}
_LEVEL_ALIASES = {
    "low": "Low", "低": "Low", "低负荷": "Low",
    "medium": "Medium", "mid": "Medium", "中": "Medium", "中负荷": "Medium",
    "high": "High", "高": "High", "高负荷": "High",
}


def _pick(*candidates: Any, default: Any) -> Any:
    """按顺序取第一个"有值"的候选。

    必须用 ``is not None`` / ``""`` 判断而非 ``or``：置信度 0.0 与负荷分值 0.0
    都是合法取值，用 ``or`` 会把它们误判为缺失并替换成默认值。
    """
    for value in candidates:
        if value is not None and value != "":
            return value
    return default


def normalize_payload(raw: dict[str, Any], session_id: str) -> StateFrame | None:
    """把外部系统的报文映射为 StateFrame。

    兼容两种常见形态：
      {"emotion": {"label": "Negative", "confidence": 0.86},
       "cognitive": {"level": "High", "score": 82}}
      {"emotion_label": "Negative", "confidence": 0.86,
       "cognitive_level": "High", "cognitive_score": 82}
    """
    emotion_raw = raw.get("emotion") or {}
    cognitive_raw = raw.get("cognitive") or {}

    label = _pick(
        emotion_raw.get("label") if isinstance(emotion_raw, dict) else emotion_raw,
        raw.get("emotion_label"),
        raw.get("情绪"),
        default=None,
    )
    conf = _pick(
        emotion_raw.get("confidence") if isinstance(emotion_raw, dict) else None,
        raw.get("confidence"),
        raw.get("置信度"),
        default=0.7,
    )
    level = _pick(
        cognitive_raw.get("level") if isinstance(cognitive_raw, dict) else cognitive_raw,
        raw.get("cognitive_level"),
        raw.get("认知负荷"),
        default=None,
    )
    score = _pick(
        cognitive_raw.get("score") if isinstance(cognitive_raw, dict) else None,
        raw.get("cognitive_score"),
        raw.get("认知负荷分值"),
        default=50.0,
    )

    norm_label: EmotionLabel = _EMOTION_ALIASES.get(str(label).strip().lower(), "Neutral")
    norm_level: CognitiveLevel = _LEVEL_ALIASES.get(str(level).strip().lower(), "Medium")

    try:
        return StateFrame(
            session_id=session_id,
            emotion=Emotion(label=norm_label, confidence=float(conf)),  # type: ignore[arg-type]
            cognitive=Cognitive(level=norm_level, score=float(score)),  # type: ignore[arg-type]
            source="external",
        )
    except (TypeError, ValueError) as exc:
        logger.warning("外部报文无法解析为状态帧：%s（原始：%s）", exc, raw)
        return None


class ExternalSource(StateSource):
    name = "external"

    def __init__(
        self,
        bus: EventBus,
        sample_hz: float = 10.0,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(bus, sample_hz)
        self.cfg = cfg or default_settings
        self.url = self.cfg.external_state_url
        self._backoff = 1.0

    async def run(self) -> None:
        if not self.url:
            raise RuntimeError("SOURCE_MODE=external 但未配置 EXTERNAL_STATE_URL")
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    logger.info("已接入外部状态系统 %s", self.url)
                    self._backoff = 1.0
                    async for message in ws:
                        frame = self._decode(message)
                        if frame:
                            self.bus.publish(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 外部依赖需整体兜底
                logger.warning(
                    "外部状态系统连接异常：%s，%.1fs 后重连", exc, self._backoff
                )
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

    def _decode(self, message: str | bytes) -> StateFrame | None:
        try:
            raw = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("收到非 JSON 报文，已忽略")
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("type") not in (None, "state"):
            return None
        return normalize_payload(raw.get("payload", raw), self.session_id)
