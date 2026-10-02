/**
 * 语音通道客户端：录音上行 + ASR 结果接收。
 *
 * 帧约定（与后端 api/ws_voice.py 对齐）：
 *   二进制帧 0x01 + 音频字节 → 音频分片
 *   文本帧 JSON             → 控制消息
 *
 * 降级策略：浏览器不支持 MediaRecorder 或麦克风授权失败时，自动退回到文本输入，
 * 保证"语音助手"在受限环境下依然可用。
 */
import { onMounted, onUnmounted, ref } from 'vue'

import { monitor } from '../state'
import type { Envelope } from '../types/contract'

const FRAME_AUDIO = 0x01

let socket: WebSocket | null = null
let recorder: MediaRecorder | null = null
let stream: MediaStream | null = null

export function useVoiceSocket() {
  const recording = ref(false)
  const voiceConnected = ref(false)
  const voiceError = ref('')

  function endpoint(): string {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${protocol}://${location.host}/ws/voice`
  }

  function connect(): void {
    if (socket && socket.readyState <= WebSocket.OPEN) return
    socket = new WebSocket(endpoint())
    socket.binaryType = 'arraybuffer'

    socket.onopen = () => {
      voiceConnected.value = true
      voiceError.value = ''
    }
    socket.onclose = () => {
      voiceConnected.value = false
    }
    socket.onerror = () => {
      voiceError.value = '语音通道连接失败'
    }
    socket.onmessage = (event) => {
      const env = JSON.parse(event.data as string) as Envelope
      if (env.type === 'asr_final') monitor.asrText = String(env.payload.text ?? '')
      if (env.type === 'error') voiceError.value = String(env.payload.message ?? '')
    }
  }

  async function startRecording(): Promise<void> {
    voiceError.value = ''
    connect()
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      voiceError.value = '麦克风不可用（未授权或无设备），请改用文字输入'
      return
    }
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm'
    recorder = new MediaRecorder(stream, { mimeType: mime })

    recorder.ondataavailable = async (event) => {
      if (!event.data.size || socket?.readyState !== WebSocket.OPEN) return
      const buffer = new Uint8Array(await event.data.arrayBuffer())
      const frame = new Uint8Array(buffer.length + 1)
      frame[0] = FRAME_AUDIO
      frame.set(buffer, 1)
      socket.send(frame)
    }
    recorder.start(500) // 每 500ms 分片上行，避免一次性攒大包
    recording.value = true
  }

  function stopRecording(): void {
    if (!recorder) return
    const mime = recorder.mimeType
    recorder.stop()
    recorder.stream.getTracks().forEach((track) => track.stop())
    stream = null
    recorder = null
    recording.value = false

    // 通知后端音频结束，触发 ASR
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'audio_end', mime }))
    }
  }

  function sendText(text: string): void {
    connect()
    const payload = JSON.stringify({ type: 'text', text })
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(payload)
    } else {
      // 通道尚未就绪时排队一次，避免首次点击丢失输入
      socket?.addEventListener('open', () => socket?.send(payload), { once: true })
    }
  }

  onMounted(connect)
  onUnmounted(() => {
    recorder?.stop()
    stream?.getTracks().forEach((track) => track.stop())
    socket?.close()
    socket = null
  })

  return { recording, voiceConnected, voiceError, startRecording, stopRecording, sendText }
}
