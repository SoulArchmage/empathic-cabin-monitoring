"""pytest 全局配置。

必须在导入 app 之前设置环境变量：Settings 在模块导入时就实例化，
晚于导入再改环境变量不会生效。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 测试库放在系统临时目录：既不污染仓库里的 data/，也避免上次运行残留的
# 库文件被占用（Windows 上 sqlite 文件被锁时 unlink 会直接 PermissionError）。
TEST_DB_DIR = Path(tempfile.gettempdir()) / "soothe-tests"
TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
TEST_DB = TEST_DB_DIR / "test_soothe.db"
TEST_DB.unlink(missing_ok=True)

os.environ["DB_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SOURCE_MODE"] = "simulator"
# 测试默认关闭第二路（脑电负荷）数据源：用例只验证各自关心的那一路，
# 避免内置负荷模拟在长用例里爬升到 High 而误触发 high_load 干预。
os.environ["LOAD_SOURCE_MODE"] = "off"

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
