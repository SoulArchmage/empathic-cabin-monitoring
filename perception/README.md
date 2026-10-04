# 感知服务（perception）

独立于疏导仓后端运行的"算法侧"服务骨架：把**情绪标签**与**认知负荷等级**按固定
频率通过 WebSocket 推给疏导仓后端。

它刻意与主项目解耦——真实的算法（音视频情绪识别、EEG 认知负荷识别）通常依赖
GPU、摄像头、脑电采集设备，不适合塞进 Web 服务进程；本服务只负责"统一契约 +
定频推送 + 断线容忍"，算法以插件（Backend）形式接入。

```
音视频 / EEG ──算法──> 感知服务（本目录） ──WebSocket──> 疏导仓后端 ──> 前端大屏
```

## 组成

| 文件 | 职责 |
|---|---|
| `protocol.py` | 输出契约（对齐 `backend/app/schemas.py` 的 `StateFrame` 载荷） |
| `backends.py` | 后端抽象 + 模拟（simulator）/ 回放（replay）两种实现 |
| `serve.py` | WebSocket 服务器 + 定频推送 + 命令行入口 |

## 快速开始

需要 Python 3.10+，唯一依赖 `websockets`（与疏导仓后端同版本，已装过后端依赖的可跳过）。

```powershell
# 在仓库根目录执行
python -m perception.serve                                   # 内置模拟，默认 10Hz，端口 8765
python -m perception.serve --scenario anxiety_escalation     # 换演示剧本
python -m perception.serve --backend replay --file result.jsonl --speed 2
```

## 输出协议

按 `--hz`（默认每秒 10 次）向所有已连接客户端推送：

```json
{"type": "state", "payload": {
  "emotion": {"label": "Negative", "confidence": 0.86},
  "cognitive": {"level": "High", "score": 82.0}}}
```

取值与疏导仓契约一致：`label ∈ Positive/Neutral/Negative`、`level ∈ Low/Medium/High`、
`confidence ∈ [0,1]`、`score ∈ [0,100]`。`ts` / `session_id` / `source` 不在本服务报文中，
由疏导仓按自身会话补齐（`source` 固定为 `external`）。客户端接入时会先收到一条
`type=hello` 的握手帧（仅供人工调试，疏导仓会自动忽略）。

## 接入疏导仓

在 `backend/.env` 中改两行并重启后端：

```ini
SOURCE_MODE=external
EXTERNAL_STATE_URL=ws://127.0.0.1:8765
```

- 前端顶部"数据源"徽标会从 `simulator / mock` 变为 `external / real`；
- 断线自动指数退避重连（1s 起步、最长 30s），外部抖动不会影响后端稳定性。

## 演示注意

- 切到 `external` 后，大屏上的「演示剧本」按钮**不再驱动数据**——剧本由本服务启动时的
  `--scenario` 决定（按钮只会切换大屏上显示的剧本名）；
- 剧本计时从本服务**进程启动**时开始：先启动服务、很久后才打开大屏，会看到剧本已推进
  过半。演示前重启一次本服务即可复位（后端会自动重连，最多等 30s）；
- 若先启动疏导仓、后启动本服务，后端首次连接会失败并退避重试，日志出现
  "外部状态系统连接异常……N s 后重连"属正常现象，稍候即自动接入。

## 时序约定（重要）

真实算法的自然输出节奏通常是 1~2 Hz（表情按秒、EEG 滑窗 0.5s），而疏导仓的触发
判定按**帧数**计算窗口（默认 15 帧 ≈ 1.5 秒 @10Hz）。因此本服务**统一升频**：
后端在算法自己的节奏上更新结果（`SimulatorBackend` 用 2Hz 模拟这一形态），服务端
按 `--hz` 恒定频率推送最新值。

接入真实算法时请沿用这一约定；如果推送频率被迫偏离 10Hz，需同步调整疏导仓的
`TRIGGER_WINDOW` / `TRIGGER_MIN_CONSEC`，否则"持续 8 秒升级高危"这类时间语义会失真。

## 接入真实算法

1. 实现 `PerceptionBackend` 子类，提供 `latest() -> Frame | None`
   （在算法自己的线程/进程里推理，这里只返回最新结果）；
2. 在 `backends.build_backend()` 中注册新的 `--backend` 取值；
3. 若算法只能离线运行（例如 EEG 用公开数据集回放），把推理结果导出为 JSONL，
   直接用 `--backend replay` 演示，实现"真算法 + 可复现演示"：

```jsonl
{"offset_s": 0.0, "emotion": "Neutral",  "confidence": 0.71, "level": "Low",    "score": 28.0}
{"offset_s": 0.5, "emotion": "Negative", "confidence": 0.83, "level": "Medium", "score": 55.0}
{"offset_s": 1.0, "emotion": "Negative", "confidence": 0.88, "level": "High",   "score": 79.0}
```

`offset_s` 为相对起点的秒数；`emotion` / `level` 缺失时按 `Neutral` / `Medium` 兜底；
文件播完默认循环，加 `--no-loop` 则停在最后一帧。回放时间轴在**首个客户端接入时**
才开始计时，因此先启动服务、后接入疏导仓不会错过内容。