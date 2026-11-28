<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const STATUS_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'open', label: '待处理' },
  { value: 'confirmed', label: '已确认' },
  { value: 'converted_to_count', label: '已转盘点' },
  { value: 'rejected', label: '已驳回' },
]
const STATUS_NAMES = {
  open: '待处理',
  confirmed: '已确认',
  rejected: '已驳回',
  converted_to_count: '已转盘点',
  counted: '已盘点',
}
const STATUS_CLASS = {
  open: 'incidents__status--open',
  confirmed: 'incidents__status--confirmed',
  rejected: 'incidents__status--rejected',
  converted_to_count: 'incidents__status--converted',
  counted: 'incidents__status--counted',
}
const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const PHOTO_MAX_BYTES = 5 * 1024 * 1024
const PAGE_SIZE = 20

const userStore = useUserStore()
const activeRole = computed(() => userStore.activeRole ?? userStore.user?.role)
const isStudent = computed(() => activeRole.value === 'student')
const isStaff = computed(() => ['super_admin', 'teacher', 'admin'].includes(activeRole.value))
const canManage = computed(() => userStore.hasPermission('incident.manage'))

const consumables = ref([])
const consumablesLoading = ref(false)
const consumablesError = ref('')
const form = reactive({ consumableId: null, location: '', description: '' })
const photoFile = ref(null)
const photoName = ref('')
const submitting = ref(false)

const items = ref([])
const total = ref(0)
const page = ref(1)
const statusFilter = ref('')
const listLoading = ref(false)
const listError = ref('')
const handlingId = ref(null)
const convertingId = ref(null)

const photoDialogVisible = ref(false)
const photoDialogSrc = ref('')

const photoQuery = computed(() => (photoDialogVisible.value ? photoDialogSrc.value : ''))

function statusLabel(status) {
  return STATUS_NAMES[status] ?? status
}

function statusClass(status) {
  return STATUS_CLASS[status] ?? ''
}

function effectiveStatus(item) {
  return item.count_status === 'posted' ? 'counted' : item.status
}

function countStatusLabel(item) {
  return item.count_status === 'posted' ? '已入账' : '盘点中'
}

function formatTime(value) {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 16)
}

function consumableLabel(item) {
  return item.spec ? `${item.name}（${item.spec}）` : item.name
}

function handleConsumableChange(consumableId) {
  const item = consumables.value.find((entry) => entry.id === consumableId)
  form.location = item?.location ?? ''
}

async function loadConsumables() {
  if (!isStudent.value) return
  consumablesLoading.value = true
  consumablesError.value = ''
  try {
    const response = await api.get('/consumables')
    consumables.value = response.data
  } catch {
    consumablesError.value = '耗材列表加载失败'
  } finally {
    consumablesLoading.value = false
  }
}

async function loadList() {
  listLoading.value = true
  listError.value = ''
  try {
    const params = { page: page.value, page_size: PAGE_SIZE }
    if (statusFilter.value) params.status = statusFilter.value
    const response = await api.get('/incidents', { params })
    items.value = response.data.items
    total.value = response.data.total
    if (items.value.length === 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(total.value / PAGE_SIZE))
      await loadList()
    }
  } catch {
    listError.value = '异常单加载失败，请稍后重试'
  } finally {
    listLoading.value = false
  }
}

function handleFilterChange() {
  page.value = 1
  loadList()
}

function handlePageChange(nextPage) {
  page.value = nextPage
  loadList()
}

function handlePhotoChange(uploadFile) {
  const raw = uploadFile?.raw
  if (!raw) return
  if (!PHOTO_TYPES.includes(raw.type)) {
    ElMessage.error('仅支持 JPEG、PNG 或 WebP 照片')
    return
  }
  if (raw.size > PHOTO_MAX_BYTES) {
    ElMessage.error('照片不能超过 5MB')
    return
  }
  photoFile.value = raw
  photoName.value = raw.name
}

function resetForm() {
  form.consumableId = null
  form.location = ''
  form.description = ''
  photoFile.value = null
  photoName.value = ''
}

async function handleSubmit() {
  if (submitting.value) return
  if (!form.consumableId) {
    ElMessage.warning('请选择耗材')
    return
  }
  if (!form.description.trim()) {
    ElMessage.warning('请填写异常描述')
    return
  }
  submitting.value = true
  try {
    const payload = new FormData()
    payload.append('consumable_id', String(form.consumableId))
    payload.append('description', form.description.trim())
    if (form.location.trim()) payload.append('location', form.location.trim())
    if (photoFile.value) payload.append('photo', photoFile.value)
    await api.post('/incidents', payload)
    ElMessage.success('异常单已提交')
    resetForm()
    page.value = 1
    await loadList()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail ?? '提交失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

async function submitHandle(item, payload) {
  handlingId.value = item.id
  try {
    await api.patch(`/incidents/${item.id}`, payload)
    ElMessage.success(payload.action === 'confirm' ? '已确认' : '已驳回')
    await loadList()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail ?? '操作失败，请稍后重试')
  } finally {
    handlingId.value = null
  }
}

async function confirmIncident(item) {
  try {
    await ElMessageBox.confirm(
      `确认「${item.consumable_name}」的异常单？`,
      '确认异常',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  await submitHandle(item, { action: 'confirm' })
}

async function rejectIncident(item) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请填写驳回原因', '驳回异常单', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputPlaceholder: '说明不通过的原因',
      inputValidator: (value) => (value?.trim() ? true : '请填写驳回原因'),
    })
    reason = result.value.trim()
  } catch {
    return
  }
  await submitHandle(item, { action: 'reject', reject_reason: reason })
}

