<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../api'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  eventId: {
    type: [Number, String],
    default: null,
  },
})

const emit = defineEmits(['update:modelValue'])

const EVENT_TONES = {
  opening: 'status-tag--neutral',
  requisition: 'status-tag--info',
  reversal: 'status-tag--reversal',
  restock: 'status-tag--success',
  return: 'status-tag--success',
  scrap: 'status-tag--warning',
  inventory_adjustment: 'status-tag--warning',
  restock_correction: 'status-tag--warning',
}

const DOC_TONES = {
  requisition_order: 'status-tag--info',
  requisition: 'status-tag--info',
  requisition_reversal: 'status-tag--reversal',
  requisition_reentry: 'status-tag--info',
  requisition_return: 'status-tag--success',
  requisition_scrap: 'status-tag--warning',
  requisition_correction: 'status-tag--warning',
  requisition_backfill: 'status-tag--neutral',
  restock_order: 'status-tag--success',
  restock_correction: 'status-tag--warning',
  inventory_count: 'status-tag--warning',
  inventory_count_line: 'status-tag--neutral',
}

const STATUS_TONES = {
  待处理: 'status-tag--warning',
  处理中: 'status-tag--warning',
  盘点中: 'status-tag--warning',
  已提交: 'status-tag--info',
  有效: 'status-tag--success',
  已处理: 'status-tag--success',
  已入账: 'status-tag--success',
  已退库: 'status-tag--success',
  已报损: 'status-tag--success',
  已扣回: 'status-tag--success',
  已红冲: 'status-tag--neutral',
  已驳回: 'status-tag--neutral',
}

const loading = ref(false)
const error = ref('')
const trace = ref(null)
const viewportWidth = ref(window.innerWidth)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const drawerSize = computed(() => (viewportWidth.value < 768 ? '100%' : '600px'))
const event = computed(() => trace.value?.event ?? null)
const timeline = computed(() => trace.value?.timeline ?? [])

const drawerTitle = computed(() => {
  const name = event.value?.consumable_name ?? ''
  return name ? `变动轨迹 · ${name}` : '变动轨迹'
})

function timeKey(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 19)
}

function formatTime(value) {
  return timeKey(value) || '—'
}

function displayUnit(value) {
  return value || ''
}

function formatStock(value, unit) {
  if (value === null || value === undefined) return '—'
  return `${value}${displayUnit(unit)}`
}

function formatDelta(item) {
  if (!item) return '—'
  const prefix = item.delta > 0 ? '+' : ''
  return `${prefix}${item.delta}${displayUnit(item.unit_snapshot)}`
}

function eventTone(item) {
  return EVENT_TONES[item?.event_type] ?? 'status-tag--neutral'
}

function docTone(doc) {
  return DOC_TONES[doc?.kind] ?? 'status-tag--neutral'
}

function statusTone(label) {
  return STATUS_TONES[label] ?? 'status-tag--neutral'
}

