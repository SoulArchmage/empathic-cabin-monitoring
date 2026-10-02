/**
 * 监测通道客户端：断线指数退避重连 + 心跳探测 + 按 type 分发。
 * 心跳的意义：WS 半开连接（服务端已崩但 TCP 未及时感知）不会触发 onclose，
 * 只有靠周期性 ping/pong 才能及时发现并重连。
 */
import { onMounted, onUnmounted } from 'vue'

import { api } from '../api/http'
import { pushFrame, pushIntervention, monitor, speakSignal } from '../state'
import type {
  Envelope,
  Intervention,
  PendingItem,
  ScenarioName,
  SpeakItem,
  StateFrame
} from '../types/contract'

const HEARTBEAT_MS = 15000
const HEALTH_POLL_MS = 5000

let socket: WebSocket | null = null
let heartbeatTimer: number | undefined
let healthTimer: number | undefined
let lastPong = 0
let retry = 0
let closedByUser = false

function endpoint(): string {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${location.host}/ws/monitor`
}

function dispatch(env: Envelope): void {
  const payload = env.payload
  switch (env.type) {
    case 'meta':
      if (payload.pong) {
        lastPong = Date.now()
        return
      }
      monitor.sessionId = String(payload.session_id ?? '')
      monitor.scenario = (payload.scenario as ScenarioName) ?? 'calm'
      monitor.providers = (payload.providers as never) ?? {}
      break
    case 'state':
      pushFrame(payload.frame as unknown as StateFrame)
      break
    case 'pending':
      monitor.pending = payload as unknown as PendingItem
      break
    case 'intervention':
      pushIntervention(payload.item as unknown as Intervention)
      break
    case 'speak':
      speakSignal.value = payload as unknown as SpeakItem
      break
    case 'scenario':
      monitor.scenario = payload.scenario as ScenarioName
      break
    case 'error':
      monitor.error = String(payload.message ?? '未知错误')
      break
    default:
      break
  }
}

function scheduleReconnect(): void {
  if (closedByUser) return
  retry = Math.min(retry + 1, 6)
  const delay = Math.min(1000 * 2 ** (retry - 1), 15000)
  window.setTimeout(connect, delay)
}

function connect(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }
  socket = new WebSocket(endpoint())

  socket.onopen = () => {
    monitor.connected = true
    monitor.error = ''
    retry = 0
    lastPong = Date.now()
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = window.setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return
      // 心跳超时（连着两次没回 pong）→ 主动断开，交给重连逻辑
      if (Date.now() - lastPong > HEARTBEAT_MS * 2) {
        socket.close()
        return
      }
      socket.send('ping')
    }, HEARTBEAT_MS)
  }

  socket.onmessage = (event) => {
    try {
      dispatch(JSON.parse(event.data as string) as Envelope)
    } catch {
      /* 忽略非 JSON 帧 */
    }
  }

  socket.onclose = () => {
    monitor.connected = false
    window.clearInterval(heartbeatTimer)
    scheduleReconnect()
  }

  socket.onerror = () => {
    socket?.close()
  }
}

async function pollHealth(): Promise<void> {
  try {
    const health = await api.health()
    monitor.providers = health.providers
    monitor.serverFrameRate = health.frame_rate_hz
    monitor.activeClients = health.active_clients
    monitor.scenario = health.current_scenario as ScenarioName
  } catch {
    /* 健康轮询失败不影响实时流 */
  }
}

export function useMonitorSocket() {
  onMounted(() => {
    closedByUser = false
    connect()
    void pollHealth()
    healthTimer = window.setInterval(() => void pollHealth(), HEALTH_POLL_MS)
  })

  onUnmounted(() => {
    closedByUser = true
    window.clearInterval(heartbeatTimer)
    window.clearInterval(healthTimer)
    socket?.close()
    socket = null
  })

  return { monitor, connect }
}