async function convertToCount(item) {
  try {
    await ElMessageBox.confirm(
      `将「${item.consumable_name}」的异常单转为盘点？`,
      '转盘点',
      { confirmButtonText: '转盘点', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    return
  }
  convertingId.value = item.id
  try {
    const response = await api.post(`/incidents/${item.id}/convert-to-count`)
    ElMessage.success(`已生成盘点单 ${response.data.count_order_no}`)
    await loadList()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail ?? '转盘点失败，请稍后重试')
  } finally {
    convertingId.value = null
  }
}

function openPhoto(item) {
  photoDialogSrc.value = item.photo_url
  photoDialogVisible.value = true
}

onMounted(async () => {
  statusFilter.value = isStaff.value ? 'open' : ''
  await Promise.all([loadConsumables(), loadList()])
})
</script>

<template>
  <div class="page incidents">
    <PageHeader
      title="异常上报"
      :breadcrumb="['领用管理', '异常上报']"
    />

    <div class="incidents__layout">
      <section v-if="isStudent" class="panel incidents__panel">
        <h2 class="incidents__panel-title">提交异常单</h2>
        <el-alert
          v-if="consumablesError"
          type="error"
          :closable="false"
          show-icon
          :title="consumablesError"
          class="incidents__alert"
        />
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="耗材">
            <el-select
              v-model="form.consumableId"
              filterable
              placeholder="选择耗材"
              :loading="consumablesLoading"
              class="incidents__control"
              @change="handleConsumableChange"
            >
              <el-option
                v-for="item in consumables"
                :key="item.id"
                :label="consumableLabel(item)"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="位置">
            <el-input
              v-model="form.location"
              maxlength="64"
              placeholder="默认取耗材柜位"
              class="incidents__control"
            />
          </el-form-item>
          <el-form-item label="异常描述">
            <el-input
              v-model="form.description"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="描述现场异常情况"
            />
          </el-form-item>
          <el-form-item label="现场照片（可选）">
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              accept="image/jpeg,image/png,image/webp"
              :on-change="handlePhotoChange"
            >
              <el-button class="incidents__action">选择照片</el-button>
            </el-upload>
            <span v-if="photoName" class="incidents__photo-name">{{ photoName }}</span>
          </el-form-item>
          <el-button
            type="primary"
            class="incidents__submit"
            :loading="submitting"
            @click="handleSubmit"
          >
            提交异常单
          </el-button>
        </el-form>
      </section>

      <section class="panel incidents__panel">
        <div class="incidents__list-head">
          <h2 class="incidents__panel-title">
            {{ isStaff ? '异常队列' : '我的异常记录' }}
          </h2>
          <el-radio-group
            v-model="statusFilter"
            size="small"
            class="incidents__filters"
            @change="handleFilterChange"
          >
            <el-radio-button
              v-for="option in STATUS_OPTIONS"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </el-radio-button>
          </el-radio-group>
        </div>

        <el-alert
          v-if="listError"
          type="error"
          :closable="false"
          show-icon
          :title="listError"
          class="incidents__alert"
        />

        <div v-loading="listLoading" class="incidents__list">
          <EmptyState
            v-if="!listLoading && items.length === 0"
            title="暂无异常单"
            :description="isStaff ? '队列中没有符合条件的记录' : '提交后会显示在这里'"
          />
          <article v-for="item in items" :key="item.id" class="incidents__card">
            <div class="incidents__card-head">
              <span
                class="incidents__status"
                :class="statusClass(effectiveStatus(item))"
              >
                {{ statusLabel(effectiveStatus(item)) }}
              </span>
              <span class="incidents__time">{{ formatTime(item.created_at) }}</span>
            </div>
            <p class="incidents__card-title">
              {{ item.consumable_name }}<span v-if="item.consumable_spec">（{{ item.consumable_spec }}）</span>
            </p>
            <p class="incidents__card-meta">
              {{ item.location }}<template v-if="isStaff"> · {{ item.reporter_name }}</template>
            </p>
            <p class="incidents__card-desc">{{ item.description }}</p>
            <p v-if="item.count_order_no" class="incidents__card-handle">
              盘点单 {{ item.count_order_no }} · {{ countStatusLabel(item) }}
            </p>
            <p v-if="item.status === 'rejected'" class="incidents__card-handle">
              驳回原因：{{ item.reject_reason }}（{{ item.handler_name }}）
            </p>
            <p
              v-else-if="item.status === 'confirmed' && isStaff"
              class="incidents__card-handle"
            >
              {{ item.handler_name }} 于 {{ formatTime(item.handled_at) }} 确认
            </p>
            <img
              v-if="item.photo_url"
              :src="item.photo_url"
              alt="现场照片"
              class="incidents__thumb"
            />
            <div class="incidents__card-actions">
              <el-button
                v-if="item.photo_url"
                class="incidents__action"
                @click="openPhoto(item)"
              >
                查看照片
              </el-button>
              <template v-if="canManage && item.status === 'open'">
                <el-button
                  type="primary"
                  class="incidents__action"
                  :loading="handlingId === item.id"
                  @click="confirmIncident(item)"
                >
                  确认
                </el-button>
                <el-button
                  type="danger"
                  plain
                  class="incidents__action"
                  :loading="handlingId === item.id"
                  @click="rejectIncident(item)"
                >
                  驳回
                </el-button>
              </template>
              <el-button
                v-if="canManage && ['open', 'confirmed'].includes(item.status)"
                type="success"
                plain
                class="incidents__action"
                :loading="convertingId === item.id"
                @click="convertToCount(item)"
              >
                转盘点
              </el-button>
            </div>
          </article>
        </div>

        <div v-if="total > PAGE_SIZE" class="incidents__pager">
          <el-pagination
            :current-page="page"
            :page-size="PAGE_SIZE"
            :total="total"
            :pager-count="5"
            layout="prev, pager, next"
            small
            @current-change="handlePageChange"
          />
        </div>
      </section>
    </div>

    <el-dialog
      v-model="photoDialogVisible"
      title="现场照片"
      width="92%"
      class="incidents__photo-dialog"
    >
      <img v-if="photoQuery" :src="photoQuery" alt="现场照片" class="incidents__photo" />
    </el-dialog>
  </div>
</template>

<style scoped>
.incidents {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.incidents__layout {
  display: grid;
  grid-template-columns: minmax(0, 360px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.incidents__panel {
  padding: var(--space-4);
}

.incidents__panel-title {
  margin: 0 0 var(--space-3);
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.incidents__control {
  width: 100%;
}

.incidents__alert {
  margin-bottom: var(--space-3);
}

.incidents__photo-name {
  margin-left: var(--space-2);
  font-size: 12px;
  color: var(--text-muted);
  word-break: break-all;
}

.incidents__submit {
  width: 100%;
  min-height: 44px;
}

.incidents__list-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.incidents__list-head .incidents__panel-title {
  margin: 0;
}

.incidents__filters {
  flex-wrap: wrap;
}

.incidents__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-height: 80px;
}

.incidents__card {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-panel);
  padding: var(--space-3);
  background: var(--surface);
}

.incidents__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.incidents__time {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.incidents__status {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
}

.incidents__status--open {
  color: var(--warning);
  background: var(--warning-soft);
}

.incidents__status--confirmed {
  color: var(--success);
  background: var(--success-soft);
}

.incidents__status--rejected {
  color: var(--danger);
  background: var(--danger-soft);
}

.incidents__status--converted {
  color: var(--primary);
  background: var(--primary-soft);
}

.incidents__status--counted {
  color: var(--success);
  background: var(--success-soft);
}

.incidents__card-title {
  margin: var(--space-2) 0 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.incidents__card-meta {
  margin: var(--space-1) 0 0;
  font-size: 12px;
  color: var(--text-muted);
  word-break: break-all;
}

.incidents__card-desc {
  margin: var(--space-2) 0 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.incidents__card-handle {
  margin: var(--space-2) 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.incidents__thumb {
  display: block;
  max-width: 100%;
  max-height: 160px;
  margin-top: var(--space-2);
  border-radius: var(--radius-control);
  border: 1px solid var(--line-soft);
  object-fit: cover;
}

.incidents__card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.incidents__card-actions:empty {
  display: none;
}

.incidents__action {
  min-height: 44px;
  min-width: 72px;
}

.incidents__pager {
  display: flex;
  justify-content: center;
  margin-top: var(--space-3);
}

.incidents__photo {
  display: block;
  width: 100%;
  max-height: 70vh;
  object-fit: contain;
}

.incidents__photo-dialog :deep(.el-dialog) {
  max-width: 720px;
}

@media (max-width: 900px) {
  .incidents__layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 480px) {
  .incidents {
    padding: var(--content-padding-narrow);
  }

  .incidents__panel {
    padding: var(--space-3);
  }
}
</style>