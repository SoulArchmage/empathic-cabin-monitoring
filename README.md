# 智能疏导仓 · 共情语音交互与状态监测系统

> 计算机网络综合工程设计 · 题目二
> 基于大模型的疏导仓共情语音交互与状态监测系统（B/S 架构 · 事件驱动 · 适配器模式）

一套可端到端运行的演示系统：**多源状态实时接入 → 异常状态去抖判定 → 大模型共情话术生成 →
抗幻觉校验兜底 → 语音播报 → 状态曲线与干预日志落库/查询/导出**。

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

1. 进入「实时大屏」，确认顶部出现绿色「通道已连接」与 `9.x Hz` 帧率徽标；
2. 点击左侧「演示剧本」→ **焦虑升级**：约 1 秒后出现「轻度 · 主动陪伴」共情话术卡片（同时语音播报），
   持续约 8 秒恶化后自动升级为「高危 · 安全出口」专业转介话术；
3. 点击 **情绪恢复**：出现「状态恢复」确认话术；
4. 在「语音对话」面板按住说话（或直接输入文字）→ 观察 ASR 文本 → 共情回应 → 语音播报；
5. 点击右上「历史日志」→ 查看会话列表 / 状态曲线采样点 / 干预记录，并导出两份 CSV。

---

## 二、目录结构

```
backend/
  app/
    main.py            应用入口：依赖组装 + 后台任务编排（唯一知道所有实现的地方）
    config.py          全部可调参数（.env）
    schemas.py         数据契约单一真源（WS 下行 / REST / 落库 / 前端共有）
    bus.py             事件总线：多订阅者扇出 + drop-oldest 背压
    hub.py             WS 连接管理：每连接队列 + 独立发送协程
    api/               ws_monitor / ws_voice / logs / system
    sources/           StateSource 抽象 + simulator + external(对接真实情绪/脑电系统)
    decision/          trigger(去抖状态机) / prompt / validator(抗幻觉) / empathizer
    providers/         llm(mock|deepseek|zhipu|qwen|openai) / asr(mock|openai) / tts(mock|edge)
    db/                models / session / repo
    service/pipeline.py  顶层业务编排
  tests/               pytest 用例 + smoke.py + loadcheck.py
perception/            独立感知服务（算法侧骨架）：情绪/认知负荷定频推送
  protocol.py          输出契约（与 schemas.py 的状态帧对齐）
  backends.py          感知后端抽象 + simulator（2Hz 模拟）/ replay（离线结果回放）
  serve.py             定频 WebSocket 服务端（对接 SOURCE_MODE=external）+ 命令行入口
  README.md            用法、时序约定与接入真实算法的方式
frontend/
  src/
    types/contract.ts  与 schemas.py 一一对应
    state.ts           全局响应式状态
    composables/       useMonitorSocket（重连/心跳）useVoiceSocket（录音上行）
    components/        EmotionGauge / CognitiveLoadChart / StateTimeline /
                       InterventionFeed / VoicePanel / ScenarioPanel
    views/             DashboardView / HistoryView
docs/
  系统设计与工程实现报告.md
```

---

## 三、接口速览

| 类型 | 路径 | 说明 |
|---|---|---|
| WS | `/ws/monitor` | 下行：`meta` / `state` / `pending` / `intervention` / `speak` / `scenario` |
| WS | `/ws/voice` | 上行：`0x01+音频` 二进制帧、`{"type":"audio_end"}`、`{"type":"text","text":"…"}` |
| GET | `/api/health` | 各适配层实现与可用性、实时帧率、在线客户端数 |
| POST | `/api/scenario` | 注入演示剧本：`calm` / `anxiety_escalation` / `recovery` / `high_load` |
| GET | `/api/sessions` | 会话列表（时间范围、采样点数、干预次数） |
| GET | `/api/states` | 状态曲线采样点（1Hz 聚合），支持会话与时间范围过滤、分页 |
| GET | `/api/interventions` | 干预记录，含触发原因、话术、策略、风险、时延、是否降级 |
| GET | `/api/export/states.csv` \| `/api/export/interventions.csv` | CSV 导出（流式） |

---

## 四、切换到真实云服务

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

# 真实语音合成（可选，需 pip install edge-tts）
TTS_PROVIDER=edge
TTS_VOICE=zh-CN-XiaoxiaoNeural

# 对接真实的情绪识别 / 认知负荷系统
SOURCE_MODE=external
EXTERNAL_STATE_URL=ws://外部系统地址
```

切换后 `/api/health` 会把对应适配层标记为 `real`，前端顶部徽标同步变化，
避免"以为在调用真模型、实际在跑 Mock"的误判。

没有现成外部系统时，本仓库自带的 `perception/` 感知服务骨架可直接充当 `EXTERNAL_STATE_URL`
的数据源（内置模拟，无需算法与硬件；接入真实算法的方式见 `perception/README.md`）：

```powershell
python -m perception.serve        # 监听 ws://127.0.0.1:8765，默认 10Hz
```

注意：切到 `external` 后，大屏「演示剧本」按钮不再驱动数据——外部流的剧本由感知侧
`--scenario` 决定，且计时从感知服务进程启动时算起（先起服务、后开大屏会看到剧本已推进
一段；重启感知服务即可复位，后端会自动重连）。

> 提示：真实大模型输出会被 `decision/validator.py` 做禁词与格式硬校验，
> 不合规时自动降级为规则话术并在记录中标记 `degraded=true`，因此换任何模型都不会输出越界内容。

---

## 五、测试

```powershell
cd backend
python -m pytest                 # 50 个单元/集成用例
python -m tests.smoke            # 端到端冒烟（需先启动后端）
python -m tests.loadcheck        # 并发推送 + 干预延迟压测（需先启动后端）
```

前端类型检查：

```powershell
cd frontend
npm run typecheck                # vue-tsc --noEmit；npm run build 也会先跑一遍
```

`tests/` 说明：

- `test_trigger.py` —— 去抖、冷却、升级打断、恢复重置、偶发噪声不误触发、降频聚合；
- `test_validator.py` —— JSON 容错、禁词拦截、超长截断、高危安全出口、兜底库覆盖；
- `test_llm_provider.py` —— 厂商默认值、请求报文形态、错误透出、模型故障/超时降级；
- `test_asr_provider.py` —— 工厂选择、Whisper 请求报文（multipart / 模型 / 文件名后缀）、错误透出、无 Key 未就绪；
- `test_hub.py` —— 控制消息不被状态流阻塞、状态帧唤醒空闲发送协程、drop-oldest 背压；
- `test_api.py` —— 健康检查、剧本切换、历史查询结构、CSV 导出；
- `test_ws.py` —— 状态帧下行、1Hz 落库、语音文本上行闭环、空输入拒绝。
