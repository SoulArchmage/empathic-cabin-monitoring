# 智能疏导仓 · 共情语音交互与状态监测系统

> 计算机网络综合工程设计 · 题目二
> 基于大模型的疏导仓共情语音交互与状态监测系统（B/S 架构 · 事件驱动 · 适配器模式）

一套可端到端运行的演示系统：**感知系统状态实时接入 → 负向情绪去抖判定 → 大模型共情话术生成 →
抗幻觉校验兜底 → 语音播报 → 状态曲线与干预日志落库/查询/导出**。

系统同时接入**两路外部数据**：

| 通道 | 来源 | 数据 | 节奏 |
|---|---|---|---|
| 情绪 | 感知系统（`state.json` / WebSocket） | `emotion` + `confidence` + `window` | 2s 一个窗口 |
| 脑电负荷 | 独立 EEG 监测服务（WebSocket / JSON） | `level` + `confidence` + `window` | 4s 窗、1s 步长 |

脑电负荷按申报书的 4 级划分：**Low / Medium / High / VeryHigh（低 / 中 / 高 / 极高）**。
情绪决定"陪伴还是转介"，负荷决定"要不要降速"：高/极高负荷连续出现会触发一条
`high_load` 降速引导话术（固定 mild，绝不触发心理危机热线）。

情绪通道的数据与感知系统写出的 `state.json` 完全一致，本系统使用其中三个标签：

```json
{"emotion": "Neutral", "confidence": 0.7196246385574341, "window": "55.0s - 57.0s"}
```

情绪固定为 **Neutral / Positive / Negative** 三种；`window` 是感知系统给出的滑动窗口区间
（每 2 秒一个）。`latency_ms`、`camera_ok`、`frame_count`、`logs` 等字段属于算法侧诊断信息，
本系统一律忽略。仓库内的 [samples/state.json](samples/state.json) 就是一份真实录制的输入样例。

在没有配置任何云服务 API Key 的情况下也能完整跑通全链路（默认走 Mock 适配层，
语音播报由浏览器原生能力承担）；配置 Key 后仅需改 `.env`，业务代码零改动。

> 部署边界：系统未实现鉴权（WebSocket 与 REST 接口均可匿名访问），定位为单机演示与
> 内网试用，请勿直接暴露到公网。

---

## 一、快速开始

需要 Python 3.11+ 与 Node 18+。

### 1. 后端

```powershell
cd backend
python -m pip install -r requirements.txt
Copy-Item .env.example .env          # 可选：不创建也能用默认配置直接跑
python -m uvicorn app.main:app --reload --port 8000
```

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173/（已配置 `/api`、`/ws` 代理到 8000 端口）。

### 3. 演示流程（答辩/录屏用）

感知窗口每 2 秒一个，因此演示节奏以"窗口"为单位：

1. 进入「实时大屏」，确认顶部出现绿色「通道已连接」与 `0.5 窗口/s` 徽标；
2. 点击左侧「模拟演示」→ **负向升级**：约 4 秒后开始出现 Negative 窗口，约 8~10 秒触发
   「轻度 · 主动陪伴」共情话术卡片（同时语音播报），持续恶化后约 14 秒升级为「高危 · 安全出口」转介话术；
3. 点击 **情绪恢复**：约 8 秒后出现「状态恢复」确认话术；
4. 点击 **情绪波动**：每 10 秒一个负向尖峰，验证"单个窗口不触发"的去抖设计；
5. 另开一个终端启动 EEG 服务（`python -m eeg_service.serve --level High`），观察右侧
   「脑电认知负荷」面板与中间第二条「负荷色带」：高/极高连续约 4 秒触发
   「脑电负荷偏高」降速引导话术，冷却 90 秒内不重复，等级继续恶化（高→极高）会打断冷却；
6. 在「语音对话」面板按住说话（或直接输入文字）→ 观察 ASR 文本 → 共情回应 → 语音播报；
7. 点击右上「历史日志」→ 查看会话列表 / 情绪窗口采样点（含负荷两列）/ 干预记录，并导出两份 CSV。

---

## 二、数据输入契约

### 1. 情绪通道：三个标签

| 字段 | 含义 | 取值 |
|---|---|---|
| `emotion` | 情绪三分类 | `Neutral` / `Positive` / `Negative` |
| `confidence` | 分类置信度 | `0.0 ~ 1.0` |
| `window` | 感知窗口区间 | 形如 `"55.0s - 57.0s"` |

