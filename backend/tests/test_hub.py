"""WS 连接管理测试：控制消息不被状态流阻塞、状态队列 drop-oldest 背压。"""

from __future__ import annotations

import asyncio

from app.hub import WSClient
from app.schemas import Envelope


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_ctrl_message_delivered_without_state_frames() -> None:
    """控制消息必须立刻发出。

    回归：sender 曾用 ``await state_queue.get()`` 阻塞等待，数据源没有状态帧时，
    干预卡片/语音播报/pong 会一直卡在队列里发不出去。
    """
    client = WSClient(FakeWebSocket(), queue_size=8)  # type: ignore[arg-type]
    client.offer(Envelope.of("speak", text="先说话"))
    task = asyncio.create_task(client.sender())
    await asyncio.sleep(0.05)

    assert [message["type"] for message in client.ws.sent] == ["speak"]
    assert not task.done(), "发送协程应继续等待后续消息"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_state_frame_wakes_idle_sender() -> None:
    client = WSClient(FakeWebSocket(), queue_size=8)  # type: ignore[arg-type]
    task = asyncio.create_task(client.sender())
    await asyncio.sleep(0.02)  # 让 sender 进入等待

    client.offer(Envelope.of("state", frame={"n": 1}), droppable=True)
    await asyncio.sleep(0.05)

    assert [message["type"] for message in client.ws.sent] == ["state"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_state_queue_drops_oldest_and_ctrl_is_immune() -> None:
    client = WSClient(FakeWebSocket(), queue_size=2)  # type: ignore[arg-type]
    for index in range(4):
        client.offer(Envelope.of("state", frame=index), droppable=True)

    assert client.dropped == 2
    kept = [client.state_queue.get_nowait().payload["frame"] for _ in range(2)]
    assert kept == [2, 3], "队列满时应丢最旧帧，保留最新帧"

    client.offer(Envelope.of("speak", text="x"))
    assert client.ctrl_queue.qsize() == 1, "状态流拥塞不得顶掉控制消息"