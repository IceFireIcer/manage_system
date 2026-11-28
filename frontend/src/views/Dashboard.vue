<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Box,
  Document,
  Plus,
  Refresh,
  Setting,
  TrendCharts,
} from '@element-plus/icons-vue'
import api from '../api'
import { useUserStore } from '../stores/user'
import { useAlertsStore } from '../stores/alerts'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const userStore = useUserStore()
const alertsStore = useAlertsStore()

const activeRole = computed(() => userStore.activeRole ?? userStore.user?.role)
const isStudent = computed(() => activeRole.value === 'student')
const isStaff = computed(() => ['super_admin', 'teacher', 'admin'].includes(activeRole.value))

const loading = ref(true)
const refreshing = ref(false)
const errorKeys = ref([])
const overview = ref({ summary: null, items: [] })
const records = ref({ items: [], total: 0 })
const suggestions = ref({ items: [] })
const consumption = ref({ items: [], unit_totals: [] })
const classSignals = ref(null)
const logs = ref({ items: [] })
const consumables = ref([])
const courseRisks = ref([])

const items = computed(() => overview.value.items ?? [])
const summary = computed(() => overview.value.summary ?? null)
const outCount = computed(
  () => items.value.filter((item) => item.is_out || item.current_stock === 0).length,
)
const lowOnlyCount = computed(
  () =>
    items.value.filter(
      (item) => item.low_stock && !(item.is_out || item.current_stock === 0),
    ).length,
)
const availableConsumables = computed(() =>
  consumables.value.filter(
    (item) => item.status === 'enabled' && Number(item.current_stock) > 0,
  ),
)
const alertItems = computed(() => (alertsStore.activeItems ?? []).slice(0, 3))

const TIER_RANK = { urgent: 0, moderate: 1, observe: 2 }
const TIER_META = {
  urgent: { label: '紧急', className: 'status-tag--danger' },
  moderate: { label: '一般', className: 'status-tag--warning' },
  observe: { label: '观察', className: 'status-tag--info' },
}

const suggestionItems = computed(() =>
  [...(suggestions.value.items ?? [])]
    .sort(
      (left, right) =>
        (TIER_RANK[left.tier] ?? 9) - (TIER_RANK[right.tier] ?? 9),
    )
    .slice(0, 3),
)

const RISK_RANK = { insufficient: 0, critical: 1, unconfigured: 2, sufficient: 3 }
const RISK_META = {
  sufficient: { label: '充足', className: 'status-tag--success' },
  critical: { label: '临界', className: 'status-tag--warning' },
  insufficient: { label: '不足', className: 'status-tag--danger' },
  unconfigured: { label: '未配置', className: 'status-tag--neutral' },
}

const upcomingRisks = computed(() =>
  [...courseRisks.value]
    .sort(
      (left, right) =>
        (RISK_RANK[left.level] ?? 9) - (RISK_RANK[right.level] ?? 9) ||
        left.session_date.localeCompare(right.session_date),
    )
    .slice(0, 3),
)

const unitTotals = computed(() =>
  (consumption.value.unit_totals ?? []).filter(
    (row) => Number(row.net_quantity) > 0,
  ),
)
const maxUnit = computed(() =>
  Math.max(1, ...unitTotals.value.map((row) => Number(row.net_quantity) || 0)),
)

const classSignalUnits = computed(() =>
  (classSignals.value?.units ?? []).slice(0, 3),
)
const maxSignalUnit = computed(() =>
  Math.max(
    1,
    ...classSignalUnits.value.map((row) => Number(row.net_quantity) || 0),
  ),
)

const stats = computed(() => {
  if (isStudent.value) {
    return [
      { label: '我的领用笔数', value: records.value.total ?? 0, path: '/records' },
      { label: '可领用耗材', value: availableConsumables.value.length },
      { label: '班级缺货', value: outCount.value, tone: 'danger' },
      { label: '班级低库存', value: lowOnlyCount.value, tone: 'warning' },
    ]
  }
  return [
    {
      label: '耗材总类数',
      value: summary.value?.total_kinds ?? items.value.length,
      path: '/overview',
    },
    { label: '缺货', value: outCount.value, tone: 'danger' },
    { label: '低库存', value: lowOnlyCount.value, tone: 'warning' },
    {
      label: '今日领用',
      value: summary.value?.today_requisitions ?? 0,
      path: '/records',
    },
  ]
})

