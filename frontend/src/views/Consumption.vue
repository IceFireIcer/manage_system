<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import InventoryAuditDrawer from '../components/InventoryAuditDrawer.vue'
import { businessDate } from '../utils/businessDate'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const WINDOWS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
]

const router = useRouter()

const windowDays = ref(7)
const appliedWindow = ref(7)
const items = ref([])
const unitTotals = ref([])
const provisioningScope = ref('')
const loading = ref(false)
const error = ref('')
const staleError = ref(false)
const appliedStart = ref('')
const appliedEnd = ref('')
const unitFilter = ref('all')
const chartRef = ref(null)

const audit = reactive({
  visible: false,
  consumableId: null,
  consumableName: '',
})

let chart = null
let requestSeq = 0
let active = true

const unitOptions = computed(() => {
  const units = []
  for (const item of items.value) {
    if (!units.includes(item.unit)) units.push(item.unit)
  }
  return units
})

const unitGroups = computed(() => {
  const groups = new Map()
  for (const item of items.value) {
    if (!groups.has(item.unit)) groups.set(item.unit, [])
    groups.get(item.unit).push(item)
  }
  return [...groups.entries()]
    .map(([unit, rows]) => ({
      unit,
      rows: [...rows].sort(
        (left, right) =>
          (right.net_quantity ?? right.quantity) - (left.net_quantity ?? left.quantity) ||
          (right.cross_period_reversed_quantity ?? 0) -
            (left.cross_period_reversed_quantity ?? 0) ||
          left.name.localeCompare(right.name),
      ),
      total: rows.reduce(
        (sum, row) => sum + (row.net_quantity ?? row.quantity ?? 0),
        0,
      ),
    }))
    .sort((left, right) => right.total - left.total || left.unit.localeCompare(right.unit))
})

const visibleUnitTotals = computed(() =>
  unitFilter.value === 'all'
    ? unitTotals.value
    : unitTotals.value.filter((row) => row.unit === unitFilter.value),
)

const tableItems = computed(() => {
  const groups =
    unitFilter.value === 'all'
      ? unitGroups.value
      : unitGroups.value.filter((group) => group.unit === unitFilter.value)
  return groups.flatMap((group) => group.rows)
})

const chartUnit = computed(() =>
  unitFilter.value !== 'all' ? unitFilter.value : unitOptions.value[0] ?? '',
)

const chartItems = computed(() =>
  items.value.filter((item) => item.unit === chartUnit.value),
)

const rangeText = computed(() => {
  if (!appliedStart.value || !appliedEnd.value) return '—'
  return `${appliedStart.value} 至 ${appliedEnd.value}`
})

const failedWindowText = computed(() => `近 ${windowDays.value} 天`)

function rangeOf(windowSize) {
  return {
    start: businessDate(-(windowSize - 1)),
    end: businessDate(0),
  }
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function formatRate(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return String(Math.round(number * 100) / 100)
}

function renderChart() {
  if (!chartRef.value) return
  if (!chartItems.value.length) {
    chart?.clear()
    return
  }
  if (!chart) chart = echarts.init(chartRef.value)
  chart.setOption(
    {
      grid: { top: 36, right: 16, bottom: 64, left: 48 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const point = Array.isArray(params) ? params[0] : params
          return `${point.name}<br/>净额 ${point.value}${chartUnit.value}`
        },
      },
      xAxis: {
        type: 'category',
        data: chartItems.value.map((item) => item.name),
        axisTick: { show: false },
        axisLabel: {
          interval: 0,
          rotate: chartItems.value.length > 5 ? 30 : 0,
          color: cssVar('--text-muted'),
        },
        axisLine: { lineStyle: { color: cssVar('--line') } },
      },
      yAxis: {
        type: 'value',
        min: 0,
        minInterval: 1,
        name: chartUnit.value ? `净额（${chartUnit.value}）` : '净额',
        nameTextStyle: { color: cssVar('--text-muted') },
        axisLabel: { color: cssVar('--text-muted') },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: cssVar('--line-soft') } },
      },
      series: [
        {
          name: '净额',
          type: 'bar',
          barMaxWidth: 36,
          itemStyle: {
            color: cssVar('--primary'),
            borderRadius: [4, 4, 0, 0],
          },
          data: chartItems.value.map((item) => item.net_quantity),
        },
      ],
    },
    true,
  )
  chart.resize()
}

