"""OpenAI Whisper 兼容的语音识别适配实现。

OpenAI 的 `POST {base_url}/audio/transcriptions` 是主流云端转写的事实标准，
自建服务（whisper.cpp 网关、vLLM、各类代理）大多兼容该协议，因此厂商差异
同样收敛为 (地址, 默认模型) 两项，用 ASR_BASE_URL / ASR_MODEL 覆盖即可。

切换方式（.env）：
    ASR_PROVIDER=openai
    ASR_API_KEY=sk-xxxxxxxx
未配置 Key 时 ready=False，/api/health 与前端顶栏会如实显示"未就绪"，
不会静默退回 Mock 冒充真实识别。
"""

from __future__ import annotations

import logging

import httpx

from ...config import Settings
from .base import AsrProvider

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "whisper-1"

# 转写接口按文件名后缀判断容器格式，因此把浏览器上报的 mime 映射为扩展名
_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/flac": "flac",
}


def _extension_for(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    return _EXTENSIONS.get(base, "webm")


class OpenAICompatAsrProvider(AsrProvider):
    name = "openai"
    mode = "real"

    def __init__(self, cfg: Settings) -> None:
        self.model = cfg.asr_model or DEFAULT_MODEL
        # 显式拼出完整地址，避免依赖 httpx 的 base_url 路径合并规则
        self.endpoint = (
            f"{(cfg.asr_base_url or DEFAULT_BASE_URL).rstrip('/')}/audio/transcriptions"
        )
        self._api_key = cfg.asr_api_key
        self._client = httpx.AsyncClient(
            timeout=cfg.asr_timeout_s,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    @property
    def ready(self) -> bool:
        return bool(self._api_key)

    async def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        response = await self._client.post(
            self.endpoint,
            files={"file": (f"utterance.{_extension_for(mime)}", audio, mime)},
            data={"model": self.model},
        )
        if response.status_code >= 400:
            # 带上厂商返回的错误体，便于排查 Key／模型名／额度问题
            raise RuntimeError(
                f"{self.name} 接口返回 {response.status_code}：{response.text[:300]}"
            )
        return str(response.json().get("text", "")).strip()

    async def aclose(self) -> None:
        await self._client.aclose()