<script setup lang="ts">
/** 历史日志页：按会话与时间范围查询状态曲线与干预记录，并支持 CSV 导出。 */
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/http'
import type { Intervention, SessionSummary, StatePoint } from '../types/contract'

const sessions = ref<SessionSummary[]>([])
const states = ref<StatePoint[]>([])
const interventions = ref<Intervention[]>([])
const stateTotal = ref(0)
const interventionTotal = ref(0)

const sessionId = ref('')
const startInput = ref('')
const endInput = ref('')
const loading = ref(false)
const error = ref('')

const PAGE = 200
const IV_PAGE = 200
const stateOffset = ref(0)
const ivOffset = ref(0)

const query = computed(() => ({
  session_id: sessionId.value || undefined,
  start: startInput.value ? new Date(startInput.value).getTime() / 1000 : undefined,
  end: endInput.value ? new Date(endInput.value).getTime() / 1000 : undefined
}))

const exportStatesUrl = computed(() => api.exportUrl('states', query.value))
const exportInterventionsUrl = computed(() => api.exportUrl('interventions', query.value))

function time(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [sessionList, statePage, interventionPage] = await Promise.all([
      api.sessions(),
      api.states({ ...query.value, limit: PAGE, offset: stateOffset.value }),
      api.interventions({ ...query.value, limit: IV_PAGE, offset: ivOffset.value })
    ])
    sessions.value = sessionList.items
    states.value = statePage.items
    stateTotal.value = statePage.total
    interventions.value = interventionPage.items
    interventionTotal.value = interventionPage.total
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '查询失败'
  } finally {
    loading.value = false
  }
}

function resetAndLoad(): void {
  stateOffset.value = 0
  ivOffset.value = 0
  void load()
}

function selectSession(id: string): void {
  sessionId.value = sessionId.value === id ? '' : id
  resetAndLoad()
}

function page(delta: number): void {
  stateOffset.value = Math.max(0, stateOffset.value + delta * PAGE)
  void load()
}

function pageIv(delta: number): void {
  ivOffset.value = Math.max(0, ivOffset.value + delta * IV_PAGE)
  void load()
}

onMounted(load)
</script>

<template>
  <div class="history">
    <section class="panel filters">
      <h3 class="panel-title">查询条件</h3>
      <div class="row">
        <label>
          会话
          <select v-model="sessionId" @change="resetAndLoad()">
            <option value="">全部会话</option>
            <option v-for="item in sessions" :key="item.session_id" :value="item.session_id">
              {{ item.session_id }}（{{ item.state_points }} 点 / {{ item.interventions }} 次干预）
            </option>
          </select>
        </label>
        <label>
          起始时间
          <input v-model="startInput" type="datetime-local" />
        </label>
        <label>
          结束时间
          <input v-model="endInput" type="datetime-local" />
        </label>
        <button class="btn" :disabled="loading" @click="resetAndLoad()">
          {{ loading ? '查询中…' : '查询' }}
        </button>
        <a class="btn" :href="exportStatesUrl">导出状态 CSV</a>
        <a class="btn" :href="exportInterventionsUrl">导出干预 CSV</a>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <section class="panel">
      <h3 class="panel-title">
        会话列表
        <span class="badge mono">{{ sessions.length }}</span>
      </h3>
      <table>
        <thead>
          <tr>
            <th>会话 ID</th>
            <th>开始</th>
            <th>结束</th>
            <th>状态点数</th>
            <th>干预次数</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in sessions" :key="item.session_id">
            <td class="mono">{{ item.session_id }}</td>
            <td class="mono">{{ time(item.started_at) }}</td>
            <td class="mono">{{ time(item.ended_at) }}</td>
            <td class="mono">{{ item.state_points }}</td>
            <td class="mono">{{ item.interventions }}</td>
            <td>
              <button class="btn tiny" @click="selectSession(item.session_id)">筛选</button>
            </td>
          </tr>
          <tr v-if="!sessions.length">
            <td colspan="6" class="empty">暂无历史数据，请先在实时大屏运行一段演示剧本。</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3 class="panel-title">
        状态曲线采样点（1Hz 聚合）
        <span class="badge mono">共 {{ stateTotal }} 条</span>
      </h3>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>会话</th>
            <th>情绪</th>
            <th>置信度</th>
            <th>认知等级</th>
            <th>负荷分值</th>
            <th>数据源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in states" :key="`${item.session_id}-${item.ts}`">
            <td class="mono">{{ time(item.ts) }}</td>
            <td class="mono">{{ item.session_id }}</td>
            <td>{{ item.emotion_label }}</td>
            <td class="mono">{{ (item.emotion_confidence * 100).toFixed(1) }}%</td>
            <td>{{ item.cognitive_level }}</td>
            <td class="mono">{{ item.cognitive_score.toFixed(1) }}</td>
            <td class="muted">{{ item.source }}</td>
          </tr>
          <tr v-if="!states.length">
            <td colspan="7" class="empty">无数据</td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <button class="btn tiny" :disabled="stateOffset === 0" @click="page(-1)">上一页</button>
        <span class="mono muted">
          {{ stateOffset + 1 }} - {{ Math.min(stateOffset + PAGE, stateTotal) }} / {{ stateTotal }}
        </span>
        <button
          class="btn tiny"
          :disabled="stateOffset + PAGE >= stateTotal"
          @click="page(1)"
        >
          下一页
        </button>
      </div>
    </section>

    <section class="panel">
      <h3 class="panel-title">
        干预记录
        <span class="badge mono">共 {{ interventionTotal }} 条</span>
      </h3>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th>严重度</th>
            <th>话术</th>
            <th>策略</th>
            <th>模型</th>
            <th>时延</th>
            <th>兜底</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in interventions" :key="item.id ?? item.ts">
            <td class="mono">{{ time(item.ts) }}</td>
            <td>{{ item.kind }}</td>
            <td>{{ item.severity }}</td>
            <td class="speech">{{ item.speech }}</td>
            <td>{{ item.strategy }}</td>
            <td class="mono muted">{{ item.provider }}</td>
            <td class="mono">{{ item.latency_ms }}ms</td>
            <td :class="{ warn: item.degraded }">{{ item.degraded ? '是' : '否' }}</td>
          </tr>
          <tr v-if="!interventions.length">
            <td colspan="8" class="empty">无数据</td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <button class="btn tiny" :disabled="ivOffset === 0" @click="pageIv(-1)">上一页</button>
        <span class="mono muted">
          {{ ivOffset + 1 }} - {{ Math.min(ivOffset + IV_PAGE, interventionTotal) }} / {{ interventionTotal }}
        </span>
        <button
          class="btn tiny"
          :disabled="ivOffset + IV_PAGE >= interventionTotal"
          @click="pageIv(1)"
        >
          下一页
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.history {
  display: grid;
  gap: 16px;
}

.filters .row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}

.filters label {
  display: grid;
  gap: 5px;
  font-size: 12px;
  color: var(--muted);
}

.filters select,
.filters input {
  padding: 7px 10px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.25);
  color: var(--text);
  font-size: 13px;
  min-width: 210px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  text-align: left;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

th {
  color: var(--muted);
  font-weight: 500;
  font-size: 12px;
}

tbody tr:hover {
  background: rgba(255, 255, 255, 0.03);
}

.speech {
  max-width: 460px;
  line-height: 1.55;
}

.btn.tiny {
  padding: 4px 9px;
  font-size: 12px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 10px;
}

.error {
  margin: 10px 0 0;
  color: var(--negative);
  font-size: 12px;
}

.warn {
  color: var(--warn);
}
</style>
