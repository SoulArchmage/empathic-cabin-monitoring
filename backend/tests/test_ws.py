"""WebSocket 集成测试：状态流下行、1Hz 落库、语音文本上行闭环。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from fastapi.testclient import TestClient

from app.main import app


def wait_until(check: Callable[[], bool], timeout: float = 10.0) -> bool:
    """轮询等待异步链路（LLM → TTS → 落库）完成。

    固定 time.sleep 在慢机器 / CI 上容易偶发失败，这里改为轮询到超时为止。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return False


def test_monitor_stream_pushes_state_frames() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/monitor") as ws:
            meta = ws.receive_json()
            assert meta["type"] == "meta"
            assert "providers" in meta["payload"]

            states = 0
            deadline = time.time() + 5
            while states < 5 and time.time() < deadline:
                message = ws.receive_json()
                if message["type"] == "state":
                    frame = message["payload"]["frame"]
                    assert frame["emotion"]["label"] in {"Positive", "Negative", "Neutral"}
                    assert 0 <= frame["cognitive"]["score"] <= 100
                    states += 1
            assert states >= 5, "未在预期时间内收到足够的状态帧"

        # 状态帧按 1Hz 聚合落库，轮询等待一个落库周期
        assert wait_until(lambda: client.get("/api/states").json()["total"] >= 1), (
            "状态曲线未落库"
        )


def test_voice_text_roundtrip_persists_intervention() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "text", "text": "我今天有点累"}))
            message = ws.receive_json()
            assert message["type"] == "asr_final"
            assert message["payload"]["text"] == "我今天有点累"

            # 回复在独立后台任务中生成，需保持语音通道打开，任务才能跑完并落库。
            # 轮询等待 LLM → TTS → 落库完成，而非固定 sleep（后者在慢机器上会 flaky）。
            assert wait_until(
                lambda: client.get("/api/interventions").json()["total"] >= 1
            ), "语音闭环未落库"

        latest = client.get("/api/interventions").json()["items"][0]
        assert latest["speech"]
        assert latest["strategy"] in {"呼吸引导", "共情复述", "环境调节", "轻量提问"}
        assert "语音交互" in latest["reason"]


def test_voice_rejects_empty_utterance() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "text", "text": "   "}))
            message = ws.receive_json()
            assert message["type"] == "error"
