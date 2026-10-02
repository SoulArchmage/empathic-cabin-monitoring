"""ASR 适配层测试：工厂选择、厂商默认值、请求报文形态、错误透出。

与 test_llm_provider.py 一致，用 httpx.MockTransport 只替换传输层，
因此在没有真实 API Key 的环境下也能验证适配层正确性。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.providers.asr.base import build_asr_provider
from app.providers.asr.mock import MockAsrProvider
from app.providers.asr.openai_compat import OpenAICompatAsrProvider

AUDIO = b"\x00" * 2048


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response], **overrides
) -> OpenAICompatAsrProvider:
    provider = OpenAICompatAsrProvider(Settings(**overrides))
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {provider._api_key}"},
    )
    return provider


async def test_transcribe_request_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "  我今天有点累。 "})

    provider = make_provider(handler, asr_provider="openai", asr_api_key="sk-asr")
    text = await provider.transcribe(AUDIO, "audio/webm;codecs=opus")

    assert text == "我今天有点累。"  # 返回文本去除首尾空白
    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer sk-asr"
    assert "multipart/form-data" in captured["content_type"]
    body = captured["body"]
    assert b'name="model"' in body and b"whisper-1" in body
    # mime 参数（codecs=opus）不应影响文件名后缀
    assert b'name="file"' in body and b"utterance.webm" in body
    await provider.aclose()


async def test_custom_base_url_and_model_override() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    provider = make_provider(
        handler,
        asr_provider="openai",
        asr_api_key="sk-x",
        asr_base_url="https://gateway.internal/v1/",  # 结尾斜杠应被规整
        asr_model="whisper-large-v3",
    )
    await provider.transcribe(AUDIO)

    assert captured["url"] == "https://gateway.internal/v1/audio/transcriptions"
    assert b"whisper-large-v3" in captured["body"]
    await provider.aclose()


async def test_http_error_surfaces_vendor_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    provider = make_provider(handler, asr_provider="openai", asr_api_key="bad")
    with pytest.raises(RuntimeError) as excinfo:
        await provider.transcribe(AUDIO)
    message = str(excinfo.value)
    assert "401" in message and "invalid api key" in message
    await provider.aclose()


def test_ready_follows_api_key() -> None:
    """未配置 Key 时 ready=False：/api/health 会如实暴露，不静默冒充真实识别。"""
    assert OpenAICompatAsrProvider(Settings(asr_provider="openai")).ready is False
    assert (
        OpenAICompatAsrProvider(Settings(asr_provider="openai", asr_api_key="sk")).ready
        is True
    )


def test_factory_honors_asr_provider() -> None:
    assert isinstance(build_asr_provider(Settings(asr_provider="mock")), MockAsrProvider)

    real = build_asr_provider(Settings(asr_provider="openai", asr_api_key="sk"))
    assert isinstance(real, OpenAICompatAsrProvider)
    assert real.mode == "real"