# 感知服务（perception）

独立于疏导仓后端运行的"算法侧"服务骨架：每 **2 秒**产出一个滑动窗口的三分类
情绪结果，通过 WebSocket 推给疏导仓后端，或按 `state.json` 的形状落盘供其直接读取。

它刻意与主项目解耦——真实算法（音视频情绪三分类识别）通常依赖 GPU、摄像头等
资源，不适合塞进 Web 服务进程；本服务只负责"统一契约 + 按窗口输出 + 断线容忍"，
算法以插件（Backend）形式接入。

```
音视频 ──算法──> 感知服务（本目录） ──WebSocket / state.json──> 疏导仓后端 ──> 前端大屏
```

## 组成

| 文件 | 职责 |
|---|---|
| `protocol.py` | 输出契约：`emotion` / `confidence` / `window` 三个标签 |
| `backends.py` | 后端抽象 + 模拟（simulator）/ 回放（replay）两种实现 |
| `serve.py` | WebSocket 服务器 + 新窗口推送 + 可选 state.json 落盘 |

## 快速开始

需要 Python 3.10+，唯一依赖 `websockets`（与疏导仓后端同版本，已装过后端依赖的可跳过）。

```powershell
# 在仓库根目录执行
python -m perception.serve                                   # 内置模拟，每 2s 一个窗口，端口 8765
python -m perception.serve --scenario anxiety_escalation     # 换模拟演示
python -m perception.serve --dump state.json                 # 同时把结果写成 state.json
python -m perception.serve --backend replay --file result.jsonl --speed 2
```

## 输出协议

每出现一个新窗口向所有已连接客户端推送一条（自然节奏约 0.5Hz）：

```json
{"type": "state", "payload": {
  "emotion": "Negative", "confidence": 0.86, "window": "55.0s - 57.0s",
  "latency_ms": 2210, "camera_ok": true, "audio_ok": true,
  "device": "cpu", "gpu_name": "", "precision": "fp32"}}
```

本系统只使用 `emotion` / `confidence` / `window` 三个标签：

- `emotion ∈ Positive / Neutral / Negative`；
- `confidence ∈ [0,1]`；
- `window` 为感知窗口区间，形如 `"55.0s - 57.0s"`。

其余字段是算法侧诊断信息，疏导仓会忽略。`ts` / `session_id` / `source` 不在本服务报文中，
由疏导仓按自身会话补齐（`source` 固定为 `external` 或 `file`）。客户端接入时会先收到
一条 `type=hello` 的握手帧（仅供人工调试，疏导仓会自动忽略）。

同一个窗口不会被重复推送——疏导仓按窗口计数做去抖，重复推送会虚增触发次数。

## 接入疏导仓

### 方式一：WebSocket（默认）

`backend/.env` 中改两行并重启后端：

```ini
SOURCE_MODE=external
EXTERNAL_STATE_URL=ws://127.0.0.1:8765
```

### 方式二：直接读 state.json

如果感知程序（含真实算法）每 2 秒把结果写到同一个 `state.json`，疏导仓可以不去连
WebSocket，直接轮询该文件：

```ini
SOURCE_MODE=file
STATE_FILE=D:\path\to\state.json
```

文件内容与真实感知程序写出的完全一致（`emotion` / `confidence` / `window` 三个标签 +
`latency_ms` / `logs` 等诊断字段）；疏导仓按 `window` 去重，文件被截断或写一半时会
静默跳过、下一轮再读。`python -m perception.serve --dump state.json` 可以用来生成同形状的
模拟文件，便于在没有算法时先联调文件模式。

- 前端顶部"数据源"徽标会显示 `external / real` 或 `file / real`；
- WebSocket 断线自动指数退避重连（1s 起步、最长 30s），外部抖动不会影响后端稳定性。

## 演示注意

- 切到 `external` / `file` 后，大屏上的「模拟演示」按钮**不再驱动数据**——模拟由本服务
  启动时的 `--scenario`（或真实算法）决定（按钮只会切换大屏上显示的模拟名）；
- 模拟计时从本服务**进程启动**时开始：先启动服务、很久后才打开大屏，会看到模拟已推进
  过半。演示前重启一次本服务即可复位（后端会自动重连，最多等 30s）；
- 若先启动疏导仓、后启动本服务，后端首次连接会失败并退避重试，日志出现
  "感知系统连接异常……N s 后重连"属正常现象，稍候即自动接入。

## 时序约定（重要）

真实情绪算法以 2 秒滑动窗口出结果（与录制数据 `55.0s - 57.0s` 一致），疏导仓的触发
判定按**窗口数**计算：默认窗口 4 个（≈8 秒）、连续 2 个（≈4 秒）才判定为持续负向。
两侧节奏必须一致，否则"持续 6 秒升级高危"这类时间语义会失真。

## 接入真实算法

1. 实现 `PerceptionBackend` 子类，提供 `latest() -> Frame | None`
   （在算法自己的线程/进程里推理，这里只返回最新窗口结果）；
2. 在 `backends.build_backend()` 中注册新的 `--backend` 取值；
3. 若算法只能离线运行，把推理结果导出为 JSONL，直接用 `--backend replay` 演示，
   实现"真算法 + 可复现演示"：

```jsonl
{"offset_s": 0.0, "emotion": "Neutral",  "confidence": 0.71}
{"offset_s": 2.0, "emotion": "Negative", "confidence": 0.83}
{"offset_s": 4.0, "emotion": "Negative", "confidence": 0.88}
```

`offset_s` 为窗口起点相对录制起点的秒数；`emotion` / `confidence` 缺失时按
`Neutral` / `0.7` 兜底；文件播完默认循环，加 `--no-loop` 则停在最后一帧。回放时间轴在
**首个客户端接入时**才开始计时，因此先启动服务、后接入疏导仓不会错过内容。
