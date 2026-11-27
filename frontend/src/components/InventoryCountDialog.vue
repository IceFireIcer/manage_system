<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  consumables: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['update:modelValue', 'completed'])

const REASONS = [
  { value: '漏录', label: '漏录' },
  { value: '丢失', label: '丢失' },
  { value: '破损', label: '破损' },
  { value: '历史迁移', label: '历史迁移' },
  { value: '其他', label: '其他' },
]

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const step = ref('create')
const selectedIds = ref([])
const reviewerId = ref(null)
const remark = ref('')
const staff = ref([])
const count = ref(null)
const entries = reactive({})
const submitting = ref(false)
const posting = ref(false)
const errorMessage = ref('')
let requestId = ''


const totalAdjustment = computed(() => {
  if (!count.value) return 0
  return count.value.lines.reduce((sum, row) => {
    const counted = entries[row.consumable_id]?.counted_qty
    if (counted === null || counted === undefined) return sum
    return sum + (Number(counted) - row.baseline_stock)
  }, 0)
})

const allCounted = computed(() => {
  if (!count.value) return false
  return count.value.lines.every((row) => {
    const counted = entries[row.consumable_id]?.counted_qty
    return Number.isInteger(Number(counted)) && Number(counted) >= 0
  })
})

function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll('-', '')
  }
  return `cnt-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

function resetState() {
  step.value = 'create'
  selectedIds.value = []
  remark.value = ''
  count.value = null
  errorMessage.value = ''
  submitting.value = false
  posting.value = false
  requestId = newRequestId()
  Object.keys(entries).forEach((key) => delete entries[key])
}

async function fetchStaff() {
  try {
    const { data } = await api.get('/users', { params: { status: 'enabled' } })
    staff.value = (Array.isArray(data) ? data : []).filter((item) =>
      ['teacher', 'admin'].includes(item.role),
    )
  } catch {
    staff.value = []
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    resetState()
    fetchStaff()
  },
)

async function createCount() {
  if (submitting.value) return
  if (!selectedIds.value.length) {
    errorMessage.value = '请选择至少一项需要盘点的耗材'
    return
  }
  errorMessage.value = ''
  submitting.value = true
  try {
    const { data } = await api.post('/inventory/counts', {
      lines: selectedIds.value.map((id) => ({ consumable_id: id })),
      reviewer_id: reviewerId.value || null,
      remark: remark.value.trim() || null,
      request_id: requestId,
    })
    count.value = data
    data.lines.forEach((row) => {
      entries[row.consumable_id] = {
        counted_qty: row.counted_qty,
        reason: row.reason || '',
      }
    })
    step.value = 'entry'
  } catch (error) {
    if (!error.response) {
      errorMessage.value = '网络异常，盘点单是否创建成功未知，请关闭后重新进入核对。'
    } else if (error.response.status === 403) {
      errorMessage.value = '仅班委可以发起盘点。'
    } else {
      errorMessage.value = error.response.data?.detail || '盘点单创建失败，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}

function entryPayload() {
  return {
    lines: count.value.lines.map((row) => ({
      consumable_id: row.consumable_id,
      counted_qty: Number(entries[row.consumable_id]?.counted_qty ?? 0),
      reason: entries[row.consumable_id]?.reason || null,
    })),
  }
}

async function saveEntries() {
  if (!count.value) return false
  try {
    const { data } = await api.patch(
      `/inventory/counts/${count.value.order_no}/lines`,
      entryPayload(),
    )
    count.value = data
    return true
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '实盘量保存失败，请重试。'
    return false
  }
}

async function saveDraft() {
  if (submitting.value || posting.value) return
  if (!allCounted.value) {
    errorMessage.value = '请先为每一项耗材填写实盘量'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  const saved = await saveEntries()
  submitting.value = false
  if (saved) {
    ElMessage.success('实盘量已保存，可稍后继续过账')
  }
}

async function postCount() {
  if (!count.value || posting.value || submitting.value) return
  if (!allCounted.value) {
    errorMessage.value = '请先为每一项耗材填写实盘量'
    return
  }
  errorMessage.value = ''
  posting.value = true
  const saved = await saveEntries()
  if (!saved) {
    posting.value = false
    return
  }
  try {
    const { data } = await api.post(`/inventory/counts/${count.value.order_no}/post`)
    count.value = data
    step.value = 'posted'
    ElMessage.success(
      `盘点单 ${data.order_no} 已过账${data.warning ? '，存在需复核的基准差异' : ''}`,
    )
    emit('completed', data)
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '过账失败，请稍后重试。'
  } finally {
    posting.value = false
  }
}

function diffOf(row) {
  const counted = entries[row.consumable_id]?.counted_qty
  if (counted === null || counted === undefined || counted === '') return null
  return Number(counted) - row.baseline_stock
}

function formatDiff(value) {
  if (value === null) return '—'
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${value}`
}

