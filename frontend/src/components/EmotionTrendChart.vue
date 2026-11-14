<script setup lang="ts">
/**
 * 情绪置信度实时曲线。
 * 数据每 2 秒才有一个窗口，但仍然做 100ms 渲染节流——列布局变化时避免重复重绘。
 * 点的颜色即情绪分类，一眼就能看出"哪里开始转负、置信度有多高"。
 */
import * as echarts from 'echarts'
import { onMounted, onUnmounted, ref, watch } from 'vue'

import { monitor } from '../state'
import { EMOTION_COLOR } from '../types/contract'

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let throttled = false

function render(): void {
  if (!chart) return
  const data = monitor.frames.map((frame) => ({
    value: [frame.ts * 1000, frame.emotion.confidence],
    itemStyle: { color: EMOTION_COLOR[frame.emotion.label] },
    window: frame.window,
    labelText: frame.emotion.label
  }))
  // ECharts 的 setOption 默认合并；替换整份 data 即可，不必重建图表
  chart.setOption({ series: [{ id: 'confidence', data }] })
}

function scheduleRender(): void {
  if (throttled) return
  throttled = true
  window.setTimeout(() => {
    throttled = false
    render()
  }, 100)
}

onMounted(() => {
  if (!container.value) return
  chart = echarts.init(container.value)
  chart.setOption({
    animation: false,
    grid: { left: 44, right: 18, top: 26, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(13,20,36,0.92)',
      borderColor: 'rgba(255,255,255,0.12)',
      textStyle: { color: '#e9eefb', fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{ data?: { value?: number[]; window?: string; labelText?: string } }>
        const item = list?.[0]?.data
        if (!item?.value) return ''
        const time = new Date(item.value[0]).toLocaleTimeString('zh-CN', { hour12: false })
        return [
          time,
          `${item.labelText} · 置信度 ${(item.value[1] * 100).toFixed(0)}%`,
          item.window ? `窗口 ${item.window}` : ''
        ]
          .filter(Boolean)
          .join('<br/>')
      }
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.14)' } },
      axisLabel: { color: '#8a97b0', hideOverlap: true },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      min: 0.4,
      max: 1,
      axisLabel: { color: '#8a97b0', formatter: (value: number) => `${(value * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }
    },
    series: [
      {
        id: 'confidence',
        name: '情绪置信度',
        type: 'line',
        showSymbol: true,
        symbolSize: 6,
        smooth: false,
        lineStyle: { width: 2, color: 'rgba(76,201,240,0.55)' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(76,201,240,0.22)' },
            { offset: 1, color: 'rgba(76,201,240,0.01)' }
          ])
        },
        markLine: {
          silent: true,
          symbol: 'none',
          label: {
            formatter: '门槛 0.55',
            position: 'insideEndTop',
            color: '#ffb703',
            fontSize: 11
          },
          lineStyle: { color: 'rgba(255,183,3,0.6)', type: 'dashed' },
          data: [{ yAxis: 0.55 }]
        }
      }
    ]
  })
  // 等一帧再按最终尺寸重算：列内滚动条出现/消失会挤占可用宽度，直接 resize 会残留偏差
  resizeObserver = new ResizeObserver(() => requestAnimationFrame(() => chart?.resize()))
  resizeObserver.observe(container.value)
  render()
})

watch(() => monitor.frameSeq, scheduleRender)

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">
      情绪置信度实时曲线
      <span class="badge mono">滑动窗口 300 个 · 点颜色 = 情绪分类</span>
    </h3>
    <div ref="container" class="chart"></div>
  </section>
</template>

<style scoped>
/* 在整页布局里由列高决定图表高度（ResizeObserver 会自动 resize） */
section {
  display: flex;
  flex-direction: column;
  min-height: 300px;
}

.chart {
  flex: 1;
  width: 100%;
  min-height: 240px;
}
</style>
