<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }
const STATUS_NAMES = { enabled: '启用', disabled: '停用' }
const TARGET_NAMES = { user: '用户', consumable: '耗材', requisition: '领用流水' }

const ACTIONS = [
  { value: 'login', label: '登录', type: 'info' },
  { value: 'logout', label: '退出登录', type: 'info' },
  { value: 'requisition_create', label: '领用登记', type: 'primary' },
  { value: 'requisition_reverse', label: '撤销领用', type: 'warning' },
  { value: 'restock', label: '补货入库', type: 'success' },
  { value: 'consumable_create', label: '耗材建档', type: 'primary' },
  { value: 'consumable_update', label: '耗材修改', type: 'primary' },
  { value: 'consumable_disable', label: '耗材停用', type: 'danger' },
  { value: 'consumable_enable', label: '耗材启用', type: 'success' },
  { value: 'user_create', label: '新增用户', type: 'primary' },
  { value: 'user_enable', label: '启用用户', type: 'success' },
  { value: 'user_disable', label: '停用用户', type: 'danger' },
]
const ACTION_MAP = Object.fromEntries(ACTIONS.map((item) => [item.value, item]))

const userStore = useUserStore()
const userOptions = ref([])
const logs = ref([])
const loading = ref(false)

const filters = reactive({
  action: null,
  operatorId: null,
  dateRange: null,
})

const page = reactive({ current: 1, size: 20, total: 0 })

const isStaff = computed(() => ['teacher', 'admin'].includes(userStore.user?.role))

function buildParams() {
  const params = {}
  if (filters.action) params.action = filters.action
  if (filters.operatorId) params.operator_id = filters.operatorId
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    params.start_date = filters.dateRange[0]
    params.end_date = filters.dateRange[1]
  }
  return params
}