const quickActions = computed(() => {
  if (isStudent.value) {
    return [
      { label: '登记领用', icon: Plus, path: '/requisition' },
      { label: '我的记录', icon: Document, path: '/records' },
      { label: '库存总览', icon: Box, path: '/overview' },
    ]
  }
  const actions = [
    { label: '登记领用', icon: Plus, path: '/requisition' },
    { label: '补货建议', icon: TrendCharts, path: '/restock' },
    { label: '记录查询', icon: Document, path: '/records' },
    { label: '消耗汇总', icon: TrendCharts, path: '/consumption' },
  ]
  if (userStore.hasPermission('consumable.manage')) {
    actions.push({ label: '耗材档案', icon: Box, path: '/consumables' })
  }
  if (userStore.hasPermission('account.manage')) {
    actions.push({ label: '账号管理', icon: Setting, path: '/settings/accounts' })
  }
  return actions
})

const loadFailed = computed(() => errorKeys.value.length > 0)

function alertMeta(item) {
  const level =
    item.risk_level || (Number(item.current_stock) === 0 ? 'out' : 'low')
  return level === 'out'
    ? { label: '缺货', className: 'status-tag--danger' }
    : { label: '低库存', className: 'status-tag--warning' }
}

function suggestionMeta(item) {
  return TIER_META[item.tier] ?? { label: item.tier ?? '建议', className: 'status-tag--info' }
}

function riskMeta(risk) {
  return (
    RISK_META[risk.level] ?? {
      label: risk.level,
      className: 'status-tag--info',
    }
  )
}

