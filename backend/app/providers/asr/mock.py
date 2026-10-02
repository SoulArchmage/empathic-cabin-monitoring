"""Mock ASR：返回模拟的用户话术，使无云服务凭证时也能演示完整语音链路。

注意：它并不真正"识别"音频，而是轮换返回典型疏导场景语料，用于验证
录音上传 → ASR → LLM → TTS → 播报 的管线连通性。/api/health 会明确标注 mode=mock。
真实场景下应接入云 ASR，或由前端使用浏览器 Web Speech API 转写后上行文本。
"""

from __future__ import annotations

import asyncio
import itertools

from .base import AsrProvider

_SAMPLES = itertools.cycle(
    [
        "我今天有点累，感觉什么都做不好。",
        "脑子里事情太多了，静不下来。",
        "能陪我聊两句吗？",
        "刚刚那阵紧张过去了一些。",
    ]
)


class MockAsrProvider(AsrProvider):
    name = "mock"
    mode = "mock"

    def __init__(self, delay_s: float = 0.4) -> None:
        self.delay_s = delay_s

    async def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        await asyncio.sleep(self.delay_s)
        return next(_SAMPLES)
