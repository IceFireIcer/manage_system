<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import RecordDetailDrawer from '../components/RecordDetailDrawer.vue'
import ReversalDialog from '../components/ReversalDialog.vue'
import CorrectionDialog from '../components/CorrectionDialog.vue'
import ReturnScrapDialog from '../components/ReturnScrapDialog.vue'
import { validateDateRange } from '../utils/businessDate'

const QUICK_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '有效领用' },
  { key: 'reversed', label: '已撤销' },
  { key: 'reversal', label: '红冲' },
]

const QUICK_CONDITIONS = {
  all: { type: null, status: null },
  active: { type: 'normal', status: 'active' },
  reversed: { type: 'normal', status: 'reversed' },
  reversal: { type: 'reversal', status: 'active' },
}

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const isStudent = computed(() => userStore.user?.role === 'student')
const isStaff = computed(() => ['teacher', 'admin'].includes(userStore.user?.role))

const pageTitle = computed(() => (isStudent.value ? '我的记录' : '记录查询'))
const breadcrumb = computed(() => ['领用管理', pageTitle.value])

function hasDateRange(range) {
  return (
    Array.isArray(range) &&
    range.length === 2 &&
    Boolean(range[0]) &&
    Boolean(range[1])
  )
}

const draft = reactive({
  consumableId: null,
  userId: null,
  dateRange: null,
  type: null,
  status: null,
})

const applied = reactive({
  consumableId: null,
  userId: null,
  dateRange: null,
  type: null,
  status: null,
})

const records = ref([])
const page = reactive({ current: 1, size: 20, total: 0 })
const loading = ref(false)
const initialized = ref(false)
const loadError = ref('')
const stale = ref(false)
const lastLoadedAt = ref(null)
const pageCorrected = ref(false)
const filterError = ref('')

const consumables = ref([])
const consumablesLoadError = ref('')
const userOptions = ref([])
const userLoadError = ref('')
const exporting = ref(false)

const detailVisible = ref(false)
const detailId = ref(null)
const detailRefresh = ref(0)
const reversalVisible = ref(false)
const reversalRecord = ref(null)
const correctionVisible = ref(false)
const correctionRecord = ref(null)
const stockActionVisible = ref(false)
const stockActionRecord = ref(null)
const stockActionKind = ref('return')
const receipt = ref(null)

const tableRegion = ref(null)
const lastTrigger = ref(null)
let requestSerial = 0
let pendingRequisitionId = null


const quickKey = computed(() => {
  if (!applied.type && !applied.status) return 'all'
  if (applied.type === 'normal' && applied.status === 'active') return 'active'
  if (applied.type === 'normal' && applied.status === 'reversed') return 'reversed'
  if (applied.type === 'reversal' && applied.status === 'active') return 'reversal'
  return ''
})

const hasAppliedFilters = computed(() => {
  const hasDates = hasDateRange(applied.dateRange)
  return Boolean(
    applied.consumableId ||
      (isStaff.value && applied.userId) ||
      applied.type ||
      applied.status ||
      hasDates,
  )
})

const lastLoadedLabel = computed(() => {
  if (!lastLoadedAt.value) return ''
  const date = lastLoadedAt.value
  const pad = (value) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
})

const receiptQuantityText = computed(() => {
  if (!receipt.value) return ''
  return `${receipt.value.quantity}${receipt.value.unit ? ` ${receipt.value.unit}` : ' 单位'}`
})

const receiptStockText = computed(() => {
  if (!receipt.value) return ''
  return `${receipt.value.stockAfter}${receipt.value.unit ? ` ${receipt.value.unit}` : ''}`
})

const appliedSummary = computed(() => {
  const parts = []
  if (applied.consumableId) {
    const item = consumables.value.find((entry) => entry.id === applied.consumableId)
    parts.push(
      item
        ? `耗材：${item.spec ? `${item.name}（${item.spec}）` : item.name}`
        : `耗材：#${applied.consumableId}`,
    )
  }
  if (isStaff.value && applied.userId) {
    const user = userOptions.value.find((entry) => entry.id === applied.userId)
    parts.push(user ? `领用人：${user.name}` : `领用人：#${applied.userId}`)
  }
  if (hasDateRange(applied.dateRange)) {
    parts.push(`日期：${applied.dateRange[0]} 至 ${applied.dateRange[1]}`)
  }
  if (applied.type) {
    parts.push(`类型：${applied.type === 'reversal' ? '红冲' : '领用'}`)
  }
  if (applied.status) {
    parts.push(`状态：${applied.status === 'reversed' ? '已撤销' : '有效'}`)
  }
  return parts
})

