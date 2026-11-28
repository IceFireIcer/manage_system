<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import RequisitionReceiptCard from '../components/RequisitionReceiptCard.vue'
import { businessDate, businessNowInput, businessToday } from '../utils/businessDate'
import { announce } from '../utils/announcer'
import { copyRequestId, recordRequestId, requestIdOf } from '../utils/requestTrace'
import { keepFocusedFieldVisible } from '../utils/mobileFocus'
import {
  appendReceipt,
  clearReceipts,
  readBreaktimeMode,
  readReceipts,
  writeBreaktimeMode,
} from '../utils/requisitionBreaktime'

const MAX_ROWS = 5
const MAX_BACKFILL_DAYS = 30
const PENDING_KEY = 'manage_system.requisition.pending'
const DRAFT_TTL = 30 * 60 * 1000

const draftOwnerId = ref(null)
const draftNotice = ref('')
const recentItems = ref([])
const breaktimeMode = ref(false)
const showCompactRemark = ref(false)
const savedReceipts = ref([])
let draftReady = false
let draftTimer = null

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }
const STATUS_NAMES = { processing: '处理中', submitted: '已提交', rejected: '已拒绝' }

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const isStudent = computed(() => userStore.user?.role === 'student')
const isAdmin = computed(() => ['super_admin', 'admin'].includes(userStore.user?.role))

const locked = computed(() => {
  if (userStore.identityLocked) return true
  if (!userStore.isLoggedIn) return true
  if (draftOwnerId.value === null) return false
  return (userStore.user?.id ?? null) !== draftOwnerId.value
})

const consumables = ref([])
const userOptions = ref([])
const consumableLoading = ref(false)
const userLoading = ref(false)
const consumableError = ref('')
const userLoadError = ref('')
const prefillMessage = ref('')
const refreshWarning = ref('')

const submitting = ref(false)
const result = ref(null)
const submitError = ref('')
const uncertain = ref(false)
const requestStatus = ref(null)
const checkingRequest = ref(false)
const bypassLeaveGuard = ref(false)
const receiptSectionRef = ref(null)
const errorRequestId = ref('')
const pendingRequestId = ref('')

const form = reactive({
  userId: null,
  timeMode: 'now',
  backfillAt: '',
  backfillReason: '',
  remark: '',
})

const metaErrors = reactive({ user: '', time: '', reason: '' })
const rowErrors = reactive({})

let rowSeq = 0
const rowSelectRefs = new Map()
const rowQtyRefs = new Map()
const rowDomRefs = new Map()

function createRow(consumableId = null, quantity = 1) {
  rowSeq += 1
  return { key: rowSeq, consumableId, quantity }
}

const rows = ref([createRow()])

const userSelectRef = ref(null)
const timePickerRef = ref(null)
const reasonInputRef = ref(null)
const addButtonRef = ref(null)


function dateKey(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function disabledBackfillDate(date) {
  const key = dateKey(date)
  return key < businessDate(-MAX_BACKFILL_DAYS) || key > businessToday()
}

function findConsumable(consumableId) {
  return consumables.value.find((item) => item.id === consumableId) ?? null
}

function optionLabel(item, index) {
  const spec = item.spec ? `（${item.spec}）` : ''
  const stock = item.current_stock <= 0
    ? '缺货'
    : `库存 ${item.current_stock}${item.unit ? ` ${item.unit}` : ''}`
  const occupied = rows.value.findIndex(
    (row, rowIndex) => rowIndex !== index && row.consumableId === item.id,
  )
  const occupiedText = occupied >= 0 ? ` · 已被第 ${occupied + 1} 行选用` : ''
  return `${item.name}${spec} · ${stock}${occupiedText}`
}

function isOptionDisabled(item, index) {
  if (item.current_stock <= 0) return true
  return rows.value.some(
    (row, rowIndex) => rowIndex !== index && row.consumableId === item.id,
  )
}

function rowItem(row) {
  return findConsumable(row.consumableId)
}

function rowStockText(row) {
  const item = rowItem(row)
  if (!item) return '—'
  return `${item.current_stock}${item.unit ? ` ${item.unit}` : ''}`
}

function rowUnit(row) {
  return rowItem(row)?.unit ?? ''
}

function quantityOverStock(row) {
  const item = rowItem(row)
  if (!item) return false
  return row.quantity > item.current_stock
}

const totalQuantity = computed(() =>
  rows.value.reduce((sum, row) => {
    const value = Number(row.quantity)
    return Number.isInteger(value) && value > 0 ? sum + value : sum
  }, 0),
)

const quickItems = computed(() => {
  const byId = new Map(consumables.value.map((item) => [item.id, item]))
  const result = []
  for (const recent of recentItems.value) {
    const item = byId.get(recent.consumableId)
    if (item && !result.some((entry) => entry.id === item.id)) {
      result.push({ ...item, defaultQuantity: recent.quantity })
    }
  }
  if (result.length < 4) {
    for (const item of consumables.value) {
      if (result.length >= 6) break
      if (item.current_stock > 0 && !result.some((entry) => entry.id === item.id)) {
        result.push({ ...item, defaultQuantity: 1 })
      }
    }
  }
  return result.slice(0, 6)
})

function clearRowErrors() {
  Object.keys(rowErrors).forEach((key) => delete rowErrors[key])
}

function clearUncertain() {
  if (!uncertain.value) return
  uncertain.value = false
  submitError.value = ''
}

function setSelectRef(el, index) {
  if (el) rowSelectRefs.set(index, el)
  else rowSelectRefs.delete(index)
}

function setQtyRef(el, index) {
  if (el) rowQtyRefs.set(index, el)
  else rowQtyRefs.delete(index)
}

function setRowDom(el, index) {
  if (el) rowDomRefs.set(index, el)
  else rowDomRefs.delete(index)
}

function focusRow(index, field = 'select') {
  nextTick(() => {
    rowDomRefs.get(index)?.scrollIntoView({ block: 'center' })
    if (field === 'qty') {
      rowQtyRefs.get(index)?.focus?.()
    } else {
      rowSelectRefs.get(index)?.focus?.()
    }
  })
}

function focusUser() {
  nextTick(() => {
    userSelectRef.value?.$el?.scrollIntoView?.({ block: 'center' })
    userSelectRef.value?.focus?.()
  })
}

function focusTime() {
  nextTick(() => {
    timePickerRef.value?.$el?.scrollIntoView?.({ block: 'center' })
    timePickerRef.value?.focus?.()
  })
}

function handleRowConsumableChange(index) {
  delete rowErrors[index]
  clearUncertain()
}

function handleQuantityChange(index) {
  delete rowErrors[index]
  clearUncertain()
}

function addRow() {
  if (rows.value.length >= MAX_ROWS) return
  rows.value.push(createRow())
  clearUncertain()
  const index = rows.value.length - 1
  nextTick(() => rowSelectRefs.get(index)?.focus?.())
}

function removeRow(index) {
  if (rows.value.length <= 1) return
  rows.value.splice(index, 1)
  clearRowErrors()
  clearUncertain()
  nextTick(() => {
    const targetIndex = Math.min(index, rows.value.length - 1)
    const target = rowSelectRefs.get(targetIndex)
    if (target) target.focus?.()
    else addButtonRef.value?.$el?.focus?.()
  })
}

function handleUserChange() {
  metaErrors.user = ''
  clearUncertain()
}

function handleTimeModeChange() {
  metaErrors.time = ''
  metaErrors.reason = ''
  clearUncertain()
  if (form.timeMode === 'backfill') {
    if (!form.backfillAt) form.backfillAt = businessNowInput()
    focusTime()
  }
}

function validateBackfillTime() {
  if (!form.backfillAt) return '请选择补录时间'
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(form.backfillAt)) {
    return '请选择有效的补录时间'
  }
  if (form.backfillAt > businessNowInput()) return '领用时间不能晚于当前时间'
  if (form.backfillAt < `${businessDate(-MAX_BACKFILL_DAYS)}T00:00:00`) {
    return `领用时间不能早于 ${MAX_BACKFILL_DAYS} 天前，超出普通补录区间；请由教师或班委通过受控调整登记`
  }
  return ''
}

