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

# 真实外部服务的凭证与地址一律清空：开发者本地 .env 往往填了真实 Key，
# 环境变量优先级高于 .env，因此在这里显式置空可保证测试既不误发真实网络请求，
# 也不依赖本机配置（用例自己通过 Settings(**overrides) 传入所需值）。
for _key in (
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "ASR_API_KEY",
    "ASR_MODEL",
    "ASR_BASE_URL",
):
    os.environ[_key] = ""
os.environ["ASR_PROVIDER"] = "mock"
os.environ["TTS_PROVIDER"] = "mock"
