/**
 * 全局响应式状态：集中管理实时数据，避免组件各自维护副本。
 * 未引入 Pinia —— 单页大屏场景下，一个 reactive 单例足够且更直观。
 */
import { computed, reactive, ref } from 'vue'

import type {
  CognitiveLoad,
  Intervention,
  PendingItem,
  ProviderStatus,
  ScenarioName,
  SpeakItem,
  StateFrame
} from './types/contract'

const MAX_FRAMES = 300 // 滑动窗口：约 10 分钟 @0.5Hz（每 2s 一个情绪窗口），兼顾观感与渲染开销
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
  /** 最新一条脑电负荷（第二路数据源）；未接入时为 null */
  load: null as CognitiveLoad | null,
  interventions: [] as Intervention[],
  pending: null as PendingItem | null,
  asrText: '',
  error: ''
})

/** 播报信号：由 useMonitorSocket 写入，AudioNarrator 统一消费 */
export const speakSignal = ref<SpeakItem | null>(null)
/** 最近一次播报内容：保留 audio_b64，供"重播最近回应"复用真实合成音频 */
export const lastSpeak = ref<SpeakItem | null>(null)
export const muted = ref(false)
/** 浏览器拦截了自动播放：需要用户点击一次页面才能出声 */
export const audioBlocked = ref(false)
/** 当前是否正在播报（用于顶栏状态提示） */
export const audioPlaying = ref(false)

export const emotionCounts = computed(() => {
  const counts = { Positive: 0, Neutral: 0, Negative: 0 }
  for (const f of monitor.frames) counts[f.emotion.label] += 1
  return counts
})

/** 滑动窗口内各负荷等级的出现次数（只统计带负荷快照的窗口） */
export const loadCounts = computed(() => {
  const counts = { Low: 0, Medium: 0, High: 0, VeryHigh: 0 }
  for (const f of monitor.frames) {
    if (f.load) counts[f.load.level] += 1
  }
  return counts
})

/** 最近窗口内"高及以上"的占比：与后端 load_high_ratio 同一口径 */
export const highLoadShare = computed(() => {
  const total = loadCounts.value.Low + loadCounts.value.Medium +
    loadCounts.value.High + loadCounts.value.VeryHigh
  if (!total) return null
  return (loadCounts.value.High + loadCounts.value.VeryHigh) / total
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
  // 情绪帧上带着"当时最新的负荷快照"：先用它兜底，独立的 load 推送到了再覆盖
  if (frame.load) monitor.load = frame.load
}

/** 独立的负荷推送：EEG 侧 1Hz，比 0.5Hz 的情绪窗口更密，因此单独更新 */
export function pushLoad(load: CognitiveLoad): void {
  monitor.load = load
}

export function pushIntervention(item: Intervention): void {
  monitor.pending = null
  monitor.interventions.unshift(item)
  if (monitor.interventions.length > MAX_INTERVENTIONS) {
    monitor.interventions.length = MAX_INTERVENTIONS
  }
}
