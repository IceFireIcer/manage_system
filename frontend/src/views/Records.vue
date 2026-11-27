<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const PAGE_SIZE = 20

const TYPE_NAMES = { normal: '领用', reversal: '红冲' }
const STATUS_NAMES = { active: '有效', reversed: '已撤销' }

const userStore = useUserStore()

const canReverse = computed(() =>
  ['teacher', 'admin'].includes(userStore.user?.role)
)

const records = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(PAGE_SIZE)
const loading = ref(false)

const reverseDialog = reactive({
  visible: false,
  record: null,
  reason: '',
  submitting: false,
})

function formatTime(value) {
  if (!value) return ''
  const d = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function quantityText(row) {
  return row.type === 'reversal' ? `+${row.quantity}` : `${row.quantity}`
}

function typeTagType(row) {
  return row.type === 'reversal' ? 'warning' : 'primary'
}

function statusTagType(row) {
  return row.status === 'reversed' ? 'info' : 'success'
}

function rowClassName({ row }) {
  return row.status === 'reversed' ? 'records__row--reversed' : ''
}

function canReverseRow(row) {
  return canReverse.value && row.type === 'normal' && row.status === 'active'
}

async function fetchRecords() {
  loading.value = true
  try {
    const { data } = await api.get('/requisitions', {
      params: { page: page.value, page_size: pageSize.value },
    })
    records.value = data.items
    total.value = data.total
  } catch {
    ElMessage.error('加载流水列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function handlePageChange(next) {
  page.value = next
  fetchRecords()
}

function openReverse(row) {
  reverseDialog.record = row
  reverseDialog.reason = ''
  reverseDialog.visible = true
}

async function confirmReverse() {
  if (!reverseDialog.reason.trim()) {
    ElMessage.warning('请填写撤销原因')
    return
  }
  reverseDialog.submitting = true
  try {
    const { data } = await api.post(
      `/requisitions/${reverseDialog.record.id}/reversal`,
      { reason: reverseDialog.reason.trim() }
    )
    ElMessage.success(`已撤销，库存回加至 ${data.stock_after}`)
    reverseDialog.visible = false
    await fetchRecords()
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '撤销失败，请稍后重试')
  } finally {
    reverseDialog.submitting = false
  }
}

onMounted(fetchRecords)
</script>

<template>
  <el-card>
    <div class="records__intro">
      <p>
        领用流水按时间倒序排列。教师/班委可对「有效」的领用流水执行撤销：
        库存即时回加，原流水标记已撤销并生成红冲流水。
      </p>
    </div>

    <el-table
      :data="records"
      v-loading="loading"
      :row-class-name="rowClassName"
      class="records__table"
    >
      <el-table-column label="时间" width="150">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="consumable_name" label="耗材" min-width="140" />
      <el-table-column label="数量" width="90">
        <template #default="{ row }">
          <span>{{ quantityText(row) }}</span>
          <span v-if="row.unit" class="records__unit">{{ row.unit }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="user_name" label="领用人" width="110" />
      <el-table-column prop="stock_after" label="领后剩余" width="90" />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag :type="typeTagType(row)" size="small">
            {{ TYPE_NAMES[row.type] ?? row.type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.status === 'reversed' && row.reversal_reason"
            :content="`撤销原因：${row.reversal_reason}`"
            placement="top"
          >
            <el-tag :type="statusTagType(row)" size="small" class="records__status-tag">
              {{ STATUS_NAMES[row.status] ?? row.status }}
            </el-tag>
          </el-tooltip>
          <el-tag v-else :type="statusTagType(row)" size="small">
            {{ STATUS_NAMES[row.status] ?? row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button
            v-if="canReverseRow(row)"
            type="danger"
            link
            @click="openReverse(row)"
          >
            撤销
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="records__pagination">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="handlePageChange"
      />
    </div>

    <el-dialog
      v-model="reverseDialog.visible"
      title="撤销领用流水"
      width="420px"
      :close-on-click-modal="false"
    >
      <div v-if="reverseDialog.record" class="records__reverse-summary">
        {{ reverseDialog.record.consumable_name }} ×
        {{ reverseDialog.record.quantity }}（{{ reverseDialog.record.user_name }}）
      </div>
      <el-form label-position="top">
        <el-form-item label="撤销原因（必填）" required>
          <el-input
            v-model="reverseDialog.reason"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            placeholder="如：登记错误，需回退库存"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reverseDialog.visible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="reverseDialog.submitting"
          :disabled="reverseDialog.submitting"
          @click="confirmReverse"
        >
          确认撤销
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.records__intro {
  margin-bottom: 16px;
  color: #606266;
}

.records__intro p {
  margin: 0;
  font-size: 13px;
}

.records__table {
  width: 100%;
}

.records__table :deep(.records__row--reversed) {
  color: #909399;
}

.records__unit {
  margin-left: 2px;
  font-size: 12px;
  color: #909399;
}

.records__status-tag {
  cursor: help;
}

.records__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.records__reverse-summary {
  margin-bottom: 12px;
  font-size: 14px;
  color: #303133;
}
</style>