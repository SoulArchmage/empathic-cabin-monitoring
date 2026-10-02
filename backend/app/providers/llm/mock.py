"""Mock 大模型：无需 API Key 即可跑通全链路。

它不是"随便返回一句固定文案"，而是解析 Prompt 中的 [STATE] 摘要，
按 (异常类型, 认知负荷) 从话术池中选槽位，因此演示时语义与真实状态一致。
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
    "high_cognitive_load": [
        ("感觉你脑子里的东西有点多。我们把节奏放慢，先停三秒再继续。", "环境调节"),
        ("此刻任务有点挤，我们先只做一件事，其余的先放着。", "轻量提问"),
        ("跟着我：吸气四秒，呼气六秒，重复三次，让大脑先降一降速。", "呼吸引导"),
        ("你不用一次想清楚所有事，慢慢来，我在这儿。", "共情复述"),
    ],
    "combined": [
        ("你现在又累又沉，这种感觉我理解。先什么都不用决定，靠一靠，慢慢呼吸。", "呼吸引导"),
        ("我感觉到你已经撑了很久了。我们先停一下，把呼吸放慢，其余交给我记着。", "共情复述"),
        ("这会儿信息太多了。我们先把眼睛闭上十秒，再决定下一步做什么。", "环境调节"),
    ],
    "recovered": [
        ("刚才那种紧绷感慢慢退下去了，你做得很好。把这份平稳多留一会儿。", "环境调节"),
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
        if digest.get("severity") == "severe" or digest.get("cognitive_score", 0) >= 80:
            risk = "high"
        elif digest.get("cognitive_score", 0) >= 52:
            risk = "medium"

        return json.dumps(
            {"speech": speech, "strategy": strategy, "risk": risk},
            ensure_ascii=False,
        )
