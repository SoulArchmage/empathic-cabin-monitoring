"""TTS（文字转语音）适配层抽象。

统一返回 TtsResult，用两种能力形态覆盖不同部署条件：
  - audio_b64 有值 → 前端用 <audio> 播放真实合成音频；
  - 仅 speak_text → 前端用浏览器原生 speechSynthesis 播报（Mock 模式）。
同一份前端逻辑即可兼容"有云服务"与"无云服务"两种情况。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Literal

from ...config import Settings


@dataclass(slots=True)
class TtsResult:
    text: str
    provider: str = "mock"
    voice: str = ""
    audio_b64: str | None = None
    mime: str = "audio/mpeg"


class TtsProvider(abc.ABC):
    name: str = "base"
    mode: Literal["mock", "real"] = "mock"

    @property
    def ready(self) -> bool:
        return True

    @abc.abstractmethod
    async def synth(self, text: str, voice: str = "") -> TtsResult:
        ...

    async def aclose(self) -> None:  # noqa: B027
        pass


def build_tts_provider(cfg: Settings) -> TtsProvider:
    if cfg.tts_provider == "edge":
        from .edge import EdgeTtsProvider

        return EdgeTtsProvider(cfg.tts_voice)
    from .mock import MockTtsProvider

    return MockTtsProvider(cfg.tts_voice)
