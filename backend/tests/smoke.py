"""端到端冒烟检查：WS 状态流 → 剧本注入 → 干预生成 → 历史查询 → CSV 导出。

以脚本方式运行（需先启动后端）：python -m tests.smoke
"""

from __future__ import annotations

import asyncio
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws/monitor"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        health = (await client.get("/api/health")).json()
        print("[health]", json.dumps(health, ensure_ascii=False))

        received = {"state": 0, "meta": 0, "pending": 0, "intervention": 0, "speak": 0}

        async with websockets.connect(WS, max_size=None) as ws:
            # 注入焦虑升级剧本，验证"状态异常 → 触发共情"闭环
            await client.post("/api/scenario", json={"name": "anxiety_escalation"})
            deadline = asyncio.get_event_loop().time() + 25
            seen: list[dict] = []
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                received[msg["type"]] = received.get(msg["type"], 0) + 1
                if msg["type"] == "intervention":
                    seen.append(msg["payload"]["item"])
                # 至少观察到一次 mild（首次陪伴）与一次 severe（安全出口）后再退出
                if len(seen) >= 2:
                    break

        print("[ws 收帧统计]", received)
        for item in seen:
            print(
                f"[干预] severity={item['severity']} degraded={item['degraded']} "
                f"provider={item['provider']} latency={item['latency_ms']}ms "
                f"reason={item['reason']}\n        speech={item['speech']}"
            )

        # 切回平静并等待恢复事件
        await client.post("/api/scenario", json={"name": "recovery"})
        await asyncio.sleep(6)

        sessions = (await client.get("/api/sessions")).json()
        print("[会话列表]", json.dumps(sessions, ensure_ascii=False)[:300])

        states = (await client.get("/api/states", params={"limit": 5})).json()
        print("[状态点数]", states["total"], "首条：", states["items"][:1])

        items = (await client.get("/api/interventions", params={"limit": 5})).json()
        print("[干预记录数]", items["total"])

        csv = await client.get("/api/export/interventions.csv")
        print("[CSV 导出] 状态码", csv.status_code, "行数", len(csv.text.splitlines()))
        print(csv.text.splitlines()[:2])

        health2 = (await client.get("/api/health")).json()
        print("[推送帧率]", health2["frame_rate_hz"], "Hz  在线客户端", health2["active_clients"])


if __name__ == "__main__":
    asyncio.run(main())
