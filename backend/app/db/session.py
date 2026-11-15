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
    """建表；旧库就地迁移，保留历史曲线。

    历史上表结构变动过两次，都在这里做"加列/删列"而不是删表重建：

    - 早期版本有 cognitive_level / cognitive_score，后来契约改为感知系统三标签，两列被移除；
    - 现在重新接入独立的 EEG 负荷通道，但字段语义变为"负荷快照"，
      因此新增 load_level / load_confidence（与旧列名区分，避免历史数据被误读）。
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
        added: list[str] = []
        if "load_level" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE state_frames ADD COLUMN load_level VARCHAR(16) "
                    "NOT NULL DEFAULT ''"
                )
            )
            added.append("load_level")
        if "load_confidence" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE state_frames ADD COLUMN load_confidence FLOAT "
                    "NOT NULL DEFAULT 0"
                )
            )
            added.append("load_confidence")
        for name in legacy:
            conn.execute(text(f"ALTER TABLE state_frames DROP COLUMN {name}"))
        if legacy or added:
            logger.info(
                "已迁移旧版状态表：移除 %s、新增 %s（历史数据保留）",
                " / ".join(sorted(legacy)) or "无",
                " / ".join(added) or "无",
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