function validateMeta() {
  metaErrors.user = ''
  metaErrors.reason = ''
  if (isStudent.value) {
    if (!userStore.user?.id) metaErrors.user = '当前账号信息不可用，请重新登录'
  } else if (!form.userId) {
    metaErrors.user = '请选择领用人'
  }
  metaErrors.time = form.timeMode === 'backfill' ? validateBackfillTime() : ''
  if (form.timeMode === 'backfill' && !form.backfillReason.trim()) {
    metaErrors.reason = '请填写补录原因'
  }
  if (metaErrors.user) {
    focusUser()
    return false
  }
  if (metaErrors.time) {
    focusTime()
    return false
  }
  if (metaErrors.reason) {
    reasonInputRef.value?.focus?.()
    return false
  }
  return true
}

function validateRows() {
  clearRowErrors()
  let firstInvalid = null
  for (let index = 0; index < rows.value.length; index += 1) {
    const row = rows.value[index]
    if (!row.consumableId) {
      rowErrors[index] = '请选择耗材'
      if (!firstInvalid) firstInvalid = { index, field: 'select' }
      continue
    }
    const duplicateIndex = rows.value.findIndex(
      (other, otherIndex) => otherIndex < index && other.consumableId === row.consumableId,
    )
    if (duplicateIndex >= 0) {
      rowErrors[index] = `与第 ${duplicateIndex + 1} 行耗材重复`
      if (!firstInvalid) firstInvalid = { index, field: 'select' }
      continue
    }
    if (!Number.isInteger(row.quantity) || row.quantity < 1) {
      rowErrors[index] = '数量需为正整数'
      if (!firstInvalid) firstInvalid = { index, field: 'qty' }
    }
  }
  if (firstInvalid) {
    focusRow(firstInvalid.index, firstInvalid.field)
    return false
  }
  return true
}

function hasDraft() {
  if (result.value) return false
  return (
    rows.value.some((row) => row.consumableId !== null) ||
    form.remark.trim() !== '' ||
    form.backfillReason.trim() !== '' ||
    form.timeMode === 'backfill' ||
    form.userId !== (userStore.user?.id ?? null)
  )
}

function resetFormState() {
  rows.value = [createRow()]
  clearRowErrors()
  metaErrors.user = ''
  metaErrors.time = ''
  metaErrors.reason = ''
  form.userId = userStore.user?.id ?? null
  form.timeMode = 'now'
  form.backfillAt = ''
  form.backfillReason = ''
  form.remark = ''
  prefillMessage.value = ''
  submitError.value = ''
  uncertain.value = false
}

function draftKey(userId) {
  return `requisition-draft:${userId}`
}

function recentKey(userId) {
  return `recent-items:${userId}`
}

function removeDraft() {
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  try {
    localStorage.removeItem(draftKey(ownerId))
  } catch {
    draftNotice.value = ''
  }
}

function saveDraft() {
  if (!draftReady) return
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  if (locked.value) return
  try {
    if (result.value || !hasDraft()) {
      localStorage.removeItem(draftKey(ownerId))
      return
    }
    const payload = {
      savedAt: Date.now(),
      userId: form.userId,
      timeMode: form.timeMode,
      backfillAt: form.backfillAt,
      backfillReason: form.backfillReason,
      remark: form.remark,
      rows: rows.value.map((row) => ({
        consumableId: row.consumableId,
        quantity: row.quantity,
      })),
    }
    localStorage.setItem(draftKey(ownerId), JSON.stringify(payload))
  } catch {
    draftNotice.value = ''
  }
}

function restoreDraft() {
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  let draft = null
  try {
    const raw = localStorage.getItem(draftKey(ownerId))
    if (!raw) return
    draft = JSON.parse(raw)
  } catch {
    removeDraft()
    return
  }
  if (!draft || Date.now() - Number(draft.savedAt) > DRAFT_TTL) {
    removeDraft()
    return
  }
  const restoredRows = Array.isArray(draft.rows)
    ? draft.rows.filter(
        (row) =>
          row &&
          Number.isInteger(row.consumableId) &&
          Number.isInteger(row.quantity) &&
          row.quantity > 0,
      )
    : []
  if (!restoredRows.length && !draft.remark) return
  rows.value = restoredRows.length
    ? restoredRows.slice(0, MAX_ROWS).map((row) => createRow(row.consumableId, row.quantity))
    : [createRow()]
  if (consumables.value.length) {
    rows.value.forEach((row) => {
      if (row.consumableId && !findConsumable(row.consumableId)) {
        row.consumableId = null
      }
    })
  }
  form.userId = isStudent.value
    ? ownerId
    : Number.isInteger(draft.userId)
      ? draft.userId
      : ownerId
  form.timeMode = draft.timeMode === 'backfill' ? 'backfill' : 'now'
  form.backfillAt = typeof draft.backfillAt === 'string' ? draft.backfillAt : ''
  form.backfillReason = typeof draft.backfillReason === 'string' ? draft.backfillReason : ''
  form.remark = typeof draft.remark === 'string' ? draft.remark : ''
  draftNotice.value = '已恢复上次未提交的领用草稿，提交前请重新核对库存与领用人。'
}

function loadRecent() {
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  try {
    const raw = localStorage.getItem(recentKey(ownerId))
    if (!raw) return
    const data = JSON.parse(raw)
    if (!Array.isArray(data?.items)) return
    recentItems.value = data.items.filter(
      (item) =>
        item &&
        Number.isInteger(item.consumableId) &&
        Number.isInteger(item.quantity) &&
        item.quantity > 0,
    )
  } catch {
    recentItems.value = []
  }
}

function rememberRecent(snapshot) {
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  const items = snapshot
    .filter((row) => row.consumableId)
    .map((row) => ({ consumableId: row.consumableId, quantity: row.quantity }))
  if (!items.length) return
  recentItems.value = items
  try {
    localStorage.setItem(recentKey(ownerId), JSON.stringify({ savedAt: Date.now(), items }))
  } catch {
    recentItems.value = items
  }
}

function applyRecent() {
  if (!recentItems.value.length || locked.value) return
  rows.value = recentItems.value
    .slice(0, MAX_ROWS)
    .map((item) => createRow(item.consumableId, item.quantity))
  clearRowErrors()
  clearUncertain()
  draftNotice.value = '已填入最近明细，提交前请重新核对库存、业务时间与领用人。'
}

