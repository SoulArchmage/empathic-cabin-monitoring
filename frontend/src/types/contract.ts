/**
 * 数据契约：与后端 app/schemas.py 一一对应。
 * 后端改字段时同步修改此文件，保证类型对齐（方案 D1 单一真源）。
 */

export type EmotionLabel = 'Positive' | 'Negative' | 'Neutral'
export type CognitiveLevel = 'Low' | 'Medium' | 'High'
export type Severity = 'normal' | 'mild' | 'severe'
export type Strategy = '呼吸引导' | '共情复述' | '环境调节' | '轻量提问'
export type Risk = 'low' | 'medium' | 'high'
export type ScenarioName = 'calm' | 'anxiety_escalation' | 'recovery' | 'high_load'

export interface Emotion {
  label: EmotionLabel
  confidence: number
}

export interface Cognitive {
  level: CognitiveLevel
  score: number
}

export interface StateFrame {
  ts: number
  session_id: string
  emotion: Emotion
  cognitive: Cognitive
  source: string
}

export interface WindowStats {
  window_size: number
  negative_ratio: number
  high_load_ratio: number
  consec_abnormal: number
  avg_cognitive_score: number
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
  cognitive_level: CognitiveLevel
  cognitive_score: number
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

/** 剧本中文标签，集中在一处便于界面与文档保持一致 */
export const SCENARIO_LABELS: Record<ScenarioName, string> = {
  calm: '平静常态',
  anxiety_escalation: '焦虑升级',
  recovery: '情绪恢复',
  high_load: '高认知负荷'
}

export const EMOTION_TEXT: Record<EmotionLabel, string> = {
  Positive: '积极',
  Neutral: '中性',
  Negative: '负向'
}

export const KIND_TEXT: Record<string, string> = {
  negative_emotion: '负向情绪',
  high_cognitive_load: '高认知负荷',
  combined: '情绪+负荷双异常',
  recovered: '状态恢复'
}
