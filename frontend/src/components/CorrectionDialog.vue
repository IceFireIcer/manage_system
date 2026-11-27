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

const emit = defineEmits(['update:modelValue', 'success'])

const reason = ref('')
const remark = ref('')
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
  remark.value = ''
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
    reasonError.value = '请说明需要纠正的内容'
    nextTick(() => reasonInputRef.value?.focus?.())
    return
  }
  reasonError.value = ''
  requestError.value = ''
  submitting.value = true
  try {
    const { data } = await api.post(`/requisitions/${props.record.id}/corrections`, {
      reason: trimmed,
      remark: remark.value.trim() || undefined,
    })

    emit('success', data)
    emit('update:modelValue', false)
  } catch (error) {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (!error.response) {
      requestError.value = '暂未确认本次申请结果，请到记录中查看纠错状态'
    } else if (status === 409) {
      requestError.value = typeof detail === 'string' ? detail : '该记录已有待处理的纠错申请'
    } else if (status === 400) {
      requestError.value = typeof detail === 'string' ? detail : '该记录当前无法申请纠错'
    } else if (status === 403) {
      requestError.value = '只能申请本人记录的纠错'
    } else if (status === 404) {
      requestError.value = '流水不存在或不可访问'
    } else {
      requestError.value = '纠错申请提交失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="申请纠错"
    width="520px"
    class="correction-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    append-to-body
  >
    <div v-if="record" class="correction__summary">
      <div class="correction__row">
        <span class="correction__label">原流水号</span>
        <span class="correction__value">#{{ record.id }}</span>
      </div>
      <div class="correction__row">
        <span class="correction__label">耗材</span>
        <span class="correction__value">{{ consumableLabel(record) }}</span>
      </div>
      <div class="correction__row">
        <span class="correction__label">领用时间</span>
        <span class="correction__value">{{ formatTime(record.created_at) }}</span>
      </div>
      <div class="correction__row">
        <span class="correction__label">数量</span>
        <span class="correction__value">{{ quantityText }}</span>
      </div>
    </div>

    <el-alert
      v-if="record"
      type="info"
      :closable="false"
      show-icon
      class="correction__hint"
      title="提交后由教师核对原单处理，学生不能直接红冲"
    />

    <el-alert
      v-if="requestError"
      type="error"
      :closable="false"
      show-icon
      class="correction__request-error"
      :title="requestError"
    />

    <el-form label-position="top" class="correction__form" @submit.prevent="handleConfirm">
      <el-form-item label="需要纠正的内容" required :error="reasonError">
        <el-input
          ref="reasonInputRef"
          v-model="reason"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="如：实际只领了 2 卷，登记成 3 卷"
          :disabled="submitting"
          aria-label="需要纠正的内容"
          @input="handleReasonInput"
        />
      </el-form-item>
      <el-form-item label="补充说明">
        <el-input
          v-model="remark"
          maxlength="255"
          show-word-limit
          placeholder="选填，可说明发现时间和现场情况"
          :disabled="submitting"
          aria-label="补充说明"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="correction__footer">
        <el-button :disabled="submitting" @click="visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="submitting"
          :disabled="submitting"
          @click="handleConfirm"
        >
          提交纠错申请
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.correction__summary {
  display: grid;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.correction__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.correction__label {
  flex: 0 0 64px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.correction__value {
  min-width: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.correction__hint {
  margin-top: 16px;
}

.correction__request-error {
  margin-top: 16px;
}

.correction__form {
  margin-top: 16px;
}

.correction__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
<style>
@media (max-width: 767px) {
  .correction-dialog.el-dialog {
    width: calc(100% - 32px);
  }
}
</style>