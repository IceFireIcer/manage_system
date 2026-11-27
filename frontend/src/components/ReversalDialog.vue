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
const reasonError = ref('')
const requestError = ref('')
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
  reasonError.value = ''
  requestError.value = ''
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
  requestError.value = ''
  submitting.value = true
  try {
    const { data } = await api.post(`/requisitions/${props.record.id}/reversal`, {
      reason: trimmed,
    })
    emit('success', data)
    emit('update:modelValue', false)
  } catch (error) {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (!error.response) {
      requestError.value = '暂未确认本次撤销结果，请到记录中核对原流水与红冲流水'
      emit('refresh')
    } else if (status === 400) {
      requestError.value = typeof detail === 'string' ? detail : '该流水当前无法撤销，请刷新记录'
      emit('refresh')
    } else if (status === 404) {
      requestError.value = '流水不存在或不可访问'
      emit('refresh')
    } else if (status === 403) {
      requestError.value = '你没有权限撤销领用流水'
    } else if (status >= 500) {
      requestError.value = '暂未确认本次撤销结果，请到记录中核对原流水与红冲流水'
      emit('refresh')
    } else {
      requestError.value = '撤销失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="撤销领用"
    width="520px"
    class="reversal-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    append-to-body
  >
    <div v-if="record" class="reversal__summary">
      <div class="reversal__row">
        <span class="reversal__label">原流水号</span>
        <span class="reversal__value">#{{ record.id }}</span>
      </div>
      <div class="reversal__row">
        <span class="reversal__label">领用人</span>
        <span class="reversal__value">{{ record.user_name }}</span>
      </div>
      <div class="reversal__row">
        <span class="reversal__label">耗材</span>
        <span class="reversal__value">{{ consumableLabel(record) }}</span>
      </div>
      <div class="reversal__row">
        <span class="reversal__label">领用时间</span>
        <span class="reversal__value">{{ formatTime(record.created_at) }}</span>
      </div>
      <div class="reversal__row">
        <span class="reversal__label">数量</span>
        <span class="reversal__value">{{ quantityText }}</span>
      </div>
    </div>

    <el-alert
      v-if="record"
      type="warning"
      :closable="false"
      show-icon
      class="reversal__impact"
      :title="`将生成一条红冲流水，库存回加 ${quantityText}`"
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
      <el-form-item label="撤销原因" required :error="reasonError">
        <el-input
          ref="reasonInputRef"
          v-model="reason"
          type="textarea"
          :rows="3"
          maxlength="255"
          show-word-limit
          placeholder="如：登记有误，需要退回库存"
          :disabled="submitting"
          aria-label="撤销原因"
          @input="handleReasonInput"
        />
      </el-form-item>
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
          确认撤销
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.reversal__summary {
  display: grid;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.reversal__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.reversal__label {
  flex: 0 0 64px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.reversal__value {
  min-width: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.reversal__impact {
  margin-top: 16px;
}

.reversal__request-error {
  margin-top: 16px;
}

.reversal__form {
  margin-top: 16px;
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
}
</style>