`window` 会被规范化为 `"起点s - 终点s"` 的形式（`"55-57"`、`{"start":55,"end":57}` 也能识别），
并原样写入状态曲线与 CSV，保证每个曲线点都能追溯到感知系统当时的窗口，便于答辩举证。

### 2. 脑电负荷通道：4 级等级

独立 EEG 服务按 1s 推送一条（4s 滑窗）：

```json
{"type": "load", "payload": {
  "level": "High", "confidence": 0.92,
  "window": "12.0s - 16.0s", "task": "起飞"
}}
```

| 字段 | 含义 | 取值 |
|---|---|---|
| `level` | 认知负荷等级 | `Low` / `Medium` / `High` / `VeryHigh`（低/中/高/极高） |
| `confidence` | 等级置信度 | `0.0 ~ 1.0`（百分数 `92` 也识别） |
| `window` | EEG 侧窗口 | 形如 `"12.0s - 16.0s"`，可与情绪窗口不同 |

解析器（`backend/app/sources/load_parser.py`）对写法漂移做了兼容：中文等级（`极高`）、
`L/M/H/VH`、`1~4` 类别编号、`score`（0~100 连续分）、旧版 3 级写法、以及
`{"type":"load","payload":{…}}` / `{"cognitive":{…}}` / 扁平键三种包装都能识别。
等级键缺失或无法识别时整帧丢弃，不影响情绪链路。

负荷是**状态量**：后端只保留最新一条（`LoadStore`），情绪帧落库/推送时带上此刻的负荷快照，
前端另有独立的 `load` 信封即时刷新（1Hz 比情绪窗口更密）。未接入 EEG 服务时
`StateFrame.load` 恒为 `null`，契约与旧版本完全一致。

### 3. 接入方式

情绪通道（`SOURCE_MODE`）：

| 模式 | 说明 | 关键配置 |
|---|---|---|
| `simulator` | 内置模拟器，按 2s 窗口构造与 `state.json` 同形的数据 | 无 |
| `external` | 直连感知系统 WebSocket（每窗口推送一条） | `EXTERNAL_STATE_URL` |
| `file` | 轮询感知系统写出的 `state.json`（按 `window` 去重） | `STATE_FILE` |

脑电负荷通道（`LOAD_SOURCE_MODE`）：

| 模式 | 说明 | 关键配置 |
|---|---|---|
| `off` | 不接入，行为与旧版本一致 | 无 |
| `simulator` | 内置 4 级模拟（默认），按"巡航→转弯→起飞→着陆"爬升 | 无 |
| `external` | 直连 EEG 服务 WebSocket | `LOAD_EXTERNAL_URL`（默认 `ws://127.0.0.1:8766`） |
| `file` | 轮询 EEG 服务写出的 JSON | `LOAD_STATE_FILE` |

`external` 接收两种报文形态，解析器只取三个标签：

```json
{"type": "state", "payload": {"emotion": "Negative", "confidence": 0.86, "window": "55.0s - 57.0s"}}
```

```json
{"emotion": "Negative", "confidence": 0.86, "window": "55.0s - 57.0s", "latency_ms": 2210, "logs": []}
```

文件被截断（感知侧正在写入）时会静默跳过、下一轮再读；同一 `window` 重复出现不会重复计数，
避免触发判定被高频重复推送放大。

### 4. 内置模拟

情绪模拟（`scenario`）：

| 模拟 | 说明 |
|---|---|
| `calm` | valence ≈ 0.10，以 Neutral 为主，保持静默监测 |
| `anxiety_escalation` | 约 4.5s 内从常态跌到明确 Negative，演示"先陪伴、后转介" |
| `recovery` | 目标直接回到 Positive，约 3 个窗口脱离 Negative |
| `fluctuation` | 每 10s 一个约 1.5s 的负向尖峰（至多命中一个窗口），验证去抖不误触发 |

负荷模拟：96s 一个循环，巡航(低) 24s → 水平转弯(中) 18s → 起飞(高) 18s → 着陆(极高) 14s
→ 落地滑行(低) 22s，等级切换后前 3 秒置信度偏低（模拟状态过渡）。

置信度由"离分类边界距离"决定（0.55 ~ 0.97）：Neutral 通常 0.7 左右，明确 Negative 时更高，
与真实算法给出的分布一致。

---

## 三、目录结构

