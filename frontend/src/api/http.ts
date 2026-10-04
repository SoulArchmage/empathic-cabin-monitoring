/** REST 接口封装。导出类接口直接用 <a download> 交给浏览器，避免前端拼装大文件。 */
import type { Intervention, Page, Health, SessionSummary, StatePoint } from '../types/contract'

async function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  const suffix = query.toString() ? `?${query}` : ''
  const response = await fetch(`/api${url}${suffix}`)
  if (!response.ok) throw new Error(`${url} 请求失败：${response.status}`)
  return (await response.json()) as T
}

export interface StateQuery {
  session_id?: string
  start?: number
  end?: number
  limit?: number
  offset?: number
}

export const api = {
  health: () => get<Health>('/health'),

  sessions: () => get<{ items: SessionSummary[] }>('/sessions'),

  states: (query: StateQuery = {}) => get<Page<StatePoint>>('/states', { ...query }),

  interventions: (query: StateQuery = {}) =>
    get<Page<Intervention>>('/interventions', { ...query }),

  setScenario: async (name: string) => {
    const response = await fetch('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    })
    if (!response.ok) throw new Error('模拟切换失败')
    return (await response.json()) as { scenario: string; session_id: string }
  },

  exportUrl: (kind: 'states' | 'interventions', query: StateQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
    }
    const suffix = params.toString() ? `?${params}` : ''
    return `/api/export/${kind}.csv${suffix}`
  }
}
