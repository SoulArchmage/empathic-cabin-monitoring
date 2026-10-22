/**
 * 语音通道客户端：录音上行 + ASR 结果接收。
 *
 * 帧约定（与后端 api/ws_voice.py 对齐）：
 *   二进制帧 0x01 + 音频字节 → 音频分片
 *   文本帧 JSON             → 控制消息
 *
 * 音频格式：MediaRecorder 产出的 webm/mp4 属于"流式容器"（缺时长头），部分云端
 * ASR（实测硅基流动）解析极慢甚至直接 5xx，因此录音结束后先在本地解码并转成
 * 16kHz 单声道 WAV 再上行；转换失败时退回原始音频，保证功能可用。
 *
 * 降级策略：浏览器不支持 MediaRecorder 或麦克风授权失败时，自动退回到文本输入，
 * 保证"语音助手"在受限环境下依然可用。
 */
import { onMounted, onUnmounted, ref } from 'vue'

import { monitor } from '../state'
import type { Envelope } from '../types/contract'

const FRAME_AUDIO = 0x01
const TARGET_SAMPLE_RATE = 16000
const UPLOAD_CHUNK_BYTES = 16384

let socket: WebSocket | null = null
let recorder: MediaRecorder | null = null
let stream: MediaStream | null = null
let recordedChunks: Blob[] = []

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

    // 录音期间只在本地攒块，结束后统一转 WAV 上行（见 uploadRecording）
    recordedChunks = []
    recorder.ondataavailable = (event) => {
      if (event.data.size) recordedChunks.push(event.data)
    }
    recorder.start(500)
    recording.value = true
  }

  function stopRecording(): void {
    const rec = recorder
    if (!rec) return
    const stopped = new Promise<void>((resolve) => {
      rec.onstop = () => resolve()
    })
    rec.stop()
    rec.stream.getTracks().forEach((track) => track.stop())
    stream = null
    recorder = null
    recording.value = false
    void uploadRecording(stopped, rec.mimeType || 'audio/webm')
  }

  /** 录音结束：本地转 WAV（失败则用原始格式）→ 分片上行 → 通知后端触发 ASR。 */
  async function uploadRecording(stopped: Promise<void>, fallbackMime: string): Promise<void> {
    await stopped
    const chunks = recordedChunks
    recordedChunks = []
    const raw = new Blob(chunks, { type: fallbackMime })
    if (!raw.size) return

    const payload = (await encodeWav(raw)) ?? raw
    if (socket?.readyState !== WebSocket.OPEN) return
    const bytes = new Uint8Array(await payload.arrayBuffer())
    for (let offset = 0; offset < bytes.length; offset += UPLOAD_CHUNK_BYTES) {
      const part = bytes.subarray(offset, offset + UPLOAD_CHUNK_BYTES)
      const frame = new Uint8Array(part.length + 1)
      frame[0] = FRAME_AUDIO
      frame.set(part, 1)
      socket.send(frame)
    }
    socket.send(JSON.stringify({ type: 'audio_end', mime: payload.type || fallbackMime }))
  }

  /** webm/opus 等流式容器 → 16kHz 单声道 16bit WAV；解码失败返回 null。 */
  async function encodeWav(blob: Blob): Promise<Blob | null> {
    const ctx = new AudioContext()
    try {
      const decoded = await ctx.decodeAudioData(await blob.arrayBuffer())
      const frames = Math.ceil(decoded.duration * TARGET_SAMPLE_RATE)
      if (!frames) return null
      const offline = new OfflineAudioContext(1, frames, TARGET_SAMPLE_RATE)
      const source = offline.createBufferSource()
      source.buffer = decoded
      source.connect(offline.destination)
      source.start()
      const rendered = await offline.startRendering()
      return new Blob([toPcm16(rendered)], { type: 'audio/wav' })
    } catch {
      return null
    } finally {
      void ctx.close()
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

/** AudioBuffer 通道 0 → 16bit PCM 的小端 WAV 字节流。 */
function toPcm16(buffer: AudioBuffer): ArrayBuffer {
  const samples = buffer.getChannelData(0)
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2))
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i))
  }
  text(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  text(8, 'WAVE')
  text(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, buffer.sampleRate, true)
  view.setUint32(28, buffer.sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  text(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true)
  }
  return view.buffer
}