function formatSigned(value, unit) {
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${value}${unit}`
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="库存盘点"
    width="760px"
    :close-on-click-modal="!posting"
    :close-on-press-escape="!posting"
  >
    <div class="count-dialog">
      <template v-if="step === 'create'">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="盘点耗材" required>
            <el-select
              v-model="selectedIds"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              placeholder="选择需要盘点的耗材"
              :disabled="submitting"
              class="count-dialog__control"
            >
              <el-option
                v-for="item in consumables"
                :key="item.id"
                :label="item.spec ? `${item.name}（${item.spec}）` : item.name"
                :value="item.id"
              />
            </el-select>
            <p class="count-dialog__hint">
              系统会在创建时冻结所选耗材的盘点时点系统量与执行序号。
            </p>
          </el-form-item>
          <el-form-item label="复核人">
            <el-select
              v-model="reviewerId"
              clearable
              placeholder="默认为当前操作人"
              :disabled="submitting"
              class="count-dialog__control"
            >
              <el-option
                v-for="member in staff"
                :key="member.id"
                :label="`${member.name}（${member.role === 'teacher' ? '教师' : '班委'}）`"
                :value="member.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="备注">
            <el-input
              v-model="remark"
              type="textarea"
              :rows="2"
              maxlength="255"
              show-word-limit
              :disabled="submitting"
              placeholder="可选，如盘点范围或依据"
            />
          </el-form-item>
        </el-form>
      </template>

      <template v-else>
        <div class="count-dialog__summary">
          <div class="count-dialog__row">
            <span class="count-dialog__label">盘点单号</span>
            <span class="count-dialog__value">{{ count.order_no }}</span>
          </div>
          <div class="count-dialog__row">
            <span class="count-dialog__label">盘点时点</span>
            <span class="count-dialog__value">{{ String(count.count_at).replace('T', ' ') }}</span>
          </div>
          <div class="count-dialog__row">
            <span class="count-dialog__label">盘点人 · 复核人</span>
            <span class="count-dialog__value">
              {{ count.counter_name }} · {{ count.reviewer_name }}
            </span>
          </div>
        </div>

        <el-alert
          v-if="count.recheck_required"
          type="warning"
          :closable="false"
          show-icon
          title="盘点期间库存基准已变化"
          description="系统保留盘点时点快照，并列出期间净变动，请核对后再过账。"
        />

        <el-table :data="count.lines" class="table-dense count-dialog__table" row-key="consumable_id">
          <el-table-column label="耗材" min-width="150">
            <template #default="{ row }">
              <div>{{ row.consumable_name }}</div>
              <div v-if="row.period_delta" class="count-dialog__hint">
                期间净变动 {{ formatSigned(row.period_delta, row.unit) }}，应有
                {{ row.expected_stock }}{{ row.unit }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="系统量" width="90" align="right">
            <template #default="{ row }">{{ row.baseline_stock }}{{ row.unit }}</template>
          </el-table-column>
          <el-table-column label="实盘量" width="140">
            <template #default="{ row }">
              <el-input-number
                v-model="entries[row.consumable_id].counted_qty"
                :min="0"
                :precision="0"
                :step="1"
                :disabled="step === 'posted'"
                controls-position="right"
                class="count-dialog__number"
              />
            </template>
          </el-table-column>
          <el-table-column label="差异" width="90" align="right">
            <template #default="{ row }">
              {{ formatDiff(diffOf(row)) }}
            </template>
          </el-table-column>
          <el-table-column label="原因" width="140">
            <template #default="{ row }">
              <el-select
                v-model="entries[row.consumable_id].reason"
                :disabled="step === 'posted'"
                placeholder="可选"
                class="count-dialog__control"
              >
                <el-option
                  v-for="reason in REASONS"
                  :key="reason.value"
                  :label="reason.label"
                  :value="reason.value"
                />
              </el-select>
            </template>
          </el-table-column>
        </el-table>

        <div class="count-dialog__totals">
          预计调整合计：{{ formatSigned(totalAdjustment, '') }}
        </div>

        <el-alert
          v-if="count.warning"
          type="warning"
          :closable="false"
          show-icon
          title="过账需复核"
          :description="count.warning"
        />
      </template>

      <el-alert
        v-if="errorMessage"
        type="error"
        :closable="false"
        show-icon
        title="无法完成盘点"
        :description="errorMessage"
      />
    </div>

    <template #footer>
      <el-button :disabled="posting" @click="visible = false">
        {{ step === 'posted' ? '关闭' : '取消' }}
      </el-button>
      <template v-if="step === 'create'">
        <el-button type="primary" :loading="submitting" :disabled="submitting" @click="createCount">
          创建盘点单
        </el-button>
      </template>
      <template v-else-if="step === 'entry'">
        <el-button :loading="submitting" :disabled="posting" @click="saveDraft">
          保存实盘量
        </el-button>
        <el-button type="primary" :loading="posting" :disabled="posting" @click="postCount">
          过账
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.count-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.count-dialog__control {
  width: 100%;
}

.count-dialog__number {
  width: 100%;
}

.count-dialog__summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.count-dialog__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.count-dialog__label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.count-dialog__value {
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.count-dialog__hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.count-dialog__totals {
  text-align: right;
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.count-dialog__table {
  width: 100%;
}
</style>