const emptyStateMeta = computed(() => {
  if (hasAppliedFilters.value) {
    const summary = appliedSummary.value.length
      ? `当前条件：${appliedSummary.value.join('；')}。`
      : ''
    return {
      title: '没有符合条件的记录',
      description: `${summary}可清除筛选查看全部记录。`,
      action: 'clear',
    }
  }
  if (isStudent.value) {
    return {
      title: '你还没有领用记录',
      description: '完成一次领用登记后，本人流水会出现在这里。',
      action: 'create',
    }
  }
  return {
    title: '暂无领用流水',
    description: '还没有登记过领用，可先创建一条领用记录。',
    action: 'create',
  }
})

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function rowUnit(row) {
  return row.unit || '待核对'
}

function consumableLabel(row) {
  return row.consumable_spec ? `${row.consumable_name}（${row.consumable_spec}）` : row.consumable_name
}

function consumableOptionLabel(item) {
  const base = item.spec ? `${item.name}（${item.spec}）` : item.name
  return item.status === 'disabled' ? `${base}（已停用）` : base
}

function userOptionLabel(option) {
  const role = option.role === 'teacher' ? '教师' : option.role === 'admin' ? '班委' : '学生'
  const disabled = option.status === 'disabled' ? '·已停用' : ''
  return `${option.name}（${role}·${option.account}${disabled}）`
}

function quantityText(row) {
  const prefix = row.type === 'reversal' ? '+' : ''
  const unit = rowUnit(row)
  return `${prefix}${row.quantity}${unit ? ` ${unit}` : ''}`
}

function stockText(row) {
  const unit = rowUnit(row)
  return `${row.stock_after}${unit ? ` ${unit}` : ''}`
}

function typeMeta(row) {
  if (row.type === 'reversal') {
    return { label: '红冲', className: 'status-tag--reversal' }
  }
  return { label: '领用', className: 'status-tag--info' }
}

const CORRECTION_STATUS_META = {
  submitted: { label: '待处理', className: 'status-tag--warning' },
  accepted: { label: '已处理', className: 'status-tag--success' },
  rejected: { label: '已驳回', className: 'status-tag--neutral' },
}

function statusMeta(row) {
  if (row.status === 'reversed') {
    return { label: '已撤销', className: 'status-tag--neutral' }
  }
  return { label: '有效', className: 'status-tag--success' }
}

function correctionMeta(row) {
  return (
    CORRECTION_STATUS_META[row.correction_status] ?? {
      label: row.correction_status ?? '',
      className: 'status-tag--neutral',
    }
  )
}

function relatedLabel(row) {
  if (!row.related) return ''
  return row.type === 'reversal' ? `原领用 #${row.related.id}` : `红冲 #${row.related.id}`
}

function canReverseRow(row) {
  return isStaff.value && row.type === 'normal' && row.status === 'active'
}

function canStockActionRow(row) {
  return row.type === 'normal' && row.status === 'active'
}

function canScrapRow(row) {
  return isStaff.value && canStockActionRow(row)
}

function rowClassName({ row }) {
  return row.status === 'reversed' ? 'records__row--reversed' : ''
}

function buildParams(source = applied) {
  const params = {}
  if (source.consumableId) params.consumable_id = source.consumableId
  if (isStaff.value && source.userId) params.user_id = source.userId
  if (source.type) params.type = source.type
  if (source.status) params.status = source.status
  if (hasDateRange(source.dateRange)) {
    params.start_date = source.dateRange[0]
    params.end_date = source.dateRange[1]
  }
  return params
}

function buildRouteQuery() {
  const query = {}
  if (applied.consumableId) query.consumable_id = applied.consumableId
  if (isStaff.value && applied.userId) query.user_id = applied.userId
  if (applied.type) query.type = applied.type
  if (applied.status) query.status = applied.status
  if (hasDateRange(applied.dateRange)) {
    query.start_date = applied.dateRange[0]
    query.end_date = applied.dateRange[1]
  }
  if (page.current > 1) query.page = page.current
  return query
}

