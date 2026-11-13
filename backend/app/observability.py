"""适配层运行时健康：记录"最近一次真实调用"的结果。

`/api/health` 里的 ready 只能说明配置齐不齐（例如 Key 是否存在），
无法反映额度耗尽、Key 失效、上游 402 这类运行时问题；而主动探测
ASR / LLM 又会真实消耗额度或 token。因此这里只做被动埋点：
每次真实调用后覆盖记录，健康检查读取它，前端据此把
"配置就绪但实际不可用"如实暴露出来，而不是继续显示绿色。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class CallRecord:
    ok: bool | None = None  # None = 本次进程启动后还没调用过
    detail: str = ""
    ts: float = 0.0  # epoch 秒，前端负责本地化展示


_records: dict[str, CallRecord] = {}


def record(name: str, ok: bool, detail: str = "") -> None:
    """记录一次真实调用的结果；detail 面向人，超长直接截断。"""
    _records[name] = CallRecord(ok=ok, detail=detail[:120], ts=time.time())


def snapshot(name: str) -> CallRecord:
    return _records.get(name, CallRecord())
