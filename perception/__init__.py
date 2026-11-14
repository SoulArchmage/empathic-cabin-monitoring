"""感知服务包。

独立于疏导仓后端运行的"算法侧"服务：每 2 秒产出一个滑动窗口的三分类情绪结果
（Neutral / Positive / Negative），通过 WebSocket 推给疏导仓（SOURCE_MODE=external），
或按 state.json 的形状落盘供疏导仓直接读取（SOURCE_MODE=file）。
"""
