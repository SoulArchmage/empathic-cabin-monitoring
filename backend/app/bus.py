"""事件总线：把"数据源"与"消费者"彻底解耦。

数据源只做 publish；广播、决策、落库各自 subscribe 独立消费。
新增一个消费者（例如后期加 Webhook 告警）无需改动数据源一行代码。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """基于 asyncio.Queue 的多订阅者扇出，带 drop-oldest 背压。"""

    def __init__(self, name: str = "bus") -> None:
        self.name = name
        self._subs: set[asyncio.Queue[Any]] = set()

    def subscribe(self, maxsize: int = 64) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self._subs.discard(q)

    def publish(self, item: Any) -> None:
        """同步发布：任何订阅者队列满时丢弃最旧元素，绝不阻塞生产者。

        实时系统里"推送不卡顿"优先于"帧不丢失"，因此不做 await。
        """
        # 用快照迭代：订阅者可能在运行期（如消费任务退出时）退订
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - 竞态兜底
                    pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:  # pragma: no cover - 竞态兜底
                pass
