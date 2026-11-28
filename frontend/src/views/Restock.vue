<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import { businessToday } from '../utils/businessDate'
import { announce } from '../utils/announcer'
import { copyRequestId, recordRequestId, requestIdOf } from '../utils/requestTrace'
import { keepFocusedFieldVisible } from '../utils/mobileFocus'

const TIER_META = {
  urgent: { label: '紧急', tagClass: 'status-tag--danger' },
  moderate: { label: '一般', tagClass: 'status-tag--warning' },
  observe: { label: '观察', tagClass: 'status-tag--info' },
}

const TIER_ORDER = { urgent: 0, moderate: 1, observe: 2 }

const DISPOSITION_META = {
  pending: { label: '待跟进', tagClass: 'status-tag--warning' },
  awaiting: { label: '待到货', tagClass: 'status-tag--info' },
  partial: { label: '部分到货', tagClass: 'status-tag--warning' },
  resolved: { label: '风险解除', tagClass: 'status-tag--success' },
}

const RATIONALE_SOURCE_META = {
  llm: { label: '模型润色', tagClass: 'status-tag--info' },
  rules: { label: '规则模板', tagClass: 'status-tag--success' },
}

const TIER_FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'urgent', label: '紧急' },
  { value: 'moderate', label: '一般' },
  { value: 'observe', label: '观察' },
]

const FEEDBACK_REASON_OPTIONS = [
  { value: 'substitute', label: '已有替代耗材' },
  { value: 'schedule_adjust', label: '课时调整' },
  { value: 'low_usage', label: '预计用量低于规则估计' },
  { value: 'other', label: '其他' },
]

const FEEDBACK_REASON_META = Object.fromEntries(
  FEEDBACK_REASON_OPTIONS.map((option) => [option.value, option.label]),
)

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const isAdmin = computed(() => ['super_admin', 'admin'].includes(userStore.user?.role))
const canFeedback = computed(
  () => userStore.user?.role === 'teacher' || ['super_admin', 'admin'].includes(userStore.user?.role),
)

const items = ref([])
const ruleSpec = ref(null)
const generatedAt = ref('')
const loading = ref(false)
const requestError = ref('')
const staleError = ref(false)
const handledAll = ref(false)
const tierFilter = ref('all')
const quantityRef = ref(null)
const focusId = ref(0)
const hasFetched = ref(false)
const restockTrigger = ref(null)
const feedbackTrigger = ref(null)
const errorRequestId = ref('')

const restockDialog = reactive({
  visible: false,
  item: null,
  quantity: null,
  receivedDate: businessToday(),
  source: '',
  remark: '',
  requestId: '',
  lastSignature: '',
  submitting: false,
  fieldError: '',
  errorKind: '',
  errorMessage: '',
})

const receiptDialog = reactive({
  visible: false,
  loading: false,
  error: '',
  data: null,
})

const feedbackDialog = reactive({
  visible: false,
  item: null,
  reason: '',
  note: '',
  submitting: false,
  reasonError: '',
  noteError: '',
  errorMessage: '',
})

let requestSeq = 0
let active = true

const counts = computed(() => {
  const result = { all: items.value.length, urgent: 0, moderate: 0, observe: 0 }
  for (const item of items.value) {
    if (result[item.tier] !== undefined) result[item.tier] += 1
  }
  return result
})

const sortedItems = computed(() =>
  items.value
    .map((item, index) => ({ item, index }))
    .sort(
      (left, right) =>
        (TIER_ORDER[left.item.tier] ?? 9) - (TIER_ORDER[right.item.tier] ?? 9) ||
        left.index - right.index,
    )
    .map((entry) => entry.item),
)

const visibleItems = computed(() =>
  tierFilter.value === 'all'
    ? sortedItems.value
    : sortedItems.value.filter((item) => item.tier === tierFilter.value),
)

const currentTierLabel = computed(
  () => TIER_FILTERS.find((option) => option.value === tierFilter.value)?.label ?? '',
)

const focusedMissing = computed(
  () =>
    Boolean(focusId.value) &&
    hasFetched.value &&
    !items.value.some((item) => item.consumable_id === focusId.value),
)

const generatedAtText = computed(() => formatGeneratedAt(generatedAt.value))

const emptyTitle = computed(() => {
  if (requestError.value) return '巡检暂时不可用'
  if (handledAll.value) return '本次建议已全部处置'
  return '本次巡检暂无补货建议'
})

const emptyDescription = computed(() => {
  if (requestError.value) return requestError.value
  if (handledAll.value) return '所有建议均已完成入库。'
  return '当前没有触发补货规则的耗材。'
})

