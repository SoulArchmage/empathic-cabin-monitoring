"""Prompt 工程：把"情绪 + 认知"状态向量翻译为结构化共情话术的提示词。

采用五段式结构（角色 / 约束 / 输入 / 输出模式 / 示例），并强制 JSON 输出，
便于下游做机器校验（见 validator.py）。用户消息里保留一行 [STATE] 机器可读摘要，
既给模型看，也让 Mock Provider 与日志审计都能拿到同一份上下文。
"""

from __future__ import annotations

import json

from ..schemas import TriggerEvent

SYSTEM_PROMPT = """你是智能疏导仓内的心理安抚助手"小仓"。你的职责是情绪陪伴与放松引导。

【硬性约束】
1. 只做陪伴、共情与放松引导，绝不进行任何医学诊断或病理判断；
2. 禁止提及药物、处方、停药、偏方，禁止承诺疗效（如"保证你会好起来"）；
3. 话术不超过 60 个字，口语化、温和、可直接朗读；不要出现 Markdown、表情符号或列表；
4. 不使用"抑郁症""焦虑症""精神病"等临床名词来描述用户；
5. 无论用户状态如何，都必须保持尊重、不评判、不说教。

【输出格式】以 json 返回，且只返回一个对象，不要任何多余文字、不要代码块围栏：
{"speech": "话术", "strategy": "呼吸引导|共情复述|环境调节|轻量提问", "risk": "low|medium|high"}"""

_FEW_SHOT = [
    {
        "role": "user",
        "content": (
            '[STATE] {"kind": "negative_emotion", "severity": "mild", '
            '"emotion": "Negative", "cognitive_score": 45}\n'
            "请生成安抚话术。"
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"speech": "我注意到你好像有点低落，没关系，我在这儿陪着你。'
            '要不要先慢慢深吸一口气？", "strategy": "共情复述", "risk": "low"}'
        ),
    },
    {
        "role": "user",
        "content": (
            '[STATE] {"kind": "combined", "severity": "severe", '
            '"emotion": "Negative", "cognitive_score": 86}\n'
            "请生成安抚话术。"
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"speech": "你现在承受的东西有点多，我们先把节奏放慢。跟着我，'
            '吸气四秒，呼气六秒，我陪你一起。", "strategy": "呼吸引导", "risk": "medium"}'
        ),
    },
]

_KIND_DESC = {
    "negative_emotion": "音视频情绪识别持续为 Negative（负向情绪）",
    "high_cognitive_load": "脑电认知负荷持续处于 High（高负荷）",
    "combined": "负向情绪与高认知负荷同时持续",
    "recovered": "状态已从异常回落至常态",
}


def state_digest(event: TriggerEvent) -> str:
    """机器可读的状态摘要，供模型、Mock Provider 与审计日志共用。"""
    payload = {
        "kind": event.kind,
        "severity": event.severity,
        "emotion": "Negative" if event.stats and event.stats.negative_ratio >= 0.5 else "Normal",
        "cognitive_score": round(event.stats.avg_cognitive_score, 1) if event.stats else 0.0,
        "negative_ratio": event.stats.negative_ratio if event.stats else 0.0,
        "high_load_ratio": event.stats.high_load_ratio if event.stats else 0.0,
        "duration_s": event.stats.duration_s if event.stats else 0.0,
    }
    return "[STATE] " + json.dumps(payload, ensure_ascii=False)


def build_messages(event: TriggerEvent, hint: str | None = None) -> list[dict[str, str]]:
    """构造 Chat Completions 风格的消息列表。"""
    lines = [
        state_digest(event),
        f"触发原因：{event.reason}",
        f"状态含义：{_KIND_DESC.get(event.kind, event.kind)}",
        f"严重程度：{event.severity}",
    ]
    if hint:
        lines.append(f"附加要求：{hint}")
    lines.append("请生成安抚话术。")
    return _compose(lines)


def build_reply_messages(
    event: TriggerEvent, user_text: str, hint: str | None = None
) -> list[dict[str, str]]:
    """构造语音对话回复的提示词：把用户原话 + 实时状态一起交给模型。"""
    lines = [
        state_digest(event),
        f"用户刚刚说：「{user_text}」",
        f"当前状态：{_KIND_DESC.get(event.kind, event.kind)}（严重度 {event.severity}）",
        (
            "请针对用户这句话做一句共情回应：先承接他的感受，"
            "再给一个轻量、立刻可做的建议，不要追问过多问题。"
        ),
    ]
    if hint:
        lines.append(f"附加要求：{hint}")
    lines.append("请生成安抚话术。")
    return _compose(lines)


def _compose(lines: list[str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_FEW_SHOT,
        {"role": "user", "content": "\n".join(lines)},
    ]


def prompt_digest(messages: list[dict[str, str]], limit: int = 280) -> str:
    """Prompt 摘要（脱敏 + 截断），随干预记录落库以便审计与答辩举证。"""
    flat = " | ".join(f"{m['role']}: {m['content']}" for m in messages)
    flat = " ".join(flat.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")
