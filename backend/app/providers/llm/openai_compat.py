"""OpenAI 兼容协议适配实现。

DeepSeek、智谱 GLM、通义千问（DashScope 兼容模式）、OpenAI 等主流厂商都提供
OpenAI 风格的 `POST {base_url}/chat/completions` 接口，因此无需为每家单独写实现：
厂商差异只是 (base_url, 默认模型) 两项，收敛成下面一张表。

切换厂商只需改 .env：
    LLM_PROVIDER=deepseek
    LLM_API_KEY=sk-xxxxxxxx
需要非默认模型或自建网关时，再额外填 LLM_MODEL / LLM_BASE_URL 覆盖。
"""

from __future__ import annotations

import logging

import httpx

from ...config import Settings
from .base import LlmProvider

logger = logging.getLogger(__name__)

# 厂商 -> (默认接入地址, 默认模型)。地址不含 /chat/completions 后缀。
VENDORS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


class OpenAICompatProvider(LlmProvider):
    mode = "real"

    def __init__(self, cfg: Settings) -> None:
        default_base, default_model = VENDORS.get(
            cfg.llm_provider, ("", "deepseek-chat")
        )
        self.name = cfg.llm_provider
        self.model = cfg.llm_model or default_model
        # 显式拼出完整地址，避免依赖 httpx 的 base_url 路径合并规则
        self.endpoint = f"{(cfg.llm_base_url or default_base).rstrip('/')}/chat/completions"
        self._api_key = cfg.llm_api_key
        self._client = httpx.AsyncClient(
            timeout=cfg.llm_timeout_s,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    @property
    def ready(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        response = await self._client.post(
            self.endpoint,
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                # 各厂商均支持 json_object；Prompt 中已包含 "json" 字样以满足其前置要求
                "response_format": {"type": "json_object"},
            },
        )
        if response.status_code >= 400:
            # 把厂商返回的错误体带上，便于排查 Key／模型名／额度问题
            raise RuntimeError(
                f"{self.name} 接口返回 {response.status_code}：{response.text[:300]}"
            )
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    async def aclose(self) -> None:
        await self._client.aclose()
