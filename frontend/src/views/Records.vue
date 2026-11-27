<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }
const TYPE_NAMES = { normal: '领用', reversal: '红冲' }
const STATUS_NAMES = { active: '有效', reversed: '已撤销' }

const userStore = useUserStore()

const isStudent = computed(() => userStore.user?.role === 'student')
const isStaff = computed(() => ['teacher', 'admin'].includes(userStore.user?.role))
const canReverse = computed(() =>
  ['teacher', 'admin'].includes(userStore.user?.role)
)

const consumables = ref([])
const userOptions = ref([])
const records = ref([])
const loading = ref(false)
const exporting = ref(false)

const filters = reactive({
  consumableId: null,
  userId: null,
  dateRange: null,
  type: null,
  status: null,
})

const page = reactive({ current: 1, size: 20, total: 0 })

const reverseDialog = reactive({
  visible: false,
  record: null,
  reason: '',
  submitting: false,
})

function buildParams() {
  const params = {}
  if (filters.consumableId) params.consumable_id = filters.consumableId
  if (isStaff.value && filters.userId) params.user_id = filters.userId
  if (filters.type) params.type = filters.type
  if (filters.status) params.status = filters.status
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    params.start_date = filters.dateRange[0]
    params.end_date = filters.dateRange[1]
  }
  return params
}

async function fetchRecords() {
  loading.value = true
  try {
    const { data } = await api.get('/requisitions', {
      params: { ...buildParams(), page: page.current, page_size: page.size },
    })
    records.value = data.items
    page.total = data.total
  } catch {
    ElMessage.error('查询领用记录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  page.current = 1
  fetchRecords()
}

function handleReset() {
  filters.consumableId = null
  filters.userId = null
  filters.dateRange = null
  filters.type = null
  filters.status = null
  page.current = 1
  fetchRecords()
}

function handlePageChange(nextPage) {
  page.current = nextPage
  fetchRecords()
}

function handleSizeChange(nextSize) {
  page.size = nextSize
  page.current = 1
  fetchRecords()
}

async function handleExport() {
  exporting.value = true
  try {
    const response = await api.get('/requisitions/export', {
      params: buildParams(),
      responseType: 'blob',
    })
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = `requisitions_${Date.now()}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    ElMessage.success('CSV 导出成功')
  } catch {
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

function consumableLabel(item) {
  return item.consumable_spec
    ? `${item.consumable_name}（${item.consumable_spec}）`
    : item.consumable_name
}

function userOptionLabel(option) {
  return `${option.name}（${ROLE_NAMES[option.role] ?? option.role}）`
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : ''
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

async function fetchConsumables() {
  try {
    const { data } = await api.get('/consumables')
    consumables.value = data
  } catch {
    ElMessage.error('加载耗材列表失败，请稍后重试')
  }
}

async function fetchUserOptions() {
  if (!isStaff.value) return
  try {
    const { data } = await api.get('/users/options')
    userOptions.value = data
  } catch {
    ElMessage.error('加载用户列表失败，请稍后重试')
  }
}

onMounted(async () => {
  await Promise.all([fetchConsumables(), fetchUserOptions()])
  fetchRecords()
})
</script>

<template>
  <el-card>
    <div class="records__intro">
      <p>
        领用流水按时间倒序排列。教师/班委可对「有效」的领用流水执行撤销：
        库存即时回加，原流水标记已撤销并生成红冲流水。
      </p>
    </div>

    <div class="records">
      <div class="records__filters">
        <el-select
          v-model="filters.consumableId"
          filterable
          clearable
          placeholder="全部耗材"
          class="records__filter-consumable"
        >
          <el-option
            v-for="item in consumables"
            :key="item.id"
            :label="item.spec ? `${item.name}（${item.spec}）` : item.name"
            :value="item.id"
          />
        </el-select>

        <el-select
          v-if="isStaff"
          v-model="filters.userId"
          filterable
          clearable
          placeholder="全部领用人"
          class="records__filter-user"
        >
          <el-option
            v-for="option in userOptions"
            :key="option.id"
            :label="userOptionLabel(option)"
            :value="option.id"
          />
        </el-select>

        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          class="records__filter-date"
        />

        <el-select v-model="filters.type" clearable placeholder="全部类型" class="records__filter-type">
          <el-option label="领用" value="normal" />
          <el-option label="红冲" value="reversal" />
        </el-select>

        <el-select
          v-model="filters.status"
          clearable
          placeholder="全部状态"
          class="records__filter-status"
        >
          <el-option label="有效" value="active" />
          <el-option label="已撤销" value="reversed" />
        </el-select>

        <el-button type="primary" @click="handleSearch">查询</el-button>
        <el-button @click="handleReset">重置</el-button>
        <el-button
          v-if="isStaff"
          type="success"
          plain
          :loading="exporting"
          @click="handleExport"
        >
          导出 CSV
        </el-button>
      </div>

      <el-alert
        v-if="isStudent"
        type="info"
        :closable="false"
        class="records__own-tip"
        title="仅展示您本人的领用记录"
      />

      <el-table
        v-loading="loading"
        :data="records"
        :row-class-name="rowClassName"
        class="records__table"
      >
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="耗材" min-width="160">
          <template #default="{ row }">{{ consumableLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="数量" width="90" align="center">
          <template #default="{ row }">
            <span>{{ quantityText(row) }}</span>
            <span v-if="row.unit" class="records__unit">{{ row.unit }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_name" label="领用人" width="110" />
        <el-table-column prop="stock_after" label="领后剩余" width="100" align="center" />
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTagType(row)" size="small">
              {{ TYPE_NAMES[row.type] ?? row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
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
        <el-table-column prop="reversal_reason" label="撤销原因" min-width="120" show-overflow-tooltip />
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
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
          v-model:current-page="page.current"
          :page-size="page.size"
          :page-sizes="[10, 20, 50, 100]"
          :total="page.total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
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

.records__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.records__filter-consumable {
  width: 220px;
}

.records__filter-user {
  width: 180px;
}

.records__filter-date {
  width: 260px;
}

.records__filter-type,
.records__filter-status {
  width: 130px;
}

.records__own-tip {
  margin-bottom: 12px;
}

.records__table {
  width: 100%;
  margin-bottom: 16px;
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