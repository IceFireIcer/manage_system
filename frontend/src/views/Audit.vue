<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import LogDetailDrawer from '../components/LogDetailDrawer.vue'
import { validateDateRange } from '../utils/businessDate'
import { useUserStore } from '../stores/user'

const ROLE_NAMES = {
  super_admin: '超级管理员',
  teacher: '教师',
  class_committee: '班委',
  admin: '班委',
  student: '学生',
  operator: '校方授权人',
}
const STATUS_NAMES = { enabled: '启用', disabled: '停用' }
const TARGET_NAMES = {
  user: '用户',
  consumable: '耗材',
  requisition: '领用流水',
  requisition_order: '领用单',
  requisition_correction: '纠错申请',
  operation_log: '操作日志',
  class: '班级',
  role: '角色',
  llm_provider: 'LLM 供应商',
  llm_prompt: 'LLM 提示词',
  llm_binding: 'LLM 功能绑定',
  assistant_tool: '助手工具',
  system: '系统',
}
const RESULTS = [
  { value: 'success', label: '成功', tagClass: 'status-tag--success' },
  { value: 'failure', label: '失败', tagClass: 'status-tag--danger' },
  { value: 'rejected', label: '已拒绝', tagClass: 'status-tag--danger' },
]
const RESULT_MAP = Object.fromEntries(RESULTS.map((item) => [item.value, item]))

const ACTIONS = [
  { value: 'login', label: '登录' },
  { value: 'logout', label: '退出登录' },
  { value: 'logout_all', label: '撤销全部会话' },
  { value: 'password_change', label: '修改密码' },
  { value: 'user_password_reset', label: '重置密码' },
  { value: 'role_switch', label: '角色切换' },
  { value: 'switch_class', label: '班级切换' },
  { value: 'setup_complete', label: '完成系统初始化' },
  { value: 'admin_bootstrap', label: '初始化管理员' },
  { value: 'teacher_provision', label: '开通教师' },
  { value: 'admin_handover', label: '班委交接' },
  { value: 'admin_handover_create', label: '开通班委' },
  { value: 'admin_recover', label: '管理员恢复' },
  { value: 'account_create', label: '创建账号' },
  { value: 'account_update', label: '更新账号' },
  { value: 'account_status', label: '账号状态' },
  { value: 'account_password_reset', label: '重置账号密码' },
  { value: 'account_role_update', label: '账号角色变更' },
  { value: 'account_class_update', label: '账号班级变更' },
  { value: 'permission_override_update', label: '权限覆盖' },
  { value: 'permission_role_update', label: '角色权限变更' },
  { value: 'class_create', label: '创建班级' },
  { value: 'class_update', label: '更新班级' },
  { value: 'class_delete', label: '删除班级' },
  { value: 'class_term_create', label: '创建学期' },
  { value: 'class_term_update', label: '更新学期' },
  { value: 'class_term_delete', label: '删除学期' },
  { value: 'class_teacher_assign', label: '分配教师班级' },
  { value: 'class_teacher_remove', label: '移除教师班级' },
  { value: 'llm_provider_create', label: '创建 LLM 供应商' },
  { value: 'llm_provider_update', label: '更新 LLM 供应商' },
  { value: 'llm_provider_delete', label: '删除 LLM 供应商' },
  { value: 'llm_provider_test', label: '测试 LLM 供应商' },
  { value: 'llm_prompt_create', label: '创建 LLM 提示词' },
  { value: 'llm_prompt_update', label: '更新 LLM 提示词' },
  { value: 'llm_prompt_activate', label: '启用 LLM 提示词' },
  { value: 'llm_binding_update', label: '更新 LLM 绑定' },
  { value: 'assistant_tool_call', label: '助手工具调用' },
  { value: 'profile_update', label: '更新个人资料' },
  { value: 'profile_admin_update', label: '维护成员资料' },
  { value: 'profile_avatar_update', label: '更新头像' },
  { value: 'audit_export', label: '导出审计' },
  { value: 'log_export', label: '导出操作日志' },
  { value: 'requisition_export', label: '导出领用流水' },
]
const ACTION_MAP = Object.fromEntries(ACTIONS.map((item) => [item.value, item]))
const SYSTEM_ONLY_ACTIONS = new Set([
  'setup_complete',
  'admin_bootstrap',
  'teacher_provision',
  'admin_handover',
  'admin_handover_create',
  'admin_recover',
  'permission_role_update',
])

const userStore = useUserStore()
const isSuper = computed(
  () => (userStore.activeRole ?? userStore.user?.role) === 'super_admin',
)
const actionOptions = computed(() =>
  isSuper.value
    ? ACTIONS
    : ACTIONS.filter(
        (item) =>
          !SYSTEM_ONLY_ACTIONS.has(item.value) && !item.value.startsWith('llm_'),
      ),
)

const logs = ref([])
const loading = ref(false)
const error = ref('')
const filterError = ref('')
const staleError = ref(false)
const operatorOptions = ref([])
const optionsError = ref(false)
const exporting = ref(false)
const viewportWidth = ref(window.innerWidth)
const isMobile = computed(() => viewportWidth.value < 768)

const draft = reactive({
  action: null,
  operatorId: null,
  result: null,
  keyword: '',
  dateRange: null,
})
const applied = reactive({
  action: null,
  operatorId: null,
  result: null,
  keyword: '',
  dateRange: null,
})
const page = reactive({ current: 1, size: 20, total: 0 })
const detail = reactive({ visible: false, log: null })

let requestSeq = 0
let active = true

const hasFilters = computed(() =>
  Boolean(
    applied.action ||
      applied.operatorId ||
      applied.result ||
      applied.keyword ||
      applied.dateRange,
  ),
)

function buildParams() {
  const params = {}
  if (applied.action) params.action = applied.action
  if (applied.operatorId) params.operator_id = applied.operatorId
  if (applied.result) params.result = applied.result
  if (applied.keyword) params.keyword = applied.keyword
  if (Array.isArray(applied.dateRange) && applied.dateRange.length === 2) {
    params.start_date = applied.dateRange[0]
    params.end_date = applied.dateRange[1]
  }
  return params
}

function applyFilters(source) {
  applied.action = source.action
  applied.operatorId = source.operatorId
  applied.result = source.result
  applied.keyword = source.keyword
  applied.dateRange = Array.isArray(source.dateRange) ? [...source.dateRange] : null
}

function resetFilters(target) {
  target.action = null
  target.operatorId = null
  target.result = null
  target.keyword = ''
  target.dateRange = null
}

async function fetchLogs() {
  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  staleError.value = false
  try {
    const { data } = await api.get('/settings/audit', {
      params: { ...buildParams(), page: page.current, page_size: page.size },
    })
    if (seq !== requestSeq || !active) return
    logs.value = Array.isArray(data.items) ? data.items : []
    page.total = data.total ?? 0
    const maxPage = Math.max(1, Math.ceil(page.total / page.size))
    if (!logs.value.length && page.current > maxPage) {
      page.current = maxPage
      await fetchLogs()
    }
  } catch {
    if (seq !== requestSeq || !active) return
    if (logs.value.length) {
      staleError.value = true
    } else {
      error.value = '审计记录读取失败，请稍后重试。'
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function fetchOperatorOptions() {
  optionsError.value = false
  try {
    const { data } = await api.get('/settings/audit/operators')
    operatorOptions.value = Array.isArray(data) ? data : []
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
  return ACTION_MAP[action] ?? { label: action }
}

function resultMeta(result) {
  return RESULT_MAP[result] ?? { label: result || '—', tagClass: 'status-tag--neutral' }
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

function snapshotName(row) {
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
  const name = row.target_label || snapshotName(row)
  if (name) return `${typeName} #${row.target_id}（${name}）`
  return `${typeName} #${row.target_id}`
}

function displayValue(key, value) {
  if (value === undefined || value === null || value === '') return '—'
  if (key === 'status') return STATUS_NAMES[value] ?? value
  if (key === 'role') return ROLE_NAMES[value] ?? value
  return String(value)
}

function diffSummary(before, after) {
  if (!before || !after) return ''
  const parts = []
  for (const key of ['status', 'role', 'class_id', 'class_name', 'name', 'account', 'enabled']) {
    if (before[key] === after[key]) continue
    parts.push(`${key} ${displayValue(key, before[key])} → ${displayValue(key, after[key])}`)
    if (parts.length >= 3) break
  }
  return parts.join('；')
}

function summaryText(row) {
  const before = parseSnapshot(row.before_value)
  const after = parseSnapshot(row.after_value)
  return diffSummary(before, after) || row.remark || '—'
}

function userOptionLabel(option) {
  return `${option.name}（${ROLE_NAMES[option.role] ?? option.role}·${option.account}）`
}

async function handleExport() {
  if (exporting.value) return
  exporting.value = true
  try {
    const response = await api.get('/settings/audit/export', {
      params: buildParams(),
      responseType: 'blob',
      timeout: 60000,
    })
    const disposition = response.headers?.['content-disposition'] || ''
    const matched = /filename="?([^";]+)"?/.exec(disposition)
    const filename = matched?.[1] || 'audit_logs.csv'
    const url = window.URL.createObjectURL(
      new Blob([response.data], { type: 'text/csv;charset=utf-8' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    ElMessage.success('导出完成')
  } catch {
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

function openDetail(row) {
  detail.log = row
  detail.visible = true
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  fetchOperatorOptions()
  fetchLogs()
})

onBeforeUnmount(() => {
  active = false
  requestSeq += 1
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="audit">
    <PageHeader
      title="审计"
      description="按类型、操作人和时间查看治理审计，并导出脱敏记录。"
      :breadcrumb="['设置', '审计']"
    />

    <div v-if="optionsError" class="audit__notice">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="操作人选项加载失败"
        description="操作人筛选暂不可用。"
      />
      <el-button link type="primary" @click="fetchOperatorOptions">重试</el-button>
    </div>

    <section class="panel audit__panel">
      <div class="audit__filters">
        <el-form inline class="audit__filter-grid" @submit.prevent>
          <el-form-item label="操作类型">
            <el-select
              v-model="draft.action"
              clearable
              filterable
              placeholder="全部操作类型"
              class="audit__filter-action"
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
              class="audit__filter-operator"
              :disabled="optionsError"
            >
              <el-option
                v-for="option in operatorOptions"
                :key="option.id"
                :label="userOptionLabel(option)"
                :value="option.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="日期范围" class="audit__filter-item--wide">
            <el-date-picker
              v-model="draft.dateRange"
              type="daterange"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              class="audit__filter-date"
            />
          </el-form-item>

          <el-form-item label="结果">
            <el-select
              v-model="draft.result"
              clearable
              placeholder="全部结果"
              class="audit__filter-result"
            >
              <el-option
                v-for="option in RESULTS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="关键词">
            <el-input
              v-model="draft.keyword"
              clearable
              placeholder="对象、备注或变更内容"
              class="audit__filter-keyword"
            />
          </el-form-item>
        </el-form>

        <div class="audit__filter-actions">
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
          <el-button :loading="exporting" @click="handleExport">导出 CSV</el-button>
        </div>
      </div>

      <p v-if="filterError" class="audit__filter-error" role="alert">{{ filterError }}</p>

      <div v-if="staleError" class="audit__notice audit__notice--inline">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="刷新失败，显示上次结果"
        />
        <el-button link type="primary" @click="fetchLogs">重试</el-button>
      </div>

      <div v-if="loading && !logs.length" class="audit__skeleton" aria-busy="true">
        <p class="audit__placeholder">正在读取…</p>
        <el-skeleton :rows="8" animated />
      </div>

      <EmptyState
        v-else-if="!logs.length && error"
        title="审计记录暂时不可用"
        :description="error"
      >
        <template #action>
          <el-button type="primary" @click="fetchLogs">重试</el-button>
        </template>
      </EmptyState>

      <EmptyState
        v-else-if="!logs.length"
        :title="hasFilters ? '没有符合条件的审计记录' : '暂无审计记录'"
        :description="hasFilters ? '调整条件后重新查询。' : '产生治理操作后会记录在此。'"
      >
        <template #action>
          <el-button v-if="hasFilters" @click="handleReset">清除筛选</el-button>
        </template>
      </EmptyState>

      <template v-else>
        <el-table v-loading="loading" :data="logs" class="table-dense audit__table">
          <el-table-column label="操作时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作人" width="150">
            <template #default="{ row }">
              <div class="audit__operator">
                <span>{{ row.operator_name ?? '—' }}</span>
                <span v-if="row.role" class="audit__operator-role">
                  {{ ROLE_NAMES[row.role] ?? row.role }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column v-if="!isMobile" label="班级" width="130">
            <template #default="{ row }">{{ row.class_name ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="操作类型" width="150">
            <template #default="{ row }">
              <span class="status-tag status-tag--neutral">
                {{ actionMeta(row.action).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column v-if="!isMobile" label="结果" width="90">
            <template #default="{ row }">
              <span class="status-tag" :class="resultMeta(row.result).tagClass">
                {{ resultMeta(row.result).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="对象与变更" min-width="320">
            <template #default="{ row }">
              <div class="audit__target">{{ targetLabel(row) }}</div>
              <div class="audit__summary">{{ summaryText(row) }}</div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="audit__pagination">
          <el-pagination
            v-model:current-page="page.current"
            :page-size="page.size"
            :page-sizes="[20, 50, 100]"
            :total="page.total"
            :layout="isMobile ? 'total, prev, pager, next' : 'total, sizes, prev, pager, next, jumper'"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </template>
    </section>

    <LogDetailDrawer v-model="detail.visible" :log="detail.log" />
  </div>
</template>

<style scoped>
.audit__notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.audit__notice .el-alert {
  flex: 1 1 320px;
}

.audit__notice--inline {
  margin-bottom: 12px;
}

.audit__panel {
  padding: 20px;
}

.audit__filters {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audit__filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  align-items: end;
}

.audit__filter-grid :deep(.el-form-item) {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  margin: 0;
}

.audit__filter-grid :deep(.el-form-item__label) {
  justify-content: flex-start;
  width: auto;
  padding: 0 0 4px;
  line-height: 16px;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  color: var(--text-muted);
}

.audit__filter-grid :deep(.el-form-item__content) {
  width: 100%;
  margin-left: 0;
}

.audit__filter-item--wide {
  grid-column: span 2;
}

.audit__filter-action,
.audit__filter-operator,
.audit__filter-result,
.audit__filter-keyword,
.audit__filter-date {
  width: 100%;
}

.audit__filter-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.audit__filter-actions .el-button + .el-button {
  margin-left: 0;
}

.audit__operator {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.audit__operator-role {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.audit__target {
  font-size: 14px;
  line-height: 20px;
  color: var(--text);
}

.audit__summary {
  margin-top: 2px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.audit__filter-error {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--danger);
}

.audit__skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audit__placeholder {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.audit__table {
  width: 100%;
}

.audit__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
  overflow-x: auto;
}

@media (max-width: 1100px) {
  .audit__filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .audit__panel {
    padding: 16px;
  }

  .audit__filter-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .audit__filter-item--wide {
    grid-column: auto;
  }

  .audit__filter-actions .el-button {
    min-height: 44px;
  }
}
</style>