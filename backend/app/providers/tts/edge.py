"""可选的真实 TTS 实现：基于 edge-tts（免费、无需 API Key）。

版本要求 edge-tts>=7.3.1：7.2.x 及更早版本使用旧的 Sec-MS-GEC 令牌，
微软轮换后所有请求都会返回 403，而系统会静默降级成浏览器发音（听感很"机器"）。
需额外安装：pip install "edge-tts>=7.3.1"；未安装时 ready=False，
工厂会由 /api/health 明确暴露该状态。
"""

from __future__ import annotations

import asyncio
import base64
import logging

from .base import TtsProvider, TtsResult

logger = logging.getLogger(__name__)


class EdgeTtsProvider(TtsProvider):
    name = "edge-tts"
    mode = "real"

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        *,
        rate: str = "-4%",
        pitch: str = "+0Hz",
        timeout_s: float = 10.0,
    ) -> None:
        self.voice = voice
        # 默认略慢于常速：疏导场景下语速过快会加重"机械播报"的观感
        self.rate = rate
        self.pitch = pitch
        self.timeout_s = timeout_s
        self._edge_tts = None
        try:  # 延迟导入：未安装时不影响系统启动
            import edge_tts  # type: ignore[import-not-found]

            self._edge_tts = edge_tts
        except ImportError:
            logger.warning(
                "未安装 edge-tts，真实 TTS 不可用（pip install \"edge-tts>=7.3.1\"）"
            )

    @property
    def ready(self) -> bool:
        return self._edge_tts is not None

    async def synth(self, text: str, voice: str = "") -> TtsResult:
        if self._edge_tts is None:
            raise RuntimeError("edge-tts 未安装")
        communicate = self._edge_tts.Communicate(
            text,
            voice or self.voice,
            rate=self.rate,
            pitch=self.pitch,
        )

        chunks = bytearray()

        async def collect() -> None:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.extend(chunk["data"])

        # 超时兜底：微软接口偶发长时间无响应，不能让一次合成拖住整条干预链路
        await asyncio.wait_for(collect(), timeout=self.timeout_s)
        if not chunks:
            raise RuntimeError("edge-tts 未返回音频数据")
        return TtsResult(
            text=text,
            provider=self.name,
            voice=voice or self.voice,
            audio_b64=base64.b64encode(bytes(chunks)).decode(),
            mime="audio/mpeg",
        )
