/**
 * 全局响应式状态：集中管理实时数据，避免组件各自维护副本。
 * 未引入 Pinia —— 单页大屏场景下，一个 reactive 单例足够且更直观。
 */
import { computed, reactive, ref } from 'vue'

import type {
  Intervention,
  PendingItem,
  ProviderStatus,
  ScenarioName,
  SpeakItem,
  StateFrame
} from './types/contract'

const MAX_FRAMES = 300 // 滑动窗口：约 30 秒 @10Hz，兼顾观感与渲染开销
const MAX_INTERVENTIONS = 50

export const monitor = reactive({
  connected: false,
  sessionId: '',
  scenario: 'calm' as ScenarioName,
  providers: {} as Record<string, ProviderStatus>,
  serverFrameRate: 0,
  activeClients: 0,
  frameSeq: 0, // 单调递增序号：滑动窗口长度会饱和，靠它触发下游重绘
  latest: null as StateFrame | null,
  frames: [] as StateFrame[],
  interventions: [] as Intervention[],
  pending: null as PendingItem | null,
  asrText: '',
  error: ''
})

/** 播报信号：由 useMonitorSocket 写入，AudioNarrator 统一消费 */
export const speakSignal = ref<SpeakItem | null>(null)
export const muted = ref(false)

export const emotionCounts = computed(() => {
  const counts = { Positive: 0, Neutral: 0, Negative: 0 }
  for (const f of monitor.frames) counts[f.emotion.label] += 1
  return counts
})

/** 由滑动窗口内的帧时间戳实时推算推送帧率，比轮询 /api/health 更直观 */
export const frameRate = computed(() => {
  const n = monitor.frames.length
  if (n < 2) return 0
  const span = monitor.frames[n - 1].ts - monitor.frames[0].ts
  return span > 0 ? Number(((n - 1) / span).toFixed(1)) : 0
})

export function pushFrame(frame: StateFrame): void {
  monitor.frames.push(frame)
  if (monitor.frames.length > MAX_FRAMES) {
    monitor.frames.splice(0, monitor.frames.length - MAX_FRAMES)
  }
  monitor.frameSeq += 1
  monitor.latest = frame
}

export function pushIntervention(item: Intervention): void {
  monitor.pending = null
  monitor.interventions.unshift(item)
  if (monitor.interventions.length > MAX_INTERVENTIONS) {
    monitor.interventions.length = MAX_INTERVENTIONS
  }
}
