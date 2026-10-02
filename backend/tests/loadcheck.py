"""压力与延迟测试：验证"WebSocket 推送不卡顿"这一工程要求。

覆盖三个场景：
  1. 多客户端并发订阅，观察各端实际收帧率是否与采集频率一致；
  2. 故意加入一个"慢消费者"（每条消息额外 sleep），验证慢客户端不会拖垮其他客户端，
     也不会阻塞服务端广播（这是每连接队列 + drop-oldest 背压的设计目标）；
  3. 端到端干预延迟：注入异常剧本，测量从状态异常到收到 intervention 的耗时。

运行方式（需先启动后端）：python -m tests.loadcheck
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws/monitor"
DURATION = 20.0
SLOW_DELAY = 0.35  # 慢消费者每条消息的处理耗时


async def subscriber(name: str, delay: float, stop_at: float, stats: dict) -> None:
    gaps: list[float] = []
    frames = 0
    last = None
    try:
        async with websockets.connect(WS, max_size=None) as ws:
            while time.time() < stop_at:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                message = json.loads(raw)
                if message["type"] != "state":
                    continue
                now = time.time()
                if last is not None:
                    gaps.append(now - last)
                last = now
                frames += 1
                if delay:
                    await asyncio.sleep(delay)
    finally:
        span = DURATION
        stats[name] = {
            "frames": frames,
            "rate_hz": round(frames / span, 2),
            "max_gap_ms": round(max(gaps) * 1000, 1) if gaps else 0.0,
            "p95_gap_ms": round(statistics.quantiles(gaps, n=20)[18] * 1000, 1) if len(gaps) > 20 else 0.0,
        }


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        before = (await client.get("/api/health")).json()
        print(f"[基线] 服务端帧率 {before['frame_rate_hz']} Hz，在线客户端 {before['active_clients']}")

        stop_at = time.time() + DURATION
        stats: dict = {}
        tasks = [
            asyncio.create_task(subscriber("normal-A", 0.0, stop_at, stats)),
            asyncio.create_task(subscriber("normal-B", 0.0, stop_at, stats)),
            asyncio.create_task(subscriber("normal-C", 0.0, stop_at, stats)),
            asyncio.create_task(subscriber("slow (每帧+350ms)", SLOW_DELAY, stop_at, stats)),
        ]

        # 并发进行干预延迟测量
        await asyncio.sleep(1.0)
        started = time.time()
        await client.post("/api/scenario", json={"name": "anxiety_escalation"})
        latency = await measure_first_intervention(timeout=DURATION - 5)

        await asyncio.gather(*tasks)

        after = (await client.get("/api/health")).json()
        print(f"[结束] 服务端帧率 {after['frame_rate_hz']} Hz，在线客户端 {after['active_clients']}")
        print(f"[干预延迟] 剧本注入 → 收到 intervention：{latency:.2f} s（含首次去抖确认 ~1s）")
        print(f"[收帧统计] 采样窗口 {DURATION:.0f}s")
        for name, item in stats.items():
            print(
                f"  - {name:<22} 收帧 {item['frames']:>4} 帧  "
                f"实测 {item['rate_hz']:>5} Hz  "
                f"最大间隔 {item['max_gap_ms']:>6} ms  P95 间隔 {item['p95_gap_ms']:>6} ms"
            )

        await client.post("/api/scenario", json={"name": "calm"})


async def measure_first_intervention(timeout: float) -> float:
    started = time.time()
    try:
        async with websockets.connect(WS, max_size=None) as ws:
            while time.time() - started < timeout:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                message = json.loads(raw)
                if message["type"] == "intervention":
                    return time.time() - started
    except (asyncio.TimeoutError, OSError):
        return float("nan")
    return float("nan")


if __name__ == "__main__":
    asyncio.run(main())
