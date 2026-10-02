"""语音交互 WebSocket：全双工上行通道。

帧协议（二进制帧首字节为类型标记）：
  0x01 + 音频字节 → 音频分片，累积到本地缓冲
  0x02 + JSON     → 控制消息
文本帧直接为 JSON 控制消息：
  {"type": "audio_end", "mime": "audio/webm"}  结束录音，触发 ASR
  {"type": "text", "text": "..."}              直接上行文本（浏览器 Web Speech 转写结果）
  {"type": "ping"}                             心跳

处理链路：音频/文本 → ASR → 结合实时状态上下文 → 大模型共情回应 → TTS → 播报。
注意：干预卡片与语音播报统一通过 /ws/monitor 广播，本通道只回传 ASR/错误信息，
避免同一句话被两条连接各播一次。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..schemas import Envelope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

_FRAME_AUDIO = 0x01
_FRAME_CONTROL = 0x02
_MIN_AUDIO_BYTES = 1024


@router.websocket("/ws/voice")
async def voice(ws: WebSocket) -> None:
    app = ws.app
    await ws.accept()
    logger.info("语音通道已建立")

    buffer = bytearray()
    last_utterance = 0.0
    tasks: set[asyncio.Task] = set()

    def _spawn(coro) -> None:
        """把耗时的上行处理（ASR / 大模型 / TTS）交给独立任务。

        必须与接收循环解耦：否则一次生成会阻塞数秒，期间本连接既收不到心跳、
        也感知不到断开、更无法接收后续输入。
        """
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(_on_task_done)

    def _on_task_done(task: asyncio.Task) -> None:
        tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error("语音上行处理异常：%s", exc)

    async def send(env: Envelope) -> None:
        await ws.send_json(env.model_dump())

    async def fail(message: str) -> None:
        await send(Envelope.of("error", message=message))

    async def handle_utterance(text: str) -> None:
        nonlocal last_utterance
        text = (text or "").strip()
        if not text:
            await fail("未识别到有效语音内容")
            return
        now = time.monotonic()
        if now - last_utterance < settings.voice_min_interval_s:
            await fail("请求过于频繁，请稍候再试")
            return
        last_utterance = now
        await send(Envelope.of("asr_final", text=text))
        _spawn(app.state.pipeline.respond(text, app.state.source.session_id))

    async def transcribe_and_reply(audio: bytes, mime: str) -> None:
        if len(audio) < _MIN_AUDIO_BYTES:
            await fail("录音过短，请按住说话后再松开")
            return
        try:
            text = await asyncio.wait_for(
                app.state.asr.transcribe(audio, mime), timeout=settings.asr_timeout_s
            )
        except Exception as exc:  # noqa: BLE001 - ASR 不可用时明确降级提示
            logger.warning("ASR 转写失败：%s", exc)
            await fail("语音识别服务暂不可用，可改用文字输入")
            return
        await handle_utterance(text)

    async def handle_control(data: dict) -> None:
        kind = data.get("type")
        if kind == "ping":
            return
        if kind == "text":
            await handle_utterance(str(data.get("text", "")))
        elif kind == "audio_end":
            # 在接收循环里同步取走本次录音：若留给后台任务稍后再取，
            # 用户紧接着的下一次录音会混进同一缓冲（串音）。
            # ASR 可能耗时，因此转写本身仍交给独立任务
            audio = bytes(buffer)
            buffer.clear()
            _spawn(transcribe_and_reply(audio, str(data.get("mime") or "audio/webm")))
        else:
            logger.warning("语音通道收到未知控制消息：%s", kind)

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk is not None:
                if chunk and chunk[0] == _FRAME_CONTROL:
                    await _dispatch(chunk[1:], handle_control, fail)
                elif chunk:
                    # 有类型标记则剥掉，否则整帧视为音频数据
                    buffer.extend(chunk[1:] if chunk[0] == _FRAME_AUDIO else chunk)
                continue

            text_frame = message.get("text")
            if text_frame is not None:
                await _dispatch(text_frame.encode(), handle_control, fail)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("语音通道异常结束：%s", exc)
    finally:
        # 连接已断，取消仍在生成的上行任务并回收，避免任务悬挂到事件循环关闭
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("语音通道已关闭")


async def _dispatch(payload: bytes, handler, fail) -> None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("语音通道收到无法解析的控制帧")
        await fail("控制消息格式错误")
        return
    if isinstance(data, dict):
        await handler(data)
