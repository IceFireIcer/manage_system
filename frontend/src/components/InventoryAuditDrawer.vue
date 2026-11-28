<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../api'
import EmptyState from './EmptyState.vue'
import EventTraceDrawer from './EventTraceDrawer.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  consumableId: {
    type: Number,
    default: null,
  },
  consumableName: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['update:modelValue', 'view-source'])

const SOURCE_META = {
  opening: { label: '期初', className: 'status-tag--neutral' },
  requisition: { label: '领用', className: 'status-tag--info' },
  reversal: { label: '红冲', className: 'status-tag--reversal' },
  restock: { label: '入库', className: 'status-tag--success' },
  return: { label: '退库', className: 'status-tag--success' },
  scrap: { label: '报损', className: 'status-tag--warning' },
  inventory_adjustment: { label: '盘点调整', className: 'status-tag--warning' },
  restock_correction: { label: '入库纠错', className: 'status-tag--warning' },
}

const EVIDENCE_META = {
  trusted: { label: '可信', className: 'status-tag--success' },
  insufficient: { label: '证据不足', className: 'status-tag--warning' },
  unverifiable: { label: '无法验证', className: 'status-tag--danger' },
}

const BASIS_OPTIONS = [
  { value: 'posting', label: '按入账时间' },
  { value: 'business', label: '按业务时间重排' },
]

const loading = ref(false)
const error = ref('')
const audit = ref(null)
const cutoff = ref(null)
const basisKey = ref('posting')
const viewportWidth = ref(window.innerWidth)
const traceVisible = ref(false)
const traceEventId = ref(null)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const drawerSize = computed(() => (viewportWidth.value < 768 ? '100%' : '640px'))

const drawerTitle = computed(() => {
  const name = props.consumableName || audit.value?.name || ''
  return name ? `库存审计 · ${name}` : '库存审计'
})

const unit = computed(() => audit.value?.unit ?? '')

const activeBasis = computed(() => audit.value?.[basisKey.value] ?? null)
const otherBasisKey = computed(() => (basisKey.value === 'posting' ? 'business' : 'posting'))
const otherBasis = computed(() => audit.value?.[otherBasisKey.value] ?? null)
const events = computed(() => activeBasis.value?.events ?? [])

const evidenceMeta = computed(
  () =>
    EVIDENCE_META[audit.value?.evidence_status] ?? {
      label: audit.value?.evidence_label ?? '—',
      className: 'status-tag--neutral',
    },
)

function timeKey(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 19)
}

function formatTime(value) {
  const text = timeKey(value)
  return text || '—'
}

function eventTime(event) {
  return basisKey.value === 'business' ? event.business_at : event.posted_at
}

function displayUnit(value) {
  return value || '待核对'
}

function formatStock(value, snapshot) {
  if (value === null || value === undefined) return '—'
  return `${value}${displayUnit(snapshot === undefined ? unit.value : snapshot)}`
}

function formatDelta(event) {
  const prefix = event.delta > 0 ? '+' : ''
  return `${prefix}${event.delta}${displayUnit(event.unit_snapshot)}`
}

