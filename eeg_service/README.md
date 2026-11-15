# 脑电负荷服务（EEG Cognitive Load）

独立的第二路数据源：按申报书《基于脑电信号的认知负荷监测系统》的口径，输出
**Low / Medium / High / VeryHigh（低 / 中 / 高 / 极高）** 四级脑电认知负荷与置信度，
供智能疏导仓接入。它是算法侧的桩：内置 4 级模拟即可跑通全链路，接真实算法时只改
`simulator.py` 的取数逻辑，对外报文不用动。

## 一、报文格式

每 1 秒一条（4s 窗、1s 步长，与申报书的 EEG 处理参数一致）：

```json
{"type": "load", "payload": {
  "level": "High", "confidence": 0.92,
  "window": "12.0s - 16.0s", "task": "起飞", "source": "eeg"
}}
```

| 字段 | 含义 | 取值 |
|---|---|---|
| `level` | 认知负荷等级 | `Low` / `Medium` / `High` / `VeryHigh` |
| `confidence` | 该等级的置信度 | `0.0 ~ 1.0` |
| `window` | EEG 侧滑动窗口 | 形如 `"12.0s - 16.0s"` |
| `task` | 对应飞行任务（自查用，疏导仓忽略） | 巡航 / 水平转弯 / 起飞 / 着陆 |

疏导仓侧的解析器（`backend/app/sources/load_parser.py`）还兼容：中文等级（`极高`）、
`L/M/H/VH`、`1~4` 类别编号、百分数置信度（`92`）、旧版 3 级写法与 `score` 字段，
因此真实系统即使字段名略有差异也能直接对接。

## 二、启动

```powershell
# 默认：ws://127.0.0.1:8766，按 96s 循环走完"低 → 中 → 高 → 极高"
python -m eeg_service.serve

# 固定为"高"：用于复现 high_load 触发与冷却
python -m eeg_service.serve --level High

# 4 倍速 + 落地成 JSON（给 file 模式用）
python -m eeg_service.serve --speed 4 --dump load.json
```

参数：`--host --port --window --step --speed --level --dump --seed`。

## 三、接到疏导仓

`backend/.env`（改完重启后端）：

```ini
LOAD_SOURCE_MODE=external
LOAD_EXTERNAL_URL=ws://127.0.0.1:8766
```

或者不开放 WebSocket、直接读文件：

```ini
LOAD_SOURCE_MODE=file
LOAD_STATE_FILE=D:\path\to\load.json
```

`LOAD_SOURCE_MODE=simulator`（默认）时，疏导仓用内置模拟，不需要启动本服务；
`LOAD_SOURCE_MODE=off` 则完全关闭第二路，契约中的 `load` 恒为 `null`。

## 四、时序约定

- 4s 滑窗、1s 步长；等级切换后的前 3 秒置信度偏低（0.55~0.75），模拟状态过渡；
- 一个完整循环 96s：巡航(低) 24s → 水平转弯(中) 18s → 起飞(高) 18s → 着陆(极高) 14s → 落地滑行(低) 22s；
- 同一窗口重复推送无副作用：疏导仓按 `window + level` 去重。

## 五、接真实算法

替换 `LoadSimulator.next_frame()` 的返回值即可（`LoadFrame` 已做取值范围校验）：

```python
from eeg_service.protocol import LoadFrame

frame = LoadFrame(level="High", confidence=0.93,
                  window_start=t0, window_end=t0 + 4.0, task="起飞")
```

算法侧要做的三件事：8 通道（F3/Fz/F4/Cz/P3/Pz/P4/Oz）500Hz 采集 → 1~40Hz 带通 +
50Hz 陷波 → RST-GNN 四分类，取 softmax 最大值作为 `confidence`、对应类别作为 `level`。
