<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import { businessDate, businessNowInput, businessToday } from '../utils/businessDate'

const MAX_ROWS = 5
const MAX_BACKFILL_DAYS = 30
const DRAFT_TTL = 30 * 60 * 1000

const draftOwnerId = ref(null)
const draftNotice = ref('')
const recentItems = ref([])
let draftReady = false
let draftTimer = null

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const isStudent = computed(() => userStore.user?.role === 'student')
const isAdmin = computed(() => userStore.user?.role === 'admin')

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
const bypassLeaveGuard = ref(false)

const form = reactive({
  userId: null,
  timeMode: 'now',
  backfillAt: '',
  remark: '',
})

const metaErrors = reactive({ user: '', time: '' })
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
    return `领用时间不能早于 ${MAX_BACKFILL_DAYS} 天前`
  }
  return ''
}

function validateMeta() {
  metaErrors.user = ''
  if (isStudent.value) {
    if (!userStore.user?.id) metaErrors.user = '当前账号信息不可用，请重新登录'
  } else if (!form.userId) {
    metaErrors.user = '请选择领用人'
  }
  metaErrors.time = form.timeMode === 'backfill' ? validateBackfillTime() : ''
  if (metaErrors.user) {
    focusUser()
    return false
  }
  if (metaErrors.time) {
    focusTime()
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
    form.timeMode === 'backfill' ||
    form.userId !== (userStore.user?.id ?? null)
  )
}

