<script setup lang="ts">
/**
 * 认知负荷实时曲线。
 * 渲染节流 100ms：数据 10Hz 到达，但没必要 10Hz 重绘图表——这是"不卡顿"的前端一半。
 */
import * as echarts from 'echarts'
import { onMounted, onUnmounted, ref, watch } from 'vue'

import { monitor } from '../state'

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let throttled = false

function render(): void {
  if (!chart) return
  const frames = monitor.frames
  const series = frames.map((f) => [f.ts * 1000, f.cognitive.score])
  // 负向情绪单独用红点标出，让"情绪异常"与"负荷曲线"在同一时间轴上对齐观察
  const negativePoints = frames
    .filter((f) => f.emotion.label === 'Negative')
    .map((f) => [f.ts * 1000, f.cognitive.score])

  chart.setOption({
    series: [
      { id: 'load', data: series },
      { id: 'negative', data: negativePoints }
    ]
  })
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
    grid: { left: 42, right: 18, top: 26, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(13,20,36,0.92)',
      borderColor: 'rgba(255,255,255,0.12)',
      textStyle: { color: '#e9eefb', fontSize: 12 },
      valueFormatter: (value: number) => `${Number(value).toFixed(1)}`
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.14)' } },
      axisLabel: { color: '#8a97b0', hideOverlap: true },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: { color: '#8a97b0' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } }
    },
    series: [
      {
        id: 'load',
        name: '认知负荷',
        type: 'line',
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: '#4cc9f0' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(76,201,240,0.35)' },
            { offset: 1, color: 'rgba(76,201,240,0.02)' }
          ])
        },
        markLine: {
          silent: true,
          symbol: 'none',
          label: { formatter: '高负荷线 70', color: '#ffb703', fontSize: 11 },
          lineStyle: { color: 'rgba(255,183,3,0.6)', type: 'dashed' },
          data: [{ yAxis: 70 }]
        }
      },
      {
        id: 'negative',
        name: '负向情绪采样',
        type: 'scatter',
        symbolSize: 6,
        itemStyle: { color: '#ff6b6b' }
      }
    ]
  })
  resizeObserver = new ResizeObserver(() => chart?.resize())
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
      认知负荷实时曲线
      <span class="badge mono">滑动窗口 300 点 · 渲染节流 100ms</span>
    </h3>
    <div ref="container" class="chart"></div>
  </section>
</template>

<style scoped>
.chart {
  width: 100%;
  height: 300px;
}
</style>
