"""历史数据接口：会话列表、状态曲线、干预记录与 CSV 导出。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..db.repo import Repository
from ..schemas import InterventionPage, SessionList, StatePage

router = APIRouter(prefix="/api", tags=["logs"])


def _repo(request: Request) -> Repository:
    return request.app.state.repo


@router.get("/sessions", response_model=SessionList)
async def list_sessions(request: Request) -> SessionList:
    return SessionList(items=await _repo(request).list_sessions())


@router.get("/states", response_model=StatePage)
async def query_states(
    request: Request,
    session_id: str | None = None,
    start: float | None = None,
    end: float | None = None,
    limit: int = Query(1000, ge=1, le=20000),
    offset: int = Query(0, ge=0),
) -> StatePage:
    items, total = await _repo(request).query_states(
        session_id, start, end, limit, offset
    )
    return StatePage(items=items, total=total, limit=limit, offset=offset)


@router.get("/interventions", response_model=InterventionPage)
async def query_interventions(
    request: Request,
    session_id: str | None = None,
    start: float | None = None,
    end: float | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> InterventionPage:
    items, total = await _repo(request).query_interventions(
        session_id, start, end, limit, offset
    )
    return InterventionPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/export/states.csv")
async def export_states(
    request: Request,
    session_id: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> StreamingResponse:
    return _csv_response(
        _repo(request).export_states_csv(session_id, start, end), "states.csv"
    )


@router.get("/export/interventions.csv")
async def export_interventions(
    request: Request,
    session_id: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> StreamingResponse:
    return _csv_response(
        _repo(request).export_interventions_csv(session_id, start, end),
        "interventions.csv",
    )


def _csv_response(generator, filename: str) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # 让浏览器端 fetch/axios 也能读到文件名
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