function formatDiff(value) {
  if (value === null || value === undefined) return '—'
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${value}${displayUnit(unit.value)}`
}

function sourceMeta(event) {
  return (
    SOURCE_META[event.event_type] ?? {
      label: event.event_type,
      className: 'status-tag--neutral',
    }
  )
}

function basisStockText(basis) {
  if (!basis) return '—'
  const stock = formatStock(basis.stock_at_cutoff)
  return cutoff.value ? `时点 ${stock}` : `截至当前 ${stock}`
}

function anomalyLabel(anomaly) {
  return `第 ${anomaly.exec_seq} 步 · ${formatTime(anomaly.at)}`
}

function rangeLabel(range) {
  if (!range) return '—'
  const steps =
    range.start_exec_seq === range.end_exec_seq
      ? `第 ${range.start_exec_seq} 步`
      : `第 ${range.start_exec_seq}–${range.end_exec_seq} 步`
  return `${steps} · ${formatTime(range.start_at)} 至 ${formatTime(range.end_at)}`
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

let requestSerial = 0

async function fetchAudit() {
  const serial = ++requestSerial
  if (!props.consumableId) {
    audit.value = null
    error.value = ''
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get('/inventory/audit', {
      params: {
        consumable_id: props.consumableId,
        posting_cutoff: cutoff.value || undefined,
        business_cutoff: cutoff.value || undefined,
      },
    })
    if (serial !== requestSerial) return
    audit.value = data
  } catch (requestError) {
    if (serial !== requestSerial) return
    audit.value = null
    error.value =
      requestError.response?.status === 404 ? '该记录无法查看' : '库存审计读取失败'
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

function handleViewSource(event) {
  if (event?.can_open_source) {
    emit('view-source', event)
  }
}

function openTrace(event) {
  traceEventId.value = event.id
  traceVisible.value = true
}

watch(
  () => [props.modelValue, props.consumableId],
  ([open, consumableId], previous) => {
    if (!open) {
      traceVisible.value = false
      return
    }
    if (previous?.[1] !== consumableId) {
      audit.value = null
      basisKey.value = 'posting'
      cutoff.value = null
      traceVisible.value = false
      traceEventId.value = null
    }
    fetchAudit()
  },
  { immediate: true },
)

watch(cutoff, () => {
  if (props.modelValue) fetchAudit()
})

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
    class="audit-drawer"
    destroy-on-close
  >
    <div class="audit-drawer__body">
      <section class="audit-drawer__summary">
        <div class="audit-drawer__primary">
          <span class="audit-drawer__label">当前库存</span>
          <span class="audit-drawer__primary-value">
            {{ audit ? formatStock(audit.current_stock) : '—' }}
          </span>
        </div>
        <dl class="audit-drawer__metrics">
          <div class="audit-drawer__metric">
            <dt>可信期初</dt>
            <dd>{{ audit ? formatStock(audit.opening_stock) : '—' }}</dd>
          </div>
          <div class="audit-drawer__metric">
            <dt>流水末值</dt>
            <dd>{{ audit ? formatStock(audit.ledger_end) : '—' }}</dd>
          </div>
          <div class="audit-drawer__metric">
            <dt>差异</dt>
            <dd :class="{ 'audit-drawer__metric-value--alert': audit && audit.diff }">
              {{ audit ? formatDiff(audit.diff) : '—' }}
            </dd>
          </div>
          <div class="audit-drawer__metric">
            <dt>证据状态</dt>
            <dd>
              <span v-if="audit" class="status-tag" :class="evidenceMeta.className">
                {{ evidenceMeta.label }}
              </span>
              <span v-else>—</span>
            </dd>
          </div>
        </dl>
      </section>

      <section v-if="audit" class="audit-drawer__evidence">
        <ul class="audit-drawer__reasons">
          <li v-for="reason in audit.evidence_reasons" :key="reason">{{ reason }}</li>
        </ul>
        <dl class="audit-drawer__checks">
          <div class="audit-drawer__check">
            <dt>首次异常</dt>
            <dd v-if="audit.first_anomaly">
              {{ anomalyLabel(audit.first_anomaly) }}：{{ audit.first_anomaly.reason }}
            </dd>
            <dd v-else>—</dd>
          </div>
          <div class="audit-drawer__check">
            <dt>受影响区间</dt>
            <dd>{{ rangeLabel(audit.affected_range) }}</dd>
          </div>
        </dl>
      </section>

      <div v-if="error" class="audit-drawer__error">
        <p class="audit-drawer__error-text">{{ error }}</p>
        <el-button @click="fetchAudit">重试</el-button>
      </div>

      <div v-else-if="loading && !audit" class="audit-drawer__loading">
        <el-skeleton :rows="4" animated />
      </div>

      <template v-else-if="audit">
        <div class="audit-drawer__basis">
          <div class="audit-drawer__basis-row">
            <span class="audit-drawer__basis-label">对账口径</span>
            <el-radio-group v-model="basisKey" size="small" class="audit-drawer__segmented">
              <el-radio-button
                v-for="option in BASIS_OPTIONS"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </el-radio-button>
            </el-radio-group>
            <el-date-picker
              v-model="cutoff"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="查询时点"
              clearable
              class="audit-drawer__cutoff"
            />
          </div>
          <div class="audit-drawer__basis-stocks">
            <span
              class="audit-drawer__basis-stock"
              :class="{ 'audit-drawer__basis-stock--active': basisKey === 'posting' }"
            >
              按入账时间 {{ basisStockText(audit.posting) }}
            </span>
            <span
              class="audit-drawer__basis-stock"
              :class="{ 'audit-drawer__basis-stock--active': basisKey === 'business' }"
            >
              按业务时间重排 {{ basisStockText(audit.business) }}
            </span>
          </div>
        </div>

        <el-table
          v-if="events.length"
          v-loading="loading"
          :data="events"
          class="table-dense audit-drawer__table"
        >
          <el-table-column :label="basisKey === 'business' ? '业务时间' : '入账时间'" min-width="160">
            <template #default="{ row }">{{ formatTime(eventTime(row)) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="92">
            <template #default="{ row }">
              <span class="status-tag" :class="sourceMeta(row).className">
                {{ sourceMeta(row).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="库存增减" width="100" align="right">
            <template #default="{ row }">{{ formatDelta(row) }}</template>
          </el-table-column>
          <el-table-column label="变动前" width="88" align="right">
            <template #default="{ row }">{{ formatStock(row.stock_before, row.unit_snapshot) }}</template>
          </el-table-column>
          <el-table-column label="变动后" width="88" align="right">
            <template #default="{ row }">{{ formatStock(row.stock_after, row.unit_snapshot) }}</template>
          </el-table-column>
          <el-table-column label="证据" width="92">
            <template #default="{ row }">
              <span
                v-if="row.evidence_status !== 'trusted'"
                class="status-tag"
                :class="EVIDENCE_META[row.evidence_status]?.className ?? 'status-tag--warning'"
              >
                {{ EVIDENCE_META[row.evidence_status]?.label ?? row.evidence_status }}
              </span>
              <span v-else class="audit-drawer__muted">可信</span>
            </template>
          </el-table-column>
          <el-table-column label="来源单据" min-width="170">
            <template #default="{ row }">
              <div class="audit-drawer__source">
                <span class="audit-drawer__source-no">{{ row.order_no || '—' }}</span>
                <el-button
                  v-if="row.can_open_source"
                  link
                  type="primary"
                  @click="handleViewSource(row)"
                >
                  查看来源
                </el-button>
                <el-button link type="primary" @click="openTrace(row)">轨迹</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <EmptyState
          v-else
          title="该时点没有账面记录"
          description="该时点无库存事件。"
        />
      </template>
    </div>

    <template #footer>
      <div class="audit-drawer__footer">
        <el-button type="primary" @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>

  <EventTraceDrawer v-model="traceVisible" :event-id="traceEventId" />
</template>

<style scoped>
.audit-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.audit-drawer__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 24px;
  padding: 16px;
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.audit-drawer__primary {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 128px;
}

.audit-drawer__label {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.audit-drawer__primary-value {
  font-size: 28px;
  line-height: 32px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  flex: 1;
  gap: 8px 16px;
  margin: 0;
}

.audit-drawer__metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.audit-drawer__metric dt {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.audit-drawer__metric dd {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__metric-value--alert {
  color: var(--danger);
}

.audit-drawer__evidence {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
}

.audit-drawer__reasons {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 19px;
  color: var(--text-secondary);
}

.audit-drawer__checks {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
}

.audit-drawer__check {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 8px;
  font-size: 12px;
  line-height: 19px;
}

.audit-drawer__check dt {
  color: var(--text-muted);
}

.audit-drawer__check dd {
  margin: 0;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__basis {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audit-drawer__basis-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}

.audit-drawer__basis-label {
  font-size: 12px;
  line-height: 16px;
  font-weight: 500;
  color: var(--text-muted);
}

.audit-drawer__segmented :deep(.el-radio-button__inner) {
  border-color: var(--line);
  background: var(--surface);
  color: var(--text-secondary);
  font-weight: 500;
  box-shadow: none;
}

.audit-drawer__segmented :deep(.el-radio-button.is-active .el-radio-button__inner) {
  border-color: var(--primary-200);
  background: var(--primary-soft);
  color: var(--primary);
  box-shadow: none;
}

.audit-drawer__cutoff {
  width: 220px;
}

.audit-drawer__basis-stocks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.audit-drawer__basis-stock {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__basis-stock--active {
  border-color: var(--primary-200);
  background: var(--primary-50);
  color: var(--primary);
}

.audit-drawer__source {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.audit-drawer__source-no {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  word-break: break-all;
}

.audit-drawer__muted {
  font-size: 12px;
  color: var(--text-muted);
}

.audit-drawer__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 24px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
}

.audit-drawer__error-text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}

.audit-drawer__table {
  width: 100%;
}

.audit-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .audit-drawer__summary {
    gap: 16px;
  }

  .audit-drawer__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .audit-drawer__cutoff {
    width: 100%;
  }

  .audit-drawer__footer .el-button {
    min-height: 44px;
  }
}
</style>

<style>
.audit-drawer .el-drawer__body {
  padding: 0 24px 24px;
}

@media (max-width: 767px) {
  .audit-drawer .el-drawer__body {
    padding: 0 16px 24px;
  }
}
</style>