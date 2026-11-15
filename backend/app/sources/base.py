"""情绪数据源抽象。

情绪通道接入"现有情绪识别系统"：它每 2 秒输出一个滑动窗口的三分类结果
（Neutral / Positive / Negative）与置信度。这套外部系统属于可变依赖，因此抽象为
StateSource——内置模拟、WebSocket 直连、直接读取 state.json 三种实现可随时替换，
业务代码只依赖本接口。

第二路（脑电认知负荷等级）另有一套 LoadSource 抽象，见 load_source.py：
它是"状态量"而非窗口流，因此不与本接口混在一起。
"""

from __future__ import annotations

import abc
import logging

from ..bus import EventBus
from ..schemas import new_session_id

logger = logging.getLogger(__name__)

# 与前端 SCENARIO_LABELS、后端 ScenarioName 保持一致
SCENARIOS = ("calm", "anxiety_escalation", "recovery", "fluctuation")


class StateSource(abc.ABC):
    """情绪窗口数据源。"""

    name: str = "base"

    def __init__(self, bus: EventBus, sample_hz: float = 0.5) -> None:
        self.bus = bus
        self.sample_hz = sample_hz
        self._scenario = "calm"
        self.session_id = new_session_id()

    @property
    def current_scenario(self) -> str:
        return self._scenario

    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"未知模拟：{name}")
        logger.info("切换模拟 -> %s", name)
        self._scenario = name
        self._on_scenario_changed(name)

    def _on_scenario_changed(self, name: str) -> None:  # noqa: B027 - 可选钩子
        pass

    @abc.abstractmethod
    async def run(self) -> None:
        """持续产出状态帧并 publish 到总线，直到被取消。"""