function riskMetaDetail(risk) {
  const parts = []
  if (risk.gap > 0) parts.push(`缺 ${risk.gap}`)
  if (risk.arrival_deadline) {
    parts.push(`${risk.arrival_deadline.slice(5)} 前到货`)
  } else {
    parts.push(risk.session_date.slice(5))
  }
  return parts.join(' · ')
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatChange(value) {
  if (value === null || value === undefined) return '—'
  const percent = Math.round(value * 100)
  return `${percent > 0 ? '+' : ''}${percent}%`
}

function signalTone(value) {
  if (!value) return 'status-tag--neutral'
  return value > 0 ? 'status-tag--danger' : 'status-tag--success'
}

function go(path) {
  router.push(path)
}

function assign(key, data) {
  if (key === 'overview') {
    overview.value = { summary: data.summary ?? null, items: data.items ?? [] }
  } else if (key === 'records') {
    records.value = { items: data.items ?? [], total: data.total ?? 0 }
  } else if (key === 'suggestions') {
    suggestions.value = { items: data.items ?? [] }
  } else if (key === 'consumption') {
    consumption.value = {
      items: data.items ?? [],
      unit_totals: data.unit_totals ?? [],
    }
  } else if (key === 'classSignals') {
    classSignals.value = data ?? null
  } else if (key === 'logs') {
    logs.value = { items: data.items ?? [] }
  } else if (key === 'risks') {
    courseRisks.value = data.items ?? []
  } else if (key === 'consumables') {
    consumables.value = Array.isArray(data) ? data : []
  }
}

async function loadData({ silent = false } = {}) {
  if (!silent) loading.value = true
  refreshing.value = true
  errorKeys.value = []

  const requests = [
    { key: 'overview', request: api.get('/inventory/overview') },
    {
      key: 'records',
      request: api.get('/requisitions', { params: { page: 1, page_size: 5 } }),
    },
  ]

  if (isStaff.value) {
    requests.push(
      { key: 'suggestions', request: api.get('/inventory/suggestions') },
      { key: 'risks', request: api.get('/courses/risk', { params: { days: 14 } }) },
      {
        key: 'consumption',
        request: api.get('/consumption/summary', { params: { window: 7 } }),
      },
      {
        key: 'classSignals',
        request: api.get('/requisitions/class-signals', {
          params: { window: 7 },
        }),
      },
      {
        key: 'logs',
        request: api.get('/logs', { params: { page: 1, page_size: 5 } }),
      },
    )
  } else {
    requests.push({
      key: 'consumables',
      request: api.get('/consumables', { params: { status: 'enabled' } }),
    })
  }

  const [results] = await Promise.all([
    Promise.allSettled(requests.map((item) => item.request)),
    isStaff.value ? alertsStore.fetch({ silent: true }) : Promise.resolve(),
  ])

  const failed = []
  results.forEach((result, index) => {
    const key = requests[index].key
    if (result.status === 'rejected') {
      failed.push(key)
      return
    }
    assign(key, result.value.data)
  })

  errorKeys.value = failed
  loading.value = false
  refreshing.value = false
}

onMounted(() => loadData())
</script>

<template>
  <div class="dashboard" v-loading="loading">
    <PageHeader title="工作台">
      <template #actions>
        <el-button :loading="refreshing" @click="loadData({ silent: true })">
          <el-icon :size="16"><Refresh /></el-icon>
          <span class="dashboard__button-text">刷新</span>
        </el-button>
        <el-button type="primary" @click="go('/requisition')">登记领用</el-button>
      </template>
    </PageHeader>

    <div v-if="loadFailed" class="dashboard__notice">
      <span>部分数据加载失败</span>
      <el-button link type="primary" @click="loadData({ silent: true })">
        重试
      </el-button>
    </div>

    <div class="panel dashboard__stats" aria-label="工作台指标">
      <component
        :is="stat.path ? 'button' : 'div'"
        v-for="stat in stats"
        :key="stat.label"
        :type="stat.path ? 'button' : undefined"
        class="dashboard__stat"
        :class="{ 'dashboard__stat--action': stat.path }"
        @click="stat.path ? go(stat.path) : undefined"
      >
        <span class="dashboard__stat-label">{{ stat.label }}</span>
        <span
          class="dashboard__stat-value"
          :class="{
            'dashboard__stat-value--warning': stat.tone === 'warning',
            'dashboard__stat-value--danger': stat.tone === 'danger',
          }"
        >
          {{ loading ? '—' : stat.value }}
        </span>
      </component>
    </div>

    <div class="dashboard__grid">
      <div class="dashboard__main">
        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">下一课次风险</h2>
            <el-button link type="primary" @click="go('/courses')">全部</el-button>
          </header>
          <ul v-if="upcomingRisks.length" class="dashboard__list">
            <li v-for="risk in upcomingRisks" :key="risk.session_id">
              <button
                type="button"
                class="dashboard__risk-row"
                @click="go('/courses')"
              >
                <span class="dashboard__row-main">
                  <span class="status-tag" :class="riskMeta(risk).className">
                    {{ riskMeta(risk).label }}
                  </span>
                  <span class="dashboard__row-title">
                    {{ risk.course_name }} · 第 {{ risk.seq }} 次
                  </span>
                </span>
                <span class="dashboard__row-meta">{{ riskMetaDetail(risk) }}</span>
              </button>
            </li>
          </ul>
          <EmptyState v-else title="暂未匹配到课程风险" />
        </section>

        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <div class="dashboard__section-heading">
              <h2 class="dashboard__section-title">需要处理</h2>
              <span class="status-tag status-tag--warning">
                待办 {{ alertsStore.activeCount }}
              </span>
            </div>
            <el-button link type="primary" @click="go('/overview?focus=alerts')">
              全部
            </el-button>
          </header>
          <ul v-if="alertItems.length" class="dashboard__list">
            <li v-for="item in alertItems" :key="item.consumable_id">
              <div class="dashboard__row-main">
                <span class="status-tag" :class="alertMeta(item).className">
                  {{ alertMeta(item).label }}
                </span>
                <span class="dashboard__row-title">{{ item.consumable_name }}</span>
              </div>
              <span class="dashboard__row-meta">
                {{ item.current_stock ?? 0 }}/{{ item.safety_stock ?? 0 }}
                {{ item.unit ?? '' }}
              </span>
            </li>
          </ul>
          <EmptyState v-else title="当前没有待处理提醒" />
        </section>

        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">补货建议</h2>
            <el-button link type="primary" @click="go('/restock')">全部</el-button>
          </header>
          <ul v-if="suggestionItems.length" class="dashboard__list">
            <li v-for="item in suggestionItems" :key="item.consumable_id">
              <div class="dashboard__row-main">
                <span class="status-tag" :class="suggestionMeta(item).className">
                  {{ suggestionMeta(item).label }}
                </span>
                <span class="dashboard__row-title">{{ item.name }}</span>
              </div>
              <span class="dashboard__row-meta">
                补 {{ item.recommended_quantity }}{{ item.unit }}
              </span>
            </li>
          </ul>
          <EmptyState v-else title="当前没有补货建议" />
        </section>

        <section v-if="isStudent" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">可领用耗材</h2>
            <el-button link type="primary" @click="go('/overview')">总览</el-button>
          </header>
          <div v-if="availableConsumables.length" class="dashboard__chips">
            <button
              v-for="item in availableConsumables.slice(0, 6)"
              :key="item.id"
              type="button"
              class="dashboard__chip"
              @click="go(`/requisition?consumable_id=${item.id}`)"
            >
              <span class="dashboard__chip-name">{{ item.name }}</span>
              <span class="dashboard__chip-stock">
                {{ item.current_stock }}{{ item.unit }}
              </span>
            </button>
          </div>
          <EmptyState v-else title="当前没有可领用耗材" />
        </section>

        <section class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">最近领用</h2>
            <el-button link type="primary" @click="go('/records')">全部</el-button>
          </header>
          <ul v-if="records.items.length" class="dashboard__list">
            <li v-for="row in records.items" :key="row.id">
              <div class="dashboard__row-main">
                <span
                  v-if="row.type === 'reversal'"
                  class="status-tag status-tag--reversal"
                >
                  红冲
                </span>
                <span class="dashboard__row-title">
                  {{ row.consumable_name || '—' }}
                </span>
                <span class="dashboard__row-qty">
                  {{ row.quantity }}{{ row.unit }}
                </span>
              </div>
              <span class="dashboard__row-meta">
                {{ row.operator_name || '—' }} · {{ formatTime(row.created_at) }}
              </span>
            </li>
          </ul>
          <EmptyState v-else title="暂无领用记录" />
        </section>
      </div>

      <aside class="dashboard__side">
        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">近 7 天净消耗</h2>
          </header>
          <div v-if="unitTotals.length" class="dashboard__trend">
            <div v-for="unit in unitTotals" :key="unit.unit" class="dashboard__trend-row">
              <span class="dashboard__trend-label">{{ unit.unit }}</span>
              <div class="dashboard__trend-track">
                <span
                  class="dashboard__trend-bar"
                  :style="{
                    width: `${Math.max(4, (Number(unit.net_quantity) / maxUnit) * 100)}%`,
                  }"
                />
              </div>
              <span class="dashboard__trend-value">{{ unit.net_quantity }}</span>
            </div>
          </div>
          <EmptyState v-else title="近 7 天暂无消耗" />
        </section>

        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">班级消耗信号</h2>
            <span
              v-if="classSignals && !classSignals.insufficient"
              class="status-tag status-tag--info"
            >
              匿名聚合
            </span>
          </header>
          <template v-if="classSignals && !classSignals.insufficient">
            <div class="dashboard__signal-summary">
              <span class="dashboard__signal-total">
                {{ classSignals.total_net_quantity }}
              </span>
              <span class="dashboard__signal-caption">总净量 · 近 7 天</span>
              <span
                class="status-tag"
                :class="signalTone(classSignals.change_quantity)"
              >
                环比 {{ formatChange(classSignals.change_ratio) }}
              </span>
            </div>
            <div v-if="classSignalUnits.length" class="dashboard__trend">
              <div
                v-for="unit in classSignalUnits"
                :key="unit.unit"
                class="dashboard__trend-row"
              >
                <span class="dashboard__trend-label">{{ unit.unit }}</span>
                <div class="dashboard__trend-track">
                  <span
                    class="dashboard__trend-bar"
                    :style="{
                      width: `${Math.max(4, (Number(unit.net_quantity) / maxSignalUnit) * 100)}%`,
                    }"
                  />
                </div>
                <span class="dashboard__trend-value">
                  {{ unit.net_quantity }}
                </span>
              </div>
            </div>
          </template>
          <EmptyState v-else-if="classSignals" title="样本不足" />
          <EmptyState v-else title="班级信号暂不可用" />
        </section>

        <section v-if="isStaff" class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">最近操作</h2>
            <el-button link type="primary" @click="go('/logs')">全部</el-button>
          </header>
          <ul v-if="logs.items.length" class="dashboard__list">
            <li v-for="row in logs.items" :key="row.id">
              <div class="dashboard__row-main">
                <span class="dashboard__row-title">{{ row.operator_name || '—' }}</span>
                <span class="dashboard__row-sub">{{ row.target_label || '—' }}</span>
              </div>
              <span class="dashboard__row-meta">{{ formatTime(row.created_at) }}</span>
            </li>
          </ul>
          <EmptyState v-else title="暂无操作记录" />
        </section>

        <section class="panel dashboard__section">
          <header class="dashboard__section-head">
            <h2 class="dashboard__section-title">快捷入口</h2>
          </header>
          <div class="dashboard__quick">
            <button
              v-for="action in quickActions"
              :key="action.path"
              type="button"
              class="dashboard__quick-button"
              @click="go(action.path)"
            >
              <el-icon :size="18"><component :is="action.icon" /></el-icon>
              <span>{{ action.label }}</span>
            </button>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  min-height: 240px;
}

