<script setup lang="ts">
/**
 * 语音交互面板：按住说话 + 文本兜底输入。
 * 录音走 MediaRecorder → /ws/voice；文本输入既用于无麦克风环境，也用于快速演示。
 */
import { ref } from 'vue'

import { monitor } from '../state'
import { playSpeak } from '../audio'
import { useVoiceSocket } from '../composables/useVoiceSocket'

const { recording, voiceConnected, voiceError, startRecording, stopRecording, sendText } =
  useVoiceSocket()

const draft = ref('')

function submit(): void {
  const text = draft.value.trim()
  if (!text) return
  sendText(text)
  draft.value = ''
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      语音对话
      <span class="badge" :class="voiceConnected ? 'is-real' : 'is-off'">
        <i class="dot" />{{ voiceConnected ? '语音通道就绪' : '未连接' }}
      </span>
    </h3>

    <button
      class="btn record"
      :class="{ 'is-record': recording }"
      @mousedown="startRecording"
      @mouseup="stopRecording"
      @mouseleave="recording && stopRecording()"
    >
      {{ recording ? '松开结束录音' : '按住说话' }}
    </button>

    <div class="input">
      <input
        v-model="draft"
        placeholder="或直接输入文字，与助手对话"
        @keyup.enter="submit"
      />
      <button class="btn" @click="submit">发送</button>
    </div>

    <div v-if="monitor.asrText" class="line">
      <span class="muted">识别到</span>
      <strong>{{ monitor.asrText }}</strong>
    </div>

    <p v-if="voiceError" class="error">{{ voiceError }}</p>

    <button
      v-if="monitor.interventions.length"
      class="btn replay"
      @click="playSpeak({ text: monitor.interventions[0].speech, audio_b64: null, mime: '', voice: '', tts_provider: 'browser', intervention_id: monitor.interventions[0].id })"
    >
      重播最近回应
    </button>

    <p class="muted tip">
      当前 ASR/TTS 若为 Mock 模式，将由浏览器原生语音能力承担播报；
      配置云服务后同一套界面自动切换为真实语音。
    </p>
  </section>
</template>

<style scoped>
.record {
  width: 100%;
  padding: 12px;
  font-size: 14px;
  font-weight: 600;
  user-select: none;
}

.input {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.input input {
  flex: 1;
  padding: 8px 11px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.25);
  color: var(--text);
  font-size: 13px;
  outline: none;
}

.input input:focus {
  border-color: rgba(76, 201, 240, 0.5);
}

.line {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-top: 10px;
  font-size: 13px;
}

.error {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--negative);
}

.replay {
  width: 100%;
  margin-top: 10px;
}

.tip {
  margin: 10px 0 0;
  font-size: 11px;
  line-height: 1.6;
}
</style>
