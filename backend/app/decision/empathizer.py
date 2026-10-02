"""共情引擎：把触发事件转换为一条可播报的干预话术。

编排顺序：构造 Prompt → 调用大模型（带超时与一次重试）→ 硬校验 → 必要时降级。
无论外部服务是否可用、模型输出是否合规，本方法都保证返回一条可安全播报的话术，
这是"系统绝不因为模型问题而对用户失语"的兜底承诺。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from functools import partial

from ..config import Settings, settings as default_settings
from ..providers.llm.base import LlmProvider
from ..schemas import Intervention, TriggerEvent
from .prompt import build_messages, build_reply_messages, prompt_digest
from .validator import ValidatedSpeech, fallback_for, safety_exit, validate

logger = logging.getLogger(__name__)

_RETRY_HINT = (
    "你上一次的输出未通过系统校验（{note}）。本次必须只输出一个合法 JSON 对象，"
    "话术不超过 60 字，且不得出现任何诊断结论、药物名称或自伤相关内容。"
)


class Empathizer:
    def __init__(self, provider: LlmProvider, cfg: Settings | None = None) -> None:
        self.provider = provider
        self.cfg = cfg or default_settings

    # ---- 主动干预 -------------------------------------------------------- #
    async def generate(self, event: TriggerEvent) -> Intervention:
        started = time.perf_counter()

        # 恢复态与高危态都使用确定性话术，无需消耗大模型：恢复态是收尾确认；
        # 高危态的"安全出口"本就不允许模型自由发挥。提前短路可避免白付一次模型延迟——
        # 否则模型不可用时，这句热线话术会被拖到 llm_timeout_s 之后才播出。
        if event.kind == "recovered":
            result = fallback_for(event)
            result.degraded = False
            return self._build(event, result, "rule", started, "")
        if event.severity == "severe":
            return self._build(event, safety_exit(), "rule", started, "")

        messages = build_messages(event)
        builder = partial(build_messages, event)
        result = await self._invoke(messages, event, builder)
        return self._build(
            event, result, self.provider.name, started, prompt_digest(messages)
        )

    # ---- 语音对话回复 ---------------------------------------------------- #
    async def generate_reply(self, event: TriggerEvent, user_text: str) -> Intervention:
        started = time.perf_counter()
        # 高危状态同样直接走安全出口，不调用模型（机制与 generate 一致）
        if event.severity == "severe":
            return self._build(event, safety_exit(), "rule", started, "")
        messages = build_reply_messages(event, user_text)
        builder = partial(build_reply_messages, event, user_text)
        result = await self._invoke(messages, event, builder)
        intervention = self._build(
            event, result, self.provider.name, started, prompt_digest(messages)
        )
        return intervention

    # ---- 内部 ------------------------------------------------------------ #
    async def _invoke(
        self,
        messages: list[dict[str, str]],
        event: TriggerEvent,
        rebuild: Callable[[str], list[dict[str, str]]],
    ) -> ValidatedSpeech:
        """调用大模型并校验；快速失败与校验不通过都重试一次，仍失败则降级为规则话术。

        超时是唯一例外：重试只会让用户再多等一个 llm_timeout_s，直接降级响应更快。
        ``rebuild(hint)`` 用于在重试时重建提示词——必须由调用方给出，
        否则语音对话重试会退化成通用提示词、丢掉用户原话。
        """
        result: ValidatedSpeech | None = None
        for attempt in range(self.cfg.llm_max_retry + 1):
            try:
                raw = await asyncio.wait_for(
                    self.provider.complete(messages), timeout=self.cfg.llm_timeout_s
                )
            except asyncio.TimeoutError:
                # 超时不重试：重试意味着用户再多等一个 llm_timeout_s，直接降级更快
                return self._degrade(event, "大模型调用超时")
            except Exception as exc:  # noqa: BLE001 - 外部服务需整体兜底
                logger.warning("大模型调用失败：%s", exc)
                if attempt < self.cfg.llm_max_retry:
                    continue  # 网络抖动 / 5xx 等快速失败重试一次
                return self._degrade(event, f"大模型调用异常：{exc}")

            result = validate(raw, event)
            if not result.degraded:
                return result
            if attempt < self.cfg.llm_max_retry:
                # 就地更新，保证 prompt_digest 记录的是最后一次实际发送的内容
                messages[:] = rebuild(_RETRY_HINT.format(note=result.note))

        assert result is not None  # 循环至少执行一次
        return result

    @staticmethod
    def _degrade(event: TriggerEvent, reason: str) -> ValidatedSpeech:
        result = fallback_for(event)
        result.note = reason
        return result

    @staticmethod
    def _build(
        event: TriggerEvent,
        result: ValidatedSpeech,
        provider: str,
        started: float,
        digest: str,
    ) -> Intervention:
        return Intervention(
            ts=event.ts,
            session_id=event.session_id,
            kind=event.kind,
            severity=event.severity,
            reason=event.reason,
            prompt_digest=digest,
            speech=result.speech,
            strategy=result.strategy,
            risk=result.risk,
            degraded=result.degraded,
            provider=provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
            stats=event.stats,
        )
