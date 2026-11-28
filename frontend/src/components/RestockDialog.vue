<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { businessToday } from '../utils/businessDate'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  consumable: {
    type: Object,
    default: null,
  },
  recommendedQuantity: {
    type: Number,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue', 'completed'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const form = reactive({
  quantity: null,
  receivedDate: businessToday(),
  source: '',
  remark: '',
})
const submitting = ref(false)
const fieldError = ref('')
const errorMessage = ref('')
const receipt = ref(null)
const coverage = ref(null)
const coverageLoading = ref(false)
const coverageError = ref('')
let requestId = ''
let lastSignature = ''

const RISK_LABELS = {
  sufficient: '充足',
  critical: '临界',
  insufficient: '不足',
}

function riskLabel(level) {
  return RISK_LABELS[level] ?? level
}

const resultDescription = computed(() => {
  if (!receipt.value) return ''
  const lines = Array.isArray(receipt.value.lines) ? receipt.value.lines : []
  return lines
    .map(
      (line) =>
        `${line.consumable_name} 实收 ${line.quantity}${line.unit}，当前库存 ${line.stock_after}${line.unit}`,
    )
    .join('；')
})

const unit = computed(() => props.consumable?.unit ?? '')

const expectedStock = computed(() => {
  const stock = Number(props.consumable?.current_stock)
  const quantity = Number(form.quantity)
  if (!Number.isFinite(stock) || !Number.isInteger(quantity) || quantity <= 0) return null
  return stock + quantity
})

