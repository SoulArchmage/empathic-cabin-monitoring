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
from .providers.asr.base import build_asr_provider
from .providers.llm.base import build_llm_provider
from .providers.tts.base import build_tts_provider
from .service.pipeline import PipelineService
from .sources.base import StateSource
from .sources.external import ExternalSource
from .sources.simulator import SimulatorSource

logger = logging.getLogger(__name__)


def build_source(bus: EventBus) -> StateSource:
    if settings.source_mode == "external":
        return ExternalSource(bus, settings.sample_hz, settings)
    return SimulatorSource(bus, settings.sample_hz)


def _watch(task: asyncio.Task) -> None:
    """后台任务异常必须显式记录，否则会静默死掉。"""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("后台任务 %s 异常退出", task.get_name())


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
    )

    app.state.app_title = settings.app_name
    app.state.bus = bus
    app.state.hub = hub
    app.state.source = source
    app.state.llm = llm
    app.state.asr = asr
    app.state.tts = tts
    app.state.repo = pipeline.repo
    app.state.pipeline = pipeline

    tasks = [
        asyncio.create_task(hub.run(), name="hub-broadcast"),
        asyncio.create_task(pipeline.run(), name="pipeline"),
        asyncio.create_task(source.run(), name="state-source"),
    ]
    for task in tasks:
        task.add_done_callback(_watch)

    logger.info(
        "系统启动完成：数据源=%s LLM=%s ASR=%s TTS=%s",
        source.name,
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
