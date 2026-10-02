<script setup lang="ts">
import { computed } from 'vue'

import { emotionCounts, monitor } from '../state'
import { EMOTION_TEXT } from '../types/contract'

const total = computed(() => Math.max(1, monitor.frames.length))

const ringStyle = computed(() => {
  const counts = emotionCounts.value
  const positive = (counts.Positive / total.value) * 360
  const neutral = (counts.Neutral / total.value) * 360
  const end = positive + neutral
  return {
    background: `conic-gradient(
      var(--positive) 0deg ${positive}deg,
      var(--neutral) ${positive}deg ${end}deg,
      var(--negative) ${end}deg 360deg)`
  }
})

const current = computed(() => monitor.latest)

const emotionColor = computed(() => {
  const label = current.value?.emotion.label
  if (label === 'Positive') return 'var(--positive)'
  if (label === 'Negative') return 'var(--negative)'
  return 'var(--neutral)'
})

const loadColor = computed(() => {
  const level = current.value?.cognitive.level
  if (level === 'High') return 'var(--negative)'
  if (level === 'Medium') return 'var(--warn)'
  return 'var(--positive)'
})
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      实时情绪构成
      <span class="badge mono">近 {{ monitor.frames.length }} 帧</span>
    </h3>

    <div class="gauge-wrap">
      <div class="ring" :style="ringStyle">
        <div class="hole">
          <div class="label" :style="{ color: emotionColor }">
            {{ current ? EMOTION_TEXT[current.emotion.label] : '—' }}
          </div>
          <div class="conf mono muted">
            {{ current ? `置信度 ${(current.emotion.confidence * 100).toFixed(0)}%` : '等待数据' }}
          </div>
        </div>
      </div>

      <ul class="legend">
        <li>
          <i style="background: var(--positive)" />积极
          <span class="mono">{{ emotionCounts.Positive }}</span>
        </li>
        <li>
          <i style="background: var(--neutral)" />中性
          <span class="mono">{{ emotionCounts.Neutral }}</span>
        </li>
        <li>
          <i style="background: var(--negative)" />负向
          <span class="mono">{{ emotionCounts.Negative }}</span>
        </li>
      </ul>
    </div>

    <div class="load">
      <span class="muted">认知负荷</span>
      <strong class="mono" :style="{ color: loadColor }">
        {{ current ? `${current.cognitive.score.toFixed(1)} · ${current.cognitive.level}` : '—' }}
      </strong>
    </div>
  </section>
</template>

<style scoped>
.gauge-wrap {
  display: flex;
  align-items: center;
  gap: 18px;
}

.ring {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex: none;
  position: relative;
  box-shadow: 0 0 26px rgba(76, 201, 240, 0.12) inset;
}

.hole {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  background: var(--bg-2);
  display: grid;
  place-items: center;
  align-content: center;
  gap: 4px;
}

.label {
  font-size: 20px;
  font-weight: 700;
}

.conf {
  font-size: 11px;
}

.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.legend li {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
}

.legend i {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.legend .mono {
  margin-left: auto;
  color: var(--text);
}

.load {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  font-size: 13px;
}
</style>
