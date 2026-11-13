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
  // 略慢、不变调：即使退回系统发音，也尽量避免"新闻播报"式的生硬节奏
  utterance.rate = 0.95
  utterance.pitch = 1
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
