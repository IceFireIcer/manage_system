<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../api'
import { useUserStore } from '../stores/user'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  recordId: {
    type: [Number, String],
    default: null,
  },
  refreshToken: {
    type: Number,
    default: 0,
  },
})

const emit = defineEmits(['update:modelValue', 'closed', 'reverse', 'correct'])

const TYPE_META = {
  normal: { label: '领用', className: 'status-tag--info' },
  reversal: { label: '红冲', className: 'status-tag--reversal' },
}

const STATUS_META = {
  active: { label: '有效', className: 'status-tag--success' },
  reversed: { label: '已撤销', className: 'status-tag--neutral' },
}

const CORRECTION_META = {
  submitted: { label: '待教师处理', className: 'status-tag--warning' },
  accepted: { label: '已处理', className: 'status-tag--success' },
  rejected: { label: '已驳回', className: 'status-tag--neutral' },
}

const userStore = useUserStore()
const loading = ref(false)
const error = ref('')
const notFound = ref(false)
const detail = ref(null)
const activeId = ref(null)
const viewportWidth = ref(window.innerWidth)
let requestSerial = 0

const isStaff = computed(() =>
  ['teacher', 'admin'].includes(userStore.user?.role),
)

const isStudent = computed(() => userStore.user?.role === 'student')

const canOperate = computed(
  () => detail.value?.type === 'normal' && detail.value?.status === 'active',
)

const correctionMeta = computed(() =>
  detail.value?.correction
    ? CORRECTION_META[detail.value.correction.status] ?? {
        label: detail.value.correction.status,
        className: 'status-tag--neutral',
      }
    : null,
)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const drawerSize = computed(() => (viewportWidth.value < 768 ? '100%' : '600px'))

const drawerTitle = computed(() =>
  activeId.value ? `流水详情 · #${activeId.value}` : '流水详情',
)

const typeMeta = computed(() => TYPE_META[detail.value?.type] ?? { label: detail.value?.type ?? '', className: 'status-tag--neutral' })

const statusMeta = computed(() => STATUS_META[detail.value?.status] ?? { label: detail.value?.status ?? '', className: 'status-tag--neutral' })

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function quantityText(record) {
  const prefix = record.type === 'reversal' ? '+' : ''
  return `${prefix}${record.quantity}${record.unit ? ` ${record.unit}` : ''}`
}

function stockText(record) {
  return `${record.stock_after}${record.unit ? ` ${record.unit}` : ''}`
}

