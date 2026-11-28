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
const RESULT_NAMES = { success: '成功', failure: '失败', rejected: '已拒绝' }
const TARGET_NAMES = {
  user: '用户',
  consumable: '耗材',
  requisition: '领用流水',
  requisition_order: '领用单',
  requisition_correction: '纠错申请',
  operation_log: '操作日志',
  class: '班级',
  class_term: '学期',
  role: '角色',
  llm_provider: 'LLM 供应商',
  llm_prompt: 'LLM 提示词',
  llm_binding: 'LLM 功能绑定',
  assistant_tool: '助手工具',
  system: '系统',
}

const STATUS_ACTIONS = new Set([
  'user_enable',
  'user_disable',
  'consumable_enable',
  'consumable_disable',
])

const ACTION_NAMES = {
  login: '登录',
  logout: '退出登录',
  requisition_create: '领用登记',
  requisition_reverse: '撤销领用',
  requisition_reentry: '关联重登',
  requisition_correction_submit: '纠错申请',
  requisition_correction_accept: '纠错受理',
  requisition_correction_handle: '纠错处理',
  restock: '补货入库',
  restock_correction: '入库纠错',
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
  role_switch: '角色切换',
  switch_class: '班级切换',
  setup_complete: '完成系统初始化',
  account_create: '创建账号',
  account_update: '更新账号',
  account_status: '账号状态变更',
  account_password_reset: '重置账号密码',
  account_role_update: '账号角色变更',
  account_class_update: '账号班级变更',
  permission_override_update: '权限覆盖',
  permission_role_update: '角色权限变更',
  class_create: '创建班级',
  class_update: '更新班级',
  class_delete: '删除班级',
  class_term_create: '创建学期',
  class_term_update: '更新学期',
  class_term_delete: '删除学期',
  class_teacher_assign: '分配教师班级',
  class_teacher_remove: '移除教师班级',
  llm_provider_create: '创建 LLM 供应商',
  llm_provider_update: '更新 LLM 供应商',
  llm_provider_delete: '删除 LLM 供应商',
  llm_provider_test: '测试 LLM 供应商',
  llm_prompt_create: '创建 LLM 提示词',
  llm_prompt_update: '更新 LLM 提示词',
  llm_prompt_activate: '启用 LLM 提示词',
  llm_binding_update: '更新 LLM 绑定',
  assistant_tool_call: '助手工具调用',
  profile_update: '更新个人资料',
  profile_admin_update: '维护成员资料',
  profile_avatar_update: '更新头像',
  audit_export: '导出审计',
  log_export: '导出操作日志',
  requisition_export: '导出领用流水',
  consumable_import: '导入耗材台账',
  consumable_conflict: '耗材导入冲突',
  inventory_adjustment: '库存调整',
  requisition_backfill_controlled: '受控补录',
  requisition_return: '退库登记',
  requisition_scrap: '报损登记',
  suggestion_feedback: '补货建议反馈',
  user_update: '更正用户',
  user_password_reset: '重置密码',
  system_setting_update: '更新系统参数',
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
  reversal_order_no: '红冲单号',
  reentry_id: '重登流水号',
  reentry_order_no: '重登单号',
  reentry_of: '来源红冲流水',
  correction_id: '纠错申请号',
  handling_note: '处理结果',
  event_id: '库存事件号',
  created_at: '业务时间',
  items: '明细',
  code: '编码',
  class_id: '班级 ID',
  class_name: '班级',
  roles: '角色',
  must_change_password: '需改密',
  permission_key: '权限键',
  allowed: '允许',
  default_enabled: '默认启用',
  toggleable_by_teacher: '教师可调',
  locked: '锁定',
  updates: '调整项',
  feature: '功能',
  model: '模型',
  enabled: '启用',
  active: '激活',
  is_default: '默认',
  version: '版本',
  start_date: '开始日期',
  end_date: '结束日期',
  tool: '工具',
  args: '参数',
  summary: '摘要',
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
  'reversal_order_no',
  'reentry_id',
  'reentry_order_no',
  'reentry_of',
  'correction_id',
  'handling_note',
  'event_id',
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
  if (!log.target_id) {
    return log.target_label ? `${typeName}（${log.target_label}）` : typeName
  }
  const name =
    log.target_label ||
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
    if (STATUS_ACTIONS.has(props.log?.action)) {
      const beforeStatus = props.log?.before_status
      const afterStatus = props.log?.after_status
      if (beforeStatus || afterStatus) {
        return [
          {
            key: 'status',
            label: '状态',
            before: STATUS_NAMES[beforeStatus] ?? beforeStatus ?? null,
            after: STATUS_NAMES[afterStatus] ?? afterStatus ?? null,
            changed: beforeStatus !== afterStatus,
          },
        ]
      }
    }
    if (!rawBefore.value && !rawAfter.value) return []
    const withStatus = STATUS_ACTIONS.has(props.log?.action)
    return [
      {
        key: withStatus ? 'status' : 'content',
        label: withStatus ? '状态' : '记录内容',
        before: withStatus
          ? (STATUS_NAMES[rawBefore.value] ?? rawBefore.value) || null
          : rawBefore.value || null,
        after: withStatus
          ? (STATUS_NAMES[rawAfter.value] ?? rawAfter.value) || null
          : rawAfter.value || null,
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

      <dl class="log-detail__meta">
        <div class="log-detail__meta-item">
          <dt>操作人</dt>
          <dd>{{ log.operator_name ?? '—' }}</dd>
        </div>
        <div class="log-detail__meta-item">
          <dt>角色</dt>
          <dd>{{ ROLE_NAMES[log.role] ?? log.role ?? '—' }}</dd>
        </div>
        <div class="log-detail__meta-item">
          <dt>操作类型</dt>
          <dd>{{ ACTION_NAMES[log.action] ?? log.action }}</dd>
        </div>
        <div class="log-detail__meta-item">
          <dt>结果</dt>
          <dd>{{ RESULT_NAMES[log.result] ?? log.result ?? '—' }}</dd>
        </div>
        <div class="log-detail__meta-item">
          <dt>对象</dt>
          <dd>{{ targetLabel }}</dd>
        </div>
      </dl>

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
      description="请关闭后重新查询。"
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
  gap: 24px;
}

.log-detail__sub {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.log-detail__meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px 16px;
  margin: 0;
  padding: 16px;
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.log-detail__meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.log-detail__meta-item dt {
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.log-detail__meta-item dd {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
  word-break: break-word;
}

.log-detail__compare {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
}

.log-detail__side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.log-detail__side-title {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text-secondary);
}

.log-detail__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.log-detail__row {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 8px;
  border-radius: var(--radius-xs);
}

.log-detail__row--changed {
  background: var(--warning-soft);
}

.log-detail__row--changed .log-detail__label {
  color: var(--warning);
}

.log-detail__label {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-muted);
}

.log-detail__value {
  font-size: 13px;
  line-height: 20px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.log-detail__none {
  margin: 0;
  padding: 12px 16px;
  border: 1px dashed var(--line);
  border-radius: var(--radius-control);
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.log-detail__remark {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

.log-detail__remark-text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
  white-space: pre-line;
}

.log-detail__footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.log-detail__footer-left {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.log-detail__footer .el-button + .el-button {
  margin-left: 0;
}

@media (max-width: 767px) {
  .log-detail__compare {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }

  .log-detail__footer .el-button {
    min-height: 44px;
  }
}
</style>

<style>
.log-detail-drawer .el-drawer__body {
  padding: 0 24px 24px;
}

@media (max-width: 767px) {
  .log-detail-drawer .el-drawer__body {
    padding: 0 16px 24px;
  }
}
</style>