```
backend/
  app/
    main.py            应用入口：依赖组装 + 后台任务编排（唯一知道所有实现的地方）
    config.py          全部可调参数（.env）
    schemas.py         数据契约单一真源（WS 下行 / REST / 落库 / 前端共有）
    bus.py             事件总线：多订阅者扇出 + drop-oldest 背压
    hub.py             WS 连接管理：每连接队列 + 独立发送协程
    load_store.py      脑电负荷快照：最新值 + 扇出 + 贴到情绪帧上
    api/               ws_monitor / ws_voice / logs / system
    sources/           StateSource 抽象 + simulator + external + file + parser(state.json)
                       LoadSource 抽象 + simulator + external + file + load_parser(负荷等级)
    decision/          trigger(去抖状态机) / prompt / validator(抗幻觉) / empathizer
    providers/         llm(mock|deepseek|zhipu|qwen|openai) / asr(mock|openai) / tts(mock|edge)
    db/                models / session / repo
    service/pipeline.py  顶层业务编排
  tests/               pytest 用例 + smoke.py + loadcheck.py
perception/            独立感知服务（算法侧骨架）：按 2s 窗口输出情绪三分类
  protocol.py          输出契约（emotion / confidence / window）
  backends.py          感知后端抽象 + simulator/replay，真实算法在此接入
  serve.py             WebSocket 服务端 + 可选 state.json 落盘（--dump）
  README.md            用法、时序约定与接入真实算法的方式
eeg_service/           独立脑电负荷服务（算法侧骨架）：4s 窗 1s 步输出 4 级负荷
  protocol.py          输出契约（level / confidence / window / task）
  simulator.py         4 级负荷模拟（按申报书四类飞行任务爬升）
  serve.py             WebSocket 服务端 + 可选 JSON 落盘（--dump）
  README.md            用法、报文格式与接入真实算法的方式
frontend/
  src/
    types/contract.ts  与 schemas.py 一一对应
    state.ts           全局响应式状态（含最新负荷与等级分布）
    composables/       useMonitorSocket（重连/心跳）useVoiceSocket（录音上行）
    components/        EmotionGauge / EmotionTrendChart / StateTimeline / LoadPanel /
                       InterventionFeed / VoicePanel / ScenarioPanel
    views/             DashboardView / HistoryView
docs/
  系统设计与工程实现报告.md
samples/
  state.json           真实感知系统输出样例（本系统的输入格式）
```

---

## 四、接口速览

| 类型 | 路径 | 说明 |
|---|---|---|
| WS | `/ws/monitor` | 下行：`meta` / `state` / `load` / `pending` / `intervention` / `speak` / `scenario` |
| WS | `/ws/voice` | 上行：`0x01+音频` 二进制帧、`{"type":"audio_end"}`、`{"type":"text","text":"…"}` |
| GET | `/api/health` | 各适配层实现与可用性、窗口到达率、在线客户端数 |
| POST | `/api/scenario` | 注入模拟演示：`calm` / `anxiety_escalation` / `recovery` / `fluctuation` |
| GET | `/api/sessions` | 会话列表（时间范围、采样点数、干预次数） |
| GET | `/api/states` | 情绪窗口采样点（1Hz 聚合），支持会话与时间范围过滤、分页 |
| GET | `/api/interventions` | 干预记录，含触发原因、话术、策略、风险、时延、是否降级 |
| GET | `/api/export/states.csv` \| `/api/export/interventions.csv` | CSV 导出（流式） |

状态帧（`/ws/monitor` 的 `state`）示例：

```json
{"ts": 1794668035.9, "session_id": "s-20261114-150530-ab12",
 "emotion": {"label": "Neutral", "confidence": 0.7196},
 "load": {"level": "High", "confidence": 0.92, "window": "12.0s - 16.0s", "source": "external"},
 "window": "55.0s - 57.0s", "source": "simulator"}
```

独立的负荷更新帧（比情绪窗口更密，供大屏实时刷新负荷面板）：

```json
{"type": "load", "payload": {"load": {"level": "VeryHigh", "confidence": 0.95,
  "window": "16.0s - 20.0s", "source": "external"}}}
```

---

## 五、切换到真实云服务

### 1. 大模型：改两行即可

DeepSeek、智谱 GLM、通义千问、OpenAI 均提供 OpenAI 兼容接口，因此它们共用同一个适配器
（`providers/llm/openai_compat.py`），厂商差异只是一张 `(base_url, 默认模型)` 表。

```ini
# 例：使用 DeepSeek，只需下面两行
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-你的DeepSeekKey
```

| `LLM_PROVIDER` | 默认接入地址 | 默认模型 |
|---|---|---|
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-flash` |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |

需要换模型、或走自建网关/代理时，再额外填 `LLM_MODEL` / `LLM_BASE_URL` 覆盖即可。

### 2. 语音与数据源（可选）

```ini
# 真实语音识别（可选，OpenAI Whisper 兼容接口，OpenAI / 自建网关均可）
ASR_PROVIDER=openai
ASR_API_KEY=sk-你的Key
# 非默认模型或自建网关时再填 ASR_MODEL / ASR_BASE_URL 覆盖

