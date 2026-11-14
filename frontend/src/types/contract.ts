/**
 * 数据契约：与后端 app/schemas.py 一一对应。
 *
 * 输入只有感知系统每 2 秒给出的一个情绪窗口，本系统只使用三个标签：
 * emotion（Positive / Neutral / Negative）、confidence、window。
 */

export type EmotionLabel = 'Positive' | 'Negative' | 'Neutral'
export type Severity = 'normal' | 'mild' | 'severe'
export type Strategy = '呼吸引导' | '共情复述' | '环境调节' | '轻量提问'
export type Risk = 'low' | 'medium' | 'high'
export type ScenarioName = 'calm' | 'anxiety_escalation' | 'recovery' | 'fluctuation'

export interface Emotion {
  label: EmotionLabel
  confidence: number
}

export interface StateFrame {
  ts: number
  session_id: string
  emotion: Emotion
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

export const KIND_TEXT: Record<string, string> = {
  negative_emotion: '负向情绪',
  recovered: '状态恢复',
  stable: '状态平稳'
}
