"""感知后端：结果来源的抽象与两种骨架实现（模拟 / 回放）。

接入真实算法（音视频情绪识别、EEG 认知负荷识别）时，实现 PerceptionBackend
子类并在 build_backend 中注册即可；服务端与输出协议完全不用改。

时序约定：后端在**算法自己的节奏**上更新结果（真实算法通常 1~2Hz），
由服务端统一升频到固定推送频率——这正是接入真实算法后的时序形态。
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import random
import time
from pathlib import Path

from .protocol import Frame

logger = logging.getLogger("perception.backends")


class PerceptionBackend(abc.ABC):
    """感知后端抽象：对外只暴露"当前最新一帧"。"""

    name = "base"

    async def start(self) -> None:
        """启动内部任务（可选实现）。"""

    async def stop(self) -> None:
        """停止内部任务（可选实现）。"""

    @abc.abstractmethod
    def latest(self) -> Frame | None:
        """返回当前最新的感知结果；暂无结果时返回 None。"""


def _classify_emotion(stress: float) -> tuple[str, float]:
    if stress >= 0.55:
        label = "Negative"
    elif stress <= 0.32:
        label = "Positive"
    else:
        label = "Neutral"
    # 离分类边界越远置信度越高
    distance = min(abs(stress - 0.55), abs(stress - 0.32))
    return label, min(0.98, 0.62 + distance * 1.1)


def _classify_load(load: float) -> str:
    if load >= 70:
        return "High"
    if load >= 40:
        return "Medium"
    return "Low"


# 每种模拟 -> (压力目标, 负荷目标)，输入为其运行秒数
_SCENARIOS = {
    "calm": lambda t: (0.14, 24.0),
    "high_load": lambda t: (0.45, 86.0),
    "anxiety_escalation": lambda t: (
        min(0.92, 0.14 + t / 12.0 * 0.78),
        min(88.0, 24.0 + t / 12.0 * 64.0),
    ),
    "recovery": lambda t: (
        max(0.14, 0.92 - t / 15.0 * 0.78),
        max(24.0, 88.0 - t / 15.0 * 64.0),
    ),
}


class SimulatorBackend(PerceptionBackend):
    """内置模拟后端：用两个隐变量近似真实算法的输出形态。

    按 update_hz（默认 2Hz，贴近真实算法 1~2Hz 的自然节奏）在内部更新，
    由服务端升频推送——用于在没有算法与硬件时调通整条接入链路。
    """

    name = "simulator"

    def __init__(self, scenario: str = "calm", update_hz: float = 2.0) -> None:
        if scenario not in _SCENARIOS:
            raise ValueError(f"未知模拟：{scenario}（可选：{', '.join(_SCENARIOS)}）")
        self.scenario = scenario
        self.update_hz = update_hz
        self._stress, self._load = 0.14, 24.0
        self._started = time.monotonic()
        self._frame: Frame | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="perception-simulator")
        self._task.add_done_callback(self._on_task_done)
        logger.info(
            "模拟后端启动：scenario=%s 内部更新=%.1fHz", self.scenario, self.update_hz
        )

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        """后台任务异常必须显式记录，否则会静默死掉、latest() 永远返回旧帧。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("模拟后端后台任务异常退出：%s", exc, exc_info=exc)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _step(self) -> None:
        target_stress, target_load = _SCENARIOS[self.scenario](
            time.monotonic() - self._started
        )
        # 一阶惯性跟随 + 高斯噪声，形态与真实生理信号类似
        self._stress += (target_stress - self._stress) * 0.35
        self._stress = min(1.0, max(0.0, self._stress + random.gauss(0, 0.02)))
        self._load += (target_load - self._load) * 0.35
        self._load = min(100.0, max(0.0, self._load + random.gauss(0, 1.8)))

        label, confidence = _classify_emotion(self._stress)
        self._frame = Frame(
            label=label,
            confidence=confidence,
            level=_classify_load(self._load),
            score=self._load,
        )

    async def _run(self) -> None:
        interval = 1.0 / max(self.update_hz, 0.1)
        while True:
            self._step()
            await asyncio.sleep(interval)

    def latest(self) -> Frame | None:
        return self._frame


class ReplayBackend(PerceptionBackend):
    """回放后端：把离线推理结果按时间轴重放。

    用于"真算法 + 可复现演示"：算法在离线环境跑完（例如 EEG 公开数据集推理），
    导出为 JSONL 后由本后端按原时间轴推送。文件格式（每行一个样本）：

        {"offset_s": 0.0, "emotion": "Negative", "confidence": 0.82,
         "level": "High", "score": 78.0}

    offset_s 为相对起点的秒数；emotion/level 缺失时按 Neutral / Medium 兜底。
    """

    name = "replay"

    def __init__(self, path: str | Path, speed: float = 1.0, loop: bool = True) -> None:
        self.path = Path(path)
        self.speed = max(float(speed), 0.01)
        self.loop = loop
        self._samples = self._load(self.path)
        # 时间轴延迟到首次被请求（首个客户端接入）时才启动，
        # 否则服务先启动、后端后接入时，回放可能已经播完
        self._started: float | None = None
        self._cursor = 0
        logger.info(
            "回放后端就绪：%s（%d 条样本，时长 %.1fs，倍速 %.2f，%s）",
            self.path.name,
            len(self._samples),
            self._samples[-1][0],
            self.speed,
            "循环播放" if loop else "播完停在最后一帧",
        )

    @staticmethod
    def _load(path: Path) -> list[tuple[float, Frame]]:
        if not path.exists():
            raise FileNotFoundError(f"回放文件不存在：{path}")
        samples: list[tuple[float, Frame]] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("每行必须是 JSON 对象")
                samples.append(
                    (
                        float(raw.get("offset_s", raw.get("t", 0.0))),
                        Frame(
                            label=str(raw.get("emotion", "Neutral")),
                            confidence=float(raw.get("confidence", 0.7)),
                            level=str(raw.get("level", "Medium")),
                            score=float(raw.get("score", 50.0)),
                        ),
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"{path} 第 {lineno} 行无法解析：{exc}") from exc
        if not samples:
            raise ValueError(f"回放文件没有有效样本：{path}")
        samples.sort(key=lambda item: item[0])
        return samples

    def latest(self) -> Frame | None:
        if self._started is None:
            # 首次被请求（即首个客户端接入）才启动时间轴
            self._started = time.monotonic()
        elapsed = (time.monotonic() - self._started) * self.speed
        total = self._samples[-1][0]
        if elapsed > total:
            if not self.loop:
                return self._samples[-1][1]
            # 回绕：循环演示
            self._started = time.monotonic()
            self._cursor = 0
            elapsed = 0.0
        while (
            self._cursor + 1 < len(self._samples)
            and self._samples[self._cursor + 1][0] <= elapsed
        ):
            self._cursor += 1
        return self._samples[self._cursor][1]


def build_backend(
    kind: str,
    *,
    scenario: str = "calm",
    file: str | None = None,
    speed: float = 1.0,
    loop: bool = True,
) -> PerceptionBackend:
    """后端工厂：新增真实算法后端时在这里注册。"""
    if kind == "simulator":
        return SimulatorBackend(scenario=scenario)
    if kind == "replay":
        if not file:
            raise ValueError("--backend replay 需要 --file 指定回放文件")
        return ReplayBackend(file, speed=speed, loop=loop)
    raise ValueError(f"未知后端：{kind}（可选：simulator / replay）")