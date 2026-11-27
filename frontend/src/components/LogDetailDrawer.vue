<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  log: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue', 'view-requisition', 'view-consumable'])

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生', operator: '校方授权人' }
const STATUS_NAMES = { enabled: '启用', disabled: '停用' }
const TARGET_NAMES = { user: '用户', consumable: '耗材', requisition: '领用流水' }

const ACTION_NAMES = {
  login: '登录',
  logout: '退出登录',
  requisition_create: '领用登记',
  requisition_reverse: '撤销领用',
  restock: '补货入库',
  consumable_create: '耗材建档',
  consumable_update: '耗材修改',
  consumable_disable: '耗材停用',
  consumable_enable: '耗材启用',
  user_create: '新增用户',
  user_enable: '启用用户',
  user_disable: '停用用户',
  admin_bootstrap: '初始化管理员',
  teacher_provision: '开通教师',
  admin_handover: '班委交接',
  admin_handover_create: '开通班委',
  admin_recover: '管理员恢复',
  admin_bootstrap_rejected: '初始化被拒',
  teacher_provision_rejected: '开通教师被拒',
  admin_handover_rejected: '班委交接被拒',
  admin_recover_rejected: '管理员恢复被拒',
}

const FIELD_LABELS = {
  name: '名称',
  spec: '规格',
  unit: '单位',
  location: '存放位置',
  safety_stock: '安全库存线',
  current_stock: '当前库存',
  status: '状态',
  account: '账号',
  role: '角色',
  quantity: '数量',
  stock_before: '变动前库存',
  stock_after: '变动后库存',
  reason: '原因',
  row_count: '明细行数',
  total_quantity: '合计数量',
  owner_id: '经办人 ID',
  consumable_id: '耗材 ID',
  requisition_id: '领用流水号',
  reversal_id: '红冲流水号',
  created_at: '业务时间',
  items: '明细',
}

const FIELD_ORDER = [
  'name',
  'spec',
  'unit',
  'location',
  'account',
  'role',
  'status',
  'safety_stock',
  'current_stock',
  'stock_before',
  'stock_after',
  'quantity',
  'reason',
  'row_count',
  'total_quantity',
  'owner_id',
  'consumable_id',
  'requisition_id',
  'reversal_id',
  'created_at',
  'items',
]

const STOCK_FIELDS = new Set([
  'safety_stock',
  'current_stock',
  'stock_before',
  'stock_after',
  'quantity',
  'total_quantity',
])

const HIDDEN_KEYS = new Set([
  'password',
  'password_hash',
  'secret',
  'token',
  'access_token',
  'refresh_token',
  'api_key',
  'private_key',
  'salt',
])

const viewportWidth = ref(window.innerWidth)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const drawerSize = computed(() => (viewportWidth.value < 768 ? '100%' : '600px'))

const beforeSnapshot = computed(() => parseSnapshot(props.log?.before_value))
const afterSnapshot = computed(() => parseSnapshot(props.log?.after_value))
const rawBefore = computed(() => rawText(props.log?.before_value))
const rawAfter = computed(() => rawText(props.log?.after_value))
const hasBefore = computed(() => Boolean(beforeSnapshot.value) || Boolean(rawBefore.value))
const hasAfter = computed(() => Boolean(afterSnapshot.value) || Boolean(rawAfter.value))

const unitText = computed(
  () => afterSnapshot.value?.unit || beforeSnapshot.value?.unit || '',
)

const targetLabel = computed(() => {
  const log = props.log
  if (!log) return '—'
  const typeName = TARGET_NAMES[log.target_type] ?? log.target_type ?? '—'
  if (!log.target_id) return typeName
  const name =
    afterSnapshot.value?.name ||
    beforeSnapshot.value?.name ||
    afterSnapshot.value?.account ||
    beforeSnapshot.value?.account ||
    rawAfter.value ||
    rawBefore.value ||
    ''
  return name ? `${typeName} #${log.target_id}（${name}）` : `${typeName} #${log.target_id}`
})

const metaLine = computed(() => {
  if (!props.log) return ''
  const actionName = ACTION_NAMES[props.log.action] ?? props.log.action
  return `${actionName} · ${targetLabel.value} · ${formatTime(props.log.created_at)}`
})

const rows = computed(() => {
  const before = beforeSnapshot.value
  const after = afterSnapshot.value
  if (!before && !after) {
    if (!rawBefore.value && !rawAfter.value) return []
    return [
      {
        key: 'content',
        label: '记录内容',
        before: rawBefore.value || null,
        after: rawAfter.value || null,
        changed: rawBefore.value !== rawAfter.value,
      },
    ]
  }
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ])
  const ordered = [
    ...FIELD_ORDER.filter((key) => keys.has(key)),
    ...[...keys].filter((key) => !FIELD_ORDER.includes(key)).sort(),
  ].filter((key) => !isHiddenKey(key))
  return ordered.map((key) => ({
    key,
    label: FIELD_LABELS[key] ?? key,
    before: formatValue(key, before?.[key], unitText.value),
    after: formatValue(key, after?.[key], unitText.value),
    changed: stableValue(before?.[key]) !== stableValue(after?.[key]),
  }))
})

const canViewRequisition = computed(
  () => props.log?.target_type === 'requisition' && Boolean(props.log?.target_id),
)

const canViewConsumable = computed(
  () => props.log?.target_type === 'consumable' && Boolean(props.log?.target_id),
)

const consumableName = computed(
  () =>
    afterSnapshot.value?.name ||
    beforeSnapshot.value?.name ||
    '',
)

function parseSnapshot(value) {
  if (!value) return null
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function rawText(value) {
  if (!value) return ''
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? '' : String(parsed)
  } catch {
    return String(value)
  }
}

