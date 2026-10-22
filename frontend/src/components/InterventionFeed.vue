<script setup lang="ts">
/** 干预话术流：既展示"正在组织语言"的占位态，也展示最终话术与其决策依据。 */
import { monitor } from '../state'
import { KIND_TEXT } from '../types/contract'

function time(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function severityText(severity: string): string {
  if (severity === 'severe') return '高危 · 安全出口'
  if (severity === 'mild') return '轻度 · 主动陪伴'
  return '状态恢复'
}
</script>

<template>
  <section class="panel feed">
    <h3 class="panel-title">
      共情干预记录
      <span class="badge mono">{{ monitor.interventions.length }} 条</span>
    </h3>

    <div v-if="monitor.pending" class="card pending">
      <div class="row">
        <strong>正在组织语言…</strong>
        <span class="mono muted">{{ time(monitor.pending.ts) }}</span>
      </div>
      <p class="reason">{{ monitor.pending.reason }}</p>
      <div class="skeleton" />
    </div>

    <div v-if="!monitor.interventions.length && !monitor.pending" class="empty">
      暂未触发干预。可在左侧「模拟演示」中选择「焦虑升级」，观察完整闭环。
    </div>

    <ul class="list">
      <li
        v-for="item in monitor.interventions"
        :key="item.id ?? item.ts"
        class="card"
        :class="{ severe: item.severity === 'severe' }"
      >
        <div class="row">
          <strong>{{ KIND_TEXT[item.kind] ?? item.kind }}</strong>
          <span class="mono muted">{{ time(item.ts) }}</span>
        </div>

        <p class="speech">{{ item.speech }}</p>

        <div class="tags">
          <span class="badge">{{ item.strategy }}</span>
          <span class="badge" :class="{ 'is-off': item.risk !== 'low' }">
            风险 {{ item.risk }}
          </span>
          <span class="badge">{{ severityText(item.severity) }}</span>
          <span class="badge mono">{{ item.provider }} {{ item.latency_ms }}ms</span>
          <span v-if="item.degraded" class="badge is-mock" title="模型输出未通过校验，已使用规则兜底话术">
            规则兜底
          </span>
          <span v-if="item.audio_available" class="badge is-real">真实语音</span>
          <span v-else class="badge">浏览器播报</span>
        </div>

        <p class="reason">{{ item.reason }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.feed {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

/* 窄屏回退为自然流式布局时，限制列表高度，避免页面被无限拉长 */
@media (max-width: 1280px) {
  .list {
    max-height: 480px;
  }
}

.card {
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--panel-strong);
}

.card.severe {
  border-left-color: var(--negative);
}

.card.pending {
  border-left-color: var(--warn);
  margin-bottom: 10px;
}

.row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}

.speech {
  margin: 8px 0;
  line-height: 1.6;
  font-size: 14px;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.reason {
  margin: 8px 0 0;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.5;
}

.skeleton {
  height: 14px;
  margin-top: 8px;
  border-radius: 6px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0.06));
  background-size: 200% 100%;
  animation: shimmer 1.2s linear infinite;
}

@keyframes shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}
</style>
