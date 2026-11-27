<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import api from '../api'
import { useUserStore } from '../stores/user'

const POLL_INTERVAL_MS = 30000

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const summary = ref({ total_kinds: 0, low_stock_count: 0, today_requisitions: 0 })
const items = ref([])

const isTeacher = computed(() => ['teacher', 'admin'].includes(userStore.user?.role))

let pollTimer = null

async function fetchOverview() {
  loading.value = true
  try {
    const { data } = await api.get('/inventory/overview')
    summary.value = data.summary
    items.value = data.items
  } catch {
    ElMessage.error('加载库存总览失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function rowClassName({ row }) {
  if (row.is_out) return 'overview-row--out'
  if (row.low_stock) return 'overview-row--low'
  return ''
}

function statusTagType(row) {
  if (row.is_out) return 'danger'
  return row.low_stock ? 'warning' : 'success'
}

function statusLabel(row) {
  if (row.is_out) return '缺货'
  return row.low_stock ? '低库存' : '正常'
}

function consumableLabel(row) {
  return row.spec ? `${row.name}（${row.spec}）` : row.name
}

onMounted(() => {
  fetchOverview()
  pollTimer = setInterval(fetchOverview, POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <div class="overview">
    <div class="overview__summary">
      <el-card class="overview__metric">
        <div class="overview__metric-value">{{ summary.total_kinds }}</div>
        <div class="overview__metric-label">耗材总类数</div>
      </el-card>
      <el-card class="overview__metric">
        <div class="overview__metric-value overview__metric-value--warning">
          {{ summary.low_stock_count }}
        </div>
        <div class="overview__metric-label">低库存预警数</div>
      </el-card>
      <el-card class="overview__metric">
        <div class="overview__metric-value">{{ summary.today_requisitions }}</div>
        <div class="overview__metric-label">今日领用笔数</div>
      </el-card>
    </div>

    <el-card>
      <div class="overview__toolbar">
        <h2 class="overview__title">耗材库存</h2>
        <div class="overview__toolbar-actions">
          <el-button v-if="isTeacher" type="warning" plain @click="router.push('/restock')">
            查看补货建议
          </el-button>
          <el-button :loading="loading" @click="fetchOverview">手动刷新</el-button>
        </div>
      </div>
      <p class="overview__tip">每 30 秒自动刷新；低库存与缺货条目置顶显示。</p>

      <el-table
        v-loading="loading"
        :data="items"
        :row-class-name="rowClassName"
        class="overview__table"
      >
        <el-table-column label="名称" min-width="160">
          <template #default="{ row }">{{ consumableLabel(row) }}</template>
        </el-table-column>
        <el-table-column prop="location" label="存放位置" min-width="120" />
        <el-table-column label="当前库存" width="110" align="center">
          <template #default="{ row }">{{ row.current_stock }}{{ row.unit || '' }}</template>
        </el-table-column>
        <el-table-column label="安全库存线" width="110" align="center">
          <template #default="{ row }">{{ row.safety_stock > 0 ? row.safety_stock : '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row)">{{ statusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.overview__summary {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.overview__metric {
  flex: 1;
  text-align: center;
}

.overview__metric-value {
  font-size: 32px;
  font-weight: 700;
  color: #303133;
}

.overview__metric-value--warning {
  color: #e6a23c;
}

.overview__metric-label {
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}

.overview__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.overview__title {
  margin: 0;
  font-size: 16px;
}

.overview__toolbar-actions {
  display: flex;
  gap: 8px;
}

.overview__tip {
  margin: 0 0 12px;
  font-size: 12px;
  color: #909399;
}

.overview__table :deep(.overview-row--low) {
  background-color: #fdf6ec;
}

.overview__table :deep(.overview-row--low td.el-table__cell) {
  background-color: #fdf6ec;
}

.overview__table :deep(.overview-row--out) {
  background-color: #fef0f0;
}

.overview__table :deep(.overview-row--out td.el-table__cell) {
  background-color: #fef0f0;
}
</style>