function isHiddenKey(key) {
  const lower = key.toLowerCase()
  return (
    HIDDEN_KEYS.has(lower) ||
    lower.includes('password') ||
    lower.includes('secret') ||
    lower.includes('token') ||
    lower.endsWith('_key')
  )
}

function stableValue(value) {
  if (value === undefined || value === null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function formatTime(value) {
  if (!value) return '—'
  const text = String(value).replace('T', ' ')
  return text.length >= 19 ? text.slice(0, 19) : text
}

function describeItem(item) {
  if (!item || typeof item !== 'object') return String(item)
  const name = item.name || item.consumable_name || `耗材 #${item.consumable_id ?? ''}`
  const quantity = item.quantity ?? ''
  const unit = item.unit ?? ''
  return `${name} ${quantity}${unit}`.trim()
}

function formatValue(key, value, unit) {
  if (value === undefined || value === null || value === '') return null
  if (key === 'status') return STATUS_NAMES[value] ?? String(value)
  if (key === 'role') return ROLE_NAMES[value] ?? String(value)
  if (key === 'created_at') return formatTime(value)
  if (Array.isArray(value)) return value.map((item) => describeItem(item)).join('；')
  if (typeof value === 'object') return JSON.stringify(value)
  if (STOCK_FIELDS.has(key) && unit) return `${value}${unit}`
  return String(value)
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

function handleViewRequisition() {
  emit('view-requisition', props.log?.target_id)
}

function handleViewConsumable() {
  emit('view-consumable', { id: props.log?.target_id, name: consumableName.value })
}

onMounted(() => window.addEventListener('resize', handleResize))

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<template>
  <el-drawer
    v-model="visible"
    title="操作详情"
    :size="drawerSize"
    class="log-detail-drawer"
    destroy-on-close
  >
    <div v-if="log" class="log-detail">
      <p class="log-detail__sub">{{ metaLine }}</p>

      <div class="log-detail__meta">
        <div class="log-detail__meta-item">
          <span class="log-detail__meta-label">操作人</span>
          <span class="log-detail__meta-value">{{ log.operator_name ?? '—' }}</span>
        </div>
        <div class="log-detail__meta-item">
          <span class="log-detail__meta-label">角色</span>
          <span class="log-detail__meta-value">
            {{ ROLE_NAMES[log.role] ?? log.role ?? '—' }}
          </span>
        </div>
        <div class="log-detail__meta-item">
          <span class="log-detail__meta-label">操作类型</span>
          <span class="log-detail__meta-value">{{ ACTION_NAMES[log.action] ?? log.action }}</span>
        </div>
        <div class="log-detail__meta-item">
          <span class="log-detail__meta-label">对象</span>
          <span class="log-detail__meta-value">{{ targetLabel }}</span>
        </div>
      </div>

      <div class="log-detail__compare">
        <section class="log-detail__side">
          <h3 class="log-detail__side-title">变更前</h3>
          <p v-if="!hasBefore" class="log-detail__none">无变更前记录</p>
          <ul v-else class="log-detail__list">
            <li
              v-for="row in rows"
              :key="`before-${row.key}`"
              class="log-detail__row"
              :class="{ 'log-detail__row--changed': row.changed }"
            >
              <span class="log-detail__label">{{ row.label }}</span>
              <span class="log-detail__value">{{ row.before ?? '无记录' }}</span>
            </li>
          </ul>
        </section>

        <section class="log-detail__side">
          <h3 class="log-detail__side-title">变更后</h3>
          <p v-if="!hasAfter" class="log-detail__none">无变更后记录</p>
          <ul v-else class="log-detail__list">
            <li
              v-for="row in rows"
              :key="`after-${row.key}`"
              class="log-detail__row"
              :class="{ 'log-detail__row--changed': row.changed }"
            >
              <span class="log-detail__label">{{ row.label }}</span>
              <span class="log-detail__value">{{ row.after ?? '无记录' }}</span>
            </li>
          </ul>
        </section>
      </div>

      <section v-if="log.remark" class="log-detail__remark">
        <h3 class="log-detail__side-title">备注</h3>
        <p class="log-detail__remark-text">{{ log.remark }}</p>
      </section>
    </div>

    <EmptyState
      v-else
      title="操作记录不可用"
      description="该操作记录不存在或已失效，请关闭后重新查询。"
    />

    <template #footer>
      <div class="log-detail__footer">
        <div class="log-detail__footer-left">
          <el-button v-if="canViewRequisition" @click="handleViewRequisition">
            查看领用流水
          </el-button>
          <el-button v-if="canViewConsumable" @click="handleViewConsumable">
            查库存变动
          </el-button>
        </div>
        <el-button type="primary" @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.log-detail {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.log-detail__sub {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.log-detail__meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 16px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.log-detail__meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.log-detail__meta-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.log-detail__meta-value {
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  word-break: break-word;
}

.log-detail__compare {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.log-detail__side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.log-detail__side-title {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text-secondary);
}

.log-detail__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.log-detail__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.log-detail__row--changed {
  background: var(--primary-soft);
}

.log-detail__row--changed .log-detail__label {
  color: var(--primary);
}

.log-detail__label {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.log-detail__value {
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.log-detail__none {
  margin: 0;
  padding: 16px;
  border: 1px dashed var(--line);
  border-radius: var(--radius-control);
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.log-detail__remark {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.log-detail__remark-text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
  white-space: pre-line;
}

.log-detail__footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.log-detail__footer-left {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 767px) {
  .log-detail__compare {
    grid-template-columns: minmax(0, 1fr);
  }

  .log-detail__footer {
    justify-content: flex-end;
  }

  .log-detail__footer-left {
    width: 100%;
  }
}
</style>

<style>
.log-detail-drawer .el-drawer__body {
  padding: 0 20px 20px;
}
</style>