"""ASR（语音转文字）适配层抽象。"""

from __future__ import annotations

import abc
from typing import Literal

from ...config import Settings


class AsrError(RuntimeError):
    """语音识别失败。

    ``hint`` 是可直接展示给用户的一句话（如"账户余额或免费额度不足"），
    ``detail`` 保留厂商原文供日志排查；二者分开，避免把上游报文直接丢到界面上。
    """

    def __init__(self, hint: str, detail: str = "") -> None:
        super().__init__(detail or hint)
        self.hint = hint


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