function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll('-', '')
  }
  return `rst-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const recommended = Number(props.recommendedQuantity)
    form.quantity = Number.isInteger(recommended) && recommended > 0 ? recommended : null
    form.receivedDate = businessToday()
    form.source = ''
    form.remark = ''
    fieldError.value = ''
    errorMessage.value = ''
    receipt.value = null
    coverage.value = null
    coverageLoading.value = false
    coverageError.value = ''
    submitting.value = false
    requestId = newRequestId()
    lastSignature = ''
  },
)

function clearQuantityError() {
  if (fieldError.value) fieldError.value = ''
}

async function submit() {
  if (submitting.value || !props.consumable) return
  const quantity = Number(form.quantity)
  if (!Number.isInteger(quantity) || quantity <= 0) {
    fieldError.value = '请输入正整数实收数量'
    return
  }
  fieldError.value = ''
  errorMessage.value = ''
  submitting.value = true
  const signature = JSON.stringify([
    props.consumable.id,
    quantity,
    form.receivedDate,
    form.source.trim(),
    form.remark.trim(),
  ])
  if (signature !== lastSignature) {
    requestId = newRequestId()
    lastSignature = signature
  }
  try {
    const { data } = await api.post('/inventory/restock/orders', {
      items: [{ consumable_id: props.consumable.id, quantity }],
      received_at: form.receivedDate ? `${form.receivedDate}T00:00:00` : null,
      source: form.source.trim() || null,
      remark: form.remark.trim() || null,
      request_id: requestId,
    })
    const line = Array.isArray(data.lines) ? data.lines[0] : null
    ElMessage.success(
      `已生成入库单 ${data.order_no}${line ? `，当前库存 ${line.stock_after}${line.unit}` : ''}`,
    )
    emit('completed', data)
    receipt.value = data
    coverage.value = null
    coverageError.value = ''
    coverageLoading.value = true
    try {
      const { data: coverageData } = await api.get(
        '/courses/risk/arrival-coverage',
        { params: { order_no: data.order_no, days: 7 } },
      )
      coverage.value = coverageData
    } catch {
      coverageError.value = '到货覆盖信息读取失败，不影响本次入库结果。'
    } finally {
      coverageLoading.value = false
    }
  } catch (error) {
    if (!error.response) {
      errorMessage.value = '网络异常或请求超时，暂未确认本次入库是否成功，可重试或按单号查询。'
    } else if ([400, 404, 409, 422].includes(error.response.status)) {
      errorMessage.value = error.response.data?.detail || '该耗材当前无法入库。'
    } else if (error.response.status === 403) {
      errorMessage.value = '仅班委可以登记入库。'
    } else {
      errorMessage.value = '入库失败，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="登记实际到货"
    width="520px"
    :close-on-click-modal="!submitting"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
  >
    <div v-if="consumable && !receipt" class="receive-dialog">
      <dl class="receive-dialog__summary">
        <div class="receive-dialog__row">
          <dt class="receive-dialog__label">耗材</dt>
          <dd class="receive-dialog__value">
            {{ consumable.name }}<span v-if="consumable.spec">（{{ consumable.spec }}）</span>
          </dd>
        </div>
        <div class="receive-dialog__row">
          <dt class="receive-dialog__label">当前库存</dt>
          <dd class="receive-dialog__value">
            {{ consumable.current_stock }}{{ consumable.unit }}
          </dd>
        </div>
        <div class="receive-dialog__row">
          <dt class="receive-dialog__label">安全库存线</dt>
          <dd class="receive-dialog__value">
            {{ consumable.safety_stock }}{{ consumable.unit }}
          </dd>
        </div>
      </dl>

      <el-form label-position="top" @submit.prevent>
        <el-form-item label="实收数量" required :error="fieldError">
          <el-input-number
            v-model="form.quantity"
            :min="1"
            :precision="0"
            :step="1"
            :disabled="submitting"
            controls-position="right"
            class="receive-dialog__quantity"
            @change="clearQuantityError"
          />
          <p v-if="expectedStock !== null" class="receive-dialog__hint">
            入库后库存 {{ expectedStock }}{{ unit }}
          </p>
        </el-form-item>
        <el-form-item label="实收日期" required>
          <el-date-picker
            v-model="form.receivedDate"
            type="date"
            value-format="YYYY-MM-DD"
            :disabled="submitting"
            class="receive-dialog__control"
          />
        </el-form-item>
        <el-form-item label="来源">
          <el-input
            v-model="form.source"
            maxlength="64"
            :disabled="submitting"
            placeholder="如：采购回库、无建议收货"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="form.remark"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            :disabled="submitting"
            placeholder="选填"
          />
        </el-form-item>
      </el-form>

      <el-alert
        v-if="errorMessage"
        type="error"
        :closable="false"
        show-icon
        title="无法完成入库"
        :description="errorMessage"
      />
    </div>

    <div v-else-if="receipt" class="receive-dialog receive-result">
      <el-alert
        type="success"
        :closable="false"
        show-icon
        :title="`已生成入库单 ${receipt.order_no}`"
        :description="resultDescription"
      />

      <section class="receive-coverage">
        <h4 class="receive-coverage__title">本次到货可覆盖课次</h4>
        <el-skeleton v-if="coverageLoading" :rows="2" animated />
        <p v-else-if="coverageError" class="receive-coverage__empty">
          {{ coverageError }}
        </p>
        <p
          v-else-if="coverage && (!coverage.matched || !coverage.items.length)"
          class="receive-coverage__empty"
        >
          {{ coverage.matched ? coverage.message : `未匹配：${coverage.message}` }}
        </p>
        <template v-else-if="coverage">
          <p class="receive-coverage__hint">{{ coverage.message }}</p>
          <ul class="receive-coverage__list">
            <li v-for="row in coverage.items" :key="row.session_id">
              <div class="receive-coverage__head">
                <span>
                  {{ row.session_date }} · {{ row.course_name }} 第 {{ row.seq }}
                  次（{{ row.headcount }} 人）
                </span>
                <span
                  class="status-tag"
                  :class="row.covered ? 'status-tag--success' : 'status-tag--warning'"
                >
                  {{ row.covered ? '可覆盖' : '未覆盖' }}
                </span>
              </div>
              <p
                v-for="line in row.items"
                :key="line.consumable_id"
                class="receive-coverage__line"
              >
                {{ line.consumable_name }}：到货 {{ line.arrived_quantity
                }}{{ line.unit }}，覆盖 {{ line.covered_quantity }}/{{
                  line.required_quantity
                }}{{ line.unit }}（{{ riskLabel(line.level_before) }} →
                {{ riskLabel(line.level_after) }}）
              </p>
            </li>
          </ul>
          <p v-if="coverage.truncated" class="receive-coverage__hint">
            仅显示前 {{ coverage.items.length }} 个课次
          </p>
        </template>
      </section>
    </div>

    <template #footer>
      <template v-if="receipt">
        <el-button type="primary" @click="visible = false">完成</el-button>
      </template>
      <template v-else>
        <el-button :disabled="submitting" @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="submitting" @click="submit">
          确认入库
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.receive-dialog {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.receive-dialog__summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.receive-dialog__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
}

.receive-dialog__label {
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text-muted);
}

.receive-dialog__value {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--text);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.receive-dialog__quantity,
.receive-dialog__control {
  width: 100%;
}

.receive-dialog__hint {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.receive-result {
  gap: var(--space-3);
}

.receive-coverage {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.receive-coverage__title {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.receive-coverage__hint {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.receive-coverage__empty {
  margin: 0;
  padding: 12px 14px;
  border: 1px dashed var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.receive-coverage__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.receive-coverage__list li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
}

.receive-coverage__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text);
}

.receive-coverage__line {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
</style>