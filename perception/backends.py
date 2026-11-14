"""感知后端：结果来源的抽象与两种骨架实现（模拟 / 回放）。

接入真实算法（音视频情绪三分类识别）时，实现 PerceptionBackend 子类并在
build_backend 中注册即可；服务端与输出协议完全不用改。

时序约定：真实算法通常以"2 秒滑动窗口"出结果（与 state.json 的
"55.0s - 57.0s" 一致），因此模拟后端也按 2 秒一个窗口更新，而不是人为升频。
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

_POSITIVE_BOUNDARY = 0.25
_NEGATIVE_BOUNDARY = -0.25


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


def _classify(valence: float) -> tuple[str, float]:
    """连续 valence -> 三分类 + 置信度（离边界越远置信度越高）。"""
    if valence >= _POSITIVE_BOUNDARY:
        label = "Positive"
        distance = min(1.0, (valence - _POSITIVE_BOUNDARY) / 0.75)
    elif valence <= _NEGATIVE_BOUNDARY:
        label = "Negative"
        distance = min(1.0, (_NEGATIVE_BOUNDARY - valence) / 0.75)
    else:
        label = "Neutral"
        distance = min(
            _POSITIVE_BOUNDARY - valence, valence - _NEGATIVE_BOUNDARY
        ) / _POSITIVE_BOUNDARY
    return label, min(0.97, max(0.55, 0.58 + 0.30 * distance))


# 每种模拟 -> 目标 valence，输入为其运行秒数
_SCENARIOS = {
    "calm": lambda t: 0.10,
    "anxiety_escalation": lambda t: max(-0.95, 0.10 - t / 4.5 * 1.05),
    "recovery": lambda t: 0.40,
    "fluctuation": lambda t: 0.10 - (0.95 if (t % 10.0) < 1.5 else 0.0),
}


class SimulatorBackend(PerceptionBackend):
    """内置模拟后端：按 2 秒窗口构造与真实 state.json 同形的结果。

    每 window_seconds 秒更新一帧，窗口区间连续累加（"0.0s - 2.0s"、"2.0s - 4.0s"…），
    并附带 1.8~2.6s 的模拟推理时延，用于在没有算法与硬件时调通整条接入链路。
    """

    name = "simulator"

    def __init__(
        self, scenario: str = "calm", window_seconds: float = 2.0
    ) -> None:
        if scenario not in _SCENARIOS:
            raise ValueError(f"未知模拟：{scenario}（可选：{', '.join(_SCENARIOS)}）")
        self.scenario = scenario
        self.window_seconds = max(0.5, float(window_seconds))
        self._valence = 0.10
        self._next_start = 0.0
        self._started = time.monotonic()
        self._frame: Frame | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="perception-simulator")
        self._task.add_done_callback(self._on_task_done)
        logger.info(
            "模拟后端启动：scenario=%s 窗口=%.1fs", self.scenario, self.window_seconds
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
        target = _SCENARIOS[self.scenario](time.monotonic() - self._started)
        self._valence += (target - self._valence) * 0.65
        self._valence = min(1.0, max(-1.0, self._valence + random.gauss(0, 0.02)))

        label, confidence = _classify(self._valence)
        start = self._next_start
        end = start + self.window_seconds
        self._next_start = end
        self._frame = Frame(
            emotion=label,
            confidence=confidence,
            window_start=start,
            window_end=end,
            latency_ms=random.randint(1800, 2600),
        )

    async def _run(self) -> None:
        while True:
            self._step()
            await asyncio.sleep(self.window_seconds)

    def latest(self) -> Frame | None:
        return self._frame


class ReplayBackend(PerceptionBackend):
    """回放后端：把离线推理结果按时间轴重放。

    用于"真算法 + 可复现演示"：算法在离线环境跑完（例如公开数据集推理），
    导出为 JSONL 后由本后端按原时间轴推送。文件格式（每行一个窗口）：

        {"offset_s": 0.0, "emotion": "Neutral", "confidence": 0.72}
        {"offset_s": 2.0, "emotion": "Negative", "confidence": 0.83}

    offset_s 为窗口起点相对录制起点的秒数；窗口长度由 window_seconds 决定。
    emotion 缺失时按 Neutral、confidence 缺失时按 0.7 兜底。
    """

    name = "replay"

    def __init__(
        self,
        path: str | Path,
        speed: float = 1.0,
        loop: bool = True,
        window_seconds: float = 2.0,
    ) -> None:
        self.path = Path(path)
        self.speed = max(float(speed), 0.01)
        self.loop = loop
        self.window_seconds = max(0.5, float(window_seconds))
        self._samples = self._load(self.path)
        # 时间轴延迟到首次被请求（首个客户端接入）时才启动，
        # 否则服务先启动、后端后接入时，回放可能已经播完
        self._started: float | None = None
        self._cursor = 0
        self._current: Frame | None = None
        logger.info(
            "回放后端就绪：%s（%d 个窗口，时长 %.1fs，倍速 %.2f，%s）",
            self.path.name,
            len(self._samples),
            self._samples[-1][0],
            self.speed,
            "循环播放" if loop else "播完停在最后一帧",
        )

    @staticmethod
    def _load(path: Path) -> list[tuple[float, str, float]]:
        if not path.exists():
            raise FileNotFoundError(f"回放文件不存在：{path}")
        samples: list[tuple[float, str, float]] = []
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
                        str(raw.get("emotion", "Neutral")),
                        float(raw.get("confidence", 0.7)),
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"{path} 第 {lineno} 行无法解析：{exc}") from exc
        if not samples:
            raise ValueError(f"回放文件没有有效样本：{path}")
        samples.sort(key=lambda item: item[0])
        return samples

    def _make(self, offset: float, emotion: str, confidence: float) -> Frame:
        return Frame(
            emotion=emotion,
            confidence=confidence,
            window_start=offset,
            window_end=offset + self.window_seconds,
        )

    def latest(self) -> Frame | None:
        if self._started is None:
            # 首次被请求（即首个客户端接入）才启动时间轴
            self._started = time.monotonic()
        elapsed = (time.monotonic() - self._started) * self.speed
        total = self._samples[-1][0]
        if elapsed > total + self.window_seconds:
            if not self.loop:
                # 停在最后一帧：必须复用同一个 Frame 对象，否则服务端会把它当新窗口重复推送
                if self._current is None:
                    offset, emotion, confidence = self._samples[-1]
                    self._current = self._make(offset, emotion, confidence)
                return self._current
            # 回绕：循环演示
            self._started = time.monotonic()
            self._cursor = 0
            self._current = None
            elapsed = 0.0
        while (
            self._cursor + 1 < len(self._samples)
            and self._samples[self._cursor + 1][0] <= elapsed
        ):
            self._cursor += 1
            self._current = None
        if self._current is None:
            offset, emotion, confidence = self._samples[self._cursor]
            self._current = self._make(offset, emotion, confidence)
        return self._current


def build_backend(
    kind: str,
    *,
    scenario: str = "calm",
    file: str | None = None,
    speed: float = 1.0,
    loop: bool = True,
    window_seconds: float = 2.0,
) -> PerceptionBackend:
    """后端工厂：新增真实算法后端时在这里注册。"""
    if kind == "simulator":
        return SimulatorBackend(scenario=scenario, window_seconds=window_seconds)
    if kind == "replay":
        if not file:
            raise ValueError("--backend replay 需要 --file 指定回放文件")
        return ReplayBackend(
            file, speed=speed, loop=loop, window_seconds=window_seconds
        )
    raise ValueError(f"未知后端：{kind}（可选：simulator / replay）")
