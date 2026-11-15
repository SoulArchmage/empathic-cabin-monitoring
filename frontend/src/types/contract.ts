/**
 * 数据契约：与后端 app/schemas.py 一一对应。
 *
 * 两路输入：
 * 1. 情绪通道：感知系统每 2 秒给出的一个情绪窗口（emotion / confidence / window）；
 * 2. 脑电负荷通道：独立 EEG 服务给出的 4 级负荷结果（Level / confidence / window），
 *    以 load 快照挂在情绪帧上，同时也会单独以 `load` 信封实时推送。
 */

export type EmotionLabel = 'Positive' | 'Negative' | 'Neutral'
/** 脑电认知负荷四级：低 / 中 / 高 / 极高（与申报书表 4.1 一致） */
export type LoadLevel = 'Low' | 'Medium' | 'High' | 'VeryHigh'
export type Severity = 'normal' | 'mild' | 'severe'
export type Strategy = '呼吸引导' | '共情复述' | '环境调节' | '轻量提问'
export type Risk = 'low' | 'medium' | 'high'
export type ScenarioName = 'calm' | 'anxiety_escalation' | 'recovery' | 'fluctuation'

export interface Emotion {
  label: EmotionLabel
  confidence: number
}

export interface CognitiveLoad {
  level: LoadLevel
  confidence: number
  /** EEG 侧窗口，形如 "12.0s - 16.0s"（4s 窗、1s 步长） */
  window: string
  source: string
}

export interface StateFrame {
  ts: number
  session_id: string
  emotion: Emotion
  /** 该时刻最新的脑电负荷快照；未接入 EEG 服务时为 null */
  load?: CognitiveLoad | null
  /** 感知窗口区间，形如 "55.0s - 57.0s" */
  window: string
  source: string
}

export interface WindowStats {
  window: string
  window_size: number
  negative_ratio: number
  positive_ratio: number
  neutral_ratio: number
  avg_confidence: number
  negative_confidence: number
  consec_abnormal: number
  duration_s: number
  /** 负荷判据：最新等级 / 置信度 / 窗口内"高及以上"占比 */
  load_level: string
  load_confidence: number
  load_high_ratio: number
}

export interface Intervention {
  id: number | null
  ts: number
  session_id: string
  kind: string
  severity: Severity
  reason: string
  prompt_digest: string
  speech: string
  strategy: Strategy
  risk: Risk
  degraded: boolean
  provider: string
  latency_ms: number
  audio_available: boolean
  stats: WindowStats | null
}

export interface ProviderStatus {
  name: string
  mode: 'mock' | 'real'
  ready: boolean
  /** 最近一次真实调用是否成功；null = 本次启动后尚未调用 */
  last_ok: boolean | null
  last_detail: string
  /** epoch 秒 */
  last_at: number
}

export interface PendingItem {
  kind: string
  severity: Severity
  reason: string
  ts: number
}

export interface SpeakItem {
  text: string
  audio_b64: string | null
  mime: string
  voice: string
  tts_provider: string
  intervention_id: number | null
}

export interface Envelope {
  type:
    | 'meta'
    | 'state'
    | 'load'
    | 'pending'
    | 'intervention'
    | 'speak'
    | 'asr_final'
    | 'scenario'
    | 'error'
  payload: Record<string, unknown>
}

export interface StatePoint {
  ts: number
  session_id: string
  emotion_label: EmotionLabel
  emotion_confidence: number
  window: string
  source: string
  /** 负荷快照：未接入 EEG 时为空串 / 0.0 */
  load_level: string
  load_confidence: number
}

export interface SessionSummary {
  session_id: string
  started_at: number
  ended_at: number
  state_points: number
  interventions: number
}

export interface Page<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}

export interface Health {
  status: string
  app: string
  frame_rate_hz: number
  active_clients: number
  current_scenario: string
  providers: Record<string, ProviderStatus>
}

/** 模拟中文标签，集中在一处便于界面与文档保持一致 */
export const SCENARIO_LABELS: Record<ScenarioName, string> = {
  calm: '平静常态',
  anxiety_escalation: '负向升级',
  recovery: '情绪恢复',
  fluctuation: '情绪波动'
}

export const EMOTION_TEXT: Record<EmotionLabel, string> = {
  Positive: '积极',
  Neutral: '中性',
  Negative: '负向'
}

export const EMOTION_COLOR: Record<EmotionLabel, string> = {
  Positive: '#37d399',
  Neutral: '#96a3b8',
  Negative: '#ff6b6b'
}

/** 负荷四级的展示顺序（由轻到重），与后端 LOAD_LEVELS 一致 */
export const LOAD_ORDER: LoadLevel[] = ['Low', 'Medium', 'High', 'VeryHigh']

export const LOAD_TEXT: Record<LoadLevel, string> = {
  Low: '低',
  Medium: '中',
  High: '高',
  VeryHigh: '极高'
}

export const LOAD_COLOR: Record<LoadLevel, string> = {
  Low: '#37d399',
  Medium: '#4cc9f0',
  High: '#ffb703',
  VeryHigh: '#ff6b6b'
}

export const KIND_TEXT: Record<string, string> = {
  negative_emotion: '负向情绪',
  high_load: '脑电负荷偏高',
  recovered: '状态恢复',
  stable: '状态平稳'
}
