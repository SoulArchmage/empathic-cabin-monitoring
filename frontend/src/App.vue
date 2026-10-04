<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { playSpeak, stopSpeaking } from './audio'
import { muted, monitor, speakSignal } from './state'
import { useMonitorSocket } from './composables/useMonitorSocket'

const route = useRoute()
useMonitorSocket()

// 播报统一在根组件消费，保证全局只有一条语音输出，避免多组件重复发声
watch(speakSignal, (item) => playSpeak(item))
watch(muted, (value) => {
  if (value) stopSpeaking()
})

const providerList = computed(() =>
  Object.entries(monitor.providers).map(([key, value]) => ({ key, ...value }))
)

const clock = ref(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
const timer = window.setInterval(() => {
  clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}, 1000)
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="logo">◉</span>
        <div>
          <div class="title">智能疏导仓 · 共情语音交互与状态监测系统</div>
          <div class="subtitle muted mono">
            会话 {{ monitor.sessionId || '—' }} · 模拟 {{ monitor.scenario }}
          </div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink to="/" :class="{ active: route.name === 'dashboard' }">实时大屏</RouterLink>
        <RouterLink to="/history" :class="{ active: route.name === 'history' }">历史日志</RouterLink>
      </nav>

      <div class="status">
        <span class="badge" :class="monitor.connected ? 'is-real' : 'is-off'">
          <i class="dot" />{{ monitor.connected ? '通道已连接' : '连接中断' }}
        </span>
        <span class="badge mono">{{ monitor.serverFrameRate }} Hz</span>
        <span class="badge mono">在线 {{ monitor.activeClients }}</span>
        <span
          v-for="p in providerList"
          :key="p.key"
          class="badge"
          :class="p.ready ? (p.mode === 'real' ? 'is-real' : 'is-mock') : 'is-off'"
          :title="p.ready ? `${p.name}（${p.mode}）` : `${p.name} 未就绪`"
        >
          {{ p.key.toUpperCase() }}·{{ p.name }}
        </span>
        <button class="btn" :class="{ 'is-active': muted }" @click="muted = !muted">
          {{ muted ? '语音已静音' : '语音播报中' }}
        </button>
        <span class="badge mono">{{ clock }}</span>
      </div>
    </header>

    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: rgba(8, 13, 25, 0.72);
  backdrop-filter: blur(10px);
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  font-size: 22px;
  color: var(--accent);
  text-shadow: 0 0 14px rgba(76, 201, 240, 0.7);
}

.title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.subtitle {
  font-size: 12px;
  margin-top: 2px;
}

.nav {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.nav a {
  padding: 6px 14px;
  border-radius: 9px;
  border: 1px solid transparent;
  color: var(--muted);
  font-size: 13px;
}

.nav a.active {
  color: var(--accent);
  border-color: rgba(76, 201, 240, 0.4);
  background: rgba(76, 201, 240, 0.1);
}

.status {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.content {
  flex: 1;
  padding: 16px 20px 24px;
}
</style>
