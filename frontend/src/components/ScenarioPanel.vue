<script setup lang="ts">
/** 模拟演示注入：答辩时一键复现"平静 → 异常 → 干预 → 恢复"完整闭环。 */
import { ref } from 'vue'

import { api } from '../api/http'
import { monitor } from '../state'
import { SCENARIO_LABELS, type ScenarioName } from '../types/contract'

const scenarios: { name: ScenarioName; desc: string }[] = [
  { name: 'calm', desc: '情绪积极、负荷低，保持静默监测' },
  { name: 'anxiety_escalation', desc: '负荷攀升，约 1s 陪伴、8s 升级转介' },
  { name: 'high_load', desc: '仅负荷升高，验证单一异常源' },
  { name: 'recovery', desc: '从异常回落，触发恢复确认' }
]

const busy = ref<ScenarioName | null>(null)

async function apply(name: ScenarioName): Promise<void> {
  busy.value = name
  try {
    await api.setScenario(name)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      模拟演示
      <span class="badge" :class="{ 'is-real': !!monitor.scenario }">{{ monitor.scenario }}</span>
    </h3>
    <div class="list">
      <button
        v-for="item in scenarios"
        :key="item.name"
        class="btn scenario"
        :class="{ 'is-active': monitor.scenario === item.name }"
        :disabled="busy !== null"
        @click="apply(item.name)"
      >
        <strong>{{ SCENARIO_LABELS[item.name] }}</strong>
        <span class="muted">{{ item.desc }}</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
/* 两列网格压缩高度，给整页一屏布局让出空间 */
.list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.scenario {
  display: grid;
  gap: 4px;
  align-content: start;
  text-align: left;
  padding: 9px 10px;
  line-height: 1.45;
}

.scenario strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.scenario span {
  font-size: 11px;
}

.scenario.is-active strong {
  color: var(--accent);
}
</style>