async function fetchLogs() {
  loading.value = true
  try {
    const { data } = await api.get('/logs', {
      params: { ...buildParams(), page: page.current, page_size: page.size },
    })
    logs.value = data.items
    page.total = data.total
  } catch {
    ElMessage.error('查询操作日志失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function fetchUserOptions() {
  try {
    const { data } = await api.get('/users/options')
    userOptions.value = data
  } catch {
    ElMessage.error('加载用户列表失败，请稍后重试')
  }
}

function handleSearch() {
  page.current = 1
  fetchLogs()
}

function handleReset() {
  filters.action = null
  filters.operatorId = null
  filters.dateRange = null
  page.current = 1
  fetchLogs()
}

function handlePageChange(nextPage) {
  page.current = nextPage
  fetchLogs()
}

function handleSizeChange(nextSize) {
  page.size = nextSize
  page.current = 1
  fetchLogs()
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '—'
}

function actionMeta(action) {
  return ACTION_MAP[action] ?? { label: action, type: 'info' }
}

function targetLabel(row) {
  const typeName = TARGET_NAMES[row.target_type] ?? row.target_type ?? '—'
  return row.target_id ? `${typeName} #${row.target_id}` : typeName
}

function userOptionLabel(option) {
  return `${option.name}（${ROLE_NAMES[option.role] ?? option.role}）`
}

function formatValue(raw) {
  if (raw === null || raw === undefined || raw === '') return '—'
  const text = String(raw)
  if (text === 'enabled' || text === 'disabled') return STATUS_NAMES[text]
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object') return text
    const parts = []
    if (parsed.name !== undefined) parts.push(`名称 ${parsed.name}`)
    if (parsed.account !== undefined) parts.push(`账号 ${parsed.account}`)
    if (parsed.role !== undefined) parts.push(`角色 ${ROLE_NAMES[parsed.role] ?? parsed.role}`)
    if (parsed.status !== undefined) {
      parts.push(`状态 ${STATUS_NAMES[parsed.status] ?? parsed.status}`)
    }
    if (parsed.stock_before !== undefined) parts.push(`库存 ${parsed.stock_before}`)
    if (parsed.current_stock !== undefined) {
      const extra = parsed.quantity !== undefined ? `（数量 ${parsed.quantity}）` : ''
      parts.push(`库存 ${parsed.current_stock}${extra}`)
    } else if (parsed.quantity !== undefined) {
      parts.push(`数量 ${parsed.quantity}`)
    }
    if (parsed.total_quantity !== undefined) parts.push(`合计 ${parsed.total_quantity}`)
    if (parsed.reason !== undefined) parts.push(`原因 ${parsed.reason}`)
    return parts.length ? parts.join('，') : text
  } catch {
    return text
  }
}

onMounted(async () => {
  await fetchUserOptions()
  fetchLogs()
})
</script>

<template>
  <el-card>
    <div class="logs__intro">
      <p>
        操作日志覆盖登录/退出、领用与撤销、档案变更、用户管理与补货入库，
        记录操作人、对象与变更前后值；仅教师/班委可查看，日志只读。
      </p>
    </div>

    <div class="logs__filters">
      <el-select
        v-model="filters.action"
        clearable
        filterable
        placeholder="全部操作类型"
        class="logs__filter-action"
      >
        <el-option
          v-for="option in ACTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>

      <el-select
        v-if="isStaff"
        v-model="filters.operatorId"
        clearable
        filterable
        placeholder="全部操作人"
        class="logs__filter-operator"
      >
        <el-option
          v-for="option in userOptions"
          :key="option.id"
          :label="userOptionLabel(option)"
          :value="option.id"
        />
      </el-select>

      <el-date-picker
        v-model="filters.dateRange"
        type="daterange"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        value-format="YYYY-MM-DD"
        class="logs__filter-date"
      />

      <el-button type="primary" @click="handleSearch">查询</el-button>
      <el-button @click="handleReset">重置</el-button>
    </div>

    <el-table v-loading="loading" :data="logs" class="logs__table">
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="operator_name" label="操作人" width="110">
        <template #default="{ row }">{{ row.operator_name ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="角色" width="90" align="center">
        <template #default="{ row }">{{ ROLE_NAMES[row.role] ?? row.role ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="操作类型" width="130" align="center">
        <template #default="{ row }">
          <el-tag :type="actionMeta(row.action).type" size="small">
            {{ actionMeta(row.action).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="对象" min-width="130">
        <template #default="{ row }">{{ targetLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="变更前" min-width="180">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.before_value"
            :content="row.before_value"
            placement="top"
            popper-class="logs__tooltip"
          >
            <span class="logs__value">{{ formatValue(row.before_value) }}</span>
          </el-tooltip>
          <span v-else class="logs__value">—</span>
        </template>
      </el-table-column>
      <el-table-column label="变更后" min-width="180">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.after_value"
            :content="row.after_value"
            placement="top"
            popper-class="logs__tooltip"
          >
            <span class="logs__value">{{ formatValue(row.after_value) }}</span>
          </el-tooltip>
          <span v-else class="logs__value">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
    </el-table>

    <el-empty
      v-if="!loading && !logs.length"
      description="当前筛选条件下暂无操作日志"
      class="logs__empty"
    />

    <div class="logs__pagination">
      <el-pagination
        v-model:current-page="page.current"
        :page-size="page.size"
        :page-sizes="[10, 20, 50, 100]"
        :total="page.total"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </div>
  </el-card>
</template>

<style scoped>
.logs__intro {
  margin-bottom: 16px;
  color: #606266;
}

.logs__intro p {
  margin: 0;
  font-size: 13px;
}

.logs__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.logs__filter-action {
  width: 180px;
}

.logs__filter-operator {
  width: 190px;
}

.logs__filter-date {
  width: 260px;
}

.logs__table {
  width: 100%;
  margin-bottom: 16px;
}

.logs__value {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12px;
  color: #606266;
}

.logs__empty {
  margin-top: 24px;
}

.logs__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>