async function fetchSummary() {
  const seq = ++requestSeq
  const requested = windowDays.value
  const range = rangeOf(requested)
  loading.value = true
  error.value = ''
  staleError.value = false
  try {
    const { data } = await api.get('/consumption/summary', {
      params: { window: requested },
    })
    if (seq !== requestSeq || !active) return
    items.value = Array.isArray(data.items) ? data.items : []
    unitTotals.value = Array.isArray(data.unit_totals) ? data.unit_totals : []
    provisioningScope.value = data.provisioning_scope || ''
    appliedWindow.value = requested
    appliedStart.value = range.start
    appliedEnd.value = range.end
    if (unitFilter.value !== 'all' && !unitOptions.value.includes(unitFilter.value)) {
      unitFilter.value = 'all'
    }
    await nextTick()
    renderChart()
  } catch {
    if (seq !== requestSeq || !active) return
    if (items.value.length) {
      staleError.value = true
    } else {
      error.value = '消耗汇总读取失败，请稍后重试。'
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function switchWindow(value) {
  windowDays.value = value
  fetchSummary()
}

function rowClassName({ row, rowIndex }) {
  const previous = tableItems.value[rowIndex - 1]
  return previous && previous.unit !== row.unit ? 'consumption__group-start' : ''
}

function goRecords(row) {
  router.push({
    path: '/records',
    query: {
      consumable_id: row.consumable_id,
      start_date: appliedStart.value,
      end_date: appliedEnd.value,
    },
  })
}

function openAudit(row) {
  audit.consumableId = row.consumable_id
  audit.consumableName = row.spec ? `${row.name}（${row.spec}）` : row.name
  audit.visible = true
}

function handleAuditSource() {
  const id = audit.consumableId
  audit.visible = false
  router.push({ path: '/records', query: { consumable_id: id } })
}

function handleResize() {
  chart?.resize()
}

watch(unitFilter, async () => {
  await nextTick()
  renderChart()
})

onMounted(() => {
  window.addEventListener('resize', handleResize)
  fetchSummary()
})

onBeforeUnmount(() => {
  active = false
  requestSeq += 1
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="consumption">
    <PageHeader title="消耗汇总" :breadcrumb="['库存分析', '消耗汇总']">
      <template #actions>
        <el-radio-group
          v-model="windowDays"
          class="consumption__segmented"
          @change="switchWindow"
        >
          <el-radio-button
            v-for="option in WINDOWS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </el-radio-button>
        </el-radio-group>
        <span class="consumption__range">{{ rangeText }}</span>
        <el-select v-model="unitFilter" class="consumption__unit" placeholder="全部单位">
          <el-option label="全部单位" value="all" />
          <el-option
            v-for="unit in unitOptions"
            :key="unit"
            :label="`单位：${unit}`"
            :value="unit"
          />
        </el-select>
      </template>
    </PageHeader>

    <div v-if="staleError" class="consumption__notice">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        :title="`切换到${failedWindowText}失败，仍在展示近 ${appliedWindow} 天的数据`"
        :description="rangeText"
      />
      <el-button link type="primary" @click="fetchSummary">重试</el-button>
    </div>

    <div v-if="loading && items.length" class="consumption__notice">
      <el-alert type="info" :closable="false" show-icon title="正在更新消耗汇总，当前显示上次结果" />
    </div>

    <section v-if="!items.length" class="panel consumption__state">
      <div v-if="loading" class="consumption__skeleton" aria-busy="true">
        <p class="consumption__placeholder">正在汇总…</p>
        <el-skeleton :rows="6" animated />
      </div>
      <EmptyState
        v-else-if="error"
        title="消耗汇总暂时不可用"
        :description="error"
      >
        <template #action>
          <el-button type="primary" @click="fetchSummary">重试</el-button>
        </template>
      </EmptyState>
      <EmptyState
        v-else
        title="所选时间段暂无消耗记录"
        description="可切换时间窗口后再查看。"
      >
        <template #action>
          <el-button
            type="primary"
            @click="switchWindow(appliedWindow === 7 ? 30 : 7)"
          >
            查看{{ appliedWindow === 7 ? '近 30 天' : '近 7 天' }}
          </el-button>
        </template>
      </EmptyState>
    </section>

    <div v-else class="consumption__body" :aria-busy="loading">

      <section class="panel consumption__chart-block">
        <div class="consumption__block-head">
          <h2 class="consumption__block-title">净额对比</h2>
          <span v-if="chartUnit" class="consumption__block-meta">单位 {{ chartUnit }}</span>
        </div>
        <div ref="chartRef" class="consumption__chart"></div>
        <p v-if="!chartItems.length" class="consumption__placeholder">当前单位暂无消耗记录</p>
      </section>

      <section v-if="visibleUnitTotals.length" class="panel consumption__unit-block">
        <div class="consumption__block-head">
          <h2 class="consumption__block-title">分单位合计</h2>
        </div>
        <div class="consumption__unit-grid">
          <div v-for="row in visibleUnitTotals" :key="row.unit" class="consumption__unit-card">
            <div class="consumption__unit-title">
              <span class="consumption__unit-name">{{ row.unit }}</span>
              <span class="consumption__block-meta">{{ row.consumables }} 种</span>
            </div>
            <div class="consumption__unit-primary">
              <span class="consumption__unit-value">{{ row.net_quantity }}</span>
              <span class="consumption__unit-unit">{{ row.unit }}</span>
            </div>
            <dl class="consumption__unit-meta">
              <div>
                <dt>日均</dt>
                <dd>{{ formatRate(row.daily_rate) }}{{ row.unit }}/天</dd>
              </div>
              <div>
                <dt>发出</dt>
                <dd>{{ row.issued_quantity }}{{ row.unit }}</dd>
              </div>
              <div>
                <dt>红冲</dt>
                <dd>{{ row.reversed_quantity }}{{ row.unit }}</dd>
              </div>
            </dl>
            <p v-if="row.cross_period_reversed_quantity" class="consumption__unit-note">
              跨期纠错 {{ row.cross_period_reversed_quantity }}{{ row.unit }}
            </p>
          </div>
        </div>
      </section>

      <section class="panel consumption__table-block">
        <div class="consumption__block-head">
          <h2 class="consumption__block-title">消耗汇总</h2>
          <span class="consumption__block-meta">
            {{ appliedWindow }} 天 · {{ tableItems.length }} 种耗材
          </span>
        </div>
        <el-table
          :data="tableItems"
          class="table-dense consumption__table"
          :row-class-name="rowClassName"
        >
          <el-table-column prop="name" label="耗材" min-width="140" />
          <el-table-column prop="spec" label="规格" min-width="120" />
          <el-table-column prop="unit" label="单位" width="80" align="center" />
          <el-table-column label="发出量" width="100" align="right">
            <template #default="{ row }">{{ row.issued_quantity }}</template>
          </el-table-column>
          <el-table-column label="红冲量" width="100" align="right">
            <template #default="{ row }">{{ row.reversed_quantity }}</template>
          </el-table-column>
          <el-table-column label="净额" width="100" align="right">
            <template #default="{ row }">{{ row.net_quantity }}</template>
          </el-table-column>
          <el-table-column label="跨期纠错" width="110" align="right">
            <template #default="{ row }">
              {{ row.cross_period_reversed_quantity || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="日均速率" width="120" align="right">
            <template #default="{ row }">
              {{ formatRate(row.daily_rate) }}{{ row.unit }}/天
            </template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="goRecords(row)">查领用记录</el-button>
              <el-button link type="primary" @click="openAudit(row)">查库存变动</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <InventoryAuditDrawer
      v-model="audit.visible"
      :consumable-id="audit.consumableId"
      :consumable-name="audit.consumableName"
      @view-source="handleAuditSource"
    />
  </div>
</template>

<style scoped>
.consumption__range {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.consumption__segmented {
  display: inline-flex;
}

.consumption__segmented :deep(.el-radio-button__inner) {
  height: 32px;
  padding: 0 14px;
  border-color: var(--line);
  border-radius: 0;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  box-shadow: none;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease,
    border-color var(--duration-hover) ease;
}

.consumption__segmented :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-radius: var(--radius-control) 0 0 var(--radius-control);
}

.consumption__segmented :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-radius: 0 var(--radius-control) var(--radius-control) 0;
}

.consumption__segmented :deep(.el-radio-button__inner:hover) {
  background: var(--surface-subtle);
  color: var(--text);
}

.consumption__segmented :deep(.el-radio-button.is-active .el-radio-button__inner) {
  border-color: var(--primary-200);
  background: var(--primary-soft);
  color: var(--primary);
  box-shadow: none;
}

.consumption__unit {
  width: 148px;
}

.consumption__notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.consumption__notice .el-alert {
  flex: 1 1 320px;
}

.consumption__state {
  padding: 24px;
}

.consumption__skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.consumption__placeholder {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.consumption__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}


.consumption__unit-block,
.consumption__chart-block,
.consumption__table-block {
  padding: 20px;
}

.consumption__block-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;
}

.consumption__block-title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.consumption__block-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.consumption__unit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.consumption__unit-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.consumption__unit-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.consumption__unit-name {
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text-secondary);
}

.consumption__unit-primary {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.consumption__unit-value {
  font-size: 24px;
  line-height: 30px;
  font-weight: 650;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}

.consumption__unit-unit {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.consumption__unit-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}

.consumption__unit-meta dt {
  margin: 0;
  font-size: 11px;
  line-height: 15px;
  color: var(--text-muted);
}

.consumption__unit-meta dd {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
}

.consumption__unit-note {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--warning);
}

.consumption__table {
  width: 100%;
}

.consumption__chart {
  width: 100%;
  height: 300px;
}

.consumption__table :deep(.consumption__group-start td.el-table__cell) {
  border-top: 1px solid var(--line);
}

@media (max-width: 767px) {
  .consumption__unit-block,
  .consumption__chart-block,
  .consumption__table-block {
    padding: 16px;
  }

  .consumption__unit {
    width: 100%;
  }

  .consumption__unit-meta {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
</style>