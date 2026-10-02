"""pytest 全局配置。

必须在导入 app 之前设置环境变量：Settings 在模块导入时就实例化，
晚于导入再改环境变量不会生效。
"""

from __future__ import annotations

import os
from pathlib import Path

# 与 app/db/session.py 一致：相对 sqlite 路径锚定到 backend/，不受启动目录影响
TEST_DB = Path(__file__).resolve().parents[1] / "data" / "test_soothe.db"
TEST_DB.unlink(missing_ok=True)

os.environ["DB_URL"] = "sqlite+aiosqlite:///./data/test_soothe.db"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SOURCE_MODE"] = "simulator"
