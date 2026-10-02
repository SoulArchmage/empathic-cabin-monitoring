"""抗幻觉校验：对模型输出做硬性把关，并准备确定性兜底话术。

这是抗幻觉的第二道防线。大模型的输出不可全信，因此：
  1. 宽容解析 JSON（剥离代码块围栏、截取首尾花括号）；
  2. 校验字段完备性与内容长度，超长按句读截断；
  3. 禁词表拦截诊断性/用药/自伤等越界表述；
  4. 高危状态强制切换为"安全出口"模板，不让模型在敏感场景自由发挥；
  5. 任一环节不通过 → 落到确定性规则话术库，并标记 degraded=True。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..schemas import Risk, Severity, Strategy, TriggerEvent

logger = logging.getLogger(__name__)

MAX_SPEECH_LEN = 80
MIN_SPEECH_LEN = 4
STRATEGIES: tuple[Strategy, ...] = ("呼吸引导", "共情复述", "环境调节", "轻量提问")
RISKS: tuple[Risk, ...] = ("low", "medium", "high")

# 越界表述：命中即拒。注意 "自伤/自杀" 类词也在其中——转介话术一律由系统模板给出，
# 不允许模型主动把话题引向自伤，以降低风险。
FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"诊断",
        r"抑郁症|焦虑症|精神病|精神疾病|精神障碍|双相|强迫症|心理疾病|心理障碍|心理病",
        # 完整病名之外，裸"抑郁"同样属于临床化表述，一并拦截
        r"抑郁",
        # 心理安抚助手不应涉及任何用药话题，故直接从"药"字拦截
        r"药",
        r"包治|一定能好|保证.{0,4}(好|恢复|治愈)",
        r"自杀|轻生|自残|伤害自己|结束生命",
    )
)

# 高危状态（severe）统一走"安全出口"：给出去个人化的确定性话术 + 求助渠道。
SAFE_REFERRAL_SPEECH = (
    "我感觉到你现在很不好受，你并不孤单。我们可以先放慢呼吸，"
    "也建议你联系专业心理援助热线 12356，或告诉身边可信任的人，我陪着你。"
)

# 确定性兜底话术库：(kind, severity) -> (话术, 策略)
FALLBACK_LIBRARY: dict[tuple[str, Severity], tuple[str, Strategy]] = {
    ("negative_emotion", "mild"): (
        "我注意到你好像有些低落。没关系，不用急着好起来，我在这儿陪着你。",
        "共情复述",
    ),
    ("negative_emotion", "severe"): (
        SAFE_REFERRAL_SPEECH,
        "共情复述",
    ),
    ("high_cognitive_load", "mild"): (
        "感觉你脑子里的东西有点多。我们把节奏放慢一点，先停三秒再继续。",
        "环境调节",
    ),
    ("high_cognitive_load", "severe"): (
        "负荷有点高了，先别硬撑。跟着我把肩膀放松、吸气四秒、呼气六秒，重复三次。",
        "呼吸引导",
    ),
    ("combined", "mild"): (
        "你现在又累又沉，这种感觉我理解。先什么都不用决定，靠一靠，慢慢呼吸。",
        "呼吸引导",
    ),
    ("combined", "severe"): (
        SAFE_REFERRAL_SPEECH,
        "共情复述",
    ),
    ("recovered", "normal"): (
        "刚才那种紧绷感慢慢退下去了，你做得很好。把这份平稳多留一会儿。",
        "环境调节",
    ),
}


@dataclass(slots=True)
class ValidatedSpeech:
    speech: str
    strategy: Strategy
    risk: Risk
    degraded: bool = False
    note: str = ""


def parse_json_loose(raw: str) -> dict | None:
    """宽容解析模型输出的 JSON。"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _clamp(text: str, limit: int = MAX_SPEECH_LEN) -> str:
    """超长时按标点截断到完整句子；截不出有效句子则返回空串表示放弃。"""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(p, 0, limit) for p in "。！？；，")
    if cut < MIN_SPEECH_LEN:
        return ""
    return text[: cut + 1]


def _coerce_strategy(value: object) -> Strategy:
    return value if value in STRATEGIES else "共情复述"  # type: ignore[return-value]


def _coerce_risk(value: object, severity: Severity) -> Risk:
    risk: Risk = value if value in RISKS else "low"  # type: ignore[assignment]
    # 严重状态不允许低风险标注
    if severity == "severe" and risk == "low":
        return "medium"
    return risk


def fallback_for(event: TriggerEvent) -> ValidatedSpeech:
    key = (event.kind, event.severity)
    speech, strategy = FALLBACK_LIBRARY.get(
        key, FALLBACK_LIBRARY[("combined", "severe")]
    )
    return ValidatedSpeech(
        speech=speech,
        strategy=strategy,
        risk="high" if event.severity == "severe" else "low",
        degraded=True,
        note="规则兜底话术",
    )


def safety_exit() -> ValidatedSpeech:
    """高危状态的确定性"安全出口"话术。

    该话术完全由系统模板给出、不依赖模型输出，因此调用方可以在请求大模型**之前**
    直接采用它，避免白付一次（可能长达 llm_timeout_s 的）模型延迟。
    """
    return ValidatedSpeech(
        speech=SAFE_REFERRAL_SPEECH,
        strategy="共情复述",
        risk="high",
        degraded=False,
        note="高危状态启用安全出口模板",
    )


def validate(raw: str, event: TriggerEvent) -> ValidatedSpeech:
    """校验模型输出。任何不通过都会返回降级话术（degraded=True）。"""
    # 安全出口：高危状态不让模型自由发挥，直接使用确定性话术
    if event.severity == "severe":
        return safety_exit()

    data = parse_json_loose(raw)
    if data is None:
        return _degrade(event, "模型输出不是合法 JSON")

    speech = data.get("speech")
    if not isinstance(speech, str):
        return _degrade(event, "缺少 speech 字段")
    speech = " ".join(speech.split())
    if len(speech) < MIN_SPEECH_LEN:
        return _degrade(event, "话术过短")
    clamped = _clamp(speech)
    if not clamped:
        return _degrade(event, "话术过长且无法安全截断")

    hit = next((p.pattern for p in FORBIDDEN_PATTERNS if p.search(clamped)), None)
    if hit:
        return _degrade(event, f"命中禁词规则 /{hit}/")

    return ValidatedSpeech(
        speech=clamped,
        strategy=_coerce_strategy(data.get("strategy")),
        risk=_coerce_risk(data.get("risk"), event.severity),
    )


def _degrade(event: TriggerEvent, reason: str) -> ValidatedSpeech:
    logger.warning("话术校验未通过（%s），已降级为规则话术", reason)
    result = fallback_for(event)
    result.note = reason
    return result
