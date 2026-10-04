"""系统级接口：健康检查与模拟演示注入。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import Envelope, HealthResponse, ProviderStatus, ScenarioRequest

router = APIRouter(prefix="/api", tags=["system"])


def collect_providers(state) -> dict[str, ProviderStatus]:
    """汇总各适配层的实现与可用性，供前端常驻展示。"""
    return {
        "llm": ProviderStatus(
            name=state.llm.name, mode=state.llm.mode, ready=state.llm.ready
        ),
        "asr": ProviderStatus(
            name=state.asr.name, mode=state.asr.mode, ready=state.asr.ready
        ),
        "tts": ProviderStatus(
            name=state.tts.name, mode=state.tts.mode, ready=state.tts.ready
        ),
        "source": ProviderStatus(
            name=state.source.name,
            mode="mock" if state.source.name == "simulator" else "real",
            ready=True,
        ),
    }


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    state = request.app.state
    return HealthResponse(
        app=state.app_title,
        frame_rate_hz=state.hub.frame_meter.rate,
        active_clients=state.hub.active_clients,
        current_scenario=state.source.current_scenario,
        providers=collect_providers(state),
    )


@router.post("/scenario", response_model=dict)
async def set_scenario(payload: ScenarioRequest, request: Request) -> dict:
    """注入模拟演示，用于复现"平静 → 焦虑升级 → 共情干预 → 恢复"闭环。"""
    state = request.app.state
    state.source.set_scenario(payload.name)
    state.hub.broadcast(
        Envelope.of("scenario", scenario=payload.name, session_id=state.source.session_id)
    )
    return {"scenario": payload.name, "session_id": state.source.session_id}
