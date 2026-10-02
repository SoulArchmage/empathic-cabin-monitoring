<script setup lang="ts">
/**
 * 状态色带：把最近 120 帧的状态压成一条时间轴，
 * 用于"一眼看出"异常持续了多久、干预发生在哪个位置。
 */
import { computed } from 'vue'

import { monitor } from '../state'

const SLOTS = 120

const slots = computed(() => {
  const frames = monitor.frames.slice(-SLOTS)
  const pad = SLOTS - frames.length
  const leading = Array.from({ length: Math.max(0, pad) }, () => null)
  return [...leading, ...frames].map((frame, index) => {
    // key 必须稳定：用下标派生，避免每次重绘都因随机 key 而重建全部 DOM
    if (!frame) return { key: `pad-${index}`, color: 'transparent', title: '' }
    const negative = frame.emotion.label === 'Negative'
    const high = frame.cognitive.level === 'High'
    const color = negative && high
      ? 'var(--negative)'
      : high
        ? 'var(--warn)'
        : negative
          ? '#ff9f9f'
          : 'var(--positive)'
    return {
      key: `${frame.ts}-${index}`,
      color,
      title: `${new Date(frame.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })}
情绪 ${frame.emotion.label}（${(frame.emotion.confidence * 100).toFixed(0)}%）
负荷 ${frame.cognitive.level}（${frame.cognitive.score.toFixed(1)}）`
    }
  })
})

const markers = computed(() => {
  const frames = monitor.frames
  if (!frames.length) return []
  const first = frames[Math.max(0, frames.length - SLOTS)].ts
  const last = frames[frames.length - 1].ts
  const span = Math.max(0.001, last - first)
  return monitor.interventions
    .filter((item) => item.ts >= first && item.ts <= last)
    .map((item) => ({
      id: item.id ?? item.ts,
      left: `${((item.ts - first) / span) * 100}%`,
      title: `${item.severity === 'severe' ? '高危转介' : '共情干预'}：${item.speech}`
    }))
})
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      状态变化时间轴
      <span class="badge">▮ 干预发生位置</span>
    </h3>
    <div class="ribbon">
      <span
        v-for="slot in slots"
        :key="slot.key"
        class="slot"
        :style="{ background: slot.color }"
        :title="slot.title"
      />
    </div>
    <div class="track">
      <span
        v-for="marker in markers"
        :key="marker.id"
        class="marker"
        :style="{ left: marker.left }"
        :title="marker.title"
      />
    </div>
    <p class="hint muted">
      <span>← 较早</span>
      <span>绿色=平稳 · 黄色=高负荷 · 浅红=负向情绪 · 深红=双重异常</span>
      <span>最新 →</span>
    </p>
  </section>
</template>

<style scoped>
.ribbon {
  display: flex;
  gap: 1px;
  height: 26px;
  border-radius: 6px;
  overflow: hidden;
}

.slot {
  flex: 1;
  min-width: 1px;
  transition: background 0.2s ease;
}

.track {
  position: relative;
  height: 14px;
  margin-top: 2px;
}

.marker {
  position: absolute;
  top: 0;
  width: 2px;
  height: 14px;
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
  transform: translateX(-1px);
}

.marker::after {
  content: '▾';
  position: absolute;
  top: -8px;
  left: -4px;
  font-size: 8px;
  color: var(--accent);
}

.hint {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin: 8px 0 0;
  font-size: 11px;
}
</style>