const expectedStock = computed(() => {
  const item = restockDialog.item
  const quantity = Number(restockDialog.quantity)
  if (!item || !Number.isInteger(quantity) || quantity <= 0) return null
  return item.current_stock + quantity
})

function formatGeneratedAt(value) {
  if (!value) return '—'
  const text = String(value).replace('T', ' ')
  const day = text.slice(0, 10)
  const time = text.slice(11, 16)
  if (!day || !time) return text.slice(0, 16)
  return day === businessToday() ? time : `${day.slice(5)} ${time}`
}

function formatDecimal(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return String(Math.round(number * 100) / 100)
}

function formatRate(item) {
  return `${formatDecimal(item.rate)}${item.unit}/天`
}

function formatDays(value) {
  if (value === null || value === undefined) return '—'
  return `${formatDecimal(value)} 天`
}

function consumableLabel(item) {
  return item.spec ? `${item.name}（${item.spec}）` : item.name
}

async function fetchSuggestions(force = false) {
  if (loading.value) return
  const seq = ++requestSeq
  loading.value = true
  requestError.value = ''
  staleError.value = false
  try {
    const { data } = force
      ? await api.post('/inventory/suggestions/refresh')
      : await api.get('/inventory/suggestions')
    if (seq !== requestSeq || !active) return
    items.value = Array.isArray(data.items) ? data.items : []
    ruleSpec.value = data.rules ?? null
    generatedAt.value = data.generated_at ?? ''
    handledAll.value = false
    if (force) ElMessage.success('已完成库存巡检')
  } catch {
    if (seq !== requestSeq || !active) return
    if (items.value.length) {
      staleError.value = true
    } else {
      requestError.value = '库存巡检暂时不可用，请稍后重试。'
    }
  } finally {
    if (seq === requestSeq) {
      loading.value = false
      hasFetched.value = true
    }
  }
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll('-', '')
  }
  return `rst-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

function feedbackReasonLabel(reason) {
  return FEEDBACK_REASON_META[reason] ?? reason ?? ''
}

function openFeedback(item, event) {
  feedbackTrigger.value = event?.currentTarget ?? null
  feedbackDialog.item = item
  feedbackDialog.reason = item.latest_feedback?.reason ?? ''
  feedbackDialog.note = item.latest_feedback?.note ?? ''
  feedbackDialog.reasonError = ''
  feedbackDialog.noteError = ''
  feedbackDialog.errorMessage = ''
  feedbackDialog.submitting = false
  feedbackDialog.visible = true
}

function handleFeedbackClose(done) {
  if (feedbackDialog.submitting) return
  done()
}

function handleFeedbackClosed() {
  nextTick(() => {
    const trigger = feedbackTrigger.value
    if (trigger?.isConnected) {
      trigger.focus()
      return
    }
    document.querySelector('.restock__filters button')?.focus?.()
  })
}

async function submitFeedback() {
  if (feedbackDialog.submitting) return
  const item = feedbackDialog.item
  if (!item) return
  const reason = feedbackDialog.reason
  const note = feedbackDialog.note.trim()
  feedbackDialog.reasonError = reason ? '' : '请选择拒绝原因'
  feedbackDialog.noteError =
    reason === 'other' && !note ? '选择“其他”时必须填写备注' : ''
  feedbackDialog.errorMessage = ''
  if (feedbackDialog.reasonError || feedbackDialog.noteError) {
    announce(feedbackDialog.reasonError || feedbackDialog.noteError, {
      assertive: true,
    })
    return
  }
  feedbackDialog.submitting = true
  try {
    const { data } = await api.post(
      `/inventory/suggestions/${item.consumable_id}/feedback`,
      { reason, note: note || null },
    )
    feedbackDialog.visible = false
    item.latest_feedback = {
      reason: data.reason,
      note: data.note,
      feedback_name: data.feedback_name,
      created_at: data.created_at,
    }
    ElMessage.success('已记录拒绝原因')
    announce('已记录拒绝原因')
  } catch (error) {
    feedbackDialog.errorMessage =
      error.response?.data?.detail || '保存失败，请稍后重试。'
  } finally {
    feedbackDialog.submitting = false
  }
}

function openRestock(item, event) {
  restockTrigger.value = event?.currentTarget ?? null
  restockDialog.item = item
  restockDialog.quantity = item.recommended_quantity > 0 ? item.recommended_quantity : null
  restockDialog.receivedDate = businessToday()
  restockDialog.source = '建议补货'
  restockDialog.remark = ''
  restockDialog.requestId = newRequestId()
  restockDialog.lastSignature = ''
  restockDialog.fieldError = ''
  restockDialog.errorKind = ''
  restockDialog.errorMessage = ''
  restockDialog.submitting = false
  errorRequestId.value = ''
  restockDialog.visible = true
}

function handleDialogClose(done) {
  if (restockDialog.submitting) return
  done()
}

function handleRestockClosed() {
  nextTick(() => {
    const trigger = restockTrigger.value
    if (trigger?.isConnected) {
      trigger.focus()
      return
    }
    document.querySelector('.restock__filters button')?.focus?.()
  })
}

async function copyRestockRequest() {
  const value = errorRequestId.value || restockDialog.requestId
  if (!value) return
  const ok = await copyRequestId(value)
  if (ok) ElMessage.success('请求编号已复制')
  else ElMessage.error('复制失败，请手动选择请求编号')
}

function clearQuantityError() {
  if (restockDialog.fieldError) restockDialog.fieldError = ''
}

async function submitRestock() {
  if (restockDialog.submitting) return
  const item = restockDialog.item
  if (!item) return
  const quantity = Number(restockDialog.quantity)
  if (!Number.isInteger(quantity) || quantity <= 0) {
    restockDialog.fieldError = '请输入正整数入库数量'
    quantityRef.value?.focus?.()
    announce(restockDialog.fieldError, { assertive: true })
    return
  }
  restockDialog.fieldError = ''
  restockDialog.errorKind = ''
  restockDialog.errorMessage = ''
  restockDialog.submitting = true
  const signature = JSON.stringify([
    item.consumable_id,
    quantity,
    restockDialog.receivedDate,
    restockDialog.source.trim(),
    restockDialog.remark.trim(),
  ])
  if (signature !== restockDialog.lastSignature) {
    restockDialog.requestId = newRequestId()
    restockDialog.lastSignature = signature
  }
  try {
    const { data } = await api.post('/inventory/restock/orders', {
      items: [{ consumable_id: item.consumable_id, quantity }],
      received_at: restockDialog.receivedDate
        ? `${restockDialog.receivedDate}T00:00:00`
        : null,
      source: restockDialog.source.trim() || null,
      remark: restockDialog.remark.trim() || null,
      request_id: restockDialog.requestId,
    })
    restockDialog.visible = false
    const line = Array.isArray(data.lines) ? data.lines[0] : null
    const stockText = line ? `，当前库存 ${line.stock_after}${line.unit}` : ''
    announce(`已生成入库单 ${data.order_no}${stockText}`)
    ElMessage.success(`已生成入库单 ${data.order_no}${stockText}`)
    await fetchSuggestions(true)
  } catch (error) {
    errorRequestId.value = recordRequestId(requestIdOf(error) || restockDialog.requestId)
    if (!error.response) {
      restockDialog.errorKind = 'unconfirmed'
      restockDialog.errorMessage = '网络异常或请求超时，暂未确认本次入库是否成功。'
    } else {
      const status = error.response.status
      if ([400, 404, 409, 422].includes(status)) {
        restockDialog.errorKind = 'rule'
        restockDialog.errorMessage =
          error.response.data?.detail || '该耗材当前无法入库，请刷新建议后重试。'
      } else {
        restockDialog.errorKind = 'unknown'
        restockDialog.errorMessage = '入库失败，请稍后重试。'
      }
    }
    announce(
      `${restockDialog.errorMessage}${
        errorRequestId.value ? `。请求编号 ${errorRequestId.value}` : ''
      }`,
      { assertive: true },
    )
  } finally {
    restockDialog.submitting = false
  }
}

function returnAndRefresh() {
  restockDialog.visible = false
  fetchSuggestions(true)
}

function goLogs() {
  restockDialog.visible = false
  router.push('/logs')
}

function goArchiveItem() {
  if (!focusId.value) return
  router.push({ path: '/consumables', query: { consumable_id: String(focusId.value) } })
}

function goOverview() {
  router.push('/overview')
}

function formatReceiptTime(value) {
  if (!value) return '—'
  const matched = String(value)
    .replace('T', ' ')
    .match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : String(value)
}

function receiptStatusLabel(status) {
  return (
    { submitted: '已提交', processing: '处理中', rejected: '已驳回' }[status] ?? status
  )
}

async function openRouteReceipt() {
  const orderNo = route.query.order_no
  if (!orderNo) return
  receiptDialog.visible = true
  receiptDialog.loading = true
  receiptDialog.error = ''
  try {
    const { data } = await api.get(
      `/inventory/restock/orders/${encodeURIComponent(String(orderNo))}`,
    )
    receiptDialog.data = data
  } catch {
    receiptDialog.data = null
    receiptDialog.error = '入库单读取失败，可能尚未生成回执或已被替代。'
  } finally {
    receiptDialog.loading = false
  }
}

function parseFocusId(value) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0
}

watch(
  () => route.query.consumable_id,
  (value) => {
    focusId.value = parseFocusId(value)
  },
)

watch(visibleItems, async (list) => {
  if (!focusId.value) return
  if (!list.some((item) => item.consumable_id === focusId.value)) return
  await nextTick()
  document
    .getElementById(`restock-suggestion-${focusId.value}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
})

