"""全局配置：统一从 .env 读取，所有可调参数集中于此。

设计意图：任务书要求系统需接入"现有数据源"与"外部大模型/语音 API"，
二者都属于会变化的外部依赖，因此把它们的开关与参数全部收敛到配置层，
业务代码只依赖接口，切换实现时仅需改 .env。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录：把 .env 锚定到这里，避免"从哪个目录启动就读不到 .env"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "智能疏导仓共情语音交互与状态监测系统"
    log_level: str = "INFO"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # ---- 数据接入层 ----
    source_mode: Literal["simulator", "external"] = "simulator"
    sample_hz: float = 10.0          # 状态帧采集频率
    external_state_url: str = ""     # 外部情绪/脑电系统 WebSocket 地址

    # ---- 触发引擎（抗幻觉第一道防线：先判定，再调 LLM）----
    trigger_window: int = 15         # 滑动窗口帧数
    trigger_min_consec: int = 10     # 确认异常所需连续帧数（去抖）
    trigger_ratio: float = 0.6       # 窗口内异常占比阈值
    trigger_cooldown_s: float = 60.0 # 同类干预冷却期
    trigger_recovery_frames: int = 20  # 判定恢复常态所需连续帧数
    trigger_escalate_after_s: float = 8.0  # 异常持续超过该时长才升级为 severe

    # ---- 持久化 ----
    db_url: str = "sqlite+aiosqlite:///./data/soothe.db"
    persist_hz: float = 1.0          # 状态帧降频落库频率

    # ---- LLM 适配层 ----
    # 除 mock 外，其余厂商均通过 OpenAI 兼容协议接入，切换厂商只需改 LLM_PROVIDER + LLM_API_KEY
    llm_provider: Literal["mock", "deepseek", "zhipu", "qwen", "openai"] = "mock"
    llm_api_key: str = ""
    llm_model: str = ""      # 留空则使用所选厂商的默认模型
    llm_base_url: str = ""   # 留空则使用所选厂商的默认地址
    llm_timeout_s: float = 20.0
    llm_max_retry: int = 1

    # ---- ASR / TTS 适配层 ----
    # 真实 ASR 走 OpenAI Whisper 兼容接口（POST {base_url}/audio/transcriptions），
    # 自建网关或私有部署用 ASR_BASE_URL 覆盖；未配置 Key 时 /api/health 会标记 ready=false
    asr_provider: Literal["mock", "openai"] = "mock"
    asr_api_key: str = ""
    asr_model: str = ""      # 留空则使用默认模型 whisper-1
    asr_base_url: str = ""   # 留空则使用默认 OpenAI 地址
    asr_timeout_s: float = 20.0
    tts_provider: Literal["mock", "edge"] = "mock"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # ---- WebSocket ----
    ws_queue_size: int = 64          # 每连接发送队列容量（背压用）
    # 同一语音连接两次上行之间的最小间隔：每条有效输入都会触发一次大模型调用，
    # 不限速时恶意/异常客户端可放大调用成本（限流在连接粒度上做，够用且无状态）
    voice_min_interval_s: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()