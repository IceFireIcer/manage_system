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
      rows: [...rows].sort((left, right) => right.quantity - left.quantity),
      total: rows.reduce((sum, row) => sum + row.quantity, 0),
    }))
    .sort((left, right) => right.total - left.total)
})

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
  return `${appliedStart.value} 至 ${appliedEnd.value}（含当天）`
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
      grid: { top: 44, right: 24, bottom: 72, left: 56 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const point = Array.isArray(params) ? params[0] : params
          return `${point.name}<br/>净消耗 ${point.value}${chartUnit.value}`
        },
      },
      xAxis: {
        type: 'category',
        data: chartItems.value.map((item) => item.name),
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
        name: chartUnit.value ? `净消耗（${chartUnit.value}）` : '净消耗',
        nameTextStyle: { color: cssVar('--text-muted') },
        axisLabel: { color: cssVar('--text-muted') },
        splitLine: { lineStyle: { color: cssVar('--line') } },
      },
      series: [
        {
          name: '净消耗',
          type: 'bar',
          barMaxWidth: 40,
          itemStyle: {
            color: cssVar('--primary'),
            borderRadius: [4, 4, 0, 0],
          },
          data: chartItems.value.map((item) => item.quantity),
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
        <el-radio-group v-model="windowDays" @change="switchWindow">
          <el-radio-button
            v-for="option in WINDOWS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </el-radio-button>
        </el-radio-group>
        <span class="consumption__range">{{ rangeText }}</span>
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
        <p class="consumption__placeholder">正在汇总消耗数据…</p>
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
        title="所选时间段暂无领用或红冲记录"
        description="可切换时间窗口，或在产生领用与红冲流水后再查看。"
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
      <div class="consumption__toolbar">
        <p class="consumption__definition">
          净消耗 = 领用量 − 红冲量（小于 0 时取 0）；日均速率 = 净消耗 ÷ 所选天数。
        </p>
        <el-select v-model="unitFilter" class="consumption__unit" placeholder="全部单位">
          <el-option label="全部单位" value="all" />
          <el-option
            v-for="unit in unitOptions"
            :key="unit"
            :label="`单位：${unit}`"
            :value="unit"
          />
        </el-select>
      </div>

      <div class="consumption__layout">
        <section class="panel consumption__table-block">
          <div class="consumption__block-head">
            <h2 class="consumption__block-title">消耗汇总</h2>
            <span class="consumption__block-meta">
              净消耗合计 {{ appliedWindow }} 天窗口 · 共 {{ tableItems.length }} 种耗材
            </span>
          </div>
          <el-table
            :data="tableItems"
            class="table-dense consumption__table"
            :row-class-name="rowClassName"
          >
            <el-table-column prop="name" label="耗材" min-width="150" />
            <el-table-column prop="spec" label="规格" min-width="130" />
            <el-table-column prop="unit" label="单位" width="90" align="center" />
            <el-table-column label="净消耗" width="110" align="right">
              <template #default="{ row }">{{ row.quantity }}</template>
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

        <section class="panel consumption__chart-block">
          <div class="consumption__block-head">
            <h2 class="consumption__block-title">净消耗对比</h2>
            <span v-if="chartUnit" class="consumption__block-meta">
              单位：{{ chartUnit }}
            </span>
          </div>
          <div ref="chartRef" class="consumption__chart"></div>
          <p v-if="!chartItems.length" class="consumption__placeholder">
            当前单位暂无消耗记录
          </p>
        </section>
      </div>
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
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
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
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.consumption__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.consumption__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.consumption__definition {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.consumption__unit {
  width: 160px;
}

.consumption__layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.consumption__table-block {
  order: 1;
  padding: 20px;
}

.consumption__chart-block {
  order: 2;
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
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.consumption__table {
  width: 100%;
}

.consumption__chart {
  width: 100%;
  height: 300px;
}

.consumption__table :deep(.consumption__group-start td.el-table__cell) {
  border-top: 2px solid var(--line);
}

@media (max-width: 767px) {
  .consumption__chart-block {
    order: 0;
  }

  .consumption__table-block,
  .consumption__chart-block {
    padding: 16px;
  }
}
</style>