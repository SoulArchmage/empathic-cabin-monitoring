"""大模型适配层抽象。

任务书要求"接入开源大模型 API（如智谱 GLM、通义千问等）"——模型与厂商会变，
因此业务代码只依赖 LlmProvider 接口，具体实现由 .env 的 LLM_PROVIDER 决定。
"""

from __future__ import annotations

import abc
from typing import Literal

from ...config import Settings


class LlmProvider(abc.ABC):
    name: str = "base"
    mode: Literal["mock", "real"] = "mock"

    @property
    def ready(self) -> bool:
        """是否具备真实调用条件（例如已配置 API Key）。"""
        return True

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        """返回模型生成的原始文本（预期为 JSON 字符串）。"""

    async def aclose(self) -> None:  # noqa: B027 - 可选钩子
        pass

    @staticmethod
    def extract_state_digest(messages: list[dict[str, str]]) -> dict:
        """从消息中解析 [STATE] 机器可读摘要（Mock 实现与审计共用）。"""
        import json

        for msg in reversed(messages):
            for line in msg.get("content", "").splitlines():
                if line.startswith("[STATE] "):
                    try:
                        return json.loads(line[len("[STATE] ") :])
                    except json.JSONDecodeError:
                        return {}
        return {}


def build_llm_provider(cfg: Settings) -> LlmProvider:
    if cfg.llm_provider == "mock":
        from .mock import MockLlmProvider

        return MockLlmProvider()
    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(cfg)