function toggleBreaktime() {
  if (submitting.value) return
  breaktimeMode.value = !breaktimeMode.value
  writeBreaktimeMode(draftOwnerId.value, breaktimeMode.value)
  announce(breaktimeMode.value ? '已进入课间模式' : '已退出课间模式')
}

function applyQuick(item) {
  if (!item || locked.value || submitting.value || item.current_stock <= 0) return
  const existingIndex = rows.value.findIndex((row) => row.consumableId === item.id)
  if (existingIndex >= 0) {
    focusRow(existingIndex, 'qty')
    return
  }
  const quantity = Number.isInteger(item.defaultQuantity) && item.defaultQuantity > 0
    ? item.defaultQuantity
    : 1
  const emptyIndex = rows.value.findIndex((row) => !row.consumableId)
  if (emptyIndex >= 0) {
    rows.value[emptyIndex].consumableId = item.id
    rows.value[emptyIndex].quantity = quantity
    clearRowErrors()
    clearUncertain()
    focusRow(emptyIndex, 'qty')
    return
  }
  if (rows.value.length < MAX_ROWS) {
    rows.value.push(createRow(item.id, quantity))
    clearRowErrors()
    clearUncertain()
    const index = rows.value.length - 1
    nextTick(() => rowQtyRefs.get(index)?.focus?.())
    return
  }
  ElMessage.warning(`单次最多登记 ${MAX_ROWS} 种耗材，请先移除一行`)
}

function scheduleDraftSave() {
  if (!draftReady) return
  if (draftTimer) window.clearTimeout(draftTimer)
  draftTimer = window.setTimeout(saveDraft, 400)
}

async function fetchConsumables() {
  consumableLoading.value = true
  consumableError.value = ''
  try {
    const { data } = await api.get('/consumables')
    consumables.value = data
    for (let index = 0; index < rows.value.length; index += 1) {
      const row = rows.value[index]
      if (row.consumableId && !findConsumable(row.consumableId)) {
        row.consumableId = null
        rowErrors[index] = '所选耗材已停用或不可领用，请重新选择'
      }
    }
    return true
  } catch {
    consumableError.value = '耗材列表加载失败，请稍后重试'
    return false
  } finally {
    consumableLoading.value = false
  }
}

async function fetchUserOptions() {
  if (isStudent.value) return true
  userLoading.value = true
  userLoadError.value = ''
  try {
    const { data } = await api.get('/users/options', { params: { detail: true } })
    userOptions.value = data
    if (form.userId && !data.some((option) => option.id === form.userId)) {
      form.userId = null
      metaErrors.user = '所选领用人已被停用，请重新选择'
    }
    return true
  } catch {
    userLoadError.value = '领用人列表加载失败，请稍后重试'
    return false
  } finally {
    userLoading.value = false
  }
}

function refreshAvailableStock(index) {
  fetchConsumables().then((ok) => {
    if (ok) focusRow(index, 'qty')
  })
}

async function applyPrefill() {
  const raw = route.query.consumable_id
  if (!raw) return
  const consumableId = Number(raw)
  if (!Number.isInteger(consumableId) || consumableId <= 0) {
    prefillMessage.value = '所选耗材当前无法领用，请重新选择'
    return
  }
  const item = findConsumable(consumableId)
  if (!item || item.current_stock <= 0) {
    prefillMessage.value = '所选耗材当前无法领用，请重新选择'
    return
  }
  rows.value[0].consumableId = consumableId
  prefillMessage.value = ''
  await nextTick()
  rowQtyRefs.get(0)?.focus?.()
}

function buildSnapshot() {
  return rows.value.map((row) => {
    const item = rowItem(row)
    return {
      consumableId: row.consumableId,
      quantity: row.quantity,
      name: item?.name ?? '',
      spec: item?.spec ?? '',
      unit: item?.unit ?? '',
    }
  })
}

function createRequestId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `req-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

function readPending() {
  try {
    const raw = window.localStorage.getItem(PENDING_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function savePending(requestId, payload) {
  window.localStorage.setItem(
    PENDING_KEY,
    JSON.stringify({ requestId, payload, savedAt: new Date().toISOString() }),
  )
}

function clearPending() {
  window.localStorage.removeItem(PENDING_KEY)
}

function formatMoment(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

function formatReceiptTime(value) {
  if (!value) return ''
  if (value instanceof Date) {
    const pad = (part) => String(part).padStart(2, '0')
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(
      value.getHours(),
    )}:${pad(value.getMinutes())}`
  }
  return String(value).replace('T', ' ').slice(0, 16)
}

function resolveReceiptOwnerName() {
  if (isStudent.value) return userStore.user?.name ?? ''
  return (
    userOptions.value.find((option) => option.id === form.userId)?.name
    ?? userStore.user?.name
    ?? ''
  )
}

function storeReceipt(receipt, snapshot, fallback = null) {
  const ownerId = draftOwnerId.value
  if (!ownerId) return
  const orderNo = receipt?.order_no ?? fallback?.orderNo ?? ''
  const ownerName = receipt?.owner_name || resolveReceiptOwnerName()
  const timeSource = receipt?.business_at || fallback?.businessAt || new Date()
  const businessAt = formatReceiptTime(timeSource)
  const lines = receipt?.lines?.length
    ? receipt.lines.map((line) => ({
        name: line.consumable_name,
        quantity: line.quantity,
        unit: line.unit ?? '',
      }))
    : snapshot.map((row) => ({ name: row.name, quantity: row.quantity, unit: row.unit }))
  if (!lines.length) return
  savedReceipts.value = appendReceipt(ownerId, {
    orderNo,
    ownerName,
    businessAt,
    createdAt: Date.now(),
    lines,
  })
}

