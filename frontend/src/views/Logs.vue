<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import InventoryAuditDrawer from '../components/InventoryAuditDrawer.vue'
import LogDetailDrawer from '../components/LogDetailDrawer.vue'
import { validateDateRange } from '../utils/businessDate'
import { useUserStore } from '../stores/user'

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生', operator: '校方授权人' }
const STATUS_NAMES = { enabled: '启用', disabled: '停用' }
const TARGET_NAMES = {
  user: '用户',
  consumable: '耗材',
  requisition: '领用流水',
  requisition_correction: '纠错申请',
}

const ACTIONS = [
  { value: 'login', label: '登录', tagClass: 'status-tag--neutral' },
  { value: 'logout', label: '退出登录', tagClass: 'status-tag--neutral' },
  { value: 'requisition_create', label: '领用登记', tagClass: 'status-tag--info' },
  { value: 'requisition_reverse', label: '撤销领用', tagClass: 'status-tag--reversal' },
  { value: 'requisition_reentry', label: '关联重登', tagClass: 'status-tag--info' },
  {
    value: 'requisition_correction_submit',
    label: '纠错申请',
    tagClass: 'status-tag--warning',
  },
  {
    value: 'requisition_correction_accept',
    label: '纠错受理',
    tagClass: 'status-tag--success',
  },
  {
    value: 'requisition_correction_handle',
    label: '纠错处理',
    tagClass: 'status-tag--neutral',
  },
  { value: 'restock', label: '补货入库', tagClass: 'status-tag--success' },
  { value: 'restock_correction', label: '入库纠错', tagClass: 'status-tag--warning' },
  { value: 'consumable_create', label: '耗材建档', tagClass: 'status-tag--info' },
  { value: 'consumable_update', label: '耗材修改', tagClass: 'status-tag--info' },
  { value: 'consumable_disable', label: '耗材停用', tagClass: 'status-tag--danger' },
  { value: 'consumable_enable', label: '耗材启用', tagClass: 'status-tag--success' },
  { value: 'user_create', label: '新增用户', tagClass: 'status-tag--info' },
  { value: 'user_enable', label: '启用用户', tagClass: 'status-tag--success' },
  { value: 'user_disable', label: '停用用户', tagClass: 'status-tag--danger' },
  { value: 'admin_bootstrap', label: '初始化管理员', tagClass: 'status-tag--info' },
  { value: 'teacher_provision', label: '开通教师', tagClass: 'status-tag--info' },
  { value: 'admin_handover', label: '班委交接', tagClass: 'status-tag--info' },
  { value: 'admin_handover_create', label: '开通班委', tagClass: 'status-tag--info' },
  { value: 'admin_recover', label: '管理员恢复', tagClass: 'status-tag--success' },
  { value: 'admin_bootstrap_rejected', label: '初始化被拒', tagClass: 'status-tag--danger' },
  {
    value: 'teacher_provision_rejected',
    label: '开通教师被拒',
    tagClass: 'status-tag--danger',
  },
  { value: 'admin_handover_rejected', label: '班委交接被拒', tagClass: 'status-tag--danger' },
  { value: 'admin_recover_rejected', label: '管理员恢复被拒', tagClass: 'status-tag--danger' },
]

const ACTION_MAP = Object.fromEntries(ACTIONS.map((item) => [item.value, item]))

const SENSITIVE_ACTIONS = new Set(['login', 'logout'])

const SUMMARY_FIELDS = {
  name: '名称',
  spec: '规格',
  unit: '单位',
  location: '存放位置',
  safety_stock: '安全库存线',
  current_stock: '当前库存',
  status: '状态',
  account: '账号',
  role: '角色',
}

const router = useRouter()
const userStore = useUserStore()
const isAdmin = computed(() => userStore.user?.role === 'admin')
const actionOptions = computed(() =>
  isAdmin.value ? ACTIONS : ACTIONS.filter((item) => !SENSITIVE_ACTIONS.has(item.value)),
)

const logs = ref([])
const loading = ref(false)
const error = ref('')
const filterError = ref('')
const staleError = ref(false)
const userOptions = ref([])
const optionsError = ref(false)

const draft = reactive({ action: null, operatorId: null, dateRange: null })
const applied = reactive({ action: null, operatorId: null, dateRange: null })

const page = reactive({ current: 1, size: 20, total: 0 })

const detail = reactive({ visible: false, log: null })

const audit = reactive({ visible: false, consumableId: null, consumableName: '' })

let requestSeq = 0
let active = true

