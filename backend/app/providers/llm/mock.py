"""Mock 大模型：无需 API Key 即可跑通全链路。

它不是"随便返回一句固定文案"，而是解析 Prompt 中的 [STATE] 摘要，
按 (状态类型, 负向占比/置信度) 从话术池中选槽位，因此演示时语义与真实状态一致。
同时保留一小段固定延迟，用于验证任务书要求的"大模型响应慢时的占位交互体验"。
"""

from __future__ import annotations

import asyncio
import json
import random

from .base import LlmProvider

_POOL: dict[str, list[tuple[str, str]]] = {
    "negative_emotion": [
        ("我感觉到你现在情绪有点沉，不用急着解释，我就在这儿陪着你。", "共情复述"),
        ("听起来你今天挺不容易的。愿意的话，跟我说说发生了什么？", "轻量提问"),
        ("我们先不急着解决问题，先让自己松一口气，好吗？", "呼吸引导"),
        ("这一下午好像挺耗神的。要不要把灯光调柔和一点，我陪你静一会儿。", "环境调节"),
    ],
    "recovered": [
        ("刚才那种紧绷感慢慢退下去了，你做得很好。把这份平稳多留一会儿。", "环境调节"),
    ],
    "stable": [
        ("我在呢。你想聊点什么，或者就安静待一会儿，都可以。", "共情复述"),
        ("现在这样挺好的，我们慢慢来，不着急。", "环境调节"),
    ],
}


class MockLlmProvider(LlmProvider):
    name = "mock"
    mode = "mock"

    def __init__(self, delay_s: float = 0.25, seed: int | None = None) -> None:
        self.delay_s = delay_s
        self._random = random.Random(seed)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)

        digest = self.extract_state_digest(messages)
        kind = digest.get("kind", "negative_emotion")
        pool = _POOL.get(kind, _POOL["negative_emotion"])
        speech, strategy = self._random.choice(pool)

        risk = "low"
        if digest.get("severity") == "severe" or digest.get("negative_ratio", 0) >= 0.85:
            risk = "high"
        elif digest.get("negative_ratio", 0) >= 0.5 or digest.get("confidence", 0) >= 0.85:
            risk = "medium"

        return json.dumps(
            {"speech": speech, "strategy": strategy, "risk": risk},
            ensure_ascii=False,
        )
