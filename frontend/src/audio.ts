/**
 * 语音播报：兼容两种 TTS 能力形态。
 *  - 后端返回 audio_b64（真实 TTS）→ 单个复用的 <audio> 播放合成音频；
 *  - 仅返回文本（Mock 模式）→ 浏览器原生 speechSynthesis 播报。
 *
 * 浏览器自动播放策略是这里最容易踩的坑：页面没有用户手势时，由 WebSocket 推送
 * 触发的 `audio.play()` 会被静默拒绝（NotAllowedError），表现就是"卡片弹了但没声音"。
 * 因此这里做三件事：
 *  1. 首次用户手势时用一段静音 WAV 解锁同一个 <audio> 元素（unlockAudio）；
 *  2. 被拦截时记下待播内容，并在界面顶栏显示「点击启用语音」；
 *  3. 用户点击后自动重播刚才被拦截的那条（retryPending）。
 */

import type { SpeakItem } from './types/contract'
import { audioBlocked, audioPlaying, muted } from './state'

let audioEl: HTMLAudioElement | null = null
let currentUrl: string | null = null
let pending: SpeakItem | null = null
let unlocked = false
let unlocking = false

function element(): HTMLAudioElement {
  if (audioEl) return audioEl
  audioEl = new Audio()
  audioEl.preload = 'auto'
  audioEl.addEventListener('playing', () => {
    audioPlaying.value = true
    audioBlocked.value = false
    pending = null
  })
  audioEl.addEventListener('pause', () => {
    audioPlaying.value = false
  })
  audioEl.addEventListener('ended', () => {
    audioPlaying.value = false
    revokeUrl()
  })
  audioEl.addEventListener('error', () => {
    // 主动清空 src 时也会触发 error，这里只关心"有内容却加载失败"
    if (!audioEl || !audioEl.currentSrc) return
    console.warn('[语音播报] 音频加载失败', audioEl.error)
    audioBlocked.value = true
  })
  return audioEl
}

function revokeUrl(): void {
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl)
    currentUrl = null
  }
}

function base64ToBlobUrl(b64: string, mime: string): string {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return URL.createObjectURL(new Blob([bytes], { type: mime || 'audio/mpeg' }))
}

/** 0.05s 静音 WAV：只为在用户手势里"点亮"音频元素，不产生可听声音 */
function silentWavUrl(): string {
  const sampleRate = 8000
  const samples = Math.floor(sampleRate * 0.05)
  const buffer = new ArrayBuffer(44 + samples)
  const view = new DataView(buffer)
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  write(0, 'RIFF')
  view.setUint32(4, 36 + samples, true)
  write(8, 'WAVEfmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate, true)
  view.setUint16(32, 1, true)
  view.setUint16(34, 8, true)
  write(36, 'data')
  view.setUint32(40, samples, true)
  new Uint8Array(buffer, 44, samples).fill(128)
  return URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }))
}

/** 在用户手势里调用一次，之后 WS 触发的播报才不会被自动播放策略拦截 */
export function unlockAudio(): void {
  if (unlocked || audioPlaying.value) {
    unlocked = true
    retryPending()
    return
  }
  // 同一个手势会先触发全局 pointerdown、再触发按钮 click，避免重复播放静音片段
  if (unlocking) return
  unlocking = true
  const el = element()
  const url = silentWavUrl()
  try {
    el.src = url
  } catch (err) {
    unlocking = false
    URL.revokeObjectURL(url)
    console.warn('[语音播报] 解锁音频失败', err)
    return
  }
  void el
    .play()
    .then(() => {
      unlocking = false
      unlocked = true
      audioBlocked.value = false
      URL.revokeObjectURL(url)
      retryPending()
    })
    .catch((err: unknown) => {
      unlocking = false
      URL.revokeObjectURL(url)
      console.warn('[语音播报] 解锁音频失败', err)
    })
}

/** 用户点击后重播最近一条被自动播放策略拦截的语音 */
export function retryPending(): void {
  if (!pending || muted.value) return
  const item = pending
  pending = null
  playSpeak(item)
}

export function stopSpeaking(): void {
  audioPlaying.value = false
  if (audioEl) {
    audioEl.pause()
    audioEl.removeAttribute('src')
    audioEl.load()
  }
  revokeUrl()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

export function playSpeak(item: SpeakItem | null): void {
  if (!item || muted.value) return
  // 先记下待播内容：即使这次被拦截，用户点击后也能自动补播
  pending = item
  stopSpeaking()

  if (item.audio_b64) {
    const url = base64ToBlobUrl(item.audio_b64, item.mime || 'audio/mpeg')
    currentUrl = url
    const el = element()
    el.src = url
    el.volume = 1
    const played = el.play()
    if (played) {
      played
        .then(() => {
          audioBlocked.value = false
          audioPlaying.value = true
        })
        .catch((err: unknown) => {
          // 切换语音时 stopSpeaking() 会打断上一条播放，AbortError 属预期行为，不当作拦截
          if (err instanceof DOMException && err.name === 'AbortError') return
          // 自动播放被拦截：保留 pending，顶栏会提示「点击启用语音」
          console.warn('[语音播报] 合成音频被浏览器拦截，尝试系统合成', err)
          audioBlocked.value = true
          speakLocally(item.text)
        })
    }
    return
  }
  speakLocally(item.text)
}

function speakLocally(text: string): void {
  if (!('speechSynthesis' in window)) {
    audioBlocked.value = true
    return
  }
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  // 略慢、不变调：即使退回系统发音，也尽量避免"新闻播报"式的生硬节奏
  utterance.rate = 0.95
  utterance.pitch = 1
  utterance.onstart = () => {
    audioBlocked.value = false
    audioPlaying.value = true
    pending = null
  }
  utterance.onend = () => {
    audioPlaying.value = false
  }
  utterance.onerror = (event) => {
    console.warn('[语音播报] 系统合成失败', event.error)
    audioBlocked.value = true
  }
  const voice = pickChineseVoice()
  if (voice) utterance.voice = voice
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
}

/**
 * 系统里往往同时装着老式 SAPI 音色（Huihui/Kangkang）和 Windows 自然语音，
 * 默认排序不一定选中后者，这里按名字优先挑自然语音，挑不到再退回任意中文音色。
 */
function pickChineseVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices()
  if (!voices.length) return null
  const chinese = voices.filter((v) => v.lang?.toLowerCase().startsWith('zh'))
  return (
    chinese.find((v) => /natural|xiaoxiao|yunxi|xiaoyi|yunyang/i.test(v.name)) ??
    chinese[0] ??
    null
  )
}
