"""ASR（语音转文字）适配层抽象。"""

from __future__ import annotations

import abc
from typing import Literal

from ...config import Settings


class AsrProvider(abc.ABC):
    name: str = "base"
    mode: Literal["mock", "real"] = "mock"

    @property
    def ready(self) -> bool:
        return True

    @abc.abstractmethod
    async def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        """把一段完整音频转写为文本；无法识别时返回空串。"""

    async def aclose(self) -> None:  # noqa: B027
        pass


def build_asr_provider(cfg: Settings) -> AsrProvider:
    if cfg.asr_provider == "mock":
        from .mock import MockAsrProvider

        return MockAsrProvider()
    # 与 LLM 相同：真实实现走 OpenAI 兼容协议（Whisper /audio/transcriptions）
    from .openai_compat import OpenAICompatAsrProvider

    return OpenAICompatAsrProvider(cfg)
