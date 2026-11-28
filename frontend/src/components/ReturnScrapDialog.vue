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
  action: {
    type: String,
    default: 'return',
  },
})

const emit = defineEmits(['update:modelValue', 'success', 'refresh'])

const quantity = ref(1)
const reason = ref('')
const remark = ref('')
const quantityError = ref('')
const reasonError = ref('')
const requestError = ref('')
const submitting = ref(false)
const reasonInputRef = ref(null)

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const isScrap = computed(() => props.action === 'scrap')

const title = computed(() => (isScrap.value ? '登记报损' : '登记实物退库'))

const endpoint = computed(() => (isScrap.value ? 'scraps' : 'returns'))

const maxQuantity = computed(() => {
  const value = Number(props.record?.returnable_quantity ?? props.record?.quantity ?? 1)
  return Number.isInteger(value) && value > 0 ? value : 1
})

const quantityText = computed(() => {
  if (!props.record) return ''
  const unit = props.record.unit ? ` ${props.record.unit}` : ''
  return `${quantity.value}${unit}`
})

const maxQuantityText = computed(() => {
  if (!props.record) return ''
  const unit = props.record.unit ? ` ${props.record.unit}` : ''
  return `${maxQuantity.value}${unit}`
})

const impactText = computed(() =>
  isScrap.value
    ? `仅记录损耗 ${quantityText.value}，不改变可用库存`
    : `库存将回加 ${quantityText.value}`,
)

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function consumableLabel(record) {
  if (!record) return ''
  return record.consumable_spec
    ? `${record.consumable_name}（${record.consumable_spec}）`
    : record.consumable_name
}

function resetState() {
  quantity.value = maxQuantity.value
  reason.value = ''
  remark.value = ''
  quantityError.value = ''
  reasonError.value = ''
  requestError.value = ''
}

function clearErrors() {
  quantityError.value = ''
  reasonError.value = ''
  requestError.value = ''
}

watch(
  () => [props.modelValue, props.record?.id, props.action],
  ([open]) => {
    if (open) resetState()
  },
)

async function handleConfirm() {
  if (!props.record || submitting.value) return
  clearErrors()
  const value = Number(quantity.value)
  if (!Number.isInteger(value) || value <= 0) {
    quantityError.value = '数量必须为正整数'
    return
  }
  if (value > maxQuantity.value) {
    quantityError.value = `数量不能超过可退数量 ${maxQuantityText.value}`
    return
  }
  const trimmed = reason.value.trim()
  if (isScrap.value && !trimmed) {
    reasonError.value = '请填写报损原因'
    nextTick(() => reasonInputRef.value?.focus?.())
    return
  }
  submitting.value = true
  try {
    const payload = { quantity: value }
    if (trimmed) payload.reason = trimmed
    if (remark.value.trim()) payload.remark = remark.value.trim()
    const { data } = await api.post(
      `/requisitions/${props.record.id}/${endpoint.value}`,
      payload,
    )
    emit('success', data)
    emit('update:modelValue', false)
  } catch (error) {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (!error.response) {
      requestError.value = '暂未确认本次结果，请到记录详情核对原流水'
      emit('refresh')
    } else if (status === 400) {
      requestError.value =
        typeof detail === 'string' ? detail : '该流水当前无法处理，请刷新记录'
      emit('refresh')
    } else if (status === 404) {
      requestError.value = '流水不存在或不可访问'
      emit('refresh')
    } else if (status === 403) {
      requestError.value = isScrap.value
        ? '你没有权限登记报损'
        : '你没有权限登记该流水的退库'
    } else if (status === 409) {
      requestError.value = '请求标识已用于不同内容，请勿复用'
    } else if (status >= 500) {
      requestError.value = '暂未确认本次结果，请到记录详情核对原流水'
      emit('refresh')
    } else {
      requestError.value = '操作失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="560px"
    class="return-scrap-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    append-to-body
  >
    <div v-if="record" class="return-scrap__summary">
      <div class="return-scrap__row">
        <span class="return-scrap__label">原流水号</span>
        <span class="return-scrap__value">#{{ record.id }}</span>
      </div>
      <div class="return-scrap__row">
        <span class="return-scrap__label">领用人</span>
        <span class="return-scrap__value">{{ record.user_name }}</span>
      </div>
      <div class="return-scrap__row">
        <span class="return-scrap__label">耗材</span>
        <span class="return-scrap__value">{{ consumableLabel(record) }}</span>
      </div>
      <div class="return-scrap__row">
        <span class="return-scrap__label">领用时间</span>
        <span class="return-scrap__value">{{ formatTime(record.created_at) }}</span>
      </div>
      <div class="return-scrap__row">
        <span class="return-scrap__label">可退数量</span>
        <span class="return-scrap__value">{{ maxQuantityText }}</span>
      </div>
    </div>

    <el-alert
      v-if="record"
      type="warning"
      :closable="false"
      show-icon
      class="return-scrap__impact"
      :title="impactText"
    />

    <el-alert
      v-if="requestError"
      type="error"
      :closable="false"
      show-icon
      class="return-scrap__request-error"
      :title="requestError"
    />

    <el-form label-position="top" class="return-scrap__form" @submit.prevent="handleConfirm">
      <el-form-item label="数量" required :error="quantityError">
        <el-input-number
          v-model="quantity"
          :min="1"
          :max="maxQuantity"
          :step="1"
          :disabled="submitting"
          :aria-label="isScrap ? '报损数量' : '退库数量'"
        />
      </el-form-item>
      <el-form-item
        :label="isScrap ? '报损原因' : '退库原因'"
        :required="isScrap"
        :error="reasonError"
      >
        <el-input
          ref="reasonInputRef"
          v-model="reason"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
          :placeholder="
            isScrap
              ? '如：实训中摔坏，无法继续使用'
              : '选填，如：未使用完，归还班委'
          "
          :disabled="submitting"
          :aria-label="isScrap ? '报损原因' : '退库原因'"
        />
      </el-form-item>
      <el-form-item label="补充说明">
        <el-input
          v-model="remark"
          maxlength="255"
          show-word-limit
          placeholder="选填，记录与实物核对结果"
          :disabled="submitting"
          aria-label="补充说明"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="return-scrap__footer">
        <el-button :disabled="submitting" @click="visible = false">取消</el-button>
        <el-button
          :type="isScrap ? 'warning' : 'primary'"
          :loading="submitting"
          :disabled="submitting"
          @click="handleConfirm"
        >
          {{ isScrap ? '确认报损' : '确认退库' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.return-scrap__summary {
  display: grid;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.return-scrap__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.return-scrap__label {
  flex: 0 0 72px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.return-scrap__value {
  min-width: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.return-scrap__impact,
.return-scrap__request-error {
  margin-top: 16px;
}

.return-scrap__form {
  margin-top: 16px;
}

.return-scrap__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
<style>
@media (max-width: 767px) {
  .return-scrap-dialog.el-dialog {
    width: calc(100% - 32px);
  }
}
</style>