async function loadRecord(id) {
  if (!id) {
    detail.value = null
    error.value = ''
    return
  }
  const serial = ++requestSerial
  activeId.value = Number(id)
  loading.value = true
  error.value = ''
  notFound.value = false
  detail.value = null
  try {
    const { data } = await api.get(`/requisitions/${id}`)
    if (serial !== requestSerial) return
    detail.value = data
  } catch (requestError) {
    if (serial !== requestSerial) return
    if (requestError.response?.status === 404) {
      notFound.value = true
      error.value = '这条记录不可查看'
    } else if (requestError.response?.status === 403) {
      notFound.value = true
      error.value = '这条记录不可查看'
    } else {
      error.value = '记录读取失败，请稍后重试'
    }
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

function handleClosed() {
  emit('closed')
}

function handleReverse() {
  if (detail.value) {
    emit('reverse', detail.value)
  }
}

function handleCorrect() {
  if (detail.value) {
    emit('correct', detail.value)
  }
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

watch(
  () => [props.modelValue, props.recordId, props.refreshToken],
  ([open, id]) => {
    if (open && id) {
      loadRecord(id)
    }
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
    class="record-drawer"
    destroy-on-close
    @closed="handleClosed"
  >
    <div class="record-drawer__body">
      <div v-if="loading && !detail" class="record-drawer__loading">
        <el-skeleton :rows="6" animated />
      </div>

      <div v-else-if="error" class="record-drawer__error" role="alert">
        <p class="record-drawer__error-text">{{ error }}</p>
        <div class="record-drawer__error-actions">
          <el-button v-if="notFound" @click="visible = false">返回列表</el-button>
          <el-button v-else type="primary" plain @click="loadRecord(activeId)">重试</el-button>
        </div>
      </div>

      <template v-else-if="detail">
        <div class="record-drawer__summary">
          <span class="record-drawer__id">流水号 #{{ detail.id }}</span>
          <span class="status-tag" :class="typeMeta.className">{{ typeMeta.label }}</span>
          <span class="status-tag" :class="statusMeta.className">{{ statusMeta.label }}</span>
        </div>

        <dl class="record-drawer__grid">
          <div class="record-drawer__field">
            <dt>业务时间</dt>
            <dd>{{ formatTime(detail.created_at) }}</dd>
          </div>
          <div class="record-drawer__field">
            <dt>领用人</dt>
            <dd>{{ detail.user_name }}</dd>
          </div>
          <div class="record-drawer__field">
            <dt>耗材</dt>
            <dd>{{ detail.consumable_name }}<span v-if="detail.unit" class="record-drawer__unit">（{{ detail.unit }}）</span></dd>
          </div>
          <div class="record-drawer__field">
            <dt>数量</dt>
            <dd>{{ quantityText(detail) }}</dd>
          </div>
          <div class="record-drawer__field">
            <dt>变动后库存</dt>
            <dd>
              {{ stockText(detail) }}
              <span class="record-drawer__hint">历史快照</span>
            </dd>
          </div>
          <div class="record-drawer__field">
            <dt>备注</dt>
            <dd>{{ detail.remark || '—' }}</dd>
          </div>
          <div v-if="detail.order_no" class="record-drawer__field">
            <dt>单据编号</dt>
            <dd>{{ detail.order_no }}</dd>
          </div>
          <div v-if="detail.handler_name" class="record-drawer__field">
            <dt>经办人</dt>
            <dd>{{ detail.handler_name }}</dd>
          </div>
          <div v-if="detail.reversal_reason" class="record-drawer__field record-drawer__field--full">
            <dt>红冲原因</dt>
            <dd>{{ detail.reversal_reason }}</dd>
          </div>
        </dl>

        <div v-if="detail.correction" class="record-drawer__correction">
          <div class="record-drawer__correction-head">
            <h3 class="record-drawer__related-title">纠错申请 #{{ detail.correction.id }}</h3>
            <span class="status-tag" :class="correctionMeta.className">{{ correctionMeta.label }}</span>
          </div>
          <dl class="record-drawer__grid">
            <div class="record-drawer__field record-drawer__field--full">
              <dt>申请原因</dt>
              <dd>{{ detail.correction.reason }}</dd>
            </div>
            <div class="record-drawer__field">
              <dt>申请人</dt>
              <dd>{{ detail.correction.applicant_name }}</dd>
            </div>
            <div class="record-drawer__field">
              <dt>申请时间</dt>
              <dd>{{ formatTime(detail.correction.created_at) }}</dd>
            </div>
            <div v-if="detail.correction.remark" class="record-drawer__field record-drawer__field--full">
              <dt>补充说明</dt>
              <dd>{{ detail.correction.remark }}</dd>
            </div>
            <template v-if="detail.correction.status !== 'submitted'">
              <div class="record-drawer__field">
                <dt>处理人</dt>
                <dd>{{ detail.correction.handler_name || '—' }}</dd>
              </div>
              <div class="record-drawer__field">
                <dt>处理时间</dt>
                <dd>{{ formatTime(detail.correction.handled_at) }}</dd>
              </div>
              <div class="record-drawer__field record-drawer__field--full">
                <dt>处理结果</dt>
                <dd>{{ detail.correction.handling_note || '—' }}</dd>
              </div>
              <div v-if="detail.correction.reversal_order_no" class="record-drawer__field">
                <dt>红冲单号</dt>
                <dd>{{ detail.correction.reversal_order_no }}</dd>
              </div>
              <div v-if="detail.correction.reentry_order_no" class="record-drawer__field">
                <dt>重登单号</dt>
                <dd>{{ detail.correction.reentry_order_no }}</dd>
              </div>
            </template>
          </dl>
        </div>

        <div v-if="detail.origin || detail.reversal || detail.reentry" class="record-drawer__related">
          <h3 class="record-drawer__related-title">关联流水</h3>
          <div v-if="detail.origin" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">原领用 #{{ detail.origin.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.origin.created_at) }} · {{ detail.origin.quantity }}{{ detail.unit ? ` ${detail.unit}` : '' }}
                <template v-if="detail.origin.order_no"> · {{ detail.origin.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.origin.id)">查看原领用</el-button>
          </div>
          <div v-if="detail.reversal" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">红冲 #{{ detail.reversal.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.reversal.created_at) }} · +{{ detail.reversal.quantity }}{{ detail.unit ? ` ${detail.unit}` : '' }}
                <template v-if="detail.reversal.order_no"> · {{ detail.reversal.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.reversal.id)">查看红冲流水</el-button>
          </div>
          <div v-if="detail.reentry" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">关联重登 #{{ detail.reentry.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.reentry.created_at) }} · -{{ detail.reentry.quantity }}{{ detail.unit ? ` ${detail.unit}` : '' }}
                <template v-if="detail.reentry.order_no"> · {{ detail.reentry.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.reentry.id)">查看重登流水</el-button>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="record-drawer__footer">
        <el-button v-if="isStudent && canOperate" type="primary" plain @click="handleCorrect">
          申请纠错
        </el-button>
        <el-button v-if="isStaff && canOperate" type="danger" @click="handleReverse">
          红冲并更正
        </el-button>
        <el-button type="primary" @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.record-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.record-drawer__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.record-drawer__id {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.record-drawer__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 24px;
  margin: 0;
}

.record-drawer__field {
  margin: 0;
  min-width: 0;
}

.record-drawer__field--full {
  grid-column: 1 / -1;
}

.record-drawer__field dt {
  margin-bottom: 4px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.record-drawer__field dd {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.record-drawer__hint {
  margin-left: 8px;
  font-size: 13px;
  color: var(--text-muted);
}

.record-drawer__unit {
  font-size: 13px;
  color: var(--text-muted);
}

.record-drawer__related {
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.record-drawer__correction {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.record-drawer__correction-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.record-drawer__correction-head .record-drawer__related-title {
  margin-bottom: 0;
}

.record-drawer__related-title {
  margin: 0 0 12px;
}

.record-drawer__related-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.record-drawer__related-row + .record-drawer__related-row {
  margin-top: 8px;
}

.record-drawer__related-label {
  display: block;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  font-weight: 600;
}

.record-drawer__related-meta {
  display: block;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.record-drawer__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.record-drawer__error-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.record-drawer__error-actions {
  display: flex;
  gap: 8px;
}

.record-drawer__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .record-drawer__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

<style>
.record-drawer .el-drawer__body {
  padding: 0 20px 20px;
}
</style>