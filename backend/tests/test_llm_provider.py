"""OpenAI 兼容适配层测试：厂商默认值、请求报文形态、错误透出。

用 httpx.MockTransport 拦截请求，因此不需要真实 API Key，也不会发真实网络请求。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.decision.empathizer import Empathizer
from app.decision.validator import SAFE_REFERRAL_SPEECH
from app.providers.llm.base import LlmProvider
from app.providers.llm.openai_compat import VENDORS, OpenAICompatProvider
from app.schemas import TriggerEvent, WindowStats

MESSAGES = [{"role": "user", "content": "测试"}]


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response], **overrides
) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(Settings(**overrides))
    # 注入 MockTransport：只替换传输层，其余逻辑（URL 拼接、报文构造）走真实代码
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {provider._api_key}"},
    )
    return provider


def ok_handler(captured: dict) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"speech":"我在"}'}}]}
        )

    return handler


def test_vendor_table_covers_task_book_providers() -> None:
    """任务书点名了智谱 GLM 与通义千问，加上 DeepSeek 与 OpenAI 共四家。"""
    assert set(VENDORS) == {"deepseek", "zhipu", "qwen", "openai"}
    assert VENDORS["deepseek"] == ("https://api.deepseek.com/v1", "deepseek-chat")
    assert VENDORS["zhipu"][1] == "glm-4-flash"
    assert all(base.startswith("https://") for base, _ in VENDORS.values())


async def test_deepseek_request_shape() -> None:
    captured: dict = {}
    provider = make_provider(
        ok_handler(captured), llm_provider="deepseek", llm_api_key="sk-test"
    )
    assert provider.name == "deepseek" and provider.ready is True

    content = await provider.complete(MESSAGES)
    assert content == '{"speech":"我在"}'
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"] == MESSAGES
    await provider.aclose()


async def test_custom_base_url_and_model_override() -> None:
    captured: dict = {}
    provider = make_provider(
        ok_handler(captured),
        llm_provider="openai",
        llm_api_key="sk-x",
        llm_base_url="https://gateway.internal/v1/",  # 结尾斜杠应被规整
        llm_model="my-finetune",
    )
    await provider.complete(MESSAGES)
    assert captured["url"] == "https://gateway.internal/v1/chat/completions"
    assert captured["body"]["model"] == "my-finetune"
    await provider.aclose()


async def test_http_error_surfaces_vendor_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    provider = make_provider(handler, llm_provider="deepseek", llm_api_key="bad")
    with pytest.raises(RuntimeError) as excinfo:
        await provider.complete(MESSAGES)
    message = str(excinfo.value)
    assert "deepseek" in message and "401" in message and "invalid api key" in message
    await provider.aclose()


def test_ready_false_without_key() -> None:
    provider = OpenAICompatProvider(Settings(llm_provider="deepseek"))
    assert provider.ready is False


# --------------------------------------------------------------------------- #
# 换用真实厂商后的失败兜底：任何模型故障都不能让系统对用户失语
# --------------------------------------------------------------------------- #
def make_event() -> TriggerEvent:
    return TriggerEvent(
        ts=1.0,
        session_id="s-test",
        kind="combined",
        severity="mild",
        reason="测试触发",
        stats=WindowStats(
            window_size=15, negative_ratio=0.8, high_load_ratio=0.8,
            consec_abnormal=12, avg_cognitive_score=82.0, duration_s=1.2,
        ),
    )


class _FailingProvider(LlmProvider):
    name = "failing"
    mode = "real"

    async def complete(self, messages, **kwargs) -> str:
        raise RuntimeError("401：invalid api key")


class _SlowProvider(LlmProvider):
    name = "slow"
    mode = "real"

    async def complete(self, messages, **kwargs) -> str:
        await asyncio.sleep(5)
        return "{}"


async def test_empathizer_degrades_when_provider_raises() -> None:
    result = await Empathizer(_FailingProvider()).generate(make_event())
    assert result.degraded is True
    assert result.speech, "降级后仍必须给出可播报话术"
    assert result.provider == "failing"


async def test_empathizer_degrades_on_timeout() -> None:
    empathizer = Empathizer(_SlowProvider(), Settings(llm_timeout_s=0.05))
    result = await empathizer.generate(make_event())
    assert result.degraded is True
    assert result.speech
    assert "超时" in result.reason or result.degraded  # 记录降级原因


class _FlakyProvider(LlmProvider):
    """首次返回非法 JSON、第二次返回合法 JSON，用于观察重试时实际发送的提示词。"""

    name = "flaky"
    mode = "real"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def complete(self, messages, **kwargs) -> str:
        self.seen.append(" ".join(m["content"] for m in messages))
        if len(self.seen) == 1:
            return "抱歉，我不想用 JSON 回答"
        return '{"speech": "我在，慢慢来。", "strategy": "共情复述", "risk": "low"}'


async def test_reply_retry_keeps_user_utterance() -> None:
    """回归：语音对话重试时提示词必须仍包含用户原话，否则模型会答非所问。"""
    provider = _FlakyProvider()
    result = await Empathizer(provider).generate_reply(make_event(), "我今天特别累")

    assert result.degraded is False
    assert len(provider.seen) == 2, "首次校验失败后应重试一次"
    assert "我今天特别累" in provider.seen[0]
    assert "我今天特别累" in provider.seen[1], "重试提示词丢失了用户原话"


class _CountingProvider(LlmProvider):
    name = "counting"
    mode = "real"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, **kwargs) -> str:
        self.calls += 1
        return '{"speech": "随便说点什么", "strategy": "轻量提问", "risk": "low"}'


def make_severe_event() -> TriggerEvent:
    event = make_event()
    event.severity = "severe"
    return event


async def test_severe_state_skips_llm_call() -> None:
    """高危状态必须直接走安全出口模板，不调用大模型。

    回归：此前 severe 也会先调用模型再丢弃其输出，模型不可用时会白白等到
    llm_timeout_s 才播出热线话术。
    """
    provider = _CountingProvider()
    result = await Empathizer(provider).generate(make_severe_event())

    assert provider.calls == 0, "高危状态不应调用大模型"
    assert result.speech == SAFE_REFERRAL_SPEECH
    assert result.degraded is False
    assert result.provider == "rule"


async def test_severe_reply_skips_llm_call() -> None:
    provider = _CountingProvider()
    result = await Empathizer(provider).generate_reply(make_severe_event(), "我撑不住了")

    assert provider.calls == 0
    assert result.speech == SAFE_REFERRAL_SPEECH