function resetFormState() {
  rows.value = [createRow()]
  clearRowErrors()
  metaErrors.user = ''
  metaErrors.time = ''
  form.userId = userStore.user?.id ?? null
  form.timeMode = 'now'
  form.backfillAt = ''
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
    const { data } = await api.get('/users/options')
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

function snapshotText(index) {
  const row = result.value?.rows?.[index]
  if (!row) return ''
  return row.spec ? `（${row.spec}）` : ''
}

function snapshotUnit(index) {
  return result.value?.rows?.[index]?.unit ?? ''
}

function snapshotName(index, fallback) {
  return result.value?.rows?.[index]?.name || fallback
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
    const matched = detail.match(/第\s*(\d+)\s*行\s*[：:]\s*(.*)$/)
    let rowLocated = false
    if (matched) {
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
    return
  }
  result.value = null
  submitError.value = ''
  submitting.value = true
  try {
    const [freshConsumables, freshUsers] = await Promise.all([
      fetchConsumables(),
      fetchUserOptions(),
    ])
    if (!freshConsumables || !freshUsers) {
      refreshWarning.value = '未能刷新最新库存或领用人，将由服务端在提交时再次校验。'
    }
    if (!validateMeta()) return
    if (!validateRows()) return
    if (locked.value) {
      submitError.value = '当前页面已失效，请重新登录后再提交。'
      return
    }
    const snapshot = buildSnapshot()
    const payload = {
      items: rows.value.map((row) => ({
        consumable_id: row.consumableId,
        quantity: row.quantity,
      })),
      remark: form.remark.trim() || null,
    }
    if (!isStudent.value) payload.user_id = form.userId
    if (form.timeMode === 'backfill') payload.created_at = form.backfillAt

    const { data } = await api.post('/requisitions', payload)
    result.value = { count: data.count, items: data.items, rows: snapshot }
    rememberRecent(snapshot)
    removeDraft()
    refreshWarning.value = ''
    const [consumablesOk, usersOk] = await Promise.all([fetchConsumables(), fetchUserOptions()])
    if (!consumablesOk || !usersOk) {
      refreshWarning.value = '已登记成功，但暂时无法刷新耗材可用量或可选领用人，请稍后在库存总览核对最新数值。'
    }
    clearRowErrors()
  } catch (error) {
    await handleSubmitError(error)
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
}

function handleContinue() {
  result.value = null
  refreshWarning.value = ''
  resetFormState()
  removeDraft()
  router.replace({ query: {} })
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
    remark: form.remark,
  }),
  scheduleDraftSave,
  { deep: true },
)

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  draftOwnerId.value = userStore.user?.id ?? null
  form.userId = userStore.user?.id ?? null
  loadRecent()

  const [consumablesOk] = await Promise.all([fetchConsumables(), fetchUserOptions()])
  if (!route.query.consumable_id) restoreDraft()
  if (consumablesOk) await applyPrefill()
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

    <section v-if="result" class="panel requisition__panel" role="status">
      <div class="requisition__receipt-head">
        <div>
          <h2 class="requisition__receipt-title">已登记 {{ result.count }} 项耗材</h2>
          <p class="requisition__receipt-desc">本次领用已扣减库存并生成流水，回执中的库存为服务端扣减后数值。</p>
        </div>
        <span class="status-tag status-tag--success">提交成功</span>
      </div>

      <ul class="requisition__receipt-list">
        <li
          v-for="(item, index) in result.items"
          :key="`${item.consumable_name}-${index}`"
          class="requisition__receipt-row"
        >
          <span class="requisition__receipt-name">
            {{ snapshotName(index, item.consumable_name) }}{{ snapshotText(index) }}
          </span>
          <span class="requisition__receipt-qty">
            领用 {{ item.quantity }}{{ snapshotUnit(index) ? ` ${snapshotUnit(index)}` : '' }}
          </span>
          <span class="requisition__receipt-stock">
            扣减后库存 {{ item.stock_after }}{{ snapshotUnit(index) ? ` ${snapshotUnit(index)}` : '' }}
          </span>
        </li>
      </ul>

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

    <section v-else class="panel requisition__panel" :aria-busy="consumableLoading || submitting">
      <div v-if="consumableLoading && !consumables.length" class="requisition__loading">
        <el-skeleton :rows="5" animated />
      </div>

      <div v-else-if="consumableError && !consumables.length" class="requisition__block" role="alert">
        <p class="requisition__block-text">{{ consumableError }}</p>
        <el-button type="primary" plain @click="fetchConsumables">重试</el-button>
      </div>

      <EmptyState
        v-else-if="!consumables.length"
        title="暂无可领用耗材"
        :description="isAdmin ? '先添加至少一项启用耗材，再回来登记领用。' : '当前没有启用的耗材，请联系班委建档。'"
      >
        <template #action>
          <el-button v-if="isAdmin" type="primary" @click="goConsumables">去建档</el-button>
        </template>
      </EmptyState>

      <template v-else>
        <el-form
          label-position="top"
          class="requisition__form"
          :model="form"
          @submit.prevent="handleSubmit"
        >
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
                请求超时或连接中断，请按领用人、耗材与时间到记录查询核对；确认前请勿重复提交相同明细。
              </p>
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
          />

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
                  :label="`${option.name}（${ROLE_NAMES[option.role] ?? option.role}）`"
                  :value="option.id"
                />
              </el-select>
              <p v-if="!isStudent && userLoadError" class="requisition__hint requisition__hint--error">
                {{ userLoadError }}
                <el-button link type="primary" @click="fetchUserOptions">重试</el-button>
              </p>
              <p v-else-if="!isStudent" class="requisition__hint">可搜索姓名，为学生代登记</p>
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
              <p class="requisition__hint">
                {{ form.timeMode === 'now' ? '提交时使用服务端当前时间' : `可选最近 ${MAX_BACKFILL_DAYS} 天内，精确到分钟` }}
              </p>
            </el-form-item>
          </div>

          <div class="requisition__rows-head">
            <h3 class="requisition__rows-title">领用明细</h3>
            <span class="requisition__rows-count">已添加 {{ rows.length }}／{{ MAX_ROWS }} 种</span>
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
                <span class="requisition__row-index">明细 {{ index + 1 }}</span>
                <span v-if="rowItem(row)" class="requisition__row-stock">
                  当前库存 {{ rowStockText(row) }}
                </span>
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

                <el-button
                  class="requisition__remove"
                  :disabled="submitting || rows.length <= 1"
                  :aria-label="`移除第 ${index + 1} 行`"
                  @click="removeRow(index)"
                >
                  移除
                </el-button>
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
                数量超出当前可用量（{{ rowStockText(row) }}），提交前建议
                <el-button link type="primary" @click="refreshAvailableStock(index)">刷新可用量</el-button>
              </p>
              <p v-else-if="rowItem(row)" class="requisition__row-hint">
                可用 {{ rowStockText(row) }}
              </p>
            </div>
          </div>

          <div class="requisition__rows-actions">
            <el-button ref="addButtonRef" :disabled="submitting || rows.length >= MAX_ROWS" @click="addRow">
              添加明细
            </el-button>
            <el-button v-if="recentItems.length" :disabled="submitting || locked" @click="applyRecent">
              复用最近明细
            </el-button>
            <span v-if="rows.length >= MAX_ROWS" class="requisition__rows-limit">
              每次最多登记 {{ MAX_ROWS }} 种耗材
            </span>
          </div>

          <el-form-item label="备注" class="requisition__remark">
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

          <div class="requisition__actions">
            <el-button :disabled="submitting || locked" @click="handleReset">重置</el-button>
            <el-button
              type="primary"
              native-type="submit"
              :loading="submitting"
              :disabled="submitting || uncertain || locked"
            >
              提交领用
            </el-button>
          </div>
        </el-form>
      </template>
    </section>
  </div>