onMounted(() => {
  focusId.value = parseFocusId(route.query.consumable_id)
  fetchSuggestions()
  openRouteReceipt()
})

onBeforeUnmount(() => {
  active = false
  requestSeq += 1
})
</script>

<template>
  <div class="restock">
    <PageHeader title="补货建议" :breadcrumb="['库存分析', '补货建议']">
      <template #actions>
        <span class="restock__patrol">巡检时间 {{ generatedAtText }}</span>
        <el-button :loading="loading" @click="fetchSuggestions(true)">
          {{ loading ? '巡检中…' : '重新巡检' }}
        </el-button>
      </template>
    </PageHeader>

    <div class="restock__filters" role="group" aria-label="按等级筛选补货建议">
      <el-radio-group v-model="tierFilter" class="restock__segmented">
        <el-radio-button
          v-for="option in TIER_FILTERS"
          :key="option.value"
          :value="option.value"
        >
          <span>{{ option.label }}</span>
          <span class="restock__seg-count">{{ counts[option.value] }}</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <div v-if="focusedMissing" class="restock__notice">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="该耗材当前没有补货建议"
        description="库存已补足，或未设安全库存线。"
      />
      <el-button v-if="isAdmin" link type="primary" @click="goArchiveItem">
        查看档案
      </el-button>
      <el-button v-else link type="primary" @click="goOverview">返回库存总览</el-button>
    </div>

    <div v-if="loading && items.length" class="restock__notice">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="正在重新巡检，当前显示上次巡检结果"
      />
    </div>
    <div v-else-if="staleError" class="restock__notice">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="本次巡检失败，显示上次巡检结果"
      />
      <el-button link type="primary" @click="fetchSuggestions(true)">重试</el-button>
    </div>

    <div v-if="!items.length && loading" class="restock__loading" aria-busy="true">
      <p class="restock__loading-text">正在巡检库存…</p>
      <div class="restock__grid">
        <div v-for="index in 3" :key="index" class="panel restock__card">
          <el-skeleton :rows="4" animated />
        </div>
      </div>
    </div>

    <EmptyState
      v-else-if="!items.length"
      :title="emptyTitle"
      :description="emptyDescription"
    >
      <template #action>
        <el-button type="primary" :loading="loading" @click="fetchSuggestions(true)">
          重新巡检
        </el-button>
      </template>
    </EmptyState>

    <EmptyState
      v-else-if="!visibleItems.length"
      :title="`暂无${currentTierLabel}建议`"
      description="该等级当前没有建议。"
    >
      <template #action>
        <el-button @click="tierFilter = 'all'">查看全部</el-button>
      </template>
    </EmptyState>

    <div v-else class="restock__grid" :aria-busy="loading">
      <article
        v-for="item in visibleItems"
        :id="`restock-suggestion-${item.consumable_id}`"
        :key="item.consumable_id"
        class="panel restock__card"
        :class="[
          `restock__card--${item.tier}`,
          { 'restock__card--focus': item.consumable_id === focusId },
        ]"
      >
        <header class="restock__card-head">
          <div class="restock__identity">
            <span class="status-tag" :class="TIER_META[item.tier]?.tagClass">
              {{ TIER_META[item.tier]?.label ?? item.tier }}
            </span>
            <span
              v-if="item.tier === 'observe' && !item.gap"
              class="status-tag status-tag--info"
            >
              用量上升
            </span>
            <h2 class="restock__name">
              {{ item.name }}<span v-if="item.spec" class="restock__spec">（{{ item.spec }}）</span>
            </h2>
          </div>
          <div class="restock__amount-block">
            <span class="restock__amount-label">建议补货</span>
            <span class="restock__amount">
              {{ item.recommended_quantity }}<span class="restock__amount-unit">{{ item.unit }}</span>
            </span>
          </div>
        </header>

        <div
          v-if="item.disposition_status && item.disposition_status !== 'pending'"
          class="restock__disposition"
        >
          <span
            class="status-tag"
            :class="DISPOSITION_META[item.disposition_status]?.tagClass"
          >
            {{ DISPOSITION_META[item.disposition_status]?.label ?? item.disposition_status }}
          </span>
          <span v-if="item.disposition_handler_name">
            处理人 {{ item.disposition_handler_name }}
          </span>
          <span v-if="item.disposition_order_no">入库单 {{ item.disposition_order_no }}</span>
        </div>

        <div v-if="item.latest_feedback" class="restock__disposition">
          <span class="status-tag status-tag--warning">拒绝原因</span>
          <span>{{ feedbackReasonLabel(item.latest_feedback.reason) }}</span>
          <span v-if="item.latest_feedback.note">{{ item.latest_feedback.note }}</span>
          <span>
            {{ item.latest_feedback.feedback_name }} ·
            {{ formatReceiptTime(item.latest_feedback.created_at) }}
          </span>
        </div>

        <dl class="restock__metrics">
          <div class="restock__metric">
            <dt>当前库存</dt>
            <dd>{{ item.current_stock }}{{ item.unit }}</dd>
          </div>
          <div class="restock__metric">
            <dt>安全库存线</dt>
            <dd>{{ item.safety_stock }}{{ item.unit }}</dd>
          </div>
          <div class="restock__metric">
            <dt>库存缺口</dt>
            <dd>{{ item.gap }}{{ item.unit }}</dd>
          </div>
        </dl>

        <div v-if="item.reasons?.length" class="restock__reasons">
          <span v-for="reason in item.reasons" :key="reason" class="restock__reason">
            {{ reason }}
          </span>
        </div>

        <p v-if="item.consumption_note" class="restock__note">
          {{ item.consumption_note }}
        </p>

        <section v-if="item.teaching_context" class="restock__teaching">
          <div class="restock__teaching-head">
            <span class="restock__teaching-title">教学上下文</span>
            <span
              v-if="item.teaching_context.unlinked"
              class="status-tag status-tag--info"
            >
              未关联课次
            </span>
            <span v-else class="status-tag status-tag--info">
              覆盖 {{ item.teaching_context.session_count }} 次课
            </span>
            <span
              v-if="!item.teaching_context.unlinked"
              class="restock__teaching-demand"
            >
              预计需求 {{ item.teaching_context.expected_demand }}{{ item.unit }} · 缺口
              {{ item.teaching_context.expected_gap }}{{ item.unit }}
            </span>
            <span
              v-if="item.teaching_context.consequence_degraded"
              class="status-tag status-tag--warning"
            >
              已降级
            </span>
          </div>
          <ul
            v-if="item.teaching_context.sessions?.length"
            class="restock__teaching-sessions"
          >
            <li
              v-for="entry in item.teaching_context.sessions"
              :key="entry.session_id"
            >
              {{ entry.course_name }} · 第 {{ entry.session_seq }} 次课 ·
              {{ entry.headcount }} 人 · 需 {{ entry.required_quantity }}{{ item.unit }}
            </li>
          </ul>
          <p class="restock__teaching-consequence">
            {{ item.teaching_context.consequence }}
          </p>
        </section>

        <footer class="restock__foot">
          <dl class="restock__meta">
            <div class="restock__meta-item">
              <dt>近 7 天净消耗</dt>
              <dd>{{ item.q7 }}{{ item.unit }}</dd>
            </div>
            <div class="restock__meta-item">
              <dt>日均速率</dt>
              <dd>{{ formatRate(item) }}</dd>
            </div>
            <div class="restock__meta-item">
              <dt>可支撑</dt>
              <dd>{{ formatDays(item.days_of_supply) }}</dd>
            </div>
          </dl>

          <div v-if="canFeedback" class="restock__foot-actions">
            <el-button @click="openFeedback(item, $event)">
              {{ item.latest_feedback ? '更新拒绝原因' : '拒绝建议' }}
            </el-button>
            <el-button v-if="isAdmin" type="primary" @click="openRestock(item, $event)">
              处置入库
            </el-button>
          </div>
        </footer>

        <div class="restock__rationale">
          <div class="restock__rationale-head">
            <span class="restock__rationale-title">判断依据</span>
            <span class="status-tag" :class="RATIONALE_SOURCE_META[item.rationale_source]?.tagClass">
              {{ RATIONALE_SOURCE_META[item.rationale_source]?.label ?? item.rationale_source }}
            </span>
            <span v-if="item.rationale_degraded" class="status-tag status-tag--warning">
              已降级
            </span>
            <span v-if="item.computed_at" class="restock__rationale-time">
              数据时点 {{ formatGeneratedAt(item.computed_at) }}
            </span>
          </div>
          <p class="restock__rationale-text">{{ item.rationale }}</p>
        </div>
      </article>
    </div>

    <details v-if="ruleSpec" class="panel restock__rules">
      <summary class="restock__rules-summary">
        补货规则说明 · 规则版本 {{ ruleSpec.version }}
      </summary>
      <div class="restock__rules-body">
        <p class="restock__rules-text">{{ ruleSpec.window_rule }}</p>
        <ul class="restock__rules-list">
          <li v-for="tier in ruleSpec.tiers" :key="tier.code">
            <span
              class="status-tag"
              :class="TIER_META[tier.code]?.tagClass"
            >
              {{ tier.label }}
            </span>
            {{ tier.condition }}
          </li>
        </ul>
        <p class="restock__rules-text"><strong>计算：</strong>{{ ruleSpec.formula }}</p>
        <p v-if="ruleSpec.sort_rule" class="restock__rules-text">
          <strong>排序：</strong>{{ ruleSpec.sort_rule }}
        </p>
        <p class="restock__rules-text">
          <strong>跳过条件：</strong>{{ ruleSpec.skip_conditions.join('；') }}
        </p>
      </div>
    </details>

    <el-dialog
      v-model="restockDialog.visible"
      title="处置入库"
      width="520px"
      :before-close="handleDialogClose"
      :close-on-click-modal="!restockDialog.submitting"
      :close-on-press-escape="!restockDialog.submitting"
      :show-close="!restockDialog.submitting"
      @closed="handleRestockClosed"
    >
      <div
        v-if="restockDialog.item"
        class="restock__dialog"
        @focusin="keepFocusedFieldVisible"
      >
        <dl class="restock__summary">
          <div class="restock__summary-row">
            <dt>耗材</dt>
            <dd>{{ consumableLabel(restockDialog.item) }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>当前库存</dt>
            <dd>{{ restockDialog.item.current_stock }}{{ restockDialog.item.unit }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>安全库存线</dt>
            <dd>{{ restockDialog.item.safety_stock }}{{ restockDialog.item.unit }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>建议补货量</dt>
            <dd>{{ restockDialog.item.recommended_quantity }}{{ restockDialog.item.unit }}</dd>
          </div>
        </dl>

        <el-form label-position="top" @submit.prevent>
          <el-form-item label="实际入库数量" required :error="restockDialog.fieldError">
            <el-input-number
              ref="quantityRef"
              v-model="restockDialog.quantity"
              :min="1"
              :precision="0"
              :step="1"
              :disabled="restockDialog.submitting"
              controls-position="right"
              class="restock__quantity"
              aria-label="实际入库数量"
              @change="clearQuantityError"
            />
            <p v-if="expectedStock !== null" class="restock__hint">
              预计入库后 {{ expectedStock }}{{ restockDialog.item.unit }}
            </p>
          </el-form-item>
          <el-form-item label="实收日期" required>
            <el-date-picker
              v-model="restockDialog.receivedDate"
              type="date"
              value-format="YYYY-MM-DD"
              :disabled="restockDialog.submitting"
              class="restock__quantity"
              aria-label="实收日期"
            />
          </el-form-item>
          <el-form-item label="来源">
            <el-input
              v-model="restockDialog.source"
              maxlength="64"
              :disabled="restockDialog.submitting"
              placeholder="如：建议补货、采购回库"
              aria-label="来源"
            />
          </el-form-item>
          <el-form-item label="备注">
            <el-input
              v-model="restockDialog.remark"
              type="textarea"
              :rows="3"
              maxlength="255"
              show-word-limit
              :disabled="restockDialog.submitting"
              placeholder="可选，如采购回库"
              aria-label="备注"
            />
          </el-form-item>
        </el-form>

        <div v-if="restockDialog.errorMessage" class="restock__dialog-error">
          <el-alert
            :type="restockDialog.errorKind === 'unconfirmed' ? 'warning' : 'error'"
            :closable="false"
            show-icon
            :title="restockDialog.errorKind === 'unconfirmed' ? '暂未确认提交结果' : '无法完成入库'"
            :description="restockDialog.errorMessage"
            role="alert"
          >
            <template #default>
              <p v-if="errorRequestId" class="restock__hint">
                请求编号
                <code class="restock__request-id" tabindex="0">{{ errorRequestId }}</code>
                <el-button link type="primary" @click="copyRestockRequest">复制请求编号</el-button>
              </p>
            </template>
          </el-alert>
          <div class="restock__dialog-error-action">
            <el-button v-if="restockDialog.errorKind === 'rule'" @click="returnAndRefresh">
              返回并刷新建议
            </el-button>
            <el-button
              v-else-if="restockDialog.errorKind === 'unconfirmed'"
              link
              type="primary"
              @click="goLogs"
            >
              查看操作日志
            </el-button>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button :disabled="restockDialog.submitting" @click="restockDialog.visible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="restockDialog.submitting"
          :disabled="restockDialog.submitting"
          @click="submitRestock"
        >
          确认入库
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="feedbackDialog.visible"
      title="拒绝补货建议"
      width="480px"
      :before-close="handleFeedbackClose"
      :close-on-click-modal="!feedbackDialog.submitting"
      :close-on-press-escape="!feedbackDialog.submitting"
      :show-close="!feedbackDialog.submitting"
      @closed="handleFeedbackClosed"
    >
      <div
        v-if="feedbackDialog.item"
        class="restock__dialog"
        @focusin="keepFocusedFieldVisible"
      >
        <dl class="restock__summary">
          <div class="restock__summary-row">
            <dt>耗材</dt>
            <dd>{{ consumableLabel(feedbackDialog.item) }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>建议补货量</dt>
            <dd>
              {{ feedbackDialog.item.recommended_quantity
              }}{{ feedbackDialog.item.unit }}
            </dd>
          </div>
        </dl>

        <el-form label-position="top" @submit.prevent>
          <el-form-item
            label="拒绝原因"
            required
            :error="feedbackDialog.reasonError"
          >
            <el-select
              v-model="feedbackDialog.reason"
              placeholder="请选择原因"
              class="restock__quantity"
              :disabled="feedbackDialog.submitting"
              aria-label="拒绝原因"
              @change="feedbackDialog.reasonError = ''"
            >
              <el-option
                v-for="option in FEEDBACK_REASON_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            label="备注"
            :required="feedbackDialog.reason === 'other'"
            :error="feedbackDialog.noteError"
          >
            <el-input
              v-model="feedbackDialog.note"
              type="textarea"
              :rows="3"
              maxlength="255"
              show-word-limit
              :disabled="feedbackDialog.submitting"
              placeholder="选择其他时必填"
              aria-label="拒绝备注"
              @input="feedbackDialog.noteError = ''"
            />
          </el-form-item>
        </el-form>

        <div v-if="feedbackDialog.errorMessage" class="restock__dialog-error">
          <el-alert
            type="error"
            :closable="false"
            show-icon
            title="无法保存拒绝原因"
            :description="feedbackDialog.errorMessage"
            role="alert"
          />
        </div>
      </div>

      <template #footer>
        <el-button
          :disabled="feedbackDialog.submitting"
          @click="feedbackDialog.visible = false"
        >
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="feedbackDialog.submitting"
          :disabled="feedbackDialog.submitting"
          @click="submitFeedback"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="receiptDialog.visible"
      :title="receiptDialog.data ? `入库单 ${receiptDialog.data.order_no}` : '入库单详情'"
      width="640px"
    >
      <div v-if="receiptDialog.loading" class="restock__receipt">
        <el-skeleton :rows="4" animated />
      </div>
      <div v-else-if="receiptDialog.error" class="restock__receipt">
        <el-alert
          type="error"
          :closable="false"
          show-icon
          title="无法读取入库单"
          :description="receiptDialog.error"
        />
      </div>
      <div v-else-if="receiptDialog.data" class="restock__receipt">
        <dl class="restock__summary">
          <div class="restock__summary-row">
            <dt>状态</dt>
            <dd>{{ receiptStatusLabel(receiptDialog.data.status) }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>经办人</dt>
            <dd>{{ receiptDialog.data.operator_name }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>业务时间</dt>
            <dd>{{ formatReceiptTime(receiptDialog.data.business_at) }}</dd>
          </div>
          <div class="restock__summary-row">
            <dt>入账时间</dt>
            <dd>{{ formatReceiptTime(receiptDialog.data.posted_at) }}</dd>
          </div>
          <div v-if="receiptDialog.data.source" class="restock__summary-row">
            <dt>来源</dt>
            <dd>{{ receiptDialog.data.source }}</dd>
          </div>
          <div v-if="receiptDialog.data.remark" class="restock__summary-row">
            <dt>备注</dt>
            <dd>{{ receiptDialog.data.remark }}</dd>
          </div>
        </dl>

        <el-table :data="receiptDialog.data.lines" class="table-dense">
          <el-table-column label="耗材" min-width="140">
            <template #default="{ row }">{{ row.consumable_name }}</template>
          </el-table-column>
          <el-table-column label="入库数量" width="110" align="right">
            <template #default="{ row }">{{ row.quantity }}{{ row.unit }}</template>
          </el-table-column>
          <el-table-column label="变动前" width="90" align="right">
            <template #default="{ row }">{{ row.stock_before }}</template>
          </el-table-column>
          <el-table-column label="变动后" width="90" align="right">
            <template #default="{ row }">{{ row.stock_after }}</template>
          </el-table-column>
          <el-table-column label="已纠错" width="90" align="right">
            <template #default="{ row }">{{ row.corrected_quantity }}</template>
          </el-table-column>
          <el-table-column label="入账时间" min-width="160">
            <template #default="{ row }">{{ formatReceiptTime(row.posted_at) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <template #footer>
        <el-button type="primary" @click="receiptDialog.visible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.restock__patrol {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.restock__filters {
  margin-bottom: 16px;
}

.restock__segmented {
  display: inline-flex;
  flex-wrap: wrap;
}

.restock__segmented :deep(.el-radio-button__inner) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
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

.restock__segmented :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-radius: var(--radius-control) 0 0 var(--radius-control);
}

.restock__segmented :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-radius: 0 var(--radius-control) var(--radius-control) 0;
}

.restock__segmented :deep(.el-radio-button__inner:hover) {
  background: var(--surface-subtle);
  color: var(--text);
}

.restock__segmented :deep(.el-radio-button.is-active .el-radio-button__inner) {
  border-color: var(--primary-200);
  background: var(--primary-soft);
  color: var(--primary);
  box-shadow: none;
}

.restock__seg-count {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.restock__segmented :deep(.el-radio-button.is-active .restock__seg-count) {
  color: var(--primary);
}

.restock__notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.restock__notice .el-alert {
  flex: 1 1 320px;
}

.restock__loading-text {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr));
  gap: 16px;
}

.restock__card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  border-left: 3px solid var(--line);
}

.restock__card--urgent {
  border-left-color: var(--danger);
}

.restock__card--moderate {
  border-left-color: var(--warning);
}

.restock__card--observe {
  border-left-color: var(--info);
}

.restock__card--focus {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}

.restock__card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px 16px;
}

