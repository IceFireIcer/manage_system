<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import api from '../api'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const WINDOWS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
]

const windowDays = ref(7)
const items = ref([])

const loading = ref(false)
const chartRef = ref(null)
let chart = null

function renderChart() {
  if (!chart) return
  chart.setOption(
    {
      grid: { top: 24, right: 16, bottom: 56, left: 48 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: {
        type: 'category',
        data: items.value.map((item) => item.name),
        axisLabel: { interval: 0, rotate: items.value.length > 6 ? 30 : 0 },
      },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '净消耗',
          type: 'bar',
          barMaxWidth: 48,
          itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
          data: items.value.map((item) => item.quantity),
        },
      ],
    },
    true,
  )
  chart.resize()
}

async function fetchSummary() {
  loading.value = true
  try {
    const { data } = await api.get('/consumption/summary', {
      params: { window: windowDays.value },
    })
    items.value = data.items

    renderChart()
  } catch {
    ElMessage.error('加载消耗汇总失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function handleWindowChange() {
  fetchSummary()
}

function handleResize() {
  chart?.resize()
}

onMounted(() => {
  if (chartRef.value) {
    chart = echarts.init(chartRef.value)
  }
  window.addEventListener('resize', handleResize)
  fetchSummary()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <el-card>
    <div class="consumption__intro">
      <p>净消耗 = 领用 − 红冲（负值截断为 0）；日均速率 = 净消耗 ÷ 窗口天数。</p>
    </div>

    <div class="consumption__toolbar">
      <el-radio-group v-model="windowDays" @change="handleWindowChange">
        <el-radio-button
          v-for="option in WINDOWS"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }}
        </el-radio-button>
      </el-radio-group>

    </div>

    <el-table v-loading="loading" :data="items" class="consumption__table">
      <el-table-column prop="name" label="耗材" min-width="160" />
      <el-table-column prop="spec" label="规格" min-width="140" />
      <el-table-column prop="unit" label="单位" width="90" align="center" />
      <el-table-column label="净消耗" width="110" align="center">
        <template #default="{ row }">{{ row.quantity }}</template>
      </el-table-column>
      <el-table-column label="日均速率" width="120" align="center">
        <template #default="{ row }">{{ row.daily_rate.toFixed(2) }}</template>
      </el-table-column>
    </el-table>

    <div class="consumption__chart-block">
      <h3 class="consumption__chart-title">净消耗对比</h3>
      <div v-show="items.length" ref="chartRef" class="consumption__chart"></div>
      <el-empty v-if="!loading && !items.length" description="窗口内暂无消耗流水" />
    </div>
  </el-card>
</template>

<style scoped>
.consumption__intro {
  margin-bottom: 16px;
  color: #606266;
}

.consumption__intro p {
  margin: 0;
  font-size: 13px;
}

.consumption__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}


.consumption__table {
  width: 100%;
  margin-bottom: 24px;
}

.consumption__chart-block {
  border-top: 1px solid #ebeef5;
  padding-top: 16px;
}

.consumption__chart-title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.consumption__chart {
  width: 100%;
  height: 320px;
}
</style>