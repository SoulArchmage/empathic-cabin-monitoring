"""系统级接口：健康检查与模拟演示注入。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request

from ..observability import snapshot
from ..schemas import Envelope, HealthResponse, ProviderStatus, ScenarioRequest

router = APIRouter(prefix="/api", tags=["system"])


def _status(
    key: str, name: str, mode: Literal["mock", "real"], ready: bool
) -> ProviderStatus:
    """把"配置就绪度"与"最近一次真实调用结果"合并成一个状态。"""
    rec = snapshot(key)
    return ProviderStatus(
        name=name,
        mode=mode,
        ready=ready,
        last_ok=rec.ok,
        last_detail=rec.detail,
        last_at=rec.ts,
    )


def collect_providers(state) -> dict[str, ProviderStatus]:
    """汇总各适配层的实现与可用性，供前端常驻展示。"""
    return {
        "llm": _status("llm", state.llm.name, state.llm.mode, state.llm.ready),
        "asr": _status("asr", state.asr.name, state.asr.mode, state.asr.ready),
        "tts": _status("tts", state.tts.name, state.tts.mode, state.tts.ready),
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