function formatQuantity(doc) {
  if (doc?.quantity === null || doc?.quantity === undefined) return '—'
  return `${doc.quantity}${displayUnit(doc.unit)}`
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

let requestSerial = 0

async function fetchTrace() {
  const serial = ++requestSerial
  if (!props.eventId) {
    trace.value = null
    error.value = ''
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get(`/inventory/events/${props.eventId}/trace`)
    if (serial !== requestSerial) return
    trace.value = data
  } catch (requestError) {
    if (serial !== requestSerial) return
    trace.value = null
    error.value =
      requestError.response?.status === 404 ? '该变动无法查看' : '变动轨迹读取失败'
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

watch(
  () => [props.modelValue, props.eventId],
  ([open, eventId], previous) => {
    if (!open) return
    if (previous?.[1] !== eventId) {
      trace.value = null
    }
    fetchTrace()
  },
  { immediate: true },
)

onMounted(() => window.addEventListener('resize', handleResize))

onBeforeUnmount(() => {
  requestSerial += 1
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="drawerTitle"
    :size="drawerSize"
    class="trace-drawer"
    destroy-on-close
    append-to-body
  >
    <div class="trace-drawer__body">
      <div v-if="error" class="trace-drawer__error">
        <p class="trace-drawer__error-text">{{ error }}</p>
        <el-button @click="fetchTrace">重试</el-button>
      </div>

      <el-skeleton v-else-if="loading && !trace" :rows="5" animated />

      <template v-else-if="trace">
        <section class="trace-drawer__hero">
          <header class="trace-drawer__hero-head">
            <span class="status-tag" :class="eventTone(event)">
              {{ event.event_label }}
            </span>
            <span class="trace-drawer__time">{{ formatTime(event.posted_at) }}</span>
          </header>

          <div class="trace-drawer__stock">
            <div class="trace-drawer__stock-cell">
              <span class="trace-drawer__label">变动前</span>
              <span class="trace-drawer__stock-value">
                {{ formatStock(event.stock_before, event.unit_snapshot) }}
              </span>
            </div>
            <span class="trace-drawer__arrow">→</span>
            <div class="trace-drawer__stock-cell">
              <span class="trace-drawer__label">变动后</span>
              <span class="trace-drawer__stock-value">
                {{ formatStock(event.stock_after, event.unit_snapshot) }}
              </span>
            </div>
            <span
              class="trace-drawer__delta"
              :class="{
                'trace-drawer__delta--up': event.delta > 0,
                'trace-drawer__delta--down': event.delta < 0,
              }"
            >
              {{ formatDelta(event) }}
            </span>
          </div>

          <dl class="trace-drawer__meta">
            <div class="trace-drawer__meta-item">
              <dt>耗材</dt>
              <dd>
                {{ event.consumable_name
                }}<span v-if="event.consumable_spec">（{{ event.consumable_spec }}）</span>
              </dd>
            </div>
            <div class="trace-drawer__meta-item">
              <dt>操作人</dt>
              <dd>{{ event.operator_name || '系统' }}</dd>
            </div>
            <div class="trace-drawer__meta-item">
              <dt>关联单号</dt>
              <dd>{{ event.order_no || '—' }}</dd>
            </div>
            <div class="trace-drawer__meta-item">
              <dt>业务时间</dt>
              <dd>{{ formatTime(event.business_at) }}</dd>
            </div>
          </dl>

          <div v-if="event.reason || event.remark" class="trace-drawer__notes">
            <div v-if="event.reason" class="trace-drawer__note">
              <span class="trace-drawer__label">原因</span>
              <p class="trace-drawer__note-text">{{ event.reason }}</p>
            </div>
            <div v-if="event.remark" class="trace-drawer__note">
              <span class="trace-drawer__label">备注</span>
              <p class="trace-drawer__note-text">{{ event.remark }}</p>
            </div>
          </div>
        </section>

        <section class="trace-drawer__timeline">
          <div class="trace-drawer__section-head">
            <h3 class="trace-drawer__section-title">关联单据</h3>
            <span class="trace-drawer__section-meta">
              {{ trace.timeline_total }} 张
            </span>
          </div>

          <el-timeline v-if="timeline.length" class="trace-drawer__timeline-list">
            <el-timeline-item
              v-for="doc in timeline"
              :key="`${doc.kind}-${doc.ref_id}`"
              :timestamp="formatTime(doc.at)"
              placement="top"
            >
              <div class="trace-drawer__doc">
                <div class="trace-drawer__doc-head">
                  <span class="status-tag" :class="docTone(doc)">
                    {{ doc.kind_label }}
                  </span>
                  <span
                    v-if="doc.status_label"
                    class="status-tag"
                    :class="statusTone(doc.status_label)"
                  >
                    {{ doc.status_label }}
                  </span>
                </div>
                <div class="trace-drawer__doc-line">
                  <span class="trace-drawer__doc-no">
                    {{ doc.order_no || '无单号' }}
                  </span>
                  <span class="trace-drawer__doc-qty">{{ formatQuantity(doc) }}</span>
                  <span v-if="doc.operator_name" class="trace-drawer__doc-operator">
                    {{ doc.operator_name }}
                  </span>
                </div>
                <p v-if="doc.reason" class="trace-drawer__doc-reason">
                  {{ doc.reason }}
                </p>
              </div>
            </el-timeline-item>
          </el-timeline>

          <EmptyState
            v-else
            title="暂无关联单据"
            description="该变动没有可追溯的单据。"
          />

          <p v-if="trace.truncated" class="trace-drawer__truncated">
            已按上限显示前 {{ trace.limit }} 张，共 {{ trace.timeline_total }} 张。
          </p>
        </section>
      </template>
    </div>

    <template #footer>
      <div class="trace-drawer__footer">
        <el-button type="primary" @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.trace-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.trace-drawer__hero {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.trace-drawer__hero-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.trace-drawer__time {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.trace-drawer__stock {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.trace-drawer__stock-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 96px;
}

.trace-drawer__label {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.trace-drawer__stock-value {
  font-size: 24px;
  line-height: 30px;
  font-weight: 650;
  letter-spacing: -0.01em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.trace-drawer__arrow {
  font-size: 16px;
  color: var(--text-muted);
}

.trace-drawer__delta {
  margin-left: auto;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.trace-drawer__delta--up {
  color: var(--success);
}

.trace-drawer__delta--down {
  color: var(--danger);
}

.trace-drawer__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 0;
}

.trace-drawer__meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.trace-drawer__meta-item dt {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.trace-drawer__meta-item dd {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text);
  word-break: break-all;
}

.trace-drawer__notes {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
}

.trace-drawer__note {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 8px;
}

.trace-drawer__note-text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}

.trace-drawer__section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.trace-drawer__section-title {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.trace-drawer__section-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.trace-drawer__doc {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 4px;
}

.trace-drawer__doc-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.trace-drawer__doc-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.trace-drawer__doc-no {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  word-break: break-all;
}

.trace-drawer__doc-qty {
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.trace-drawer__doc-operator {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.trace-drawer__doc-reason {
  margin: 0;
  font-size: 12px;
  line-height: 19px;
  color: var(--text-muted);
}

.trace-drawer__truncated {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.trace-drawer__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 24px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
}

.trace-drawer__error-text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}

.trace-drawer__footer {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 767px) {
  .trace-drawer__meta {
    grid-template-columns: minmax(0, 1fr);
  }

  .trace-drawer__delta {
    margin-left: 0;
  }

  .trace-drawer__footer .el-button {
    min-height: 44px;
  }
}
</style>

<style>
.trace-drawer .el-drawer__body {
  padding: 0 24px 24px;
}

@media (max-width: 767px) {
  .trace-drawer .el-drawer__body {
    padding: 0 16px 24px;
  }
}
</style>