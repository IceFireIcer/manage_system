<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../api'
import EmptyState from './EmptyState.vue'

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
  requisition: { label: '领用', className: 'status-tag--neutral' },
  reversal: { label: '红冲', className: 'status-tag--reversal' },
  restock: { label: '入库', className: 'status-tag--success' },
}

const loading = ref(false)
const error = ref('')
const audit = ref(null)
const cutoff = ref(null)
const viewportWidth = ref(window.innerWidth)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const drawerSize = computed(() => (viewportWidth.value < 768 ? '100%' : '600px'))

const drawerTitle = computed(() => {
  const name = props.consumableName || audit.value?.name || ''
  return name ? `库存变动 · ${name}` : '库存变动'
})

const unit = computed(() => audit.value?.unit ?? '')

function timeKey(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 19)
}

const displayEvents = computed(() => {
  const events = audit.value?.events ?? []
  if (!cutoff.value) return events
  const limit = timeKey(cutoff.value)
  return events.filter((event) => timeKey(event.time) <= limit)
})

const cutoffStock = computed(() => {
  if (!cutoff.value) return null
  if (displayEvents.value.length) {
    return displayEvents.value[displayEvents.value.length - 1].stock_after
  }
  return audit.value?.opening_stock ?? null
})

const sourceEvent = computed(
  () => displayEvents.value.find((event) => sourceIdOf(event) !== null) ?? null,
)

function sourceIdOf(event) {
  return event.source_id ?? event.origin_id ?? event.id ?? null
}

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function sourceLabel(event) {
  return SOURCE_META[event.source]?.label ?? event.source
}

function sourceClass(event) {
  return SOURCE_META[event.source]?.className ?? 'status-tag--neutral'
}

function formatDelta(event) {
  const prefix = event.delta > 0 ? '+' : ''
  return `${prefix}${event.delta}${unit.value}`
}

function formatStock(value) {
  if (value === null || value === undefined) return '—'
  return `${value}${unit.value}`
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

async function fetchAudit() {
  if (!props.consumableId) {
    audit.value = null
    error.value = ''
    return
  }
  loading.value = true
  error.value = ''
  cutoff.value = null
  try {
    const { data } = await api.get('/consumption/audit', {
      params: { consumable_id: props.consumableId },
    })
    audit.value = data
  } catch (requestError) {
    audit.value = null
    error.value =
      requestError.response?.status === 404 ? '该记录无法查看' : '库存变动读取失败'
  } finally {
    loading.value = false
  }
}

function handleViewSource() {
  if (sourceEvent.value) {
    emit('view-source', sourceEvent.value)
  }
}

watch(
  () => [props.modelValue, props.consumableId],
  ([open]) => {
    if (open) fetchAudit()
  },
  { immediate: true },
)

onMounted(() => window.addEventListener('resize', handleResize))

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
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
      <div class="audit-drawer__summary">
        <div class="audit-drawer__metric">
          <span class="audit-drawer__metric-label">当前库存</span>
          <span class="audit-drawer__metric-value">
            {{ audit ? formatStock(audit.current_stock) : '—' }}
          </span>
        </div>
        <div class="audit-drawer__metric">
          <span class="audit-drawer__metric-label">可确定期初库存</span>
          <span class="audit-drawer__metric-value">
            {{ audit ? formatStock(audit.opening_stock) : '—' }}
          </span>
        </div>
        <div class="audit-drawer__metric">
          <span class="audit-drawer__metric-label">一致性</span>
          <span
            v-if="audit"
            class="status-tag"
            :class="audit.consistent ? 'status-tag--success' : 'status-tag--warning'"
          >
            {{ audit.consistent ? '记录一致' : '需要核对' }}
          </span>
          <span v-else class="audit-drawer__metric-value">—</span>
        </div>
      </div>

      <el-alert
        v-if="audit && !audit.consistent"
        type="warning"
        :closable="false"
        show-icon
        title="库存变动记录需要核对"
        description="库存反推值与事件快照不一致，请结合来源类型与操作日志核对。"
      />

      <div v-if="error" class="audit-drawer__error">
        <p class="audit-drawer__error-text">{{ error }}</p>
        <el-button @click="fetchAudit">重试</el-button>
      </div>

      <div v-else-if="loading && !audit" class="audit-drawer__loading">
        <el-skeleton :rows="4" animated />
      </div>

      <template v-else-if="audit">
        <div class="audit-drawer__cutoff">
          <el-date-picker
            v-model="cutoff"
            type="datetime"
            value-format="YYYY-MM-DD HH:mm:ss"
            placeholder="选择时点"
            clearable
          />
          <span v-if="cutoff" class="audit-drawer__cutoff-stock">
            时点库存：{{ formatStock(cutoffStock) }}
          </span>
        </div>

        <el-table
          v-if="audit.events.length"
          v-loading="loading"
          :data="displayEvents"
          class="table-dense audit-drawer__table"
        >
          <el-table-column label="时间" min-width="160">
            <template #default="{ row }">{{ formatTime(row.time) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="92">
            <template #default="{ row }">
              <span class="status-tag" :class="sourceClass(row)">{{ sourceLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="库存增减" width="110" align="right">
            <template #default="{ row }">{{ formatDelta(row) }}</template>
          </el-table-column>
          <el-table-column label="变动后库存" width="120" align="right">
            <template #default="{ row }">{{ formatStock(row.stock_after) }}</template>
          </el-table-column>
        </el-table>

        <EmptyState
          v-else
          title="暂无库存变动记录"
          description="该耗材还没有领用、红冲或入库记录，期初库存暂时无法反推。"
        />
      </template>
    </div>

    <template #footer>
      <div class="audit-drawer__footer">
        <el-button v-if="sourceEvent" @click="handleViewSource">查看来源</el-button>
        <el-button type="primary" @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>
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
  gap: 24px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.audit-drawer__metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 120px;
}

.audit-drawer__metric-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.audit-drawer__metric-value {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__cutoff {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.audit-drawer__cutoff-stock {
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.audit-drawer__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.audit-drawer__error-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
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

  .audit-drawer__metric {
    min-width: 96px;
  }
}
</style>

<style>
.audit-drawer .el-drawer__body {
  padding: 0 20px 20px;
}
</style>