</template>

<style scoped>
.requisition__panel {
  padding: 24px;
  max-width: var(--content-max);
}

.requisition__loading {
  padding: 8px 0;
}

.requisition__block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.requisition__block-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.requisition__alert {
  margin-bottom: 16px;
}

.requisition__alert-text {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 22px;
}

.requisition__meta {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px 24px;
  margin-bottom: 8px;
}

.requisition__meta-item {
  min-width: 0;
}

.requisition__user-select {
  width: 100%;
}

.requisition__time {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
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

.requisition__rows-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin: 8px 0 12px;
}

.requisition__rows-title {
  margin: 0;
}

.requisition__rows-count {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.requisition__row {
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface);

}

.requisition__row--error {
  border-color: var(--danger);
  background: var(--danger-soft);
}

.requisition__row-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.requisition__row-index {
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text-secondary);
}

.requisition__row-stock {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.requisition__row-fields {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.requisition__consumable {
  flex: 1;
  min-width: 240px;
}

.requisition__quantity {
  display: flex;
  align-items: center;
  gap: 8px;
}

.requisition__quantity-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__quantity-input {
  width: 120px;
}

.requisition__quantity-unit {
  min-width: 32px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__remove {
  margin-left: auto;
}

.requisition__row-error,
.requisition__row-warning,
.requisition__row-hint {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
}

.requisition__row-error {
  color: var(--danger);
}

.requisition__row-warning {
  color: var(--warning);
}

.requisition__row-hint {
  color: var(--text-muted);
}

.requisition__rows-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 12px 0 16px;
}

.requisition__rows-limit {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.requisition__remark {
  margin-bottom: 8px;
}

.requisition__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
}

.requisition__receipt-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.requisition__receipt-title {
  margin: 0 0 4px;
}

.requisition__receipt-desc {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.requisition__receipt-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0 0 24px;
  padding: 0;
  list-style: none;
}

.requisition__receipt-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 24px;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
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

.requisition__receipt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .requisition__panel {
    padding: 16px;
  }

  .requisition__meta {
    grid-template-columns: minmax(0, 1fr);
  }

  .requisition__consumable {
    min-width: 100%;
  }

  .requisition__remove {
    margin-left: 0;
  }

  .requisition__receipt-stock {
    margin-left: 0;
  }
}
</style>