.dashboard__notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding: 8px 12px;
  border-radius: var(--radius-control);
  background: var(--warning-soft);
  color: var(--warning);
  font-size: 13px;
  line-height: 20px;
}

.dashboard__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 16px;
  overflow: hidden;
}

.dashboard__stat {
  display: block;
  padding: 18px 24px;
  border: 0;
  border-right: 1px solid var(--line-soft);
  background: transparent;
  font-family: inherit;
  text-align: left;
}

.dashboard__stat:last-child {
  border-right: 0;
}

.dashboard__stat--action {
  cursor: pointer;
  transition: background-color var(--duration-hover) ease;
}

.dashboard__stat--action:hover {
  background: var(--primary-50);
}

.dashboard__stat-label {
  display: block;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 16px;
  font-weight: 500;
}

.dashboard__stat-value {
  display: block;
  margin-top: 6px;
  color: var(--text);
  font-size: 30px;
  line-height: 36px;
  font-weight: 650;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.dashboard__stat-value--warning {
  color: var(--warning);
}

.dashboard__stat-value--danger {
  color: var(--danger);
}

.dashboard__grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
  gap: 16px;
  align-items: start;
}

.dashboard__main,
.dashboard__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.dashboard__section {
  padding: 20px 24px;
}

.dashboard__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.dashboard__section-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.dashboard__section-title {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.dashboard__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.dashboard__list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  border-top: 1px solid var(--line-soft);
}

