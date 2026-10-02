"""数据源抽象。

任务书要求接入"现有情绪识别系统"与"认知负荷监测系统"两路数据流。
这两套系统属于外部可变依赖，因此抽象为 StateSource：
当前用内置模拟器实现，后续对接真实系统只需补一个实现并改 .env 的 SOURCE_MODE。
"""

from __future__ import annotations

import abc
import logging

from ..bus import EventBus
from ..schemas import new_session_id

logger = logging.getLogger(__name__)

SCENARIOS = ("calm", "anxiety_escalation", "recovery", "high_load")


class StateSource(abc.ABC):
    """多源状态数据源。"""

    name: str = "base"

    def __init__(self, bus: EventBus, sample_hz: float = 10.0) -> None:
        self.bus = bus
        self.sample_hz = sample_hz
        self._scenario = "calm"
        self.session_id = new_session_id()

    @property
    def current_scenario(self) -> str:
        return self._scenario

    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"未知剧本：{name}")
        logger.info("切换剧本 -> %s", name)
        self._scenario = name
        self._on_scenario_changed(name)

    def _on_scenario_changed(self, name: str) -> None:  # noqa: B027 - 可选钩子
        pass

    @abc.abstractmethod
    async def run(self) -> None:
        """持续产出状态帧并 publish 到总线，直到被取消。"""
