"""触发引擎：把连续的情绪窗口 / 脑电负荷窗口判定为"是否需要干预"的状态机。

这是抗幻觉的第一道防线——**不逐窗调用大模型**。逐窗调用会导致 token 浪费、
话术重复、以及把偶发的一个负向窗口当真异常而产生的幻觉式结论。因此这里做：

    滑动窗口统计 → 连续窗口去抖 → 置信度门槛 → 冷却期抑制 → 严重度升级打断冷却 → 恢复检测

本引擎同时看两路信号，各自独立判定、各自冷却：
- 情绪通道：Negative 占比 + 负向置信度 → kind=negative_emotion（mild → severe）；
- 脑电负荷通道：High / VeryHigh 连续出现 + 置信度 → kind=high_load（固定 mild，
  因为负荷过高需要的是放松引导，不是心理危机转介，绝不走"安全出口"模板）。
"""

from __future__ import annotations

import logging
import time
from collections import deque

from ..config import Settings, settings as default_settings
from ..schemas import (
    HIGH_LOAD_LEVELS,
    LOAD_LEVEL_RANK,
    LOAD_LEVEL_ZH,
    Severity,
    StateFrame,
    TriggerEvent,
    WindowStats,
)

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
        # 脑电负荷通道的独立状态机（与情绪互不干扰）
        self._load_consec = 0
        self._load_cooldown_until = 0.0
        self._load_last_rank = 0

    # ---- 对外接口 -------------------------------------------------------- #
    def evaluate(self, frame: StateFrame) -> TriggerEvent | None:
        """返回 None 表示无需干预；否则返回一次 TriggerEvent。

        先判情绪、再判负荷：情绪是心理层面的强信号，命中后本窗口不再重复出事件，
        避免同一时刻连推两条话术。
        """
        self._window.append(frame)
        event = self._evaluate_emotion(frame)
        if event is not None:
            return event
        return self._evaluate_load(frame)

    def _evaluate_emotion(self, frame: StateFrame) -> TriggerEvent | None:
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

    # ---- 脑电负荷通道 ---------------------------------------------------- #
    def _evaluate_load(self, frame: StateFrame) -> TriggerEvent | None:
        """高/极高负荷的独立状态机：连续去抖 + 置信度门槛 + 冷却期。

        严重度固定 mild：负荷过高需要的是"降速、放松、呼吸引导"，而不是心理危机转介，
        因此绝不复用 severe 的安全出口模板（那会把 12356 热线推给一个只是累了的人）。
        """
        load = frame.load
        if load is None:
            self._load_consec = 0
            return None

        trusted = load.confidence >= self.cfg.load_trigger_min_confidence
        if load.level not in HIGH_LOAD_LEVELS or not trusted:
            self._load_consec = 0
            return None

        self._load_consec += 1
        needs = max(1, int(self.cfg.load_trigger_high_consec))
        if self._load_consec < needs:
            logger.debug(
                "负荷 %s（置信度 %.2f）连续 %d/%d 个窗口，继续观察",
                load.level,
                load.confidence,
                self._load_consec,
                needs,
            )
            return None

        rank = LOAD_LEVEL_RANK.get(load.level, 0)
        now = frame.ts
        cooled = now >= self._load_cooldown_until
        escalated = rank > self._load_last_rank  # 高 → 极高 立刻升级，不被冷却挡住
        if not cooled and not escalated:
            # 冷却期内不打扰，但连续证据要重新积累：否则冷却一结束就会"秒触发"
            self._load_consec = 0
            return None

        consec = self._load_consec
        self._load_consec = 0
        self._load_last_rank = rank
        self._load_cooldown_until = now + self.cfg.load_trigger_cooldown_s

        stats = self._build_stats()
        stats.consec_abnormal = consec
        event = TriggerEvent(
            ts=frame.ts,
            session_id=frame.session_id,
            kind="high_load",
            severity="mild",
            reason=self._load_reason(stats),
            stats=stats,
        )
        logger.info("触发干预 kind=high_load severity=mild：%s", event.reason)
        return event

    def _load_reason(self, stats: WindowStats) -> str:
        level = LOAD_LEVEL_ZH.get(stats.load_level, stats.load_level or "—")
        return (
            f"脑电认知负荷持续偏高：最近 {stats.window_size} 个窗口内高/极高占比 "
            f"{stats.load_high_ratio:.0%}，当前等级「{level}」"
            f"（置信度 {stats.load_confidence:.2f}）"
        )

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
            elif (
                stats.load_level in HIGH_LOAD_LEVELS
                and stats.load_confidence >= self.cfg.load_trigger_min_confidence
            ):
                # 情绪平稳但负荷偏高：对话上下文应说明"人是累的，不是情绪低落"
                kind, severity = "high_load", "mild"
                reason = self._load_reason(stats)
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
        # 负荷侧：只看带负荷快照的帧（未接入 EEG 时这些字段保持空/0）
        loads = [f.load for f in frames if f.load is not None]
        latest_load = loads[-1] if loads else None
        high_loads = sum(1 for load in loads if load.level in HIGH_LOAD_LEVELS)
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
            load_level=latest_load.level if latest_load else "",
            load_confidence=round(latest_load.confidence, 4) if latest_load else 0.0,
            load_high_ratio=round(high_loads / len(loads), 4) if loads else 0.0,
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
