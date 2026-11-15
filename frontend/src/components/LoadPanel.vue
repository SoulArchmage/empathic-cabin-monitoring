<script setup lang="ts">
/**
 * 脑电负荷面板（第二路数据源）：显示 EEG 服务最新给出的 4 级负荷与置信度，
 * 以及最近滑动窗口内的等级分布 —— 对应申报书界面里的
 * "当前认知负荷 / 置信度 / Low-Medium-High-VeryHigh 占比"。
 */
import { computed } from 'vue'

import { highLoadShare, loadCounts, monitor } from '../state'
import { LOAD_COLOR, LOAD_ORDER, LOAD_TEXT } from '../types/contract'

const current = computed(() => monitor.load)
const total = computed(() =>
  LOAD_ORDER.reduce((sum, level) => sum + loadCounts.value[level], 0)
)

const levelColor = computed(() =>
  current.value ? LOAD_COLOR[current.value.level] : 'var(--muted)'
)

function share(level: (typeof LOAD_ORDER)[number]): string {
  if (!total.value) return '0%'
  return `${((loadCounts.value[level] / total.value) * 100).toFixed(0)}%`
}

const highShareText = computed(() =>
  highLoadShare.value === null ? '—' : `${(highLoadShare.value * 100).toFixed(0)}%`
)

/** 高/极高是后端的触发区间，界面要给一句明确的后果说明 */
const triggerHint = computed(() => {
  const level = current.value?.level
  if (level === 'VeryHigh') return '极高负荷：已进入降速干预区间'
  if (level === 'High') return '高负荷：持续出现将触发降速引导'
  return ''
})
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      脑电认知负荷
      <span class="badge mono">4s 窗 · 1s 步</span>
    </h3>

    <div class="head">
      <div class="level" :style="{ color: levelColor }">
        {{ current ? LOAD_TEXT[current.level] : '—' }}
      </div>
      <div class="conf">
        <div class="bar">
          <i
            :style="{
              width: `${(current ? current.confidence : 0) * 100}%`,
              background: levelColor
            }"
          />
        </div>
        <span class="mono muted">
          {{ current ? `置信度 ${(current.confidence * 100).toFixed(0)}%` : '等待 EEG 服务' }}
        </span>
      </div>
    </div>

    <ul class="rows">
      <li v-for="level in LOAD_ORDER" :key="level">
        <span class="name" :style="{ color: LOAD_COLOR[level] }">{{ LOAD_TEXT[level] }}</span>
        <div class="track">
          <i :style="{ width: share(level), background: LOAD_COLOR[level] }" />
        </div>
        <span class="mono muted">{{ share(level) }}</span>
      </li>
    </ul>

    <div class="foot">
      <div>
        <span class="muted">EEG 窗口</span>
        <strong class="mono">{{ current?.window || '—' }}</strong>
      </div>
      <div>
        <span class="muted">高及以上</span>
        <strong class="mono" :style="{ color: levelColor }">{{ highShareText }}</strong>
      </div>
    </div>

    <p v-if="triggerHint" class="hint" :style="{ color: levelColor }">{{ triggerHint }}</p>
  </section>
</template>

<style scoped>
section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.head {
  display: flex;
  align-items: center;
  gap: 14px;
}

.level {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
  min-width: 52px;
}

.conf {
  flex: 1;
  display: grid;
  gap: 5px;
  font-size: 11px;
}

.bar {
  height: 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}

.bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease, background 0.3s ease;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 5px;
  font-size: 12px;
}

.rows li {
  display: grid;
  grid-template-columns: 30px 1fr 38px;
  align-items: center;
  gap: 8px;
}

.name {
  font-weight: 600;
}

.track {
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  overflow: hidden;
}

.track i {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.rows .mono {
  text-align: right;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 12px;
}

.foot div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hint {
  margin: 0;
  font-size: 11px;
}
</style>
