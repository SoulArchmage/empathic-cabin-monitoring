"""Mock TTS：不合成音频，只返回待播报文本，由浏览器原生 speechSynthesis 发声。

这样在完全没有云服务凭证的情况下，"数字语音助手"依然能出声演示。
"""

from __future__ import annotations

from .base import TtsProvider, TtsResult


class MockTtsProvider(TtsProvider):
    name = "browser"
    mode = "mock"

    def __init__(self, voice: str = "") -> None:
        self.voice = voice

    async def synth(self, text: str, voice: str = "") -> TtsResult:
        return TtsResult(
            text=text,
            provider=self.name,
            voice=voice or self.voice,
            audio_b64=None,
        )
