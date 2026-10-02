/**
 * 语音播报：兼容两种 TTS 能力形态。
 *  - 后端返回 audio_b64（真实 TTS）→ <audio> 播放合成音频；
 *  - 仅返回文本（Mock 模式）→ 浏览器原生 speechSynthesis 播报。
 * 这样"数字语音助手"在无任何云服务凭证时也能出声。
 */
import type { SpeakItem } from './types/contract'
import { muted } from './state'

let currentAudio: HTMLAudioElement | null = null

export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause()
    currentAudio = null
  }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

export function playSpeak(item: SpeakItem | null): void {
  if (!item || muted.value) return
  stopSpeaking()

  if (item.audio_b64) {
    const audio = new Audio(`data:${item.mime || 'audio/mpeg'};base64,${item.audio_b64}`)
    currentAudio = audio
    void audio.play().catch(() => {
      // 浏览器自动播放策略可能拦截，退回到本地合成
      speakLocally(item.text)
    })
    return
  }
  speakLocally(item.text)
}

function speakLocally(text: string): void {
  if (!('speechSynthesis' in window)) return
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 1
  utterance.pitch = 1
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
}
