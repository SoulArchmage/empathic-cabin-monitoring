"""触发引擎：把连续的情绪窗口判定为"是否需要干预"的状态机。

这是抗幻觉的第一道防线——**不逐窗调用大模型**。逐窗调用会导致 token 浪费、
话术重复、以及把偶发的一个负向窗口当真异常而产生的幻觉式结论。因此这里做：

    滑动窗口统计 → 连续窗口去抖 → 置信度门槛 → 冷却期抑制 → 严重度升级打断冷却 → 恢复检测

只有真正稳定的负向情绪才会被送去生成话术。由于感知系统每 2 秒才给出一个窗口，
"连续 2 个窗口"即"持续 4 秒以上"，与旧版 10Hz 的 10 帧等价。
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
        abnormal = stats.negative_ratio >= self.cfg.trigger_ratio

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
        # 就地刷新：本次统计需要反映刚刚自增的连续窗口数与连续异常时长
        stats.consec_abnormal = self._consec
        stats.duration_s = round(max(0.0, frame.ts - self._abnormal_since), 2)
        if self._consec < self.cfg.trigger_min_consec:
            return None
        # 置信度门槛：算法自己都不确定的负向，不作为干预依据（只延迟，不误触发）
        if stats.negative_confidence < self.cfg.trigger_min_confidence:
            logger.debug(
                "负向占比 %.0f%% 但平均置信度 %.2f 低于门槛 %.2f，本轮不触发",
                stats.negative_ratio * 100,
                stats.negative_confidence,
                self.cfg.trigger_min_confidence,
            )
            return None

        kind = "negative_emotion"
        severity = self._severity(stats)
        now = frame.ts
        cooled = now >= self._cooldown_until
        escalated = _SEVERITY_RANK[severity] > _SEVERITY_RANK[self._last_severity]
        if not cooled and not escalated:
            return None

        self._in_abnormal = True
        self._cooldown_until = now + self.cfg.trigger_cooldown_s
        self._last_severity = severity
        # 冷却后再次触发需要重新积累连续窗口，避免"一解除就狂发"
        self._consec = 0

        event = TriggerEvent(
            ts=frame.ts,
            session_id=frame.session_id,
            kind=kind,
            severity=severity,
            reason=self._reason(stats),
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
            ts = frame.ts if frame else frames[-1].ts
            if stats.negative_ratio >= self.cfg.trigger_ratio:
                kind, severity = "negative_emotion", self._severity(stats)
                reason = self._reason(stats)
            else:
                kind, severity = "stable", "normal"
                reason = (
                    f"当前情绪平稳：{_dominant(stats)} 占 "
                    f"{max(stats.positive_ratio, stats.neutral_ratio, stats.negative_ratio):.0%}，"
                    f"近 {stats.window_size} 个窗口未出现持续负向"
                )
        else:
            stats = WindowStats(window_size=1)
            kind, severity = "stable", "normal"
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
            reason="情绪已回落至常态区间：最近窗口内 Negative 占比低于阈值",
            stats=stats,
        )

    def _build_stats(self) -> WindowStats:
        return self._build_stats_over(list(self._window))

    def _build_stats_over(self, frames: list[StateFrame]) -> WindowStats:
        n = len(frames) or 1
        negative = [f for f in frames if f.emotion.label == "Negative"]
        positive = sum(1 for f in frames if f.emotion.label == "Positive")
        neutral = sum(1 for f in frames if f.emotion.label == "Neutral")
        avg_confidence = sum(f.emotion.confidence for f in frames) / n
        negative_confidence = (
            sum(f.emotion.confidence for f in negative) / len(negative)
            if negative
            else 0.0
        )
        # duration 用"异常持续时长"而非窗口跨度：窗口只有 4 个片段，无法反映真实恶化过程
        if self._abnormal_since is not None and frames:
            duration = max(0.0, frames[-1].ts - self._abnormal_since)
        else:
            duration = 0.0
        return WindowStats(
            window=frames[-1].window if frames else "",
            window_size=len(frames),
            negative_ratio=round(len(negative) / n, 4),
            positive_ratio=round(positive / n, 4),
            neutral_ratio=round(neutral / n, 4),
            avg_confidence=round(avg_confidence, 4),
            negative_confidence=round(negative_confidence, 4),
            consec_abnormal=self._consec,
            duration_s=round(duration, 2),
        )

    def _severity(self, stats: WindowStats) -> Severity:
        """严重度取决于"强度 + 持续时间 + 置信度"。

        刚越线即触发时属于 mild（先共情陪伴）；只有负向占比很高、置信度足够且
        持续足够久，才升级为 severe 并启用安全出口模板（专业转介），这与心理疏导的
        "先陪伴、后转介"逻辑一致。
        """
        sustained = stats.duration_s >= self.cfg.trigger_escalate_after_s
        if sustained and stats.negative_ratio >= 0.85 and stats.negative_confidence >= 0.70:
            return "severe"
        return "mild"

    def _reason(self, stats: WindowStats) -> str:
        return (
            f"情绪持续负向：最近 {stats.window_size} 个感知窗口内 "
            f"Negative 占比 {stats.negative_ratio:.0%}"
            f"（负向平均置信度 {stats.negative_confidence:.2f}），"
            f"已持续 {stats.duration_s:.1f}s"
        )


def _dominant(stats: WindowStats) -> str:
    """窗口内的主导情绪（用于状态摘要与提示词）。"""
    ratios = {
        "Positive": stats.positive_ratio,
        "Neutral": stats.neutral_ratio,
        "Negative": stats.negative_ratio,
    }
    return max(ratios, key=ratios.get)
