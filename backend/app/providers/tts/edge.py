"""可选的真实 TTS 实现：基于 edge-tts（免费、无需 API Key）。

需要额外安装：pip install edge-tts
未安装时 ready=False，工厂会由 /api/health 明确暴露该状态。
"""

from __future__ import annotations

import base64
import logging

from .base import TtsProvider, TtsResult

logger = logging.getLogger(__name__)


class EdgeTtsProvider(TtsProvider):
    name = "edge-tts"
    mode = "real"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self.voice = voice
        self._edge_tts = None
        try:  # 延迟导入：未安装时不影响系统启动
            import edge_tts  # type: ignore[import-not-found]

            self._edge_tts = edge_tts
        except ImportError:
            logger.warning("未安装 edge-tts，真实 TTS 不可用（pip install edge-tts）")

    @property
    def ready(self) -> bool:
        return self._edge_tts is not None

    async def synth(self, text: str, voice: str = "") -> TtsResult:
        if self._edge_tts is None:
            raise RuntimeError("edge-tts 未安装")
        communicate = self._edge_tts.Communicate(text, voice or self.voice)
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
        return TtsResult(
            text=text,
            provider=self.name,
            voice=voice or self.voice,
            audio_b64=base64.b64encode(bytes(chunks)).decode(),
            mime="audio/mpeg",
        )