async function clearSavedReceipts() {
  if (!savedReceipts.value.length) return
  const confirmed = await ElMessageBox.confirm(
    '将清除本机保存的最近回执，不影响已登记记录。',
    '清除最近回执？',
    {
      confirmButtonText: '清除',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) return
  clearReceipts(draftOwnerId.value)
  savedReceipts.value = []
  announce('已清除本机最近回执')
}

function applyReceiptErrors(errors) {
  let firstRow = null
  for (const error of errors ?? []) {
    if (error.line_no && error.line_no >= 1 && error.line_no <= rows.value.length) {
      rowErrors[error.line_no - 1] = error.reason
      if (firstRow === null) firstRow = error.line_no - 1
    } else if (error.field === 'user_id') {
      metaErrors.user = error.reason
    }
  }
  if (firstRow !== null) focusRow(firstRow, 'select')
}

function applyPendingDraft(pending) {
  const payload = pending?.payload
  if (!payload || !Array.isArray(payload.items) || payload.items.length === 0) return
  rows.value = payload.items.slice(0, MAX_ROWS).map((item) =>
    createRow(item.consumable_id ?? item.consumableId ?? null, item.quantity ?? 1),
  )
  form.remark = payload.remark ?? ''
  if (payload.user_id) form.userId = payload.user_id
  if (payload.created_at) {
    form.timeMode = 'backfill'
    form.backfillAt = payload.created_at
    form.backfillReason = payload.backfill_reason ?? ''
  }
}

function showReceipt(receipt, pending) {
  if (!receipt) return
  result.value = {
    count: receipt.lines.length,
    receipt,
    orderNo: receipt.order_no,
    businessAt: receipt.business_at,
    postedAt: receipt.posted_at,
  }
  requestStatus.value = {
    request_id: receipt.request_id,
    order_no: receipt.order_no,
    status: receipt.status,
    receipt,
    errors: [],
    error_detail: null,
  }
  uncertain.value = false
  submitError.value = ''
  errorRequestId.value = ''
  pendingRequestId.value = ''
  clearPending()
  nextTick(() => receiptSectionRef.value?.focus?.())
  announce(
    `已登记 ${result.value.count} 项耗材${
      result.value.orderNo ? `，领用单号 ${result.value.orderNo}` : ''
    }`,
  )
}

async function copyCurrentRequest() {
  const value = errorRequestId.value || pendingRequestId.value
  if (!value) return
  const ok = await copyRequestId(value)
  if (ok) ElMessage.success('请求编号已复制')
  else ElMessage.error('复制失败，请手动选择请求编号')
}

async function checkPendingRequest({ silent = false } = {}) {
  const pending = readPending()
  if (!pending?.requestId) {
    if (!silent) {
      ElMessage.info('没有待查证的领用请求')
      announce('没有待查证的领用请求')
    }
    return false
  }
  pendingRequestId.value = pending.requestId
  checkingRequest.value = true
  try {
    const { data } = await api.get(
      `/requisitions/requests/${encodeURIComponent(pending.requestId)}`,
    )
    requestStatus.value = data
    if (data.status === 'submitted' && data.receipt) {
      showReceipt(data.receipt, pending)
    } else if (data.status === 'rejected') {
      uncertain.value = false
      submitError.value = data.error_detail || '本次领用已被拒绝'
      applyReceiptErrors(data.errors)
      errorRequestId.value = recordRequestId(data.request_id || pending.requestId)
      clearPending()
      announce(
        `${submitError.value}${
          errorRequestId.value ? `。请求编号 ${errorRequestId.value}` : ''
        }`,
        { assertive: true },
      )
    } else {
      uncertain.value = true
      submitError.value = ''
      announce(`本次领用仍在处理中，请求标识 ${pendingRequestId.value}`)
    }
    return true
  } catch (error) {
    errorRequestId.value = recordRequestId(requestIdOf(error))
    if (!silent) {
      ElMessage.warning('暂时无法查询上次提交结果，请稍后重试')
      announce(
        `暂时无法查询上次提交结果，请稍后重试${
          errorRequestId.value ? `。请求编号 ${errorRequestId.value}` : ''
        }`,
        { assertive: true },
      )
    }
    return false
  } finally {
    checkingRequest.value = false
  }
}

function mapValidationDetail(detail) {
  if (!Array.isArray(detail) || detail.length === 0) return '请求字段校验未通过'
  const first = detail[0]
  const location = Array.isArray(first.loc) ? first.loc : []
  const itemsIndex = location.indexOf('items')
  if (itemsIndex >= 0) {
    const rowIndex = Number(location[itemsIndex + 1])
    if (Number.isInteger(rowIndex) && rowIndex >= 0 && rowIndex < rows.value.length) {
      rowErrors[rowIndex] = first.msg || '该行字段不符合要求'
      const field = location.includes('quantity') ? 'qty' : 'select'
      focusRow(rowIndex, field)
    }
  }
  return first.msg || '请求字段校验未通过'
}

async function handleSubmitError(error) {
  if (!error.response) {
    uncertain.value = true
    submitError.value = ''
    return
  }
  const status = error.response.status
  const detail = error.response.data?.detail
  if (status === 400 && typeof detail === 'string') {
    const headerRow = Number(error.response.headers?.['x-error-row'])
    const headerField = error.response.headers?.['x-error-field'] ?? ''
    const matched = detail.match(/第\s*(\d+)\s*行\s*[：:]\s*(.*)$/)
    let rowLocated = false
    if (Number.isInteger(headerRow) && headerRow > 0 && headerRow <= rows.value.length) {
      const index = headerRow - 1
      rowErrors[index] = detail.replace(/^第\s*\d+\s*行\s*[：:]\s*/, '') || detail
      focusRow(index, headerField.includes('quantity') ? 'qty' : 'select')
      rowLocated = true
    } else if (matched) {
      const index = Number.parseInt(matched[1], 10) - 1
      if (index >= 0 && index < rows.value.length) {
        rowErrors[index] = matched[2] || detail
        const field = matched[2]?.includes('库存') ? 'qty' : 'select'
        focusRow(index, field)
        rowLocated = true
      }
    }
    const userIssue = detail.includes('领用人')
    if (userIssue) {
      await fetchUserOptions()
      metaErrors.user = detail
      focusUser()
    }
    await fetchConsumables()
    if (rowLocated) {
      submitError.value = '整单未提交，请检查标出的失败行。'
    } else if (userIssue) {
      submitError.value = '整单未提交，请检查领用人信息后重新提交。'
    } else {
      submitError.value = `整单未提交：${detail}`
    }
    return
  }
  if (status === 422) {
    const message = mapValidationDetail(detail)
    submitError.value = Object.keys(rowErrors).length
      ? '整单未提交，请检查标出的字段。'
      : `整单未提交：${message}`
    await fetchConsumables()
    return
  }
  if (status === 403) {
    submitError.value = typeof detail === 'string' ? `整单未提交：${detail}` : '你没有权限登记本次领用'
    return
  }
  if (status >= 500) {
    uncertain.value = true
    submitError.value = ''
    return
  }
  if (typeof detail === 'string' && detail) {
    submitError.value = `整单未提交：${detail}`
    return
  }
  submitError.value = '提交失败，请稍后重试'
}

async function handleSubmit() {
  if (submitting.value || uncertain.value) return
  if (locked.value) {
    submitError.value = '当前页面已失效，请重新登录后再提交。'
    announce(submitError.value, { assertive: true })
    return
  }
  result.value = null
  submitError.value = ''
  errorRequestId.value = ''
  if (!validateMeta()) {
    announce('请检查领用人或领用时间后再提交', { assertive: true })
    return
  }
  if (!validateRows()) {
    announce('请检查标出的领用明细后再提交', { assertive: true })
    return
  }
  const requestId = createRequestId()
  pendingRequestId.value = requestId
  submitting.value = true
  try {
    const [freshConsumables, freshUsers] = await Promise.all([
      fetchConsumables(),
      fetchUserOptions(),
    ])
    if (!freshConsumables || !freshUsers) {
      refreshWarning.value = '未能刷新最新库存或领用人，将由服务端在提交时再次校验。'
    }
    if (!validateMeta()) {
      announce('提交前刷新发现领用人或时间有变化，请检查后重新提交', { assertive: true })
      return
    }
    if (!validateRows()) {
      announce('提交前刷新发现明细有变化，请检查后重新提交', { assertive: true })
      return
    }
    if (locked.value) {
      submitError.value = '当前页面已失效，请重新登录后再提交。'
      announce(submitError.value, { assertive: true })
      return
    }
    const snapshot = buildSnapshot()
    const payload = {
      items: rows.value.map((row) => ({
        consumable_id: row.consumableId,
        quantity: row.quantity,
      })),
      remark: form.remark.trim() || null,
      request_id: requestId,
    }
    if (!isStudent.value) payload.user_id = form.userId
    if (form.timeMode === 'backfill') {
      payload.created_at = form.backfillAt
      payload.backfill_reason = form.backfillReason.trim()
    }
    savePending(requestId, payload)

    const { data } = await api.post('/requisitions', payload)
    if (data.receipt) {
      showReceipt(data.receipt)
      result.value = { ...result.value, rows: snapshot }
      storeReceipt(data.receipt, snapshot)
    } else {
      result.value = {
        count: data.count,
        items: data.items,
        orderNo: data.order_no,
        rows: snapshot,
      }
      requestStatus.value = {
        request_id: requestId,
        order_no: data.order_no,
        status: data.status,
        errors: [],
        error_detail: null,
      }
      errorRequestId.value = ''
      pendingRequestId.value = ''
      clearPending()
      nextTick(() => receiptSectionRef.value?.focus?.())
      announce(
        `已登记 ${result.value.count} 项耗材${
          result.value.orderNo ? `，领用单号 ${result.value.orderNo}` : ''
        }`,
      )
      storeReceipt(null, snapshot, {
        orderNo: data.order_no,
        businessAt: form.timeMode === 'backfill' ? form.backfillAt : new Date(),
      })
    }
    rememberRecent(snapshot)
    removeDraft()
    refreshWarning.value = ''
    const [consumablesOk, usersOk] = await Promise.all([fetchConsumables(), fetchUserOptions()])
    if (!consumablesOk || !usersOk) {
      refreshWarning.value = '已登记成功，但暂时无法刷新耗材可用量或可选领用人，请稍后在库存总览核对最新数值。'
    }
    clearRowErrors()
  } catch (error) {
    const status = error.response?.status
    errorRequestId.value = recordRequestId(requestIdOf(error))
    if (error.response && status < 500) clearPending()
    await handleSubmitError(error)
    const trace = errorRequestId.value
    const detail = uncertain.value
      ? `暂未确认本次领用是否成功，请使用请求编号 ${
          trace || pendingRequestId.value
        } 查询提交结果`
      : `${submitError.value}${trace ? `。请求编号 ${trace}` : ''}`
    announce(detail, { assertive: true })
  } finally {
    submitting.value = false
  }
}

async function handleReset() {
  if (submitting.value) return
  if (hasDraft()) {
    const confirmed = await ElMessageBox.confirm(
      '将清空本次登记内容，恢复为一条空白明细和默认时间。',
      '清空本次登记？',
      {
        confirmButtonText: '清空',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
      .then(() => true)
      .catch(() => false)
    if (!confirmed) return
  }
  resetFormState()
  removeDraft()
  router.replace({ query: {} })
  focusRow(0, 'select')
  announce('已清空本次登记，可以重新填写')
}

function handleContinue() {
  result.value = null
  requestStatus.value = null
  refreshWarning.value = ''
  resetFormState()
  removeDraft()
  router.replace({ query: {} })
  focusRow(0, 'select')
  announce('可以继续登记新的领用')
}

function goRecords() {
  bypassLeaveGuard.value = true
  router.push('/records')
}

function goConsumables() {
  bypassLeaveGuard.value = true
  router.push('/consumables')
}

function handleBeforeUnload(event) {
  if (!hasDraft() || submitting.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(async () => {
  if (userStore.sessionExpired || !userStore.isLoggedIn) return true
  if (bypassLeaveGuard.value) {
    bypassLeaveGuard.value = false
    return true
  }
  if (submitting.value) {
    ElMessage.warning('正在提交领用，请稍候')
    return false
  }
  if (!hasDraft()) return true
  return ElMessageBox.confirm(
    '当前登记内容尚未提交，离开后填写内容将丢失。',
    '放弃本次登记？',
    {
      confirmButtonText: '放弃并离开',
      cancelButtonText: '继续填写',
      type: 'warning',
    },
  )
    .then(() => true)
    .catch(() => false)
})

watch(
  () => ({
    rows: rows.value.map((row) => [row.consumableId, row.quantity]),
    userId: form.userId,
    timeMode: form.timeMode,
    backfillAt: form.backfillAt,
    backfillReason: form.backfillReason,
    remark: form.remark,
  }),
  scheduleDraftSave,
  { deep: true },
)

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  draftOwnerId.value = userStore.user?.id ?? null
  form.userId = userStore.user?.id ?? null
  breaktimeMode.value = readBreaktimeMode(draftOwnerId.value)
  savedReceipts.value = readReceipts(draftOwnerId.value)
  const pending = readPending()
  loadRecent()

  const [consumablesOk] = await Promise.all([fetchConsumables(), fetchUserOptions()])
  if (pending) {
    applyPendingDraft(pending)
    await checkPendingRequest({ silent: true })
  } else if (consumablesOk) {
    if (!route.query.consumable_id) restoreDraft()
    await applyPrefill()
  }
  draftReady = true
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  saveDraft()
  if (draftTimer) {
    window.clearTimeout(draftTimer)
    draftTimer = null
  }
})
</script>

<template>
  <div class="requisition">
    <PageHeader title="领用登记" :breadcrumb="['领用管理', '领用登记']" />

    <section
      v-if="result"
      ref="receiptSectionRef"
      class="panel requisition__receipt"
      tabindex="-1"
      aria-labelledby="requisition-receipt-title"
    >
      <div class="requisition__receipt-head">
        <div>
          <h2 id="requisition-receipt-title" class="requisition__receipt-title">
            已登记 {{ result.count }} 项耗材
          </h2>
          <p v-if="result.orderNo" class="requisition__receipt-desc">
            领用单号 {{ result.orderNo }}
          </p>
          <p class="requisition__receipt-desc">
            业务时间 {{ formatMoment(result.businessAt) }} · 入账时间 {{ formatMoment(result.postedAt) }}
          </p>
          <p v-if="result.receipt" class="requisition__receipt-desc">
            领用人 {{ result.receipt.owner_name }} · 经办人 {{ result.receipt.operator_name }}
            （{{ ROLE_NAMES[result.receipt.operator_role] ?? result.receipt.operator_role }}）
          </p>
          <p v-if="result.receipt?.backfill_reason" class="requisition__receipt-desc">
            补录原因 {{ result.receipt.backfill_reason }}
          </p>
        </div>
        <span class="status-tag status-tag--success">
          {{ STATUS_NAMES[requestStatus?.status] ?? '提交成功' }}
        </span>
      </div>

      <ul class="requisition__receipt-list">
        <li
          v-for="line in result.receipt?.lines ?? []"
          :key="line.requisition_id"
          class="requisition__receipt-row"
        >
          <span class="requisition__receipt-name">
            第 {{ line.line_no }} 行 · {{ line.consumable_name }}
          </span>
          <span class="requisition__receipt-qty">
            领用 {{ line.quantity }}{{ line.unit ? ` ${line.unit}` : '' }}
          </span>
          <span class="requisition__receipt-stock">
            扣减前 {{ line.stock_before }} · 扣减后 {{ line.stock_after }}
            {{ line.unit ? ` ${line.unit}` : '' }}
          </span>
          <span class="requisition__receipt-event">
            <template v-if="line.event_id">
              流水 #{{ line.event_id }}（记录 #{{ line.requisition_id }}）
            </template>
            <template v-else-if="line.linked_count_order_no">
              已盘点不重复扣减 · 盘点单 {{ line.linked_count_order_no }}（记录 #{{ line.requisition_id }}）
            </template>
            <template v-else>
              已计入期初不重复扣减（记录 #{{ line.requisition_id }}）
            </template>
          </span>
        </li>
      </ul>

      <el-alert
        v-if="requestStatus"
        type="info"
        :closable="false"
        show-icon
        :title="`请求标识 ${requestStatus.request_id} · 状态 ${STATUS_NAMES[requestStatus.status] ?? requestStatus.status}`"
        class="requisition__alert"
      />

      <el-alert
        v-if="refreshWarning"
        type="warning"
        :closable="false"
        show-icon
        :title="refreshWarning"
        class="requisition__alert"
      />

      <div class="requisition__receipt-actions">
        <el-button @click="handleContinue">继续登记</el-button>
        <el-button type="primary" @click="goRecords">查看记录</el-button>
      </div>
    </section>

    <section v-else class="requisition__shell" :aria-busy="consumableLoading || submitting">
      <div class="panel requisition__mode-bar">
        <div class="requisition__mode-copy">
          <span class="requisition__mode-title">课间模式</span>
          <span class="requisition__mode-state">{{ breaktimeMode ? '已开启 · 优先最近领用' : '标准模式' }}</span>
        </div>
        <el-button
          class="requisition__mode-toggle"
          :type="breaktimeMode ? 'primary' : 'default'"
          :aria-pressed="breaktimeMode ? 'true' : 'false'"
          :disabled="submitting"
          @click="toggleBreaktime"
        >
          {{ breaktimeMode ? '退出课间模式' : '进入课间模式' }}
        </el-button>
      </div>

      <RequisitionReceiptCard
        v-if="savedReceipts.length"
        class="requisition__recent"
        :receipts="savedReceipts"
        :limit="breaktimeMode ? 2 : 3"
        @clear="clearSavedReceipts"
      />

      <section
        v-if="breaktimeMode && quickItems.length"
        class="panel requisition__quick"
        aria-labelledby="requisition-quick-title"
      >
        <h2 id="requisition-quick-title" class="requisition__quick-title">最近领用</h2>
        <div class="requisition__quick-list">
          <button
            v-for="item in quickItems"
            :key="item.id"
            type="button"
            class="requisition__quick-chip"
            :disabled="submitting || locked || item.current_stock <= 0"
            @click="applyQuick(item)"
          >
            <span class="requisition__quick-name">{{ item.name }}</span>
            <span class="requisition__quick-meta">
              {{
                item.current_stock <= 0
                  ? '缺货'
                  : `库存 ${item.current_stock}${item.unit ? ` ${item.unit}` : ''}`
              }}
            </span>
          </button>
        </div>
      </section>

      <div v-if="consumableLoading && !consumables.length" class="panel requisition__state">
        <el-skeleton :rows="5" animated />
      </div>

      <div v-else-if="consumableError && !consumables.length" class="panel requisition__state" role="alert">
        <p class="requisition__state-text">{{ consumableError }}</p>
        <el-button @click="fetchConsumables">重试</el-button>
      </div>

      <div v-else-if="!consumables.length" class="panel requisition__state">
        <EmptyState
          title="暂无可领用耗材"
          :description="isAdmin ? '先添加至少一项启用耗材。' : '请联系班委建档。'"
        >
          <template #action>
            <el-button v-if="isAdmin" type="primary" @click="goConsumables">去建档</el-button>
          </template>
        </EmptyState>
      </div>

      <div v-else class="requisition__grid">
        <el-form
          id="requisition-form"
          label-position="top"
          class="requisition__main"
          :model="form"
          @submit.prevent="handleSubmit"
          @focusin="keepFocusedFieldVisible"
        >
          <button type="submit" class="sr-only" tabindex="-1" aria-hidden="true">提交领用</button>

          <el-alert
            v-if="locked"
            type="error"
            :closable="false"
            show-icon
            title="账号已在其它标签页切换或退出，本页已锁定，请重新登录。"
            class="requisition__alert"
          />
          <el-alert
            v-else-if="draftNotice"
            type="info"
            :closable="false"
            show-icon
            :title="draftNotice"
            class="requisition__alert"
          />
          <el-alert
            v-if="prefillMessage"
            type="warning"
            :closable="false"
            show-icon
            :title="prefillMessage"
            class="requisition__alert"
          />

          <el-alert
            v-if="consumableError"
            type="warning"
            :closable="false"
            show-icon
            :title="consumableError"
            class="requisition__alert"
          >
            <template #default>
              <el-button link type="primary" @click="fetchConsumables">重试并刷新可用量</el-button>
            </template>
          </el-alert>

          <el-alert
            v-if="uncertain"
            type="error"
            :closable="false"
            show-icon
            title="暂未确认本次领用是否成功"
            class="requisition__alert"
          >
            <template #default>
              <p class="requisition__alert-text">
                请求超时或连接中断，可按请求标识查询处理结果；确认前请勿重复提交相同明细。
              </p>
              <p v-if="requestStatus?.order_no" class="requisition__alert-text">
                领用单号 {{ requestStatus.order_no }} · 状态 {{
                  STATUS_NAMES[requestStatus.status] ?? requestStatus.status
                }}
              </p>
              <p v-if="pendingRequestId || errorRequestId" class="requisition__alert-text">
                请求标识
                <code class="requisition__request-id" tabindex="0">
                  {{ pendingRequestId || errorRequestId }}
                </code>
                <el-button link type="primary" @click="copyCurrentRequest">复制请求编号</el-button>
              </p>
              <el-button
                link
                type="primary"
                :loading="checkingRequest"
                @click="checkPendingRequest()"
              >
                查询提交结果
              </el-button>
              <el-button link type="primary" @click="goRecords">去记录查询</el-button>
            </template>
          </el-alert>

          <el-alert
            v-if="submitError && !uncertain"
            type="error"
            :closable="false"
            show-icon
            :title="submitError"
            class="requisition__alert"
            role="alert"
          >
            <template #default>
              <p v-if="errorRequestId" class="requisition__alert-text">
                请求编号
                <code class="requisition__request-id" tabindex="0">{{ errorRequestId }}</code>
                <el-button link type="primary" @click="copyCurrentRequest">复制请求编号</el-button>
              </p>
            </template>
          </el-alert>

          <div class="panel requisition__card">
            <h2 class="requisition__card-title">领用信息</h2>
            <div class="requisition__meta">
              <el-form-item
                label="领用人"
                class="requisition__meta-item"
                :error="metaErrors.user"
                :required="!isStudent"
              >
                <el-input
                  v-if="isStudent"
                  :model-value="`本人（${userStore.user?.name ?? ''}）`"
                  disabled
                  aria-label="领用人"
                />
                <el-select
                  v-else
                  ref="userSelectRef"
                  v-model="form.userId"
                  filterable
                  :loading="userLoading"
                  :disabled="submitting"
                  placeholder="选择领用人"
                  class="requisition__user-select"
                  aria-label="领用人"
                  @change="handleUserChange"
                >
                  <el-option
                    v-for="option in userOptions"
                    :key="option.id"
                    :label="`${option.name}（${ROLE_NAMES[option.role] ?? option.role}·${option.account}）`"
                    :value="option.id"
                  />
                </el-select>
                <p v-if="!isStudent && userLoadError" class="requisition__hint requisition__hint--error">
                  {{ userLoadError }}
                  <el-button link type="primary" @click="fetchUserOptions">重试</el-button>
                </p>
              </el-form-item>

              <el-form-item
                label="领用时间"
                class="requisition__meta-item"
                :error="metaErrors.time"
              >
                <div class="requisition__time">
                  <el-radio-group v-model="form.timeMode" aria-label="领用时间方式" :disabled="submitting" @change="handleTimeModeChange">
                    <el-radio-button value="now">现在</el-radio-button>
                    <el-radio-button value="backfill">补录</el-radio-button>
                  </el-radio-group>
                  <el-date-picker
                    v-if="form.timeMode === 'backfill'"
                    ref="timePickerRef"
                    v-model="form.backfillAt"
                    type="datetime"
                    format="YYYY-MM-DD HH:mm"
                    value-format="YYYY-MM-DD[T]HH:mm:00"
                    :disabled-date="disabledBackfillDate"
                    :disabled="submitting"
                    placeholder="选择补录时间"
                    class="requisition__time-picker"
                    aria-label="补录时间"
                    @change="metaErrors.time = ''"
                  />
                </div>
                <p v-if="form.timeMode === 'backfill'" class="requisition__hint">
                  可选最近 {{ MAX_BACKFILL_DAYS }} 天内，超期需受控调整
                </p>
              </el-form-item>

              <el-form-item
                v-if="form.timeMode === 'backfill'"
                label="补录原因"
                class="requisition__meta-item requisition__meta-item--full"
                :error="metaErrors.reason"
              >
                <el-input
                  ref="reasonInputRef"
                  v-model="form.backfillReason"
                  type="textarea"
                  :rows="2"
                  maxlength="500"
                  show-word-limit
                  :disabled="submitting"
                  placeholder="说明漏登原因，便于追溯"
                  class="requisition__backfill-reason"
                />
              </el-form-item>
            </div>
          </div>

          <div class="panel requisition__card">
            <div class="requisition__card-head">
              <h2 class="requisition__card-title">领用明细</h2>
              <span class="requisition__card-meta">已添加 {{ rows.length }}／{{ MAX_ROWS }} 种</span>
            </div>

            <div class="requisition__rows">
              <div
                v-for="(row, index) in rows"
                :key="row.key"
                :ref="(el) => setRowDom(el, index)"
                class="requisition__row"
                :class="{ 'requisition__row--error': rowErrors[index] }"
                role="group"
                :aria-label="`第 ${index + 1} 行明细`"
                tabindex="-1"
              >
                <div class="requisition__row-head">
                  <span class="requisition__row-index" aria-hidden="true">{{ index + 1 }}</span>
                  <span v-if="rowItem(row)" class="requisition__row-stock">
                    当前库存 {{ rowStockText(row) }}
                  </span>
                  <el-button
                    class="requisition__remove"
                    :disabled="submitting || rows.length <= 1"
                    :aria-label="`移除第 ${index + 1} 行`"
                    @click="removeRow(index)"
                  >
                    <el-icon :size="14"><Close /></el-icon>
                  </el-button>
                </div>

                <div class="requisition__row-fields">
                  <el-select
                    :ref="(el) => setSelectRef(el, index)"
                    v-model="row.consumableId"
                    filterable
                    placeholder="选择耗材"
                    :loading="consumableLoading"
                    :disabled="submitting"
                    class="requisition__consumable"
                    :aria-label="`第 ${index + 1} 行耗材`"
                    :aria-describedby="rowErrors[index] ? `requisition-row-error-${index}` : undefined"
                    @change="handleRowConsumableChange(index)"
                  >
                    <el-option
                      v-for="item in consumables"
                      :key="item.id"
                      :label="optionLabel(item, index)"
                      :value="item.id"
                      :disabled="isOptionDisabled(item, index)"
                    />
                  </el-select>

                  <div class="requisition__quantity">
                    <span class="requisition__quantity-label">数量</span>
                    <el-input-number
                      :ref="(el) => setQtyRef(el, index)"
                      v-model="row.quantity"
                      :min="1"
                      :step="1"
                      step-strictly
                      :disabled="submitting || !row.consumableId"
                      class="requisition__quantity-input"
                      :aria-label="`第 ${index + 1} 行数量`"
                      @change="handleQuantityChange(index)"
                    />
                    <span class="requisition__quantity-unit">{{ rowUnit(row) }}</span>
                  </div>
                </div>

                <p
                  v-if="rowErrors[index]"
                  :id="`requisition-row-error-${index}`"
                  class="requisition__row-error"
                  role="alert"
                >
                  {{ rowErrors[index] }}
                </p>
                <p v-else-if="quantityOverStock(row)" class="requisition__row-warning">
                  超出可用量（{{ rowStockText(row) }}）
                  <el-button link type="primary" @click="refreshAvailableStock(index)">刷新可用量</el-button>
                </p>
              </div>
            </div>

            <div class="requisition__rows-actions">
              <el-button ref="addButtonRef" :disabled="submitting || rows.length >= MAX_ROWS" @click="addRow">
                添加明细
              </el-button>
              <el-button v-if="recentItems.length" text :disabled="submitting || locked" @click="applyRecent">
                复用最近明细
              </el-button>
              <span v-if="rows.length >= MAX_ROWS" class="requisition__rows-limit">
                每次最多登记 {{ MAX_ROWS }} 种耗材
              </span>
            </div>
          </div>

          <div class="panel requisition__card">
            <button
              v-if="breaktimeMode"
              type="button"
              class="requisition__quiet-toggle"
              :aria-expanded="showCompactRemark ? 'true' : 'false'"
              @click="showCompactRemark = !showCompactRemark"
            >
              {{ showCompactRemark ? '收起备注' : '添加备注' }}
            </button>
            <el-form-item
              v-show="!breaktimeMode || showCompactRemark"
              label="备注"
              class="requisition__remark"
            >
              <el-input
                v-model="form.remark"
                type="textarea"
                :rows="2"
                maxlength="255"
                show-word-limit
                placeholder="选填，例如实训课程或用途"
                aria-label="备注"
                :disabled="submitting"
              />
            </el-form-item>
          </div>
        </el-form>

        <aside class="requisition__aside" aria-label="领用摘要">
          <div class="panel requisition__summary">
            <p class="requisition__summary-label">合计数量</p>
            <p class="requisition__summary-value">{{ totalQuantity }}</p>
            <dl class="requisition__summary-list">
              <div class="requisition__summary-item">
                <dt>明细</dt>
                <dd>{{ rows.length }} 种</dd>
              </div>
            </dl>
            <div class="requisition__summary-actions">
              <el-button
                type="primary"
                :loading="submitting"
                :disabled="submitting || uncertain || locked"
                @click="handleSubmit"
              >
                提交领用
              </el-button>
              <el-button :disabled="submitting || locked" @click="handleReset">重置</el-button>
            </div>
          </div>
        </aside>
      </div>
    </section>
  </div>
</template>

<style scoped>
.requisition__shell {
  width: 100%;
}

.requisition__state {
  padding: var(--space-5);
}

.requisition__state-text {
  margin: 0 0 var(--space-3);
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.requisition__grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(268px, 1fr);
  gap: var(--space-5);
  align-items: start;
}

.requisition__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.requisition__card {
  padding: var(--space-4) var(--space-5);
}

.requisition__card-title {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text);
}

.requisition__card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: var(--space-3);
}

.requisition__card-head .requisition__card-title {
  margin-bottom: 0;
}

.requisition__card > .requisition__card-title {
  margin-bottom: var(--space-3);
}

.requisition__card-meta {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__card :deep(.el-form-item) {
  margin-bottom: var(--space-3);
}

.requisition__card :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.requisition__alert {
  margin-bottom: 0;
}

.requisition__alert-text {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 22px;
}

.requisition__request-id {
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--surface-muted);
  color: var(--text);
  font-size: 13px;
  line-height: 20px;
  word-break: break-all;
  user-select: all;
}

.requisition__meta {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0 var(--space-5);
}

.requisition__meta-item {
  min-width: 0;
}

.requisition__meta-item--full {
  grid-column: 1 / -1;
}

.requisition__user-select,
.requisition__backfill-reason {
  width: 100%;
}

.requisition__time {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.requisition__time-picker {
  flex: 1;
  min-width: 180px;
}

.requisition__hint {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__hint--error {
  color: var(--danger);
}

.requisition__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.requisition__row {
  padding: var(--space-3);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.requisition__row:focus {
  outline: none;
}

.requisition__row--error {
  border-color: var(--danger-line);
  background: var(--danger-soft);
}

.requisition__row-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.requisition__row-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-xs);
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.requisition__row-stock {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__remove {
  margin-left: auto;
  width: 28px;
  height: 28px;
  padding: 0;
  border-color: transparent;
  background: transparent;
  color: var(--text-muted);
}

.requisition__remove:hover:not(.is-disabled) {
  border-color: var(--danger-line);
  background: var(--danger-soft);
  color: var(--danger);
}

.requisition__row-fields {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.requisition__consumable {
  flex: 1 1 240px;
  min-width: 0;
}

.requisition__quantity {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
}

.requisition__quantity-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__quantity-input {
  width: 108px;
}

.requisition__quantity-unit {
  min-width: 24px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__row-error,
.requisition__row-warning {
  margin: var(--space-2) 0 0;
  font-size: 13px;
  line-height: 20px;
}

.requisition__row-error {
  color: var(--danger);
}

.requisition__row-warning {
  color: var(--warning);
}

.requisition__rows-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.requisition__rows-limit {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__remark :deep(.el-form-item__label) {
  padding-bottom: 4px;
}

.requisition__aside {
  position: sticky;
  top: calc(var(--topbar-height) + var(--space-4));
  min-width: 0;
}

.requisition__summary {
  display: flex;
  flex-direction: column;
  padding: var(--space-4);
}

.requisition__summary-label {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}

.requisition__summary-value {
  margin: 4px 0 0;
  font-size: 30px;
  line-height: 38px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.requisition__summary-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: var(--space-3) 0 0;
  border-top: 1px solid var(--line-soft);
}

.requisition__summary-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.requisition__summary-item dt {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__summary-item dd {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.requisition__summary-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.requisition__summary-actions .el-button {
  width: 100%;
  margin-left: 0;
}

.requisition__receipt {
  padding: var(--space-5);
}

.requisition__receipt:focus {
  outline: none;
}

.requisition__receipt-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.requisition__receipt-title {
  margin: 0 0 4px;
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
  color: var(--text);
}

.requisition__receipt-desc {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__receipt-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0 0 var(--space-5);
  padding: 0;
  list-style: none;
}

.requisition__receipt-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 24px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  font-size: 14px;
  line-height: 22px;
}

.requisition__receipt-name {
  min-width: 200px;
  color: var(--text);
  font-weight: 600;
}

.requisition__receipt-qty,
.requisition__receipt-stock {
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.requisition__receipt-stock {
  margin-left: auto;
}

.requisition__receipt-event {
  flex-basis: 100%;
  font-size: 13px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__receipt-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.requisition__mode-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
}

.requisition__mode-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.requisition__mode-title {
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
}

.requisition__mode-state {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.requisition__mode-toggle {
  flex: 0 0 auto;
  min-height: 44px;
  margin-left: 0;
}

.requisition__recent {
  margin-bottom: var(--space-4);
}

.requisition__quick {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
}

.requisition__quick-title {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
}

.requisition__quick-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.requisition__quick-chip {
  display: flex;
  flex: 1 1 140px;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.requisition__quick-chip:hover:not(:disabled) {
  border-color: var(--accent);
}

.requisition__quick-chip:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.requisition__quick-name {
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  word-break: break-word;
}

.requisition__quick-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__quiet-toggle {
  min-height: 44px;
  padding: 4px 0;
  border: none;
  background: transparent;
  color: var(--accent);
  font: inherit;
  font-size: 13px;
  line-height: 20px;
  cursor: pointer;
}

@media (max-width: 767px) {
  .requisition__mode-bar {
    padding: var(--space-3);
  }

  .requisition__quick {
    padding: var(--space-3);
  }

  .requisition__quick-chip {
    flex: 1 1 calc(50% - var(--space-2));
  }
}

@media (max-width: 1023px) {
  .requisition__grid {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
  }

  .requisition__aside {
    position: static;
  }

  .requisition__summary {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
  }

  .requisition__summary-value {
    margin: 0;
    font-size: 24px;
    line-height: 30px;
  }

  .requisition__summary-list {
    flex: 1 1 auto;
    margin: 0;
    padding: 0;
    border-top: none;
  }

  .requisition__summary-actions {
    flex: 0 0 auto;
    flex-direction: row;
    margin-top: 0;
  }

  .requisition__summary-actions .el-button {
    width: auto;
  }
}

@media (max-width: 767px) {
  .requisition__card {
    padding: var(--space-4);
  }

  .requisition__meta {
    grid-template-columns: minmax(0, 1fr);
  }

  .requisition__row-fields {
    gap: var(--space-2);
  }

  .requisition__consumable {
    flex: 1 1 100%;
  }

  .requisition__remove {
    width: 44px;
    height: 44px;
  }

  .requisition__row-head {
    margin-bottom: var(--space-1);
  }

  .requisition__aside {
    position: sticky;
    bottom: calc(var(--keyboard-inset, 0px) + 4px);
    z-index: 6;
  }

  .requisition__summary {
    gap: var(--space-3);
    padding: var(--space-3);
    box-shadow: var(--shadow-md);
  }

  .requisition__summary-list {
    display: none;
  }

  .requisition__summary-value {
    font-size: 22px;
    line-height: 28px;
  }

  .requisition__summary-actions {
    flex: 1 1 160px;
  }

  .requisition__summary-actions .el-button {
    flex: 1 1 0;
    min-height: 44px;
    margin-left: 0;
  }

  .requisition__receipt {
    padding: var(--space-4);
  }

  .requisition__receipt-stock {
    margin-left: 0;
  }

  .requisition__receipt-actions .el-button {
    flex: 1 1 0;
    min-height: 44px;
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .requisition__summary {
    gap: var(--space-2);
  }

  .requisition__summary-label {
    flex: 1 1 100%;
  }

  .requisition__quantity {
    flex: 1 1 100%;
  }

  .requisition__quantity-input {
    flex: 1;
    width: auto;
  }
}
</style>