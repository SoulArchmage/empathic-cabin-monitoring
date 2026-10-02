"""REST 接口测试：健康检查、剧本注入、历史查询与 CSV 导出。"""

from __future__ import annotations

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client():
    """手动驱动 lifespan，让 init_db 与后台任务在测试中真实运行。"""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as instance:
            yield instance


async def test_health_reports_providers(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["providers"]) == {"llm", "asr", "tts", "source"}
    assert body["providers"]["llm"]["mode"] in {"mock", "real"}


@pytest.mark.parametrize("name", ["calm", "anxiety_escalation", "recovery", "high_load"])
async def test_switch_scenario(client: httpx.AsyncClient, name: str) -> None:
    response = await client.post("/api/scenario", json={"name": name})
    assert response.status_code == 200
    assert response.json()["scenario"] == name

    health = (await client.get("/api/health")).json()
    assert health["current_scenario"] == name


async def test_unknown_scenario_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/scenario", json={"name": "not-exist"})
    assert response.status_code == 422


async def test_history_endpoints_shape(client: httpx.AsyncClient) -> None:
    sessions = await client.get("/api/sessions")
    assert sessions.status_code == 200
    assert isinstance(sessions.json()["items"], list)

    states = await client.get("/api/states", params={"limit": 10})
    body = states.json()
    assert states.status_code == 200
    assert body["limit"] == 10 and body["offset"] == 0
    assert body["total"] >= len(body["items"])

    interventions = await client.get("/api/interventions", params={"limit": 10})
    assert interventions.status_code == 200
    assert "items" in interventions.json()


async def test_export_csv_has_header(client: httpx.AsyncClient) -> None:
    states = await client.get("/api/export/states.csv")
    assert states.status_code == 200
    assert "text/csv" in states.headers["content-type"]
    assert states.text.splitlines()[0].startswith("session_id,ts,emotion_label")

    interventions = await client.get("/api/export/interventions.csv")
    assert interventions.status_code == 200
    assert interventions.text.splitlines()[0].startswith("id,session_id,ts,kind")
    assert "attachment" in interventions.headers["content-disposition"]
