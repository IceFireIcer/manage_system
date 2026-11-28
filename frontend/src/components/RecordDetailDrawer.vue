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

const emit = defineEmits(['update:modelValue', 'closed', 'reverse', 'correct', 'return', 'scrap'])

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

const BACKFILL_META = {
  normal: '区间内补录',
  controlled: '超期受控调整',
  counted: '已盘点，关联调整',
  opening: '已计入期初',
}

function backfillModeLabel(mode) {
  return BACKFILL_META[mode] ?? mode
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

function unitLabel(record) {
  return record.unit || '待核对'
}

function quantityText(record) {
  const prefix = record.type === 'reversal' ? '+' : ''
  return `${prefix}${record.quantity} ${unitLabel(record)}`
}

function stockText(record) {
  return `${record.stock_after} ${unitLabel(record)}`
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

function handleReturn() {
  if (detail.value) {
    emit('return', detail.value)
  }
}

function handleScrap() {
  if (detail.value) {
    emit('scrap', detail.value)
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
          <el-button v-else @click="loadRecord(activeId)">重试</el-button>
        </div>
      </div>

      <template v-else-if="detail">
        <div class="record-drawer__summary">
          <span class="record-drawer__id">流水号 #{{ detail.id }}</span>
          <span class="status-tag" :class="typeMeta.className">{{ typeMeta.label }}</span>
          <span class="status-tag" :class="statusMeta.className">{{ statusMeta.label }}</span>
        </div>

        <section class="record-drawer__section">
          <h3 class="record-drawer__section-title">流水信息</h3>
          <dl class="record-drawer__grid">
            <div class="record-drawer__field">
              <dt>业务时间</dt>
              <dd>{{ formatTime(detail.created_at) }}</dd>
            </div>
            <div class="record-drawer__field">
              <dt>入账时间</dt>
              <dd>{{ formatTime(detail.posted_at) }}</dd>
            </div>
            <div class="record-drawer__field">
              <dt>领用人</dt>
              <dd>{{ detail.user_name }}</dd>
            </div>
            <div class="record-drawer__field">
              <dt>耗材</dt>
              <dd>{{ detail.consumable_name }}<span class="record-drawer__unit">（{{ unitLabel(detail) }}）</span></dd>
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
            <div v-if="detail.operator_name" class="record-drawer__field">
              <dt>经办人</dt>
              <dd>{{ detail.operator_name }}</dd>
            </div>
            <div v-if="detail.handler_name" class="record-drawer__field">
              <dt>处理人</dt>
              <dd>{{ detail.handler_name }}</dd>
            </div>
            <div v-if="detail.backfill_mode" class="record-drawer__field">
              <dt>入账口径</dt>
              <dd>{{ backfillModeLabel(detail.backfill_mode) }}</dd>
            </div>
            <div v-if="detail.linked_count_order_no" class="record-drawer__field">
              <dt>关联盘点单</dt>
              <dd>{{ detail.linked_count_order_no }}</dd>
            </div>
            <div
              v-else-if="detail.linked_event_id && detail.backfill_mode === 'opening'"
              class="record-drawer__field"
            >
              <dt>关联期初事件</dt>
              <dd>#{{ detail.linked_event_id }}</dd>
            </div>
            <div v-if="detail.backfill_reason" class="record-drawer__field record-drawer__field--full">
              <dt>补录原因</dt>
              <dd>{{ detail.backfill_reason }}</dd>
            </div>
            <div v-if="detail.reversal_reason" class="record-drawer__field record-drawer__field--full">
              <dt>红冲原因</dt>
              <dd>{{ detail.reversal_reason }}</dd>
            </div>
            <div v-if="detail.type === 'normal'" class="record-drawer__field record-drawer__field--full">
              <dt>可退 / 可报损数量</dt>
              <dd>
                {{ detail.returnable_quantity }}<span> {{ unitLabel(detail) }}</span>
                <span class="record-drawer__hint">
                  已退 {{ detail.returned_quantity }} · 已报损 {{ detail.scrapped_quantity }}
                </span>
              </dd>
            </div>
          </dl>
        </section>

        <section v-if="detail.correction" class="record-drawer__correction">
          <div class="record-drawer__correction-head">
            <h3 class="record-drawer__section-title">纠错申请 #{{ detail.correction.id }}</h3>
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
        </section>

        <section v-if="detail.origin || detail.reversal || detail.reentry" class="record-drawer__related">
          <h3 class="record-drawer__section-title">关联流水</h3>
          <div v-if="detail.origin" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">原领用 #{{ detail.origin.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.origin.created_at) }} · {{ detail.origin.quantity }} {{ unitLabel(detail) }}
                <template v-if="detail.origin.order_no"> · {{ detail.origin.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.origin.id)">查看原领用</el-button>
          </div>
          <div v-if="detail.reversal" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">红冲 #{{ detail.reversal.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.reversal.created_at) }} · +{{ detail.reversal.quantity }} {{ unitLabel(detail) }}
                <template v-if="detail.reversal.order_no"> · {{ detail.reversal.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.reversal.id)">查看红冲流水</el-button>
          </div>
          <div v-if="detail.reentry" class="record-drawer__related-row">
            <div>
              <span class="record-drawer__related-label">关联重登 #{{ detail.reentry.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(detail.reentry.created_at) }} · -{{ detail.reentry.quantity }} {{ unitLabel(detail) }}
                <template v-if="detail.reentry.order_no"> · {{ detail.reentry.order_no }}</template>
              </span>
            </div>
            <el-button link type="primary" @click="loadRecord(detail.reentry.id)">查看重登流水</el-button>
          </div>
        </section>

        <section v-if="detail.returns?.length || detail.scraps?.length" class="record-drawer__related">
          <h3 class="record-drawer__section-title">实物退库与报损</h3>
          <div
            v-for="item in detail.returns"
            :key="`return-${item.id}`"
            class="record-drawer__related-row"
          >
            <div>
              <span class="record-drawer__related-label">实物退库 #{{ item.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(item.created_at) }} · +{{ item.quantity }} {{ unitLabel(detail) }}
                · 原领用 #{{ item.requisition_id }}
                <template v-if="item.order_no"> · {{ item.order_no }}</template>
                <template v-if="item.reason"> · 原因：{{ item.reason }}</template>
                · 经办：{{ item.operator_name }}
              </span>
            </div>
            <span class="status-tag status-tag--success">已回加库存</span>
          </div>
          <div
            v-for="item in detail.scraps"
            :key="`scrap-${item.id}`"
            class="record-drawer__related-row"
          >
            <div>
              <span class="record-drawer__related-label">报损 #{{ item.id }}</span>
              <span class="record-drawer__related-meta">
                {{ formatTime(item.created_at) }} · 损耗 {{ item.quantity }} {{ unitLabel(detail) }}
                · 原领用 #{{ item.requisition_id }}
                <template v-if="item.order_no"> · {{ item.order_no }}</template>
                · 原因：{{ item.reason }}
                · 经办：{{ item.operator_name }}
              </span>
            </div>
            <span class="status-tag status-tag--neutral">不改变可用库存</span>
          </div>
        </section>
      </template>
    </div>

    <template #footer>
      <div class="record-drawer__footer">
        <el-button
          v-if="canOperate && detail.returnable_quantity > 0"
          @click="handleReturn"
        >
          实物退库
        </el-button>
        <el-button
          v-if="isStaff && canOperate && detail.returnable_quantity > 0"
          type="warning"
          plain
          @click="handleScrap"
        >
          登记报损
        </el-button>
        <el-button v-if="isStudent && canOperate" @click="handleCorrect">
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
  gap: var(--space-5);
}

.record-drawer__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.record-drawer__id {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.record-drawer__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.record-drawer__section-title {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text);
}

.record-drawer__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
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
  font-weight: 500;
  color: var(--text-muted);
}

.record-drawer__field dd {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.record-drawer__hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted);
}

.record-drawer__unit {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-muted);
}

.record-drawer__related {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid var(--line-soft);
}

.record-drawer__correction {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.record-drawer__correction-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.record-drawer__related-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
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
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid var(--line-soft);
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
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.record-drawer__footer .el-button + .el-button {
  margin-left: 0;
}

@media (max-width: 767px) {
  .record-drawer__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .record-drawer__footer .el-button {
    flex: 1 1 auto;
    min-height: 44px;
  }
}
</style>