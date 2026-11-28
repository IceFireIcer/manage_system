<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import api from '../api'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  record: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue', 'success', 'refresh'])

const reason = ref('')
const remark = ref('')
const reasonError = ref('')
const reentryError = ref('')
const requestError = ref('')
const reentryEnabled = ref(false)
const reentryQuantity = ref(1)
const reentryRemark = ref('')
const reasonInputRef = ref(null)
const submitting = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const quantityText = computed(() => {
  if (!props.record) return ''
  return `${props.record.quantity}${props.record.unit ? ` ${props.record.unit}` : ' 单位'}`
})

const reentryQuantityText = computed(() => {
  if (!props.record) return ''
  return `${reentryQuantity.value}${props.record.unit ? ` ${props.record.unit}` : ' 单位'}`
})

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function consumableLabel(record) {
  if (!record) return ''
  return record.consumable_spec ? `${record.consumable_name}（${record.consumable_spec}）` : record.consumable_name
}

function resetState() {
  reason.value = ''
  remark.value = ''
  reasonError.value = ''
  reentryError.value = ''
  requestError.value = ''
  reentryEnabled.value = false
  reentryQuantity.value = props.record?.quantity ?? 1
  reentryRemark.value = ''
}

function handleReasonInput() {
  reasonError.value = ''
  requestError.value = ''
}

watch(
  () => [props.modelValue, props.record?.id],
  ([open]) => {
    if (open) {
      resetState()
    }
  },
)

async function handleConfirm() {
  if (!props.record || submitting.value) return
  const trimmed = reason.value.trim()
  if (!trimmed) {
    reasonError.value = '请填写撤销原因'
    nextTick(() => reasonInputRef.value?.focus?.())
    return
  }
  reasonError.value = ''
  reentryError.value = ''
  requestError.value = ''
  const quantity = Number(reentryQuantity.value)
  if (reentryEnabled.value) {
    if (!Number.isInteger(quantity) || quantity <= 0) {
      reentryError.value = '重登数量必须为正整数'
      return
    }
    if (quantity > props.record.quantity) {
      reentryError.value = `重登数量不能超过原领用数量 ${props.record.quantity}`
      return
    }
  }
  submitting.value = true
  try {
    const payload = { reason: trimmed, remark: remark.value.trim() || undefined }
    if (reentryEnabled.value) {
      payload.reentry = {
        quantity,
        remark: reentryRemark.value.trim() || undefined,
      }
    }
    const { data } = await api.post(`/requisitions/${props.record.id}/reversal`, payload)
    emit('success', data)
    emit('update:modelValue', false)
  } catch (error) {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (!error.response) {
      requestError.value = '暂未确认本次红冲结果，请到记录中核对原流水与红冲流水'
      emit('refresh')
    } else if (status === 400) {
      requestError.value = typeof detail === 'string' ? detail : '该流水当前无法红冲，请刷新记录'
      emit('refresh')
    } else if (status === 404) {
      requestError.value = '流水不存在或不可访问'
      emit('refresh')
    } else if (status === 403) {
      requestError.value = '你没有权限红冲领用流水'
    } else if (status >= 500) {
      requestError.value = '暂未确认本次红冲结果，请到记录中核对原流水与红冲流水'
      emit('refresh')
    } else {
      requestError.value = '红冲失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="红冲领用"
    width="560px"
    class="reversal-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    append-to-body
  >
    <dl v-if="record" class="reversal__summary">
      <div class="reversal__row">
        <dt class="reversal__label">原流水号</dt>
        <dd class="reversal__value">#{{ record.id }}</dd>
      </div>
      <div class="reversal__row">
        <dt class="reversal__label">领用人</dt>
        <dd class="reversal__value">{{ record.user_name }}</dd>
      </div>
      <div class="reversal__row">
        <dt class="reversal__label">耗材</dt>
        <dd class="reversal__value">{{ consumableLabel(record) }}</dd>
      </div>
      <div class="reversal__row">
        <dt class="reversal__label">领用时间</dt>
        <dd class="reversal__value">{{ formatTime(record.created_at) }}</dd>
      </div>
      <div class="reversal__row">
        <dt class="reversal__label">数量</dt>
        <dd class="reversal__value">{{ quantityText }}</dd>
      </div>
      <div v-if="record.correction" class="reversal__row">
        <dt class="reversal__label">纠错申请</dt>
        <dd class="reversal__value">
          #{{ record.correction.id }} · {{ record.correction.reason }}
        </dd>
      </div>
    </dl>

    <el-alert
      v-if="record"
      type="warning"
      :closable="false"
      show-icon
      class="reversal__impact"
      :title="`将生成一条红冲单，库存回加 ${quantityText}${reentryEnabled ? `，再按重登数量扣减 ${reentryQuantityText}` : ''}`"
    />

    <el-alert
      v-if="requestError"
      type="error"
      :closable="false"
      show-icon
      class="reversal__request-error"
      :title="requestError"
    />

    <el-form label-position="top" class="reversal__form" @submit.prevent="handleConfirm">
      <el-form-item label="红冲原因" required :error="reasonError">
        <el-input
          ref="reasonInputRef"
          v-model="reason"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
          placeholder="如：数量登记有误，需要红冲后重新登记"
          :disabled="submitting"
          aria-label="红冲原因"
          @input="handleReasonInput"
        />
      </el-form-item>
      <el-form-item label="处理备注">
        <el-input
          v-model="remark"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
          placeholder="选填，记录与原单的核对结果"
          :disabled="submitting"
          aria-label="处理备注"
        />
      </el-form-item>

      <el-form-item>
        <el-checkbox v-model="reentryEnabled" :disabled="submitting">
          生成关联重登单
        </el-checkbox>
      </el-form-item>

      <template v-if="reentryEnabled">
        <el-form-item label="重登数量" required :error="reentryError">
          <el-input-number
            v-model="reentryQuantity"
            :min="1"
            :max="record?.quantity ?? 1"
            :step="1"
            :disabled="submitting"
            aria-label="重登数量"
          />
        </el-form-item>
        <el-form-item label="重登备注">
          <el-input
            v-model="reentryRemark"
            maxlength="255"
            show-word-limit
            placeholder="选填，如：更正为 2 卷"
            :disabled="submitting"
            aria-label="重登备注"
          />
        </el-form-item>
      </template>
    </el-form>

    <template #footer>
      <div class="reversal__footer">
        <el-button :disabled="submitting" @click="visible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="submitting"
          :disabled="submitting"
          @click="handleConfirm"
        >
          确认红冲
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.reversal__summary {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
  background: var(--surface-subtle);
}

.reversal__row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
}

.reversal__label {
  flex: 0 0 76px;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text-muted);
}

.reversal__value {
  min-width: 0;
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.reversal__impact,
.reversal__request-error {
  margin-top: var(--space-4);
}

.reversal__form {
  margin-top: var(--space-4);
}

.reversal__form :deep(.el-form-item) {
  margin-bottom: var(--space-3);
}

.reversal__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
<style>
@media (max-width: 767px) {
  .reversal-dialog.el-dialog {
    width: calc(100% - 32px);
  }

  .reversal-dialog .el-dialog__footer .el-button {
    min-height: 44px;
  }
}
</style>