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
        <EmotionGauge class="grow-tall" />
        <ScenarioPanel />
        <VoicePanel />
      </div>

      <div class="col wide">
        <CognitiveLoadChart class="grow" />
        <StateTimeline />
      </div>

      <div class="col">
        <InterventionFeed class="grow" />
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
/* 大屏整页保持一屏：三列撑满可视高度，仅列内可滚动区域（如干预记录列表）自行滚动 */
.dashboard {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 12px;
  height: 100%;
  min-height: 0;
}

.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
}

.kpi {
  display: grid;
  gap: 2px;
  padding: 8px 14px;
}

.kpi span {
  font-size: 12px;
}

.kpi strong {
  font-size: 18px;
  color: var(--accent);
}

.kpi small {
  font-size: 11px;
}

.columns {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(420px, 1.8fr) minmax(300px, 1.2fr);
  gap: 12px;
  min-height: 0;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}

/* 交给列内"主角"面板（曲线 / 记录流 / 情绪环）撑满剩余高度 */
.grow {
  flex: 1;
  min-height: 0;
}

/* 内容定高的面板只吸收多余空间：高度不足时改为列内滚动，而不是压缩裁切内容 */
.grow-tall {
  flex: 1 0 auto;
}

.footnote {
  margin: 0;
  font-size: 12px;
}

/* 窄屏回退为自然流式布局（允许页面滚动），不强行塞进一屏 */
@media (max-width: 1280px) {
  .dashboard {
    height: auto;
    grid-template-rows: none;
  }

  .columns {
    grid-template-columns: 1fr;
  }

  .col {
    overflow-y: visible;
  }

  .grow,
  .grow-tall {
    flex: none;
  }
}
</style>