# 真实语音合成（可选，需 pip install "edge-tts>=7.3.1"；7.2.x 会被微软接口 403）
TTS_PROVIDER=edge
TTS_VOICE=zh-CN-XiaoxiaoNeural
# 韵律可调：略慢更自然（取值必须是 "-4%" / "+0Hz" 这种带单位的形式）
TTS_RATE=-4%
TTS_PITCH=+0Hz

# 对接真实的情绪识别系统：二选一
SOURCE_MODE=external
EXTERNAL_STATE_URL=ws://感知系统地址

# 或：直接读感知系统写出的 state.json
SOURCE_MODE=file
STATE_FILE=D:\路径\state.json

# 第二路：脑电认知负荷（独立 EEG 服务），同样二选一
LOAD_SOURCE_MODE=external
LOAD_EXTERNAL_URL=ws://127.0.0.1:8766
# 或
LOAD_SOURCE_MODE=file
LOAD_STATE_FILE=D:\路径\load.json
```

切换后 `/api/health` 会把对应适配层标记为 `real`，前端顶部徽标同步变化，
避免"以为在调用真模型、实际在跑 Mock"的误判。`providers` 里现在有 5 项：
`llm / asr / tts / source / load`；`load` 为 `off` 表示第二路未接入。

没有现成感知系统时，本仓库自带的 `perception/` 服务骨架可直接充当数据源
（内置窗口级模拟，无需算法与硬件；接入真实算法的方式见 `perception/README.md`）：

```powershell
python -m perception.serve                          # 监听 ws://127.0.0.1:8765
python -m perception.serve --dump samples/state.json  # 同时按 state.json 形状落盘
```

脑电负荷同理，用 `eeg_service/` 充当 EEG 服务（4 级负荷、4s 窗 1s 步）：

```powershell
python -m eeg_service.serve                     # ws://127.0.0.1:8766，96s 走完低→极高
python -m eeg_service.serve --level High        # 固定"高"，演示 high_load 触发与冷却
python -m eeg_service.serve --speed 4 --dump load.json   # 4 倍速 + JSON 落盘（file 模式）
```

注意：切到 `external` / `file` 后，大屏「模拟演示」按钮不再驱动数据——数据来自感知系统；
模拟计时从感知服务进程启动时算起，重启感知服务即可复位，后端会自动重连。

> 提示：真实大模型输出会被 `decision/validator.py` 做禁词与格式硬校验，
> 不合规时自动降级为规则话术并在记录中标记 `degraded=true`，因此换任何模型都不会输出越界内容。

---

## 六、测试

```powershell
cd backend
python -m pytest                 # 82 个单元/集成用例
python -m tests.smoke            # 端到端冒烟（需先启动后端）
python -m tests.loadcheck        # 并发推送 + 干预延迟压测（需先启动后端）
```

前端类型检查：

```powershell
cd frontend
npm run typecheck                # vue-tsc --noEmit；npm run build 也会先跑一遍
```

`tests/` 说明：

- `test_trigger.py` —— 窗口去抖、置信度门槛、冷却、升级打断、恢复重置、偶发负向不误触发、降频聚合；
- `test_sources.py` —— state.json 三标签解析（多余字段忽略）、窗口规范化、WS 包装解包、
  file 模式按窗口去重与半写文件容错、模拟器输出形态、报文内嵌负荷快照；
- `test_load.py` —— 负荷等级别名/百分数置信度/三种包装、快照合并（不覆盖帧自带负荷）、
  模拟器四级爬升、文件源去重、高负荷触发与冷却、升级打断、绝不走安全出口；
- `test_validator.py` —— JSON 容错、禁词拦截、超长截断、高危安全出口、兜底库覆盖；
- `test_llm_provider.py` —— 厂商默认值、请求报文形态、错误透出、模型故障/超时降级；
- `test_asr_provider.py` —— 工厂选择、Whisper 请求报文（multipart / 模型 / 文件名后缀）、错误透出、无 Key 未就绪；
- `test_hub.py` —— 控制消息不被状态流阻塞、状态帧唤醒空闲发送协程、drop-oldest 背压；
- `test_api.py` —— 健康检查、模拟切换、历史查询结构、CSV 导出；
- `test_ws.py` —— 状态帧下行（情绪三分类 + 窗口 + 负荷字段）、语音文本上行闭环、空输入拒绝。
