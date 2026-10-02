<script setup lang="ts">
import { computed } from 'vue'

import CognitiveLoadChart from '../components/CognitiveLoadChart.vue'
import EmotionGauge from '../components/EmotionGauge.vue'
import InterventionFeed from '../components/InterventionFeed.vue'
import ScenarioPanel from '../components/ScenarioPanel.vue'
import StateTimeline from '../components/StateTimeline.vue'
import VoicePanel from '../components/VoicePanel.vue'
import { frameRate, monitor } from '../state'
import { KIND_TEXT } from '../types/contract'

const degradedCount = computed(
  () => monitor.interventions.filter((item) => item.degraded).length
)

const avgLatency = computed(() => {
  const items = monitor.interventions
  if (!items.length) return 0
  return Math.round(items.reduce((sum, item) => sum + item.latency_ms, 0) / items.length)
})

const severeCount = computed(
  () => monitor.interventions.filter((item) => item.severity === 'severe').length
)

const lastKind = computed(() =>
  monitor.latest ? KIND_TEXT[monitor.latest.emotion.label] ?? '—' : '—'
)

const kpis = computed(() => [
  { label: '推送帧率', value: `${frameRate.value} Hz`, hint: '客户端实测' },
  { label: '服务端帧率', value: `${monitor.serverFrameRate} Hz`, hint: '后端统计' },
  { label: '在线观察端', value: String(monitor.activeClients), hint: 'WebSocket 连接数' },
  { label: '干预次数', value: String(monitor.interventions.length), hint: '本次会话' },
  { label: '高危转介', value: String(severeCount.value), hint: '安全出口触发' },
  { label: '规则兜底', value: String(degradedCount.value), hint: '抗幻觉降级' },
  { label: '平均生成时延', value: `${avgLatency.value} ms`, hint: '含 TTS' }
])
</script>

<template>
  <div class="dashboard">
    <div class="kpis">
      <div v-for="item in kpis" :key="item.label" class="kpi panel">
        <span class="muted">{{ item.label }}</span>
        <strong class="mono">{{ item.value }}</strong>
        <small class="muted">{{ item.hint }}</small>
      </div>
    </div>

    <div class="columns">
      <div class="col">
        <EmotionGauge />
        <ScenarioPanel />
        <VoicePanel />
      </div>

      <div class="col wide">
        <CognitiveLoadChart />
        <StateTimeline />
      </div>

      <div class="col">
        <InterventionFeed />
      </div>
    </div>

    <p class="footnote muted">
      当前情绪 {{ lastKind }} · 数据源 {{ monitor.latest?.source ?? '—' }} ·
      会话 {{ monitor.sessionId || '—' }} ·
      后端如处于 Mock 模式，可在 backend/.env 中配置 LLM_PROVIDER 与 LLM_API_KEY（如 deepseek）接入真实大模型
    </p>
  </div>
</template>

<style scoped>
.dashboard {
  display: grid;
  gap: 16px;
}

.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}

.kpi {
  display: grid;
  gap: 4px;
  padding: 10px 14px;
}

.kpi span {
  font-size: 12px;
}

.kpi strong {
  font-size: 20px;
  color: var(--accent);
}

.kpi small {
  font-size: 11px;
}

.columns {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(420px, 1.8fr) minmax(300px, 1.2fr);
  gap: 16px;
  align-items: start;
}

.col {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.footnote {
  margin: 0;
  font-size: 12px;
}

@media (max-width: 1280px) {
  .columns {
    grid-template-columns: 1fr;
  }
}
</style>
