"""顶层编排：状态流入 → 降频落库 + 异常决策 → 话术生成 → 语音合成 → 广播 → 落库。

这是唯一知道"完整业务流程"的地方，其余模块都只做一件事。
关键点：决策与生成放在独立 task 中执行，绝不阻塞状态帧的消费循环，
否则大模型几秒的延迟会直接拖垮实时推送（任务书硬性要求）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

from ..bus import EventBus
from ..config import Settings, settings as default_settings
from ..db.repo import Repository
from ..decision.empathizer import Empathizer
from ..decision.trigger import TriggerEngine
from ..hub import ConnectionHub
from ..load_store import LoadStore
from ..observability import record
from ..providers.tts.base import TtsProvider, TtsResult
from ..schemas import (
    Emotion,
    Envelope,
    StateFrame,
    TriggerEvent,
)

logger = logging.getLogger(__name__)


def _majority(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0]


def aggregate_frames(frames: list[StateFrame]) -> StateFrame:
    """把 1 秒内的若干个窗口结果聚合为一个采样点（降频落库）。"""
    last = frames[-1]
    n = len(frames)
    # 负荷是状态量：取本批最后一个带负荷的帧，而不是"多数表决"（等级没有众数意义）
    load = next((f.load for f in reversed(frames) if f.load is not None), None)
    return StateFrame(
        ts=last.ts,
        session_id=last.session_id,
        emotion=Emotion(
            label=_majority([f.emotion.label for f in frames]),  # type: ignore[arg-type]
            confidence=round(sum(f.emotion.confidence for f in frames) / n, 3),
        ),
        load=load,
        window=last.window,
        source=last.source,
    )


class PipelineService:
    def __init__(
        self,
        *,
        bus: EventBus,
        hub: ConnectionHub,
        trigger: TriggerEngine,
        empathizer: Empathizer,
        tts: TtsProvider,
        repo: Repository,
        cfg: Settings | None = None,
        load_store: LoadStore | None = None,
    ) -> None:
        self.bus = bus
        self.hub = hub
        self.trigger = trigger
        self.empathizer = empathizer
        self.tts = tts
        self.repo = repo
        self.cfg = cfg or default_settings
        self.load_store = load_store
        self.latest_frame: StateFrame | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---- 主循环 ---------------------------------------------------------- #
    async def run(self) -> None:
        queue = self.bus.subscribe(maxsize=self.cfg.ws_queue_size)
        interval = 1.0 / max(self.cfg.persist_hz, 0.1)
        buffer: list[StateFrame] = []
        last_flush = time.monotonic()
        try:
            while True:
                frame: StateFrame = await queue.get()
                if self.load_store is not None:
                    # 把"此刻最新的脑电负荷"贴到情绪帧上：触发判定、落库、广播共用同一份
                    frame = self.load_store.attach(frame)
                self.latest_frame = frame

                event = self.trigger.evaluate(frame)
                if event is not None:
                    self._spawn(self.handle_event(event))

                buffer.append(frame)
                now = time.monotonic()
                if now - last_flush >= interval:
                    last_flush = now
                    await self._flush(buffer)
                    buffer.clear()
        finally:
            self.bus.unsubscribe(queue)

    async def _flush(self, frames: list[StateFrame]) -> None:
        if not frames:
            return
        try:
            await self.repo.add_state(aggregate_frames(frames))
        except Exception as exc:  # noqa: BLE001 - 落库失败不应中断实时链路
            logger.error("状态落库失败：%s", exc)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- 干预流程 -------------------------------------------------------- #
    async def handle_event(self, event: TriggerEvent) -> None:
        # 占位交互：先把"正在组织语言"推给前端，掩盖大模型延迟（任务书明确要求）
        self.hub.broadcast(
            Envelope.of(
                "pending",
                kind=event.kind,
                severity=event.severity,
                reason=event.reason,
                ts=event.ts,
            )
        )
        intervention = await self.empathizer.generate(event)
        await self._finalize(event, intervention)

    async def respond(self, user_text: str, session_id: str | None = None) -> Intervention:
        """语音/文本对话：把用户原话与当前实时状态一起交给大模型生成共情回应。"""
        sid = session_id or (
            self.latest_frame.session_id if self.latest_frame else "s-voice"
        )
        event = self.trigger.snapshot_event(self.latest_frame, sid)
        event.reason = f"语音交互：用户说「{user_text}」"

        self.hub.broadcast(
            Envelope.of("pending", kind=event.kind, severity=event.severity,
                        reason=event.reason, ts=event.ts)
        )
        intervention = await self.empathizer.generate_reply(event, user_text)
        await self._finalize(event, intervention)
        return intervention

    async def _finalize(self, event: TriggerEvent, intervention: Intervention) -> None:
        # 只记录真实调用了大模型的场景：severe / recovered 走规则模板，不算模型故障
        if intervention.provider != "rule":
            ok = not intervention.degraded
            record(
                "llm",
                ok,
                f"{intervention.provider} 生成成功" if ok else "模型输出未通过校验，已降级",
            )

        tts_result: TtsResult | None = None
        try:
            tts_result = await self.tts.synth(intervention.speech)
        except Exception as exc:  # noqa: BLE001 - 语音不可用不影响文字干预
            logger.warning("语音合成失败，降级为纯文本：%s", exc)
            record("tts", False, f"合成失败：{exc}")
        else:
            detail = (
                f"{tts_result.provider} · {tts_result.voice}"
                if tts_result.audio_b64
                else f"{tts_result.provider}（仅文本）"
            )
            record("tts", True, detail)
        intervention.audio_available = bool(tts_result and tts_result.audio_b64)

        try:
            await self.repo.add_intervention(intervention)
        except Exception as exc:  # noqa: BLE001
            logger.error("干预日志落库失败：%s", exc)

        self.hub.broadcast(Envelope.of("intervention", item=intervention.model_dump()))
        self.hub.broadcast(
            Envelope.of(
                "speak",
                text=intervention.speech,
                audio_b64=tts_result.audio_b64 if tts_result else None,
                mime=tts_result.mime if tts_result else "",
                voice=tts_result.voice if tts_result else "",
                tts_provider=tts_result.provider if tts_result else "none",
                intervention_id=intervention.id,
            )
        )
        logger.info(
            "干预完成 kind=%s provider=%s degraded=%s latency=%dms speech=%s",
            event.kind,
            intervention.provider,
            intervention.degraded,
            intervention.latency_ms,
            intervention.speech,
        )

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
