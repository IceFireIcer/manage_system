<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const MAX_ROWS = 5
const MAX_BACKFILL_DAYS = 30

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }

const userStore = useUserStore()

const isStudent = computed(() => userStore.user?.role === 'student')

const consumables = ref([])
const userOptions = ref([])
const consumableLoading = ref(false)
const submitting = ref(false)
const result = ref(null)

const form = reactive({
  userId: null,
  createdAt: '',
  remark: '',
})

const rows = ref([{ consumableId: null, quantity: 1 }])
const rowErrors = reactive({})

function nowMinute() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`
}

function resetForm() {
  rows.value = [{ consumableId: null, quantity: 1 }]
  Object.keys(rowErrors).forEach((key) => delete rowErrors[key])
  form.userId = userStore.user?.id
  form.createdAt = nowMinute()
  form.remark = ''
}

function consumableLabel(item) {
  const spec = item.spec ? `（${item.spec}）` : ''
  return `${item.name}${spec} · 库存 ${item.current_stock}${item.unit || ''}`
}

function rowQuantityMax(row) {
  const consumable = consumables.value.find((item) => item.id === row.consumableId)
  return consumable ? consumable.current_stock : 9999
}

function addRow() {
  if (rows.value.length >= MAX_ROWS) return
  rows.value.push({ consumableId: null, quantity: 1 })
}

function removeRow(index) {
  if (rows.value.length <= 1) return
  rows.value.splice(index, 1)
  delete rowErrors[index]
}

function disabledDate(date) {
  const time = date.getTime()
  return time > Date.now() || time < Date.now() - MAX_BACKFILL_DAYS * 24 * 3600 * 1000
}

function validateRows() {
  Object.keys(rowErrors).forEach((key) => delete rowErrors[key])
  for (let index = 0; index < rows.value.length; index++) {
    const row = rows.value[index]
    if (!row.consumableId) {
      rowErrors[index] = '请选择耗材'
      return false
    }
    if (!Number.isInteger(row.quantity) || row.quantity < 1) {
      rowErrors[index] = '数量需为正整数'
      return false
    }
    if (row.quantity > rowQuantityMax(row)) {
      rowErrors[index] = '数量超出当前库存'
      return false
    }
  }
  return true
}

function validateTime() {
  if (!form.createdAt) return '请选择领用时间'
  const time = new Date(form.createdAt).getTime()
  const now = Date.now()
  if (time > now) return '领用时间不能晚于当前时间'
  if (time < now - MAX_BACKFILL_DAYS * 24 * 3600 * 1000) {
    return `领用时间不能早于 ${MAX_BACKFILL_DAYS} 天前`
  }
  return null
}

async function fetchConsumables() {
  consumableLoading.value = true
  try {
    const { data } = await api.get('/consumables')
    consumables.value = data
  } catch {
    ElMessage.error('加载耗材列表失败，请稍后重试')
  } finally {
    consumableLoading.value = false
  }
}

async function fetchUserOptions() {
  if (isStudent.value) return
  try {
    const { data } = await api.get('/users/options')
    userOptions.value = data
  } catch {
    ElMessage.error('加载用户列表失败，请稍后重试')
  }
}

async function handleSubmit() {
  result.value = null
  if (!validateRows()) {
    ElMessage.warning('请先修正表单中的行错误')
    return
  }
  const timeError = validateTime()
  if (timeError) {
    ElMessage.warning(timeError)
    return
  }

  submitting.value = true
  try {
    const payload = {
      items: rows.value.map((row) => ({
        consumable_id: row.consumableId,
        quantity: row.quantity,
      })),
      user_id: isStudent.value ? userStore.user?.id : form.userId,
      created_at: form.createdAt,
      remark: form.remark.trim() || null,
    }
    const { data } = await api.post('/requisitions', payload)
    result.value = data
    ElMessage.success(`登记成功，共 ${data.count} 行`)
    await fetchConsumables()
    rows.value = [{ consumableId: null, quantity: 1 }]
    form.createdAt = nowMinute()
    form.remark = ''
    Object.keys(rowErrors).forEach((key) => delete rowErrors[key])
  } catch (error) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') {
      const match = detail.match(/第\s*(\d+)\s*行/)
      if (match) {
        const index = Number.parseInt(match[1], 10) - 1
        if (index >= 0 && index < rows.value.length) {
          rowErrors[index] = detail.replace(/第\s*\d+\s*行：?/, '')
        }
      }
      ElMessage.error(detail)
    } else {
      ElMessage.error('提交失败，请稍后重试')
    }
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  form.userId = userStore.user?.id
  form.createdAt = nowMinute()
  await Promise.all([fetchConsumables(), fetchUserOptions()])
})
</script>

<template>
  <el-card>
    <div class="requisition__intro">
      <p>一次最多登记 {{ MAX_ROWS }} 行耗材领用，任一行库存不足时整单提交失败。支持补录最近 {{ MAX_BACKFILL_DAYS }} 天内的领用。</p>
    </div>

    <el-form label-position="top" class="requisition__form">
      <div class="requisition__meta">
        <el-form-item label="领用人">
          <el-input v-if="isStudent" :model-value="`${userStore.user?.name}（本人）`" disabled />
          <el-select
            v-else
            v-model="form.userId"
            filterable
            placeholder="选择领用人"
            class="requisition__user-select"
          >
            <el-option
              v-for="option in userOptions"
              :key="option.id"
              :label="`${option.name}（${ROLE_NAMES[option.role] ?? option.role}）`"
              :value="option.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="领用时间">
          <el-date-picker
            v-model="form.createdAt"
            type="datetime"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DD[T]HH:mm:00"
            :disabled-date="disabledDate"
            placeholder="选择领用时间"
            class="requisition__time-picker"
          />
        </el-form-item>
      </div>

      <div class="requisition__rows">
        <div
          v-for="(row, index) in rows"
          :key="index"
          class="requisition__row"
          :class="{ 'requisition__row--error': rowErrors[index] }"
        >
          <div class="requisition__row-index">第 {{ index + 1 }} 行</div>
          <el-select
            v-model="row.consumableId"
            filterable
            placeholder="选择耗材"
            :loading="consumableLoading"
            class="requisition__consumable"
          >
            <el-option
              v-for="item in consumables"
              :key="item.id"
              :label="consumableLabel(item)"
              :value="item.id"
            />
          </el-select>
          <el-input-number
            v-model="row.quantity"
            :min="1"
            :max="rowQuantityMax(row)"
            :step="1"
            step-strictly
            class="requisition__quantity"
          />
          <el-button
            type="danger"
            link
            :disabled="rows.length <= 1"
            @click="removeRow(index)"
          >
            删除
          </el-button>
          <div v-if="rowErrors[index]" class="requisition__row-error">{{ rowErrors[index] }}</div>
        </div>
      </div>

      <div class="requisition__actions">
        <el-button :disabled="rows.length >= MAX_ROWS" @click="addRow">添加一行</el-button>
        <span v-if="rows.length >= MAX_ROWS" class="requisition__row-limit">已达 {{ MAX_ROWS }} 行上限</span>
      </div>

      <el-form-item label="备注（可选）">
        <el-input
          v-model="form.remark"
          maxlength="255"
          show-word-limit
          placeholder="如：实训课领用"
        />
      </el-form-item>

      <el-button type="primary" :loading="submitting" :disabled="submitting" @click="handleSubmit">
        提交登记
      </el-button>
    </el-form>

    <el-alert v-if="result" type="success" :closable="false" class="requisition__result">
      <div class="requisition__result-title">登记成功（共 {{ result.count }} 行）：</div>
      <div v-for="(item, index) in result.items" :key="index">
        {{ item.consumable_name }} × {{ item.quantity }}，剩余 {{ item.stock_after }}
      </div>
    </el-alert>
  </el-card>
</template>

<style scoped>
.requisition__intro {
  margin-bottom: 16px;
  color: #606266;
}

.requisition__intro p {
  margin: 0;
  font-size: 13px;
}

.requisition__meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.requisition__user-select {
  width: 240px;
}

.requisition__time-picker {
  width: 220px;
}

.requisition__rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
}

.requisition__row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  position: relative;
}

.requisition__row--error {
  border-color: #f56c6c;
  background-color: #fef0f0;
}

.requisition__row-index {
  width: 60px;
  flex-shrink: 0;
  font-size: 13px;
  color: #606266;
}

.requisition__consumable {
  flex: 1;
  min-width: 260px;
}

.requisition__quantity {
  width: 140px;
}

.requisition__row-error {
  width: 100%;
  font-size: 12px;
  color: #f56c6c;
}

.requisition__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.requisition__row-limit {
  font-size: 12px;
  color: #909399;
}

.requisition__result {
  margin-top: 16px;
}

.requisition__result-title {
  font-weight: 600;
  margin-bottom: 4px;
}
</style>