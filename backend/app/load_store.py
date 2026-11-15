"""脑电负荷等级的共享快照（第二路数据源的中枢）。

为什么需要它：情绪通道与脑电负荷通道是两个独立的外部系统，节奏也不同
（情绪 2s 一窗；EEG 1s 一步、4s 一窗）。但业务上两者必须合成同一条时间线：

- 情绪帧落库/推送时，要带上"此刻最新的脑电负荷"；
- 负荷单独更新时，前端也要立刻看到（不能等下一个 2s 情绪窗口）。

因此这里只保留**最新值**而不是流：负荷是状态量，晚到的情绪帧贴上最新快照即可，
无需为中间丢失的帧做补偿。``attach()`` 是幂等的，pipeline 与 hub 都调用它也安全。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .schemas import CognitiveLoad, StateFrame

logger = logging.getLogger(__name__)

Listener = Callable[[CognitiveLoad], None]


class LoadStore:
    """最新一条脑电负荷结果 + 更新事件扇出。"""

    def __init__(self) -> None:
        self.latest: CognitiveLoad | None = None
        self.updated_at: float = 0.0
        self._listeners: list[Listener] = []

    # ---- 订阅 ------------------------------------------------------------ #
    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # ---- 写入 ------------------------------------------------------------ #
    def update(self, load: CognitiveLoad) -> None:
        """写入最新负荷并同步扇出（监听者异常不得影响数据源）。"""
        self.latest = load
        self.updated_at = time.time()
        for listener in list(self._listeners):
            try:
                listener(load)
            except Exception:  # noqa: BLE001 - 广播失败不应打断 EEG 接收
                logger.exception("负荷更新订阅者执行失败")

    # ---- 读取 ------------------------------------------------------------ #
    def attach(self, frame: StateFrame) -> StateFrame:
        """把最新负荷贴到情绪帧上；帧已自带负荷（报文里直接带了 load）时不覆盖。"""
        if frame.load is None and self.latest is not None:
            frame.load = self.latest
        return frame

    @property
    def level(self) -> str:
        return self.latest.level if self.latest else ""

    def snapshot(self) -> CognitiveLoad | None:
        return self.latest
