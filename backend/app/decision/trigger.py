"""触发引擎：把连续状态流判定为"是否需要干预"的状态机。

这是抗幻觉的第一道防线——**不逐帧调用大模型**。逐帧调用会导致 token 浪费、
话术重复、以及把随机噪声当真异常而产生的幻觉式结论。因此这里做：
  滑动窗口统计 → 连续帧去抖 → 冷却期抑制 → 严重度升级打断冷却 → 恢复检测
只有真正稳定的异常状态才会被送去生成话术。
"""

from __future__ import annotations

import logging
import time
from collections import deque

from ..config import Settings, settings as default_settings
from ..schemas import Severity, StateFrame, TriggerEvent, WindowStats

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"normal": 0, "mild": 1, "severe": 2}


class TriggerEngine:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self._window: deque[StateFrame] = deque(maxlen=self.cfg.trigger_window)
        self._consec = 0
        self._cooldown_until = 0.0
        self._last_severity: Severity = "normal"
        self._in_abnormal = False
        self._recovery_streak = 0
        self._abnormal_since: float | None = None

    # ---- 对外接口 -------------------------------------------------------- #
    def evaluate(self, frame: StateFrame) -> TriggerEvent | None:
        """返回 None 表示无需干预；否则返回一次 TriggerEvent。"""
        self._window.append(frame)
        if len(self._window) < self.cfg.trigger_window:
            return None

        stats = self._build_stats()
        abnormal = (
            stats.negative_ratio >= self.cfg.trigger_ratio
            or stats.high_load_ratio >= self.cfg.trigger_ratio
        )

        if not abnormal:
            # 连续异常被打破：时长必须重新计时，否则中间夹一段常态也会被算作"持续恶化"；
            # 已累计的时长保留在 stats 中，供"状态恢复"事件的记录使用
            prev_since = self._abnormal_since
            self._consec = 0
            self._abnormal_since = None
            stats.consec_abnormal = 0
            stats.duration_s = (
                round(max(0.0, frame.ts - prev_since), 2)
                if prev_since is not None
                else 0.0
            )
            return self._check_recovery(frame, stats)

        self._recovery_streak = 0
        self._consec += 1
        if self._abnormal_since is None:
            self._abnormal_since = frame.ts
        # 就地刷新：本次统计需要反映刚刚自增的连续帧数与连续异常时长
        stats.consec_abnormal = self._consec
        stats.duration_s = round(max(0.0, frame.ts - self._abnormal_since), 2)
        if self._consec < self.cfg.trigger_min_consec:
            return None

        kind = self._classify(stats)
        severity = self._severity(kind, stats)
        now = frame.ts
        cooled = now >= self._cooldown_until
        escalated = _SEVERITY_RANK[severity] > _SEVERITY_RANK[self._last_severity]
        if not cooled and not escalated:
            return None

        self._in_abnormal = True
        self._cooldown_until = now + self.cfg.trigger_cooldown_s
        self._last_severity = severity
        # 冷却后再次触发需要重新积累连续帧，避免"一解除就狂发"
        self._consec = 0

        event = TriggerEvent(
            ts=frame.ts,
            session_id=frame.session_id,
            kind=kind,
            severity=severity,
            reason=self._reason(kind, stats),
            stats=stats,
        )
        logger.info("触发干预 kind=%s severity=%s：%s", kind, severity, event.reason)
        return event

    def snapshot(self) -> WindowStats:
        return self._build_stats()

    def snapshot_event(
        self, frame: StateFrame | None = None, session_id: str | None = None
    ) -> TriggerEvent:
        """基于当前窗口生成一个"快照事件"，供语音对话注入状态上下文使用。

        与 evaluate() 不同：它不改变状态机内部状态，也不受冷却期影响。
        """
        frames = list(self._window)
        if frames:
            stats = self._build_stats_over(frames)
            kind = self._classify(stats)
            severity = self._severity(kind, stats)
            reason = self._reason(kind, stats)
            ts = frame.ts if frame else frames[-1].ts
        else:
            stats = WindowStats(window_size=1)
            kind, severity = "negative_emotion", "mild"
            reason = "语音对话时暂无足够状态窗口，按温和陪伴策略回应"
            ts = frame.ts if frame else time.time()
        return TriggerEvent(
            ts=ts,
            session_id=session_id or (frame.session_id if frame else ""),
            kind=kind,
            severity=severity,
            reason=reason,
            stats=stats,
        )

    # ---- 内部实现 -------------------------------------------------------- #
    def _check_recovery(
        self, frame: StateFrame, stats: WindowStats
    ) -> TriggerEvent | None:
        if not self._in_abnormal:
            return None
        self._recovery_streak += 1
        if self._recovery_streak < self.cfg.trigger_recovery_frames:
            return None
        self._in_abnormal = False
        self._recovery_streak = 0
        self._last_severity = "normal"
        self._cooldown_until = 0.0
        self._abnormal_since = None
        logger.info("状态已恢复常态：%s", frame.session_id)
        return TriggerEvent(
            ts=frame.ts,
            session_id=frame.session_id,
            kind="recovered",
            severity="normal",
            reason="情绪与认知负荷已回落至常态区间",
            stats=stats,
        )

    def _build_stats(self) -> WindowStats:
        return self._build_stats_over(list(self._window))

    def _build_stats_over(self, frames: list[StateFrame]) -> WindowStats:
        n = len(frames) or 1
        neg = sum(1 for f in frames if f.emotion.label == "Negative") / n
        high = sum(1 for f in frames if f.cognitive.level == "High") / n
        avg = sum(f.cognitive.score for f in frames) / n
        # duration 用"异常持续时长"而非窗口跨度：窗口只有 1.5s，无法反映真实恶化过程
        if self._abnormal_since is not None and frames:
            duration = max(0.0, frames[-1].ts - self._abnormal_since)
        else:
            duration = 0.0
        return WindowStats(
            window_size=len(frames),
            negative_ratio=round(neg, 4),
            high_load_ratio=round(high, 4),
            consec_abnormal=self._consec,
            avg_cognitive_score=round(avg, 2),
            duration_s=round(duration, 2),
        )

    def _classify(self, stats: WindowStats) -> str:
        neg_abn = stats.negative_ratio >= self.cfg.trigger_ratio
        load_abn = stats.high_load_ratio >= self.cfg.trigger_ratio
        if neg_abn and load_abn:
            return "combined"
        if neg_abn:
            return "negative_emotion"
        if load_abn:
            return "high_cognitive_load"
        # 均未越线时按相对更高者归类，保证语音对话场景也有合理语义
        return (
            "negative_emotion"
            if stats.negative_ratio >= stats.high_load_ratio
            else "high_cognitive_load"
        )

    def _severity(self, kind: str, stats: WindowStats) -> Severity:
        """严重度取决于"强度 + 持续时间"。

        刚越线即触发时属于 mild（先共情陪伴）；只有异常持续足够久且强度很高，
        才升级为 severe 并启用安全出口模板（专业转介），这与心理疏导的
        "先陪伴、后转介"逻辑一致。
        """
        peak = max(stats.negative_ratio, stats.high_load_ratio)
        sustained = stats.duration_s >= self.cfg.trigger_escalate_after_s
        if sustained and peak >= 0.85:
            return "severe"
        if kind == "combined" and sustained and peak >= 0.75:
            return "severe"
        return "mild"

    def _reason(self, kind: str, stats: WindowStats) -> str:
        dur = stats.duration_s
        if kind == "combined":
            return (
                f"情绪持续负向（Negative 占比 {stats.negative_ratio:.0%}）"
                f"且认知负荷偏高（High 占比 {stats.high_load_ratio:.0%}），"
                f"已持续 {dur:.1f}s"
            )
        if kind == "negative_emotion":
            return (
                f"情绪持续负向：Negative 占比 {stats.negative_ratio:.0%}，"
                f"已持续 {dur:.1f}s"
            )
        return (
            f"认知负荷持续偏高：High 占比 {stats.high_load_ratio:.0%}，"
            f"平均负荷 {stats.avg_cognitive_score:.0f}，已持续 {dur:.1f}s"
        )
