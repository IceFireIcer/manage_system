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
let requestId = ''
let lastSignature = ''

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
    visible.value = false
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
    <div v-if="consumable" class="receive-dialog">
      <div class="receive-dialog__summary">
        <div class="receive-dialog__row">
          <span class="receive-dialog__label">耗材</span>
          <span class="receive-dialog__value">
            {{ consumable.name }}<span v-if="consumable.spec">（{{ consumable.spec }}）</span>
          </span>
        </div>
        <div class="receive-dialog__row">
          <span class="receive-dialog__label">当前库存</span>
          <span class="receive-dialog__value">
            {{ consumable.current_stock }}{{ consumable.unit }}
          </span>
        </div>
        <div class="receive-dialog__row">
          <span class="receive-dialog__label">安全库存线</span>
          <span class="receive-dialog__value">
            {{ consumable.safety_stock }}{{ consumable.unit }}
          </span>
        </div>
      </div>

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
            预计入库后 {{ expectedStock }}{{ unit }}
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
            placeholder="可选"
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

    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" :disabled="submitting" @click="submit">
        确认入库
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.receive-dialog {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.receive-dialog__summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.receive-dialog__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.receive-dialog__label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.receive-dialog__value {
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
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
}
</style>