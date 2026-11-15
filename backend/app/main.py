"""FastAPI 应用入口：在这里完成依赖组装与后台任务编排。

分层原则：main.py 是唯一"知道所有实现"的地方，它把适配层实现注入到编排层，
其余模块只依赖抽象接口。更换数据源、大模型或语音服务时，只需改 .env 与工厂函数。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import logs, system, ws_monitor, ws_voice
from .bus import EventBus
from .config import settings
from .db.repo import Repository
from .db.session import SessionFactory, dispose_db, init_db
from .decision.empathizer import Empathizer
from .decision.trigger import TriggerEngine
from .hub import ConnectionHub
from .load_store import LoadStore
from .observability import record
from .providers.asr.base import build_asr_provider
from .providers.llm.base import build_llm_provider
from .providers.tts.base import build_tts_provider
from .schemas import Envelope
from .service.pipeline import PipelineService
from .sources.base import StateSource
from .sources.external import ExternalSource
from .sources.file import FileSource
from .sources.load_source import (
    LoadExternalSource,
    LoadFileSource,
    LoadSimulatorSource,
    LoadSource,
)
from .sources.simulator import SimulatorSource

logger = logging.getLogger(__name__)


def build_source(bus: EventBus) -> StateSource:
    if settings.source_mode == "file":
        return FileSource(bus, settings.sample_hz, settings)
    if settings.source_mode == "external":
        return ExternalSource(bus, settings.sample_hz, settings)
    return SimulatorSource(bus, settings.sample_hz, settings)


def build_load_source(store: LoadStore) -> LoadSource | None:
    """第二路数据源：脑电认知负荷等级。off 时返回 None，契约行为与旧版本一致。"""
    if settings.load_source_mode == "simulator":
        return LoadSimulatorSource(store, settings)
    if settings.load_source_mode == "external":
        return LoadExternalSource(store, settings)
    if settings.load_source_mode == "file":
        return LoadFileSource(store, settings)
    return None


def _watch(task: asyncio.Task) -> None:
    """后台任务异常必须显式记录，否则会静默死掉。"""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("后台任务 %s 异常退出", task.get_name())


async def _tts_selfcheck(tts) -> None:
    """启动后做一次极短的合成自检，把结果写进 /api/health。

    edge-tts 走的是微软消费级接口，Token 轮换时会在运行时静默 403；
    不提前探测的话，界面会一直显示"已就绪"，直到第一次干预才暴露。
    只对真实 TTS 执行，且不阻塞启动（约 1～2 秒后完成）。
    """
    if tts.mode != "real":
        return
    try:
        result = await tts.synth("语音自检")
    except Exception as exc:  # noqa: BLE001 - 自检失败不应影响启动
        logger.warning("TTS 启动自检失败：%s", exc)
        record("tts", False, f"启动自检失败：{exc}")
        return
    record("tts", True, f"启动自检通过（{result.voice or tts.name}）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await init_db()

    bus = EventBus()
    hub = ConnectionHub(bus, settings)
    source = build_source(bus)
    # 第二路数据源：负荷快照对外广播 + 贴到情绪帧上（见 LoadStore）
    load_store = LoadStore()
    hub.load_store = load_store
    load_store.add_listener(
        lambda load: hub.broadcast(
            Envelope.of("load", load=load.model_dump()), droppable=True
        )
    )
    load_source = build_load_source(load_store)
    llm = build_llm_provider(settings)
    asr = build_asr_provider(settings)
    tts = build_tts_provider(settings)
    pipeline = PipelineService(
        bus=bus,
        hub=hub,
        trigger=TriggerEngine(settings),
        empathizer=Empathizer(llm, settings),
        tts=tts,
        repo=Repository(SessionFactory),
        cfg=settings,
        load_store=load_store,
    )

    app.state.app_title = settings.app_name
    app.state.bus = bus
    app.state.hub = hub
    app.state.source = source
    app.state.load_store = load_store
    app.state.load_source = load_source
    app.state.llm = llm
    app.state.asr = asr
    app.state.tts = tts
    app.state.repo = pipeline.repo
    app.state.pipeline = pipeline

    tasks = [
        asyncio.create_task(hub.run(), name="hub-broadcast"),
        asyncio.create_task(pipeline.run(), name="pipeline"),
        asyncio.create_task(source.run(), name="state-source"),
        asyncio.create_task(_tts_selfcheck(tts), name="tts-selfcheck"),
    ]
    if load_source is not None:
        tasks.append(asyncio.create_task(load_source.run(), name="load-source"))
    for task in tasks:
        task.add_done_callback(_watch)

    logger.info(
        "系统启动完成：情绪源=%s 负荷源=%s LLM=%s ASR=%s TTS=%s",
        source.name,
        load_source.name if load_source else "off",
        llm.name,
        asr.name,
        tts.name,
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pipeline.shutdown()
        await llm.aclose()
        await asr.aclose()
        await tts.aclose()
        await dispose_db()
        logger.info("系统已关闭")


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(logs.router)
app.include_router(ws_monitor.router)
app.include_router(ws_voice.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "ws": ["/ws/monitor", "/ws/voice"],
    }