const hasFilters = computed(
  () => Boolean(applied.action || applied.operatorId || applied.dateRange),
)

function buildParams() {
  const params = {}
  if (applied.action) params.action = applied.action
  if (applied.operatorId) params.operator_id = applied.operatorId
  if (Array.isArray(applied.dateRange) && applied.dateRange.length === 2) {
    params.start_date = applied.dateRange[0]
    params.end_date = applied.dateRange[1]
  }
  return params
}

function applyFilters(source) {
  applied.action = source.action
  applied.operatorId = source.operatorId
  applied.dateRange = Array.isArray(source.dateRange) ? [...source.dateRange] : null
}

function resetFilters(target) {
  target.action = null
  target.operatorId = null
  target.dateRange = null
}

async function fetchLogs() {
  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  staleError.value = false
  try {
    const { data } = await api.get('/logs', {
      params: { ...buildParams(), page: page.current, page_size: page.size },
    })
    if (seq !== requestSeq || !active) return
    logs.value = Array.isArray(data.items) ? data.items : []
    page.total = data.total ?? 0
    const maxPage = Math.max(1, Math.ceil(page.total / page.size))
    if (!logs.value.length && page.current > maxPage) {
      page.current = maxPage
      await fetchLogs()
      return
    }
  } catch {
    if (seq !== requestSeq || !active) return
    if (logs.value.length) {
      staleError.value = true
    } else {
      error.value = '操作日志读取失败，请稍后重试。'
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function fetchUserOptions() {
  optionsError.value = false
  try {
    const { data } = await api.get('/users/history-options')
    userOptions.value = Array.isArray(data) ? data : []
  } catch {
    optionsError.value = true
    ElMessage.error('操作人选项加载失败')
  }
}

function handleSearch() {
  const dateError = validateDateRange(draft.dateRange)
  if (dateError) {
    filterError.value = dateError
    return
  }
  filterError.value = ''
  applyFilters(draft)
  page.current = 1
  fetchLogs()
}

function handleReset() {
  resetFilters(draft)
  resetFilters(applied)
  filterError.value = ''
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
  if (!value) return '—'
  const text = String(value).replace('T', ' ')
  return text.length >= 19 ? text.slice(0, 19) : text
}

function actionMeta(action) {
  return ACTION_MAP[action] ?? { label: action, tagClass: 'status-tag--neutral' }
}

function roleClass(role) {
  return role && ROLE_NAMES[role] ? `role-tag--${role}` : 'status-tag--neutral'
}

function parseSnapshot(value) {
  if (!value) return null
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function plainText(value) {
  if (!value) return ''
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? '' : String(parsed)
  } catch {
    return String(value)
  }
}

function nameFromSnapshot(row) {
  const after = parseSnapshot(row.after_value)
  const before = parseSnapshot(row.before_value)
  return (
    after?.name ||
    before?.name ||
    after?.account ||
    before?.account ||
    plainText(row.after_value) ||
    plainText(row.before_value) ||
    ''
  )
}

function targetLabel(row) {
  const typeName = TARGET_NAMES[row.target_type] ?? row.target_type ?? '—'
  if (!row.target_id) return typeName
  const name = nameFromSnapshot(row)
  return name ? `${typeName} #${row.target_id}（${name}）` : `${typeName} #${row.target_id}`
}

function describeItems(items) {
  if (!Array.isArray(items) || !items.length) return ''
  return items
    .map((item) => {
      const label = item.name || item.consumable_name || `耗材 #${item.consumable_id ?? ''}`
      const quantity = item.quantity ?? ''
      const unit = item.unit ?? ''
      return `${label} ${quantity}${unit}`.trim()
    })
    .join('、')
}

function displaySummaryValue(key, value, unit) {
  if (value === undefined || value === null) return '—'
  if (key === 'status') return STATUS_NAMES[value] ?? value
  if (key === 'role') return ROLE_NAMES[value] ?? value
  if ((key === 'safety_stock' || key === 'current_stock') && unit) return `${value}${unit}`
  return String(value)
}

function diffSummary(before, after) {
  if (!before || !after) return ''
  const unit = after.unit || before.unit || ''
  const parts = []
  for (const [key, label] of Object.entries(SUMMARY_FIELDS)) {
    const oldValue = before[key]
    const newValue = after[key]
    if (oldValue === newValue) continue
    parts.push(
      `${label} ${displaySummaryValue(key, oldValue, unit)} → ${displaySummaryValue(key, newValue, unit)}`,
    )
    if (parts.length >= 3) break
  }
  return parts.join('；')
}

function summaryText(row) {
  const before = parseSnapshot(row.before_value)
  const after = parseSnapshot(row.after_value)
  switch (row.action) {
    case 'login':
    case 'logout':
      return row.remark || '账号会话事件'
    case 'requisition_create': {
      if (!after) return row.remark || '—'
      const items = Array.isArray(after.items) ? after.items : []
      const detailText = describeItems(items)
      const count = items.length || after.row_count || 0
      return `登记 ${count} 项${detailText ? `：${detailText}` : ''}`
    }
    case 'requisition_reverse': {
      if (!after) return row.remark || '—'
      const reason = after.reason ? `；原因：${after.reason}` : ''
      const orderNo = after.reversal_order_no
        ? `；红冲单 ${after.reversal_order_no}`
        : ''
      return `回加 ${after.quantity}，库存 ${after.stock_before} → ${after.stock_after}${orderNo}${reason}`
    }
    case 'requisition_reentry': {
      if (!after) return row.remark || '—'
      const orderNo = after.reentry_order_no ? `（${after.reentry_order_no}）` : ''
      return `关联重登 #${after.reentry_id ?? ''}${orderNo}，重新领用 ${after.quantity}；来源红冲 #${after.reversal_id ?? ''}`
    }
    case 'requisition_correction_submit':
      return after
        ? `提交纠错 #${after.correction_id ?? ''}：${after.reason ?? '—'}`
        : row.remark || '—'
    case 'requisition_correction_accept': {
      if (!after) return row.remark || '—'
      const reentry = after.reentry_id
        ? `；关联重登 ${after.reentry_order_no ?? ''}`
        : ''
      return `受理纠错 #${row.target_id ?? ''}：红冲 ${after.reversal_order_no ?? '—'}${reentry}`
    }
    case 'requisition_correction_handle':
      return after
        ? `处理纠错 #${row.target_id ?? ''}：${after.status === 'rejected' ? '驳回' : after.status}${after.handling_note ? `；${after.handling_note}` : ''}`
        : row.remark || '—'
    case 'restock': {
      if (!after) return row.remark || '—'
      const beforeStock = before?.current_stock ?? after.stock_before
      return `入库 ${after.quantity}，库存 ${beforeStock} → ${after.current_stock}`
    }
    case 'restock_correction': {
      if (!after) return row.remark || '—'
      const beforeStock = before?.current_stock ?? '—'
      const reason = after.reason ? `；原因：${after.reason}` : ''
      return `纠错扣回 ${Math.abs(after.quantity)}，库存 ${beforeStock} → ${after.current_stock}${reason}`
    }
    case 'consumable_create':
      return after
        ? `新建档案：${after.name}${after.spec ? `（${after.spec}）` : ''}`
        : row.remark || '—'
    case 'consumable_update':
      return diffSummary(before, after) || row.remark || '档案字段有更新'
    case 'consumable_enable':
    case 'consumable_disable':
    case 'user_enable':
    case 'user_disable':
      return `状态 ${STATUS_NAMES[before?.status] ?? '—'} → ${STATUS_NAMES[after?.status] ?? '—'}`
    case 'user_create':
      return after ? `新增账号 ${after.account}（${after.name}）` : row.remark || '—'
    case 'admin_handover':
      return after
        ? `交接 ${after.previous_account} → ${after.new_account}（执行人：${after.actor}）`
        : row.remark || '—'
    default:
      return diffSummary(before, after) || row.remark || '—'
  }
}

function userOptionLabel(option) {
  return `${option.name}（${ROLE_NAMES[option.role] ?? option.role}·${option.account}）`
}

function openDetail(row) {
  detail.log = row
  detail.visible = true
}

function handleViewConsumable(payload) {
  detail.visible = false
  audit.consumableId = payload.id
  audit.consumableName = payload.name || ''
  audit.visible = true
}

function handleViewRequisition(id) {
  detail.visible = false
  router.push({ path: '/records', query: { requisition_id: id } })
}

function handleAuditSource() {
  const id = audit.consumableId
  audit.visible = false
  router.push({ path: '/records', query: { consumable_id: id } })
}

onMounted(() => {
  fetchUserOptions()
  fetchLogs()
})

onBeforeUnmount(() => {
  active = false
  requestSeq += 1
})
</script>

<template>
  <div class="logs">
    <PageHeader title="操作日志" :breadcrumb="['日常管理', '操作日志']" />

    <div v-if="optionsError" class="logs__notice">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="操作人选项加载失败"
        description="仍可按操作类型与日期查询，操作人筛选暂不可用。"
      />
      <el-button link type="primary" @click="fetchUserOptions">重试</el-button>
    </div>

    <section class="panel logs__panel">
      <el-form inline class="logs__filters" @submit.prevent>
        <el-form-item label="操作类型">
          <el-select
            v-model="draft.action"
            clearable
            filterable
            placeholder="全部操作类型"
            class="logs__filter-action"
          >
            <el-option
              v-for="option in actionOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="操作人">
          <el-select
            v-model="draft.operatorId"
            clearable
            filterable
            placeholder="全部操作人"
            class="logs__filter-operator"
            :disabled="optionsError"
          >
            <el-option
              v-for="option in userOptions"
              :key="option.id"
              :label="userOptionLabel(option)"
              :value="option.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="日期范围">
          <el-date-picker
            v-model="draft.dateRange"
            type="daterange"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            class="logs__filter-date"
          />
        </el-form-item>

        <el-form-item class="logs__filter-actions">
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <p v-if="filterError" class="logs__filter-error" role="alert">{{ filterError }}</p>

      <div v-if="staleError" class="logs__notice logs__notice--inline">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="刷新失败，显示上次结果"
        />
        <el-button link type="primary" @click="fetchLogs">重试</el-button>
      </div>

      <div v-if="loading && !logs.length" class="logs__skeleton" aria-busy="true">
        <p class="logs__placeholder">正在读取操作日志…</p>
        <el-skeleton :rows="8" animated />
      </div>

      <EmptyState
        v-else-if="!logs.length && error"
        title="操作日志暂时不可用"
        :description="error"
      >
        <template #action>
          <el-button type="primary" @click="fetchLogs">重试</el-button>
        </template>
      </EmptyState>

      <EmptyState
        v-else-if="!logs.length"
        :title="hasFilters ? '没有符合条件的记录' : '暂无操作日志'"
        :description="
          hasFilters
            ? '当前筛选条件下没有操作日志，可调整条件后重新查询。'
            : '登录、领用、档案变更等操作产生后会在这里留下记录。'
        "
      >
        <template #action>
          <el-button v-if="hasFilters" @click="handleReset">清除筛选</el-button>
        </template>
      </EmptyState>

      <template v-else>
        <el-table v-loading="loading" :data="logs" class="table-dense logs__table">
          <el-table-column label="操作时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作人" width="110">
            <template #default="{ row }">{{ row.operator_name ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="角色" width="90" align="center">
            <template #default="{ row }">
              <span v-if="row.role" class="status-tag" :class="roleClass(row.role)">
                {{ ROLE_NAMES[row.role] ?? row.role }}
              </span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作类型" width="120" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="actionMeta(row.action).tagClass">
                {{ actionMeta(row.action).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="对象" min-width="150">
            <template #default="{ row }">{{ targetLabel(row) }}</template>
          </el-table-column>
          <el-table-column label="变更摘要" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">{{ summaryText(row) }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.remark ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">查看详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="logs__pagination">
          <el-pagination
            v-model:current-page="page.current"
            :page-size="page.size"
            :page-sizes="[20, 50, 100]"
            :total="page.total"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </template>
    </section>

    <LogDetailDrawer
      v-model="detail.visible"
      :log="detail.log"
      @view-requisition="handleViewRequisition"
      @view-consumable="handleViewConsumable"
    />

    <InventoryAuditDrawer
      v-model="audit.visible"
      :consumable-id="audit.consumableId"
      :consumable-name="audit.consumableName"
      @view-source="handleAuditSource"
    />
  </div>
</template>

<style scoped>
.logs__notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.logs__notice .el-alert {
  flex: 1 1 320px;
}

.logs__notice--inline {
  margin-bottom: 12px;
}

.logs__panel {
  padding: 20px;
}

.logs__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 4px 8px;
  margin-bottom: 4px;
}

.logs__filter-action {
  width: 170px;
}

.logs__filter-operator {
  width: 180px;
}

.logs__filter-date {
  width: 260px;
}

.logs__filter-actions {
  margin-left: auto;
}

.logs__filter-error {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--danger);
}

.logs__skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.logs__placeholder {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.logs__table {
  width: 100%;
}

.logs__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 767px) {
  .logs__panel {
    padding: 16px;
  }

  .logs__filter-action,
  .logs__filter-operator,
  .logs__filter-date {
    width: 100%;
  }

  .logs__filter-actions {
    margin-left: 0;
  }
}
</style>