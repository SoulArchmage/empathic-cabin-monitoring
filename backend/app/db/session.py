"""异步数据库会话管理。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import settings
from .models import Base

logger = logging.getLogger(__name__)

_SQLITE_PREFIX = "sqlite+aiosqlite:///"
# backend/ 目录：把相对 sqlite 路径锚定到这里，避免"从哪里启动就在哪里建库"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _resolve_sqlite(url: str) -> tuple[str, Path | None]:
    """把相对的 sqlite 路径解析为 backend/ 下的绝对路径。"""
    if not url.startswith(_SQLITE_PREFIX):
        return url, None
    raw = url[len(_SQLITE_PREFIX) :]
    if not raw or raw.startswith(":memory:"):
        return url, None
    path = Path(raw)
    if not path.is_absolute():
        path = _BACKEND_ROOT / path
    path = path.resolve()
    return f"{_SQLITE_PREFIX}{path.as_posix()}", path


DSN, _DB_PATH = _resolve_sqlite(settings.db_url)
if _DB_PATH is not None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(DSN, echo=False, future=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _prepare_schema(conn) -> None:
    """建表；旧版状态表（含认知负荷字段）就地迁移，保留历史曲线。

    旧版表结构与新契约的差异只有两列：少了 window、多了 cognitive_level / cognitive_score。
    因此不做"删表重建"（那会丢掉历史曲线），而是补 window 列、删认知负荷列。
    旧数据的 window 为空串，查询侧已按 "—" 处理。
    """
    inspector = inspect(conn)
    if "state_frames" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("state_frames")}
        legacy = {"cognitive_level", "cognitive_score"} & columns
        if "window" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE state_frames ADD COLUMN window VARCHAR(32) "
                    "NOT NULL DEFAULT ''"
                )
            )
        for name in legacy:
            conn.execute(text(f"ALTER TABLE state_frames DROP COLUMN {name}"))
        if legacy:
            logger.info(
                "已迁移旧版状态表：移除 %s 并补充 window 列（历史数据保留）",
                " / ".join(sorted(legacy)),
            )
    Base.metadata.create_all(conn)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_prepare_schema)
    logger.info("数据库就绪：%s", DSN)


async def dispose_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用。"""
    async with SessionFactory() as session:
        yield session