.restock__identity {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.restock__name {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.restock__spec {
  font-weight: 400;
  color: var(--text-muted);
}

.restock__amount-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  margin-left: auto;
  text-align: right;
}

.restock__amount-label {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.restock__amount {
  font-size: 28px;
  line-height: 32px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.restock__amount-unit {
  margin-left: 2px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-muted);
}

.restock__disposition {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.restock__metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 0;
  padding: 14px 0 0;
  border-top: 1px solid var(--line-soft);
}

.restock__metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.restock__metric dt {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.restock__metric dd {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.restock__reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.restock__reason {
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.restock__note {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.restock__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}

.restock__foot-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.restock__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  margin: 0;
}

.restock__meta-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.restock__meta-item dt {
  margin: 0;
  font-size: 11px;
  line-height: 15px;
  letter-spacing: 0.01em;
  color: var(--text-muted);
}

.restock__meta-item dd {
  margin: 0;
  font-size: 13px;
  line-height: 19px;
  font-weight: 600;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.restock__teaching {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.restock__teaching-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.restock__teaching-head .status-tag {
  height: 18px;
  padding: 0 6px;
  font-size: 11px;
}

.restock__teaching-title {
  font-size: 12px;
  line-height: 16px;
  font-weight: 600;
  color: var(--text-secondary);
}

.restock__teaching-demand {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.restock__teaching-sessions {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.restock__teaching-sessions li {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.restock__teaching-consequence {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.restock__rationale {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.restock__rationale-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.restock__rationale-head .status-tag {
  height: 18px;
  padding: 0 6px;
  font-size: 11px;
}

.restock__rationale-title {
  font-size: 12px;
  line-height: 16px;
  font-weight: 600;
  color: var(--text-muted);
}

.restock__rationale-time {
  margin-left: auto;
  font-size: 11px;
  line-height: 16px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.restock__rationale-text {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  white-space: pre-line;
}

.restock__rules {
  margin-top: 24px;
  padding: 16px 20px;
  box-shadow: none;
}

.restock__rules-summary {
  cursor: pointer;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text-secondary);
}

.restock__rules-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.restock__rules-text {
  margin: 0;
  font-size: 12px;
  line-height: 19px;
  color: var(--text-muted);
}

.restock__rules-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 12px;
  line-height: 19px;
  color: var(--text-muted);
}

.restock__rules-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.restock__dialog {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.restock__receipt {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.restock__summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
  padding: 16px;
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.restock__summary-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.restock__summary-row dt {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.restock__summary-row dd {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}

.restock__quantity {
  width: 100%;
}

.restock__hint {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.restock__dialog-error {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.restock__request-id {
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--surface-muted);
  color: var(--text);
  font-size: 12px;
  line-height: 18px;
  word-break: break-all;
  user-select: all;
}

.restock__dialog-error-action {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 767px) {
  .restock__card {
    padding: 16px;
  }

  .restock__foot .el-button {
    min-height: 44px;
  }

  .restock__segmented :deep(.el-radio-button__inner) {
    height: 36px;
  }
}
</style>