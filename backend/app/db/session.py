"""异步数据库会话管理。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

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


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库就绪：%s", DSN)


async def dispose_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用。"""
    async with SessionFactory() as session:
        yield session
