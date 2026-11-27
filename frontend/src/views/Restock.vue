<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import { businessToday } from '../utils/businessDate'

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

const TIER_FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'urgent', label: '紧急' },
  { value: 'moderate', label: '一般' },
  { value: 'observe', label: '观察' },
]

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const isAdmin = computed(() => userStore.user?.role === 'admin')

const items = ref([])
const ruleSpec = ref(null)
const generatedAt = ref('')
const loading = ref(false)
const requestError = ref('')
const staleError = ref(false)
const handledAll = ref(false)
const tierFilter = ref('all')
const quantityRef = ref(null)

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

const generatedAtText = computed(() => formatGeneratedAt(generatedAt.value))

const emptyTitle = computed(() => {
  if (requestError.value) return '巡检暂时不可用'
  if (handledAll.value) return '本次建议已全部处置'
  return '本次巡检暂无补货建议'
})

const emptyDescription = computed(() => {
  if (requestError.value) return requestError.value
  if (handledAll.value) return '所有建议均已完成入库，可重新巡检查看最新结果。'
  return '当前没有触发补货规则的耗材；无启用档案、未设安全库存线时也不会产生建议。'
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
    if (seq === requestSeq) loading.value = false
  }
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll('-', '')
  }
  return `rst-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

function openRestock(item) {
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
  restockDialog.visible = true
}

function handleDialogClose(done) {
  if (restockDialog.submitting) return
  done()
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
    ElMessage.success(`已生成入库单 ${data.order_no}${stockText}`)
    await fetchSuggestions(true)
  } catch (error) {
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

onMounted(() => {
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
        <el-button type="primary" :loading="loading" @click="fetchSuggestions(true)">
          {{ loading ? '巡检中…' : '重新巡检' }}
        </el-button>
      </template>
    </PageHeader>

    <div class="restock__filters" role="group" aria-label="按等级筛选补货建议">
      <el-radio-group v-model="tierFilter">
        <el-radio-button
          v-for="option in TIER_FILTERS"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }} {{ counts[option.value] }}
        </el-radio-button>
      </el-radio-group>
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
        <p class="restock__rules-text">
          <strong>跳过条件：</strong>{{ ruleSpec.skip_conditions.join('；') }}
        </p>
      </div>
    </details>

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
      description="当前巡检结果中该等级没有补货建议。"
    >
      <template #action>
        <el-button @click="tierFilter = 'all'">查看全部</el-button>
      </template>
    </EmptyState>

    <div v-else class="restock__grid" :aria-busy="loading">
      <article
        v-for="item in visibleItems"
        :key="item.consumable_id"
        class="panel restock__card"
        :class="`restock__card--${item.tier}`"
      >
        <header class="restock__card-head">
          <div class="restock__card-heading">
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
            <el-button v-if="isAdmin" type="primary" @click="openRestock(item)">
              处置入库
            </el-button>
          </div>
        </header>

        <div v-if="item.disposition_status" class="restock__disposition">
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
          <div class="restock__metric">
            <dt>近 7 天净消耗</dt>
            <dd>{{ item.q7 }}{{ item.unit }}</dd>
          </div>
          <div class="restock__metric">
            <dt>日均速率</dt>
            <dd>{{ formatRate(item) }}</dd>
          </div>
          <div class="restock__metric">
            <dt>可支撑天数</dt>
            <dd>{{ formatDays(item.days_of_supply) }}</dd>
          </div>
        </dl>

        <div v-if="item.reasons?.length" class="restock__reasons">
          <span class="restock__reasons-title">命中原因</span>
          <span
            v-for="reason in item.reasons"
            :key="reason"
            class="status-tag status-tag--info"
          >
            {{ reason }}
          </span>
        </div>

        <p v-if="item.consumption_note" class="restock__hint">
          {{ item.consumption_note }}
        </p>

        <div class="restock__rationale">
          <span class="restock__rationale-title">判断依据</span>
          <p class="restock__rationale-text">{{ item.rationale }}</p>
        </div>
      </article>
    </div>

    <el-dialog
      v-model="restockDialog.visible"
      title="处置入库"
      width="520px"
      :before-close="handleDialogClose"
      :close-on-click-modal="!restockDialog.submitting"
      :close-on-press-escape="!restockDialog.submitting"
      :show-close="!restockDialog.submitting"
    >
      <div v-if="restockDialog.item" class="restock__dialog">
        <div class="restock__summary">
          <div class="restock__summary-row">
            <span class="restock__summary-label">耗材</span>
            <span class="restock__summary-value">
              {{ consumableLabel(restockDialog.item) }}
            </span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">当前库存</span>
            <span class="restock__summary-value">
              {{ restockDialog.item.current_stock }}{{ restockDialog.item.unit }}
            </span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">安全库存线</span>
            <span class="restock__summary-value">
              {{ restockDialog.item.safety_stock }}{{ restockDialog.item.unit }}
            </span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">建议补货量</span>
            <span class="restock__summary-value">
              {{ restockDialog.item.recommended_quantity }}{{ restockDialog.item.unit }}
            </span>
          </div>
        </div>

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
            />
          </el-form-item>
          <el-form-item label="来源">
            <el-input
              v-model="restockDialog.source"
              maxlength="64"
              :disabled="restockDialog.submitting"
              placeholder="如：建议补货、采购回库"
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
          />
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
      v-model="receiptDialog.visible"
      :title="receiptDialog.data ? `入库单 ${receiptDialog.data.order_no}` : '入库单详情'"
      width="680px"
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
        <div class="restock__summary">
          <div class="restock__summary-row">
            <span class="restock__summary-label">状态</span>
            <span class="restock__summary-value">
              {{ receiptStatusLabel(receiptDialog.data.status) }}
            </span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">经办人</span>
            <span class="restock__summary-value">{{ receiptDialog.data.operator_name }}</span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">业务时间</span>
            <span class="restock__summary-value">
              {{ formatReceiptTime(receiptDialog.data.business_at) }}
            </span>
          </div>
          <div class="restock__summary-row">
            <span class="restock__summary-label">入账时间</span>
            <span class="restock__summary-value">
              {{ formatReceiptTime(receiptDialog.data.posted_at) }}
            </span>
          </div>
          <div v-if="receiptDialog.data.source" class="restock__summary-row">
            <span class="restock__summary-label">来源</span>
            <span class="restock__summary-value">{{ receiptDialog.data.source }}</span>
          </div>
          <div v-if="receiptDialog.data.remark" class="restock__summary-row">
            <span class="restock__summary-label">备注</span>
            <span class="restock__summary-value">{{ receiptDialog.data.remark }}</span>
          </div>
        </div>

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
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.restock__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
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
  font-size: 14px;
  line-height: 22px;
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
  border-left: 4px solid var(--line);
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

.restock__card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.restock__card-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.restock__name {
  margin: 0;
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
  color: var(--text);
}

.restock__spec {
  font-weight: 400;
  color: var(--text-muted);
}

.restock__amount-block {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.restock__amount-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__amount {
  font-size: 28px;
  line-height: 36px;
  font-weight: 700;
  color: var(--primary);
  font-variant-numeric: tabular-nums;
}

.restock__amount-unit {
  margin-left: 2px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.restock__disposition {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px 16px;
  margin: 0;
  padding: 16px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.restock__metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.restock__metric dt {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__metric dd {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.restock__rules {
  margin-bottom: 16px;
  padding: 16px 20px;
}

.restock__rules-summary {
  cursor: pointer;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.restock__rules-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.restock__rules-text {
  margin: 0;
  font-size: 13px;
  line-height: 21px;
  color: var(--text-secondary);
}

.restock__rules-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 13px;
  line-height: 21px;
  color: var(--text-secondary);
}

.restock__rules-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.restock__reasons {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.restock__reasons-title {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__hint {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__rationale {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.restock__rationale-title {
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text-secondary);
}

.restock__rationale-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
  white-space: pre-line;
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
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.restock__summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.restock__summary-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.restock__summary-value {
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.restock__quantity {
  width: 100%;
}

.restock__dialog-error {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.restock__dialog-error-action {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 767px) {
  .restock__card {
    padding: 16px;
  }

  .restock__amount {
    font-size: 24px;
    line-height: 32px;
  }
}
</style>