.dashboard__list li:first-child {
  border-top: 0;
}

.dashboard__row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.dashboard__row-title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--text);
  font-size: 14px;
  line-height: 20px;
  font-weight: 500;
}

.dashboard__row-sub {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 18px;
}

.dashboard__row-qty {
  flex: 0 0 auto;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 18px;
  font-variant-numeric: tabular-nums;
}

.dashboard__row-meta {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 18px;
  font-variant-numeric: tabular-nums;
}

.dashboard__risk-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.dashboard__risk-row:hover .dashboard__row-title {
  color: var(--primary);
}

.dashboard__chips {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.dashboard__chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    border-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.dashboard__chip:hover {
  border-color: var(--primary-200);
  background: var(--primary-50);
  color: var(--primary);
}

.dashboard__chip-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dashboard__chip-stock {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.dashboard__trend {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 4px;
}

.dashboard__signal-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.dashboard__signal-total {
  color: var(--text);
  font-size: 24px;
  line-height: 30px;
  font-weight: 650;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}

.dashboard__signal-caption {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 18px;
}

.dashboard__trend-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 44px;
  align-items: center;
  gap: 8px;
}

.dashboard__trend-label {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 16px;
}

.dashboard__trend-track {
  height: 8px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--surface-muted);
}

.dashboard__trend-bar {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--primary);
}

.dashboard__trend-value {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 16px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.dashboard__quick {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.dashboard__quick-button {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  min-height: 72px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  line-height: 18px;
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    border-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.dashboard__quick-button .el-icon {
  color: var(--primary);
}

.dashboard__quick-button:hover {
  border-color: var(--primary-200);
  background: var(--primary-50);
  color: var(--primary);
}

@media (max-width: 1100px) {
  .dashboard__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .dashboard__side {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard__side .dashboard__section:last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 767px) {
  .dashboard__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard__stat {
    padding: 16px;
    border-right: 1px solid var(--line-soft);
    border-bottom: 1px solid var(--line-soft);
  }

  .dashboard__stat:nth-child(2n) {
    border-right: 0;
  }

  .dashboard__stat:nth-last-child(-n + 2) {
    border-bottom: 0;
  }

  .dashboard__stat-value {
    font-size: 26px;
    line-height: 32px;
  }

  .dashboard__grid {
    gap: 12px;
  }

  .dashboard__main,
  .dashboard__side {
    gap: 12px;
  }

  .dashboard__section {
    padding: 16px;
  }

  .dashboard__side {
    display: flex;
    flex-direction: column;
  }

  .dashboard__chips {
    grid-template-columns: minmax(0, 1fr);
  }

  .dashboard__row-meta {
    max-width: 132px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
}
</style>