function syncRouteQuery() {
  router.replace({ query: buildRouteQuery() }).catch(() => {})
}


function applyDraftFilters() {
  applied.consumableId = draft.consumableId
  applied.userId = isStaff.value ? draft.userId : null
  applied.dateRange = hasDateRange(draft.dateRange) ? [...draft.dateRange] : null
  applied.type = draft.type
  applied.status = draft.status
}

async function fetchRecords({ correctPage = true } = {}) {
  const serial = ++requestSerial
  loading.value = true
  if (!initialized.value) loadError.value = ''
  try {
    const { data } = await api.get('/requisitions', {
      params: { ...buildParams(), page: page.current, page_size: page.size },
    })
    if (serial !== requestSerial) return
    const total = data.total ?? 0
    const maxPage = Math.max(1, Math.ceil(total / page.size))
    if (correctPage && page.current > maxPage) {
      page.current = maxPage
      pageCorrected.value = true
      fetchRecords({ correctPage: false })
      return
    }
    records.value = data.items ?? []
    page.total = total
    initialized.value = true
    loadError.value = ''
    stale.value = false
    lastLoadedAt.value = new Date()
    if (pageCorrected.value) {
      pageCorrected.value = false
      nextTick(() => tableRegion.value?.focus())
    }
  } catch {
    if (serial !== requestSerial) return
    if (!initialized.value) {
      loadError.value = '记录读取失败，请稍后重试'
      records.value = []
    } else {
      stale.value = true
    }
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

function handleSearch() {
  const dateError = validateDateRange(draft.dateRange)
  if (dateError) {
    filterError.value = dateError
    return
  }
  filterError.value = ''
  applyDraftFilters()
  page.current = 1
  syncRouteQuery()
  fetchRecords()
}

function handleReset() {
  draft.consumableId = null
  draft.userId = null
  draft.dateRange = null
  draft.type = null
  draft.status = null
  applied.consumableId = null
  applied.userId = null
  applied.dateRange = null
  applied.type = null
  applied.status = null
  filterError.value = ''
  page.current = 1
  syncRouteQuery()
  fetchRecords()
}

function handleQuick(key) {
  const condition = QUICK_CONDITIONS[key]
  if (!condition) return
  draft.type = condition.type
  draft.status = condition.status
  handleSearch()
}

function handlePageChange(nextPage) {
  page.current = nextPage
  syncRouteQuery()
  fetchRecords()
}

function handleSizeChange(nextSize) {
  page.size = nextSize
  page.current = 1
  syncRouteQuery()
  fetchRecords()
}

async function fetchConsumableOptions() {
  consumablesLoadError.value = ''
  try {
    const { data } = isStudent.value
      ? await api.get('/requisitions/consumable-options')
      : await api.get('/consumables', { params: { status: 'all' } })
    consumables.value = data
  } catch {
    consumablesLoadError.value = '耗材筛选选项加载失败'
    consumables.value = []
  }
}

async function fetchUserOptions() {
  if (!isStaff.value) return
  userLoadError.value = ''
  try {
    const { data } = await api.get('/users/history-options')
    userOptions.value = data
  } catch {
    userLoadError.value = '领用人筛选选项加载失败'
    userOptions.value = []
  }
}

async function handleExport() {
  if (!isStaff.value || exporting.value) return
  exporting.value = true
  try {
    const response = await api.get('/requisitions/export', {
      params: buildParams(),
      responseType: 'blob',
    })
    const disposition = response.headers['content-disposition'] || ''
    const matched = disposition.match(/filename="?([^";]+)"?/)
    let filename = matched ? matched[1] : ''
    if (filename) {
      try {
        filename = decodeURIComponent(filename)
      } catch {
        filename = matched[1]
      }
    }
    if (!filename) {
      const pad = (value) => String(value).padStart(2, '0')
      const now = new Date()
      filename = `requisitions_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.csv`
    }
    const blob = response.data instanceof Blob
      ? response.data
      : new Blob([response.data], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    ElMessage.success('已开始下载 CSV')
  } catch {
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

function openDetail(target, event) {
  lastTrigger.value = event?.currentTarget ?? null
  detailId.value = typeof target === 'object' ? target.id : target
  detailVisible.value = true
}

function handleDetailClosed() {
  nextTick(() => {
    if (lastTrigger.value?.isConnected) {
      lastTrigger.value.focus()
    } else {
      tableRegion.value?.focus()
    }
  })
}

function handleDetailReverse(record) {
  reversalRecord.value = { ...record }
  reversalVisible.value = true
}

function handleDetailCorrect(record) {
  correctionRecord.value = { ...record }
  correctionVisible.value = true
}

function handleCorrectionSuccess() {
  correctionRecord.value = null
  detailRefresh.value += 1
  fetchRecords()
  ElMessage.success('纠错申请已提交，等待教师处理')
}

async function openStockAction(record, kind, event) {
  if (event?.currentTarget) {
    lastTrigger.value = event.currentTarget
  }
  let target = { ...record, unit: record.unit || rowUnit(record) }
  if (target.returnable_quantity === undefined) {
    try {
      const { data } = await api.get(`/requisitions/${record.id}`)
      target = { ...target, ...data }
    } catch {
      ElMessage.error('记录读取失败，请稍后重试')
      return
    }
  }
  if (Number(target.returnable_quantity ?? 0) <= 0) {
    ElMessage.warning('该记录已无可退或可报损数量')
    return
  }
  stockActionKind.value = kind
  stockActionRecord.value = target
  stockActionVisible.value = true
}

function handleDetailReturn(record) {
  openStockAction(record, 'return')
}

function handleDetailScrap(record) {
  openStockAction(record, 'scrap')
}

function handleStockActionSuccess() {
  ElMessage.success(
    stockActionKind.value === 'scrap' ? '报损已登记' : '退库已登记，库存已按实收数量回加',
  )
  stockActionVisible.value = false
  stockActionRecord.value = null
  detailRefresh.value += 1
  fetchRecords()
}

function openReversal(row, event) {
  lastTrigger.value = event?.currentTarget ?? null
  reversalRecord.value = { ...row, unit: rowUnit(row) }
  reversalVisible.value = true
}

function handleReversalSuccess(data) {
  const originId = reversalRecord.value?.id ?? data.reversal_of
  receipt.value = {
    id: data.id,
    orderNo: data.order_no || '',
    quantity: data.quantity,
    unit: data.unit || '',
    stockAfter: data.stock_after,
    originId,
    reentry: data.reentry
      ? {
          id: data.reentry.id,
          orderNo: data.reentry.order_no || '',
          quantity: data.reentry.quantity,
        }
      : null,
    correctionAccepted: data.correction?.status === 'accepted',
  }
  reversalRecord.value = null
  detailRefresh.value += 1
  fetchRecords()
}

function applyRouteQuery() {
  const query = route.query
  const consumableId = Number(query.consumable_id)
  if (Number.isInteger(consumableId) && consumableId > 0) {
    draft.consumableId = consumableId
    applied.consumableId = consumableId
  }
  if (isStaff.value) {
    const userId = Number(query.user_id)
    if (Number.isInteger(userId) && userId > 0) {
      draft.userId = userId
      applied.userId = userId
    }
  }
  if (['normal', 'reversal'].includes(String(query.type))) {
    draft.type = String(query.type)
    applied.type = String(query.type)
  }
  if (['active', 'reversed'].includes(String(query.status))) {
    draft.status = String(query.status)
    applied.status = String(query.status)
  }
  const startDate = query.start_date ? String(query.start_date) : ''
  const endDate = query.end_date ? String(query.end_date) : ''
  if (startDate && endDate && startDate <= endDate) {
    draft.dateRange = [startDate, endDate]
    applied.dateRange = [startDate, endDate]
  }
  const requisitionId = Number(query.requisition_id)
  if (Number.isInteger(requisitionId) && requisitionId > 0) {
    pendingRequisitionId = requisitionId
  }
  const routePage = Number(query.page)
  if (Number.isInteger(routePage) && routePage > 0) {
    page.current = routePage
  }
}

async function openRouteRequisition() {
  const id = pendingRequisitionId
  pendingRequisitionId = null
  if (!id) return
  try {
    await api.get(`/requisitions/${id}`)
  } catch (error) {
    const code = error.response?.status
    if (code === 404 || code === 403) {
      ElMessage.warning('该流水不存在或无权查看')
    }
    return
  }
  openDetail(id)
}

watch(
  () => route.query.requisition_id,
  (value) => {
    const id = Number(value)
    if (!value || !Number.isInteger(id) || id <= 0) return
    if (detailVisible.value && detailId.value === id) return
    pendingRequisitionId = id
    openRouteRequisition()
  },
)

onMounted(async () => {
  applyRouteQuery()
  await Promise.all([fetchConsumableOptions(), fetchUserOptions()])
  await fetchRecords()
  openRouteRequisition()
})
</script>

<template>
  <div class="records">
    <PageHeader :title="pageTitle" :breadcrumb="breadcrumb">
      <template #actions>
        <el-button
          v-if="isStaff"
          type="primary"
          plain
          :loading="exporting"
          :disabled="exporting"
          @click="handleExport"
        >
          {{ exporting ? '正在导出…' : '导出 CSV' }}
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      v-if="receipt"
      type="success"
      show-icon
      role="status"
      class="records__receipt"
      @close="receipt = null"
    >
      <template #title>
        已红冲，库存已回加 {{ receiptQuantityText }}<template v-if="receipt.reentry">，并生成关联重登单</template>
      </template>
      <template #default>
        <p class="records__receipt-text">
          原流水 #{{ receipt.originId }} 已标记为已撤销，生成红冲单 #{{ receipt.id }}<template v-if="receipt.orderNo">（{{ receipt.orderNo }}）</template>，变动后库存 {{ receiptStockText }}。
          <template v-if="receipt.reentry">
            关联重登 #{{ receipt.reentry.id }}<template v-if="receipt.reentry.orderNo">（{{ receipt.reentry.orderNo }}）</template>已按 {{ receipt.reentry.quantity }}{{ receipt.unit ? ` ${receipt.unit}` : '' }} 重新登记。
          </template>
        </p>
        <el-button link type="primary" @click="openDetail(receipt.id, $event)">查看红冲流水</el-button>
        <el-button
          v-if="receipt.reentry"
          link
          type="primary"
          @click="openDetail(receipt.reentry.id, $event)"
        >
          查看重登流水
        </el-button>
      </template>
    </el-alert>

    <form class="panel records__filters-panel" @submit.prevent="handleSearch">
      <div class="records__filters">
        <div class="records__filter">
          <span class="records__filter-label">耗材</span>
          <el-select
            v-model="draft.consumableId"
            filterable
            clearable
            placeholder="全部耗材"
            class="records__filter-consumable"
            aria-label="耗材"
          >
            <el-option
              v-for="item in consumables"
              :key="item.id"
              :label="consumableOptionLabel(item)"
              :value="item.id"
            />
          </el-select>
        </div>

        <div v-if="isStaff" class="records__filter">
          <span class="records__filter-label">领用人</span>
          <el-select
            v-model="draft.userId"
            filterable
            clearable
            placeholder="全部领用人"
            class="records__filter-user"
            aria-label="领用人"
          >
            <el-option
              v-for="option in userOptions"
              :key="option.id"
              :label="userOptionLabel(option)"
              :value="option.id"
            />
          </el-select>
        </div>

        <div class="records__filter">
          <span class="records__filter-label">起止日期</span>
          <el-date-picker
            v-model="draft.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            unlink-panels
            class="records__filter-date"
            aria-label="起止日期"
          />
        </div>

        <div class="records__filter">
          <span class="records__filter-label">类型</span>
          <el-select
            v-model="draft.type"
            clearable
            placeholder="全部类型"
            class="records__filter-type"
            aria-label="流水类型"
          >
            <el-option label="领用" value="normal" />
            <el-option label="红冲" value="reversal" />
          </el-select>
        </div>

        <div class="records__filter">
          <span class="records__filter-label">状态</span>
          <el-select
            v-model="draft.status"
            clearable
            placeholder="全部状态"
            class="records__filter-status"
            aria-label="流水状态"
          >
            <el-option label="有效" value="active" />
            <el-option label="已撤销" value="reversed" />
          </el-select>
        </div>

        <div class="records__filter-actions">
          <el-button type="primary" native-type="submit">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </div>
      </div>

      <p v-if="filterError" class="records__filter-error" role="alert">{{ filterError }}</p>
      <p v-if="consumablesLoadError" class="records__filter-hint">
        {{ consumablesLoadError }}
        <el-button link type="primary" @click="fetchConsumableOptions">重试</el-button>
      </p>
      <p v-if="isStaff && userLoadError" class="records__filter-hint">
        {{ userLoadError }}
        <el-button link type="primary" @click="fetchUserOptions">重试</el-button>
      </p>

      <div class="records__quick" role="group" aria-label="快捷筛选">
        <span class="records__quick-label">快捷</span>
        <el-button
          v-for="option in QUICK_OPTIONS"
          :key="option.key"
          size="small"
          :type="quickKey === option.key ? 'primary' : ''"
          :plain="quickKey !== option.key"
          :aria-pressed="quickKey === option.key"
          @click="handleQuick(option.key)"
        >
          {{ option.label }}
        </el-button>
      </div>
    </form>

    <section class="panel records__list" :aria-busy="loading">
      <el-alert
        v-if="stale"
        type="warning"
        :closable="false"
        show-icon
        class="records__stale"
      >
        <template #title>
          显示上次结果{{ lastLoadedLabel ? `（${lastLoadedLabel}）` : '' }}
        </template>
        <template #default>
          <p class="records__stale-text">刷新记录失败，当前保留最后一次成功获取的内容。</p>
          <el-button link type="primary" @click="fetchRecords()">重试</el-button>
        </template>
      </el-alert>

      <div v-if="loadError && !initialized" class="records__error" role="alert">
        <p class="records__error-text">{{ loadError }}</p>
        <el-button type="primary" plain @click="fetchRecords()">重试</el-button>
      </div>

      <div v-else-if="loading && !initialized" class="records__loading">
        <el-skeleton :rows="6" animated />
      </div>

      <div
        v-else
        ref="tableRegion"
        class="records__table-region"
        tabindex="-1"
        aria-label="领用记录表格区域"
      >
        <p v-if="loading && initialized" class="records__refreshing" role="status">正在刷新记录…</p>
        <el-table
          v-loading="loading"
          :data="records"
          :row-class-name="rowClassName"
          class="table-dense records__table"
          empty-text=" "
        >
          <el-table-column label="流水号" width="96">
            <template #default="{ row }">
              <span class="records__id">#{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="业务时间" width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="耗材" min-width="180">
            <template #default="{ row }">{{ consumableLabel(row) }}</template>
          </el-table-column>
          <el-table-column v-if="isStaff" prop="user_name" label="领用人" width="110" />
          <el-table-column label="类型" width="92">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.type === 'reversal' && row.reversal_reason"
                :content="`撤销原因：${row.reversal_reason}`"
                placement="top"
              >
                <span class="status-tag records__status-tooltip" :class="typeMeta(row).className">
                  {{ typeMeta(row).label }}
                </span>
              </el-tooltip>
              <span v-else class="status-tag" :class="typeMeta(row).className">
                {{ typeMeta(row).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="100" align="right">
            <template #default="{ row }">
              <span
                class="records__quantity"
                :class="{ 'records__quantity--reversal': row.type === 'reversal' }"
              >
                {{ quantityText(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="变动后库存" width="120" align="right">
            <template #default="{ row }">
              <span :title="'历史快照'">{{ stockText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.status === 'reversed' && row.reversal_reason"
                :content="`撤销原因：${row.reversal_reason}`"
                placement="top"
              >
                <span class="status-tag records__status-tooltip" :class="statusMeta(row).className">
                  {{ statusMeta(row).label }}
                </span>
              </el-tooltip>
              <span v-else class="status-tag" :class="statusMeta(row).className">
                {{ statusMeta(row).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="备注" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ 'records__muted': !row.remark }">{{ row.remark || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="纠错" width="100">
            <template #default="{ row }">
              <span
                v-if="row.correction_status"
                class="status-tag"
                :class="correctionMeta(row).className"
              >
                {{ correctionMeta(row).label }}
              </span>
              <span v-else class="records__muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="关联" min-width="150">
            <template #default="{ row }">
              <el-button
                v-if="row.related"
                link
                type="primary"
                @click="openDetail(row.related, $event)"
              >
                {{ relatedLabel(row) }}
              </el-button>
              <span v-else class="records__muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="260" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row, $event)">查看详情</el-button>
              <el-button
                v-if="canStockActionRow(row)"
                link
                type="primary"
                @click="openStockAction(row, 'return', $event)"
              >
                退库
              </el-button>
              <el-button
                v-if="canScrapRow(row)"
                link
                type="warning"
                @click="openStockAction(row, 'scrap', $event)"
              >
                报损
              </el-button>
              <el-button
                v-if="canReverseRow(row)"
                link
                type="danger"
                @click="openReversal(row, $event)"
              >
                红冲
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <EmptyState
          v-if="initialized && !loading && !records.length"
          :title="emptyStateMeta.title"
          :description="emptyStateMeta.description"
        >
          <template #action>
            <el-button v-if="emptyStateMeta.action === 'clear'" @click="handleReset">清除筛选</el-button>
            <el-button v-else type="primary" @click="router.push('/requisition')">登记领用</el-button>
          </template>
        </EmptyState>

        <div v-if="initialized && records.length" class="records__footer">
          <el-pagination
            v-model:current-page="page.current"
            v-model:page-size="page.size"
            :page-sizes="[20, 50, 100]"
            :total="page.total"
            layout="total, sizes, prev, pager, next"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </div>
    </section>

    <RecordDetailDrawer
      v-model="detailVisible"
      :record-id="detailId"
      :refresh-token="detailRefresh"
      @closed="handleDetailClosed"
      @reverse="handleDetailReverse"
      @correct="handleDetailCorrect"
      @return="handleDetailReturn"
      @scrap="handleDetailScrap"
    />

    <ReversalDialog
      v-model="reversalVisible"
      :record="reversalRecord"
      @success="handleReversalSuccess"
      @refresh="fetchRecords"
    />

    <CorrectionDialog
      v-model="correctionVisible"
      :record="correctionRecord"
      @success="handleCorrectionSuccess"
    />

    <ReturnScrapDialog
      v-model="stockActionVisible"
      :record="stockActionRecord"
      :action="stockActionKind"
      @success="handleStockActionSuccess"
      @refresh="fetchRecords"
    />
  </div>
</template>

<style scoped>
.records__receipt {
  margin-bottom: 16px;
}

.records__receipt-text {
  margin: 0 0 4px;
  font-size: 14px;
  line-height: 22px;
}

.records__filters-panel {
  padding: 16px 24px;
  margin-bottom: 16px;
}

.records__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px 16px;
}

.records__filter {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.records__filter-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.records__filter-consumable {
  width: 200px;
}

.records__filter-user {
  width: 160px;
}

.records__filter-date {
  width: 240px;
}

.records__filter-type,
.records__filter-status {
  width: 120px;
}

.records__filter-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.records__filter-error {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--danger);
}

.records__filter-hint {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.records__quick {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.records__quick-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.records__list {
  padding: 16px 24px 24px;
}

.records__stale {
  margin-bottom: 16px;
}

.records__stale-text {
  margin: 0 0 4px;
  font-size: 14px;
  line-height: 22px;
}

.records__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.records__error-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.records__loading {
  padding: 8px 0;
}


.records__refreshing {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.records__table {
  width: 100%;
}

.records__id,
.records__quantity {
  font-variant-numeric: tabular-nums;
}

.records__quantity--reversal {
  color: var(--reversal);
  font-weight: 600;
}

.records__status-tooltip {
  cursor: help;
}

.records__muted {
  color: var(--text-muted);
}

.records__footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.records__table :deep(.records__row--reversed) {
  color: var(--text-muted);
}

@media (max-width: 767px) {
  .records__filters-panel {
    padding: 16px;
  }

  .records__list {
    padding: 16px;
  }

  .records__filter,
  .records__filter-consumable,
  .records__filter-user,
  .records__filter-date,
  .records__filter-type,
  .records__filter-status {
    width: 100%;
  }

  .records__filter-actions {
    width: 100%;
    margin-left: 0;
  }
}
</style>