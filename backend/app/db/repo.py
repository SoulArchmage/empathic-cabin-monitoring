"""数据访问层：写入状态采样与干预记录，支持历史查询与导出。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..schemas import Intervention, StateFrame
from .models import InterventionRecord, StateFrameRecord

logger = logging.getLogger(__name__)

_STATE_CSV_HEADER = (
    "session_id,ts,emotion_label,emotion_confidence,cognitive_level,"
    "cognitive_score,source"
)
_INTERVENTION_CSV_HEADER = (
    "id,session_id,ts,kind,severity,reason,speech,strategy,risk,degraded,"
    "provider,latency_ms,audio_available"
)
_EXPORT_BATCH = 500  # 流式导出的单批行数


def _csv_cell(value: Any) -> str:
    """CSV 转义：话术里可能含逗号、引号、换行，这里统一用双引号包裹。"""
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    # ---- 写入 ------------------------------------------------------------ #
    async def add_state(self, frame: StateFrame) -> None:
        async with self.session_factory() as session:
            session.add(
                StateFrameRecord(
                    session_id=frame.session_id,
                    ts=frame.ts,
                    emotion_label=frame.emotion.label,
                    emotion_confidence=frame.emotion.confidence,
                    cognitive_level=frame.cognitive.level,
                    cognitive_score=frame.cognitive.score,
                    source=frame.source,
                )
            )
            await session.commit()

    async def add_intervention(self, item: Intervention) -> Intervention:
        async with self.session_factory() as session:
            record = InterventionRecord(
                session_id=item.session_id,
                ts=item.ts,
                kind=item.kind,
                severity=item.severity,
                reason=item.reason,
                prompt_digest=item.prompt_digest,
                speech=item.speech,
                strategy=item.strategy,
                risk=item.risk,
                degraded=int(item.degraded),
                provider=item.provider,
                latency_ms=item.latency_ms,
                audio_available=int(item.audio_available),
                stats_json=item.stats.model_dump_json() if item.stats else "",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            item.id = record.id
        return item

    # ---- 查询 ------------------------------------------------------------ #
    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            state_stmt = (
                select(
                    StateFrameRecord.session_id,
                    func.min(StateFrameRecord.ts),
                    func.max(StateFrameRecord.ts),
                    func.count(StateFrameRecord.id),
                )
                .group_by(StateFrameRecord.session_id)
            )
            iv_stmt = (
                select(
                    InterventionRecord.session_id,
                    func.min(InterventionRecord.ts),
                    func.max(InterventionRecord.ts),
                    func.count(InterventionRecord.id),
                )
                .group_by(InterventionRecord.session_id)
            )
            state_rows = (await session.execute(state_stmt)).all()
            iv_rows = (await session.execute(iv_stmt)).all()

        # 两个表取并集：只有干预、还没有状态采样点的会话同样要出现在列表里
        merged: dict[str, dict[str, Any]] = {}
        for sid, started, ended, points in state_rows:
            merged[sid] = {
                "session_id": sid,
                "started_at": started,
                "ended_at": ended,
                "state_points": points,
                "interventions": 0,
            }
        for sid, started, ended, count in iv_rows:
            entry = merged.get(sid)
            if entry is None:
                merged[sid] = {
                    "session_id": sid,
                    "started_at": started,
                    "ended_at": ended,
                    "state_points": 0,
                    "interventions": count,
                }
            else:
                entry["interventions"] = count
                entry["started_at"] = min(entry["started_at"], started)
                entry["ended_at"] = max(entry["ended_at"], ended)
        return sorted(merged.values(), key=lambda item: item["ended_at"], reverse=True)

    async def query_states(
        self,
        session_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = self._state_conditions(session_id, start, end)
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count(StateFrameRecord.id)).where(*conditions)
            )
            stmt = (
                select(StateFrameRecord)
                .where(*conditions)
                # ts 相同（1Hz 聚合可能撞秒）时用自增主键兜底，保证分页顺序稳定
                .order_by(StateFrameRecord.ts, StateFrameRecord.id)
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [self._state_to_dict(r) for r in rows], int(total or 0)

    async def query_interventions(
        self,
        session_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = self._intervention_conditions(session_id, start, end)
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count(InterventionRecord.id)).where(*conditions)
            )
            stmt = (
                select(InterventionRecord)
                .where(*conditions)
                .order_by(InterventionRecord.ts.desc(), InterventionRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [self._intervention_to_dict(r) for r in rows], int(total or 0)

    # ---- 导出 ------------------------------------------------------------ #
    # 两个导出都用 stream_scalars + yield_per 真正逐批读取：历史数据可能上万行，
    # 一次性 .all() 载入会让内存随数据量线性增长，与"流式导出"的说法不符。
    async def export_states_csv(
        self, session_id: str | None = None, start: float | None = None, end: float | None = None
    ) -> AsyncIterator[str]:
        yield _STATE_CSV_HEADER + "\n"
        conditions = self._state_conditions(session_id, start, end)
        stmt = (
            select(StateFrameRecord)
            .where(*conditions)
            .order_by(StateFrameRecord.ts, StateFrameRecord.id)
            .execution_options(yield_per=_EXPORT_BATCH)
        )
        async with self.session_factory() as session:
            result = await session.stream_scalars(stmt)
            async for r in result:
                yield (
                    f"{r.session_id},{r.ts},{r.emotion_label},{r.emotion_confidence},"
                    f"{r.cognitive_level},{r.cognitive_score},{r.source}\n"
                )

    async def export_interventions_csv(
        self, session_id: str | None = None, start: float | None = None, end: float | None = None
    ) -> AsyncIterator[str]:
        yield _INTERVENTION_CSV_HEADER + "\n"
        conditions = self._intervention_conditions(session_id, start, end)
        stmt = (
            select(InterventionRecord)
            .where(*conditions)
            .order_by(InterventionRecord.ts, InterventionRecord.id)
            .execution_options(yield_per=_EXPORT_BATCH)
        )
        async with self.session_factory() as session:
            result = await session.stream_scalars(stmt)
            async for r in result:
                cells = [
                    r.id, r.session_id, r.ts, r.kind, r.severity,
                    _csv_cell(r.reason), _csv_cell(r.speech), r.strategy, r.risk,
                    r.degraded, r.provider, r.latency_ms, r.audio_available,
                ]
                yield ",".join(str(c) for c in cells) + "\n"

    # ---- 内部 ------------------------------------------------------------ #
    @staticmethod
    def _state_conditions(session_id: str | None, start: float | None, end: float | None):
        conds = []
        if session_id:
            conds.append(StateFrameRecord.session_id == session_id)
        if start is not None:
            conds.append(StateFrameRecord.ts >= start)
        if end is not None:
            conds.append(StateFrameRecord.ts <= end)
        return conds

    @staticmethod
    def _intervention_conditions(session_id: str | None, start: float | None, end: float | None):
        conds = []
        if session_id:
            conds.append(InterventionRecord.session_id == session_id)
        if start is not None:
            conds.append(InterventionRecord.ts >= start)
        if end is not None:
            conds.append(InterventionRecord.ts <= end)
        return conds

    @staticmethod
    def _state_to_dict(r: StateFrameRecord) -> dict[str, Any]:
        return {
            "ts": r.ts,
            "session_id": r.session_id,
            "emotion_label": r.emotion_label,
            "emotion_confidence": r.emotion_confidence,
            "cognitive_level": r.cognitive_level,
            "cognitive_score": r.cognitive_score,
            "source": r.source,
        }

    @staticmethod
    def _intervention_to_dict(r: InterventionRecord) -> dict[str, Any]:
        return {
            "id": r.id,
            "ts": r.ts,
            "session_id": r.session_id,
            "kind": r.kind,
            "severity": r.severity,
            "reason": r.reason,
            "prompt_digest": r.prompt_digest,
            "speech": r.speech,
            "strategy": r.strategy,
            "risk": r.risk,
            "degraded": bool(r.degraded),
            "provider": r.provider,
            "latency_ms": r.latency_ms,
            "audio_available": bool(r.audio_available),
            "stats": json.loads(r.stats_json) if r.stats_json else None,
        }
