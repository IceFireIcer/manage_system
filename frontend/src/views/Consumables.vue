<script setup>
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MoreFilled } from '@element-plus/icons-vue'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import RestockDialog from '../components/RestockDialog.vue'
import LedgerImportDialog from '../components/LedgerImportDialog.vue'

const router = useRouter()
const route = useRoute()
const focusId = ref(0)

const UNIT_OPTIONS = ['卷', '张', '支', '个', '米', '根']

const items = ref([])
const loading = ref(false)
const hasLoaded = ref(false)
const failed = ref(false)
const refreshFailed = ref(false)
const refreshFailReason = ref('')
const lastLoadedAt = ref(null)

const statusFilter = ref('enabled')
const keywordDraft = ref('')
const keyword = ref('')

const invisibleNotice = ref(false)
const staleNotice = ref('')

const viewportWidth = ref(window.innerWidth)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref(null)
const editingVersion = ref(null)
const formRef = ref()
const formError = ref('')
const nameError = ref('')

const receive = reactive({
  visible: false,
  row: null,
})

const ledgerImportVisible = ref(false)

const emptyForm = {
  code: '',
  name: '',
  spec: '',
  unit: '',
  location: '',
  safety_stock: 0,
  current_stock: 0,
}
const form = reactive({ ...emptyForm })

const dialogWidth = computed(() =>
  viewportWidth.value < 560 ? `${viewportWidth.value - 32}px` : '520px',
)

const dialogTitle = computed(() => (editingId.value ? '编辑耗材' : '新增耗材'))
const saveLabel = computed(() => (editingId.value ? '保存修改' : '保存'))

const lastLoadedLabel = computed(() => {
  if (!lastLoadedAt.value) return '—'
  const value = lastLoadedAt.value
  const pad = (number) => String(number).padStart(2, '0')
  return `${pad(value.getHours())}:${pad(value.getMinutes())}`
})

const appliedKeyword = computed(() => keyword.value.trim().toLowerCase())

const visibleItems = computed(() => {
  if (!appliedKeyword.value) return items.value
  return items.value.filter((item) =>
    [item.code, item.name, item.spec, item.location].some((field) =>
      String(field ?? '')
        .toLowerCase()
        .includes(appliedKeyword.value),
    ),
  )
})

const emptyMode = computed(() => {
  if (items.value.length) return 'search'
  return statusFilter.value === 'disabled' ? 'disabled' : 'enabled'
})

function textValidator(message, maxLength) {
  return (_rule, value, callback) => {
    const text = String(value ?? '').trim()
    if (!text) {
      callback(new Error(message))
      return
    }
    if (text.length > maxLength) {
      callback(new Error(`不能超过 ${maxLength} 个字符`))
      return
    }
    callback()
  }
}

function integerValidator(message) {
  return (_rule, value, callback) => {
    if (value === null || value === undefined || value === '') {
      callback(new Error(message))
      return
    }
    const number = Number(value)
    if (!Number.isInteger(number) || number < 0) {
      callback(new Error('请输入非负整数'))
      return
    }
    callback()
  }
}

const rules = {
  name: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        if (nameError.value) {
          callback(new Error(nameError.value))
          return
        }
        textValidator('请输入耗材名称', 128)(_rule, value, callback)
      },
      trigger: 'blur',
    },
  ],
  spec: [{ required: true, validator: textValidator('请输入规格', 128), trigger: 'blur' }],
  unit: [{ required: true, validator: textValidator('请输入计量单位', 16), trigger: 'blur' }],
  location: [{ required: true, validator: textValidator('请输入存放位置', 64), trigger: 'blur' }],
  safety_stock: [{ required: true, validator: integerValidator('请输入安全库存线'), trigger: 'blur' }],
  current_stock: [{ required: true, validator: integerValidator('请输入当前库存'), trigger: 'blur' }],
}

watch(
  () => form.name,
  () => {
    if (nameError.value) nameError.value = ''
  },
)

let requestSeq = 0

async function fetchList(failReason = '') {
  const seq = ++requestSeq
  loading.value = true
  try {
    const { data } = await api.get('/consumables', {
      params: { status: statusFilter.value },
    })
    if (seq !== requestSeq) return
    items.value = Array.isArray(data) ? data : []
    hasLoaded.value = true
    failed.value = false
    refreshFailed.value = false
    refreshFailReason.value = ''
    lastLoadedAt.value = new Date()
  } catch {
    if (seq !== requestSeq) return
    if (hasLoaded.value) {
      refreshFailed.value = true
      refreshFailReason.value = failReason || '暂时无法刷新列表'
    } else {
      failed.value = true
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function handleStatusChange(value) {
  statusFilter.value = value
  invisibleNotice.value = false
  fetchList()
}

function applySearch() {
  keyword.value = keywordDraft.value
}

function clearSearch() {
  keywordDraft.value = ''
  keyword.value = ''
}

function clearFilters() {
  statusFilter.value = 'enabled'
  clearSearch()
  invisibleNotice.value = false
  fetchList()
}

function matchesFilters(row) {
  if (statusFilter.value !== 'enabled') return false
  if (!appliedKeyword.value) return true
  return [row.code, row.name, row.spec, row.location].some((field) =>
    String(field ?? '')
      .toLowerCase()
      .includes(appliedKeyword.value),
  )
}

function openCreate() {
  editingId.value = null
  editingVersion.value = null
  Object.assign(form, emptyForm)
  formError.value = ''
  nameError.value = ''
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  editingVersion.value = row.version ?? null
  Object.assign(form, {
    code: row.code ?? '',
    name: row.name,
    spec: row.spec,
    unit: row.unit,
    location: row.location,
    safety_stock: row.safety_stock,
    current_stock: row.current_stock,
  })
  formError.value = ''
  nameError.value = ''
  dialogVisible.value = true
}

function errorMessage(error) {
  if (!error?.response) {
    return '暂未确认提交结果，请刷新档案核对后再决定是否重新提交'
  }
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean)
    return messages.length ? messages.join('；') : '保存失败，请稍后重试'
  }
  if (detail && typeof detail === 'object') {
    return detail.message || '保存失败，请稍后重试'
  }
  if (detail) return String(detail)
  return '保存失败，请稍后重试'
}

function validateForm() {
  if (!formRef.value) return Promise.resolve(false)
  return formRef.value.validate().catch(() => {
    formRef.value?.$el?.querySelector('.is-error input')?.focus()
    return false
  })
}

function hasNameFieldError(error) {
  const detail = error.response?.data?.detail
  if (!Array.isArray(detail)) return false
  return detail.some((item) => Array.isArray(item?.loc) && item.loc.includes('name'))
}

async function handleSave() {
  if (saving.value) return
  const valid = await validateForm()
  if (!valid) return
  saving.value = true
  formError.value = ''
  nameError.value = ''
  const isEdit = Boolean(editingId.value)
  const payload = {
    name: form.name.trim(),
    spec: form.spec.trim(),
    unit: form.unit.trim(),
    location: form.location.trim(),
    safety_stock: Number(form.safety_stock),
  }
  try {
    let created = null
    if (isEdit) {
      await api.put(`/consumables/${editingId.value}`, {
        ...payload,
        expected_version: editingVersion.value,
      })
    } else {
      const { data } = await api.post('/consumables', {
        ...payload,
        current_stock: Number(form.current_stock),
      })
      created = data
    }
    dialogVisible.value = false
    ElMessage.success(isEdit ? '已保存修改' : '已新增耗材')
    if (created) invisibleNotice.value = !matchesFilters(created)
    await fetchList(isEdit ? '已保存，暂时无法刷新列表' : '已新增，暂时无法刷新列表')
  } catch (error) {
    const reason = errorMessage(error)
    if (hasNameFieldError(error) || reason.includes('同名')) {
      nameError.value = reason
      formRef.value?.validateField('name').catch(() => undefined)
    }
    if (error?.response?.status === 409) {
      dialogVisible.value = false
      staleNotice.value = reason
      await fetchList('档案已被他人修改，请重新核对')
      return
    }
    formError.value = reason
  } finally {
    saving.value = false
  }
}

const CONFIRM_WARN = 'confirm-body__warn'

async function loadDeactivationPreview(row) {
  try {
    const { data } = await api.get(`/consumables/${row.id}/deactivation-preview`)
    return data
  } catch {
    return null
  }
}

function deactivationMessage(row, preview) {
  const current = preview?.consumable ?? row
  const blocks = [h('p', { class: 'confirm-body__title' }, `${row.name} / ${row.spec}`)]
  blocks.push(h('p', null, `当前余量：${current.current_stock} ${row.unit}`))
  const receipts = preview?.pending_receipts ?? []
  blocks.push(
    h(
      'p',
      null,
      receipts.length
        ? `待收货：${receipts
            .map((item) => `${item.order_no}（${item.quantity} ${row.unit}）`)
            .join('、')}`
        : '待收货：无',
    ),
  )
  const actions = preview?.pending_actions ?? []
  blocks.push(
    h(
      'p',
      null,
      actions.length
        ? `待处理事项：${actions
            .map((item) => `${item.label} ${item.count} 项`)
            .join('、')}`
        : '待处理事项：无',
    ),
  )
  if (current.current_stock > 0) {
    blocks.push(
      h(
        'p',
        { class: CONFIRM_WARN },
        `仍有 ${current.current_stock} ${row.unit} 库存，停用后将不能再领用或入库，请先核对账目。`,
      ),
    )
  }
  blocks.push(
    h('p', null, '停用后该耗材将从领用列表、库存总览及巡检中移出，历史流水与账目仍可按权限查询。'),
  )
  return h('div', { class: 'confirm-body' }, blocks)
}

function enabledMessage(row, preview) {
  const blocks = [h('p', { class: 'confirm-body__title' }, `${row.name} / ${row.spec}`)]
  blocks.push(h('p', null, `当前余量：${row.current_stock} ${row.unit}`))
  if (preview?.disabled_stock != null) {
    blocks.push(h('p', null, `停用时余量：${preview.disabled_stock} ${row.unit}`))
    if (preview.disabled_stock !== row.current_stock) {
      blocks.push(
        h('p', { class: CONFIRM_WARN }, '停用期间余额已变化，请核对流水后再启用。'),
      )
    }
  }
  const disabledReason = preview?.consumable?.disabled_reason
  if (disabledReason || preview?.disabled_by_name) {
    const parts = []
    if (preview.disabled_by_name) parts.push(`停用人：${preview.disabled_by_name}`)
    if (disabledReason) parts.push(`停用原因：${disabledReason}`)
    blocks.push(h('p', null, parts.join('；')))
  }
  blocks.push(
    h('p', null, '启用后该耗材将重新出现在库存总览与领用列表中，库存与安全库存线沿用现有数据。'),
  )
  return h('div', { class: 'confirm-body' }, blocks)
}

async function handleToggle(row, nextStatus) {
  const disabling = nextStatus === 'disabled'
  const preview = await loadDeactivationPreview(row)
  let reason = null
  if (disabling) {
    let value = ''
    try {
      const result = await ElMessageBox.prompt(deactivationMessage(row, preview), '停用耗材', {
        type: 'warning',
        confirmButtonText: '确认停用',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '请填写停用原因（必填）',
        inputValidator: (text) => (String(text ?? '').trim() ? true : '请填写停用原因'),
      })
      value = result.value
    } catch {
      return
    }
    reason = String(value).trim()
  } else {
    try {
      await ElMessageBox.confirm(enabledMessage(row, preview), '启用耗材', {
        type: 'warning',
        confirmButtonText: '确认启用',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  try {
    const { data } = await api.patch(`/consumables/${row.id}/status`, {
      status: nextStatus,
      reason,
      expected_version: row.version ?? null,
    })
    if (disabling) {
      ElMessage.success('耗材已停用')
    } else if (data?.audit?.balance_changed) {
      ElMessage.warning(
        `停用期间余额由 ${data.audit.previous_stock} 变为 ${data.audit.current_stock}，请核对`,
      )
    } else {
      ElMessage.success('耗材已启用')
    }
    await fetchList(disabling ? '已停用，暂时无法刷新列表' : '已启用，暂时无法刷新列表')
  } catch (error) {
    staleNotice.value = `操作未完成：${errorMessage(error)}`
    if (error?.response?.status === 409) {
      await fetchList('档案已被他人修改，请重新核对')
    }
  }
}

async function retryRead() {
  staleNotice.value = ''
  await fetchList()
}

function goRecords(row) {
  router.push({ path: '/records', query: { consumable_id: String(row.id) } })
}

function rowClassName({ row }) {
  return row.id === focusId.value ? 'consumables__row--focus' : ''
}

function openReceive(row) {
  receive.row = row
  receive.visible = true
}

function handleReceived() {
  fetchList('已入库，暂时无法刷新列表')
}

function handleLedgerImported() {
  statusFilter.value = 'enabled'
  clearSearch()
  fetchList('已导入台账，暂时无法刷新列表')
}

function handleRowCommand(row, command) {
  if (command === 'receive') openReceive(row)
  else if (command === 'records') goRecords(row)
  else if (command === 'disable') handleToggle(row, 'disabled')
  else if (command === 'enable') handleToggle(row, 'enabled')
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  const focus = Number(route.query.consumable_id)
  focusId.value = Number.isInteger(focus) && focus > 0 ? focus : 0
  window.addEventListener('resize', handleResize)
  fetchList()
})

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<template>
  <div class="consumables">
    <PageHeader title="耗材档案" :breadcrumb="['日常管理', '耗材档案']">
      <template #actions>
        <el-button @click="ledgerImportVisible = true">导入台账</el-button>
        <el-button type="primary" @click="openCreate">新增耗材</el-button>
      </template>
    </PageHeader>

    <div class="consumables__toolbar panel">
      <el-radio-group
        v-model="statusFilter"
        class="consumables__segment"
        aria-label="耗材状态"
        @change="handleStatusChange"
      >
        <el-radio-button value="enabled">启用中</el-radio-button>
        <el-radio-button value="disabled">已停用</el-radio-button>
      </el-radio-group>
      <div class="consumables__search">
        <el-input
          v-model="keywordDraft"
          class="consumables__search-input"
          placeholder="搜索名称、规格或位置"
          maxlength="64"
          clearable
          aria-label="搜索耗材"
          @keyup.enter="applySearch"
          @clear="applySearch"
        />
        <el-button @click="applySearch">查询</el-button>
      </div>
      <el-button text class="consumables__refresh" :loading="loading" @click="fetchList()">
        刷新
      </el-button>
    </div>

    <div v-if="staleNotice" class="consumables__banner consumables__banner--warning" role="status">
      <span>{{ staleNotice }}</span>
      <el-button link type="primary" @click="retryRead">刷新档案</el-button>
    </div>

    <div v-if="invisibleNotice" class="consumables__banner consumables__banner--info" role="status">
      <span>耗材已新增，当前筛选下不可见。</span>
      <el-button link type="primary" @click="clearFilters">清除筛选</el-button>
    </div>

    <div
      v-if="refreshFailed"
      class="consumables__banner consumables__banner--warning"
      role="status"
    >
      <span>{{ refreshFailReason }}，显示上次结果（{{ lastLoadedLabel }}）。</span>
      <el-button link type="primary" @click="fetchList()">重试读取</el-button>
    </div>

    <div v-if="!hasLoaded" class="consumables__placeholder panel" :aria-busy="!failed">
      <el-skeleton v-if="!failed" :rows="6" animated />
      <div v-else class="consumables__error" role="alert">
        <p class="consumables__error-title">耗材档案读取失败</p>
        <p class="consumables__error-desc">请检查网络连接后重试。</p>
        <el-button @click="fetchList()">重试</el-button>
      </div>
    </div>

    <template v-else>
      <p v-if="loading" class="consumables__refreshing" role="status">正在刷新…</p>
      <div class="consumables__table panel">
        <el-table :data="visibleItems" class="table-dense" row-key="id" :row-class-name="rowClassName" :aria-busy="loading">
          <el-table-column label="名称／规格" min-width="180">
            <template #default="{ row }">
              <div class="cell-main">{{ row.name }}</div>
              <div class="cell-sub">{{ row.spec }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="code" label="编码" width="128">
            <template #default="{ row }">
              <span class="cell-code">{{ row.code || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="unit" label="单位" width="80" />
          <el-table-column prop="location" label="存放位置" min-width="130" />
          <el-table-column label="安全库存线" width="132" align="right">
            <template #default="{ row }">
              <div class="cell-number">{{ row.safety_stock }} {{ row.unit }}</div>
              <div v-if="row.safety_stock === 0" class="cell-sub">未设安全库存线</div>
            </template>
          </el-table-column>
          <el-table-column label="当前库存" width="128" align="right">
            <template #default="{ row }">
              <div class="cell-number" :class="{ 'cell-number--out': row.current_stock === 0 }">
                {{ row.current_stock }} {{ row.unit }}
              </div>
              <div v-if="row.current_stock === 0" class="cell-sub cell-sub--danger">缺货</div>
              <div v-else-if="row.low_stock" class="cell-sub cell-sub--warning">低库存</div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="{ row }">
              <span
                class="status-tag"
                :class="row.status === 'enabled' ? 'status-tag--success' : 'status-tag--neutral'"
              >
                {{ row.status === 'enabled' ? '启用' : '停用' }}
              </span>
              <div v-if="row.status === 'disabled' && row.disabled_reason" class="cell-sub">
                {{ row.disabled_reason }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="consumables__row-actions">
                <el-button
                  link
                  type="primary"
                  size="small"
                  :aria-label="`编辑 ${row.name}`"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-dropdown
                  trigger="click"
                  placement="bottom-end"
                  @command="(command) => handleRowCommand(row, command)"
                >
                  <el-button
                    link
                    size="small"
                    class="consumables__more"
                    :aria-label="`${row.name} 的更多操作`"
                  >
                    更多
                    <el-icon :size="12" class="consumables__more-icon"><MoreFilled /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item v-if="row.status === 'enabled'" command="receive">
                        收货
                      </el-dropdown-item>
                      <el-dropdown-item command="records">查流水</el-dropdown-item>
                      <el-dropdown-item
                        v-if="row.status === 'enabled'"
                        divided
                        command="disable"
                        class="consumables__danger-item"
                      >
                        停用
                      </el-dropdown-item>
                      <el-dropdown-item v-else command="enable">启用</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>
          </el-table-column>

          <template #empty>
            <EmptyState
              v-if="emptyMode === 'enabled'"
              title="还没有启用中的耗材"
              description="新增后默认启用，可立即登记领用。"
            >
              <template #action>
                <el-button @click="openCreate">新增耗材</el-button>
                <el-button text @click="handleStatusChange('disabled')">查看已停用</el-button>
              </template>
            </EmptyState>
            <EmptyState
              v-else-if="emptyMode === 'disabled'"
              title="暂无停用耗材"
              description="停用后的耗材会保留在这里。"
            >
              <template #action>
                <el-button text @click="handleStatusChange('enabled')">查看启用中</el-button>
                <el-button @click="openCreate">新增耗材</el-button>
              </template>
            </EmptyState>
            <EmptyState
              v-else
              title="没有符合条件的耗材"
              :description="`没有找到与「${keyword.trim()}」匹配的结果。`"
            >
              <template #action>
                <el-button @click="clearSearch">清除搜索</el-button>
              </template>
            </EmptyState>
          </template>
        </el-table>
      </div>
    </template>

    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="dialogWidth"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
      :show-close="!saving"
      destroy-on-close
      class="consumables__dialog"
    >
      <el-alert
        v-if="formError"
        type="error"
        :closable="false"
        show-icon
        :title="formError"
        class="consumables__form-error"
      />
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        scroll-to-error
        class="consumables__form"
      >
        <el-form-item v-if="editingId" label="耗材编码">
          <span class="consumables__readonly">{{ form.code || '—' }}</span>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="128" placeholder="如：焊丝" />
        </el-form-item>
        <el-form-item label="规格" prop="spec">
          <el-input v-model="form.spec" maxlength="128" placeholder="如：240 目" />
        </el-form-item>
        <el-form-item label="单位" prop="unit">
          <el-select
            v-model="form.unit"
            filterable
            allow-create
            default-first-option
            placeholder="如：卷"
            class="consumables__unit"
          >
            <el-option v-for="unit in UNIT_OPTIONS" :key="unit" :label="unit" :value="unit" />
          </el-select>
        </el-form-item>
        <el-form-item label="存放位置" prop="location">
          <el-input v-model="form.location" maxlength="64" placeholder="如：A 柜第 2 层" />
        </el-form-item>
        <el-form-item label="安全库存线" prop="safety_stock">
          <el-input-number v-model="form.safety_stock" :min="0" :step="1" step-strictly />
          <p v-if="Number(form.safety_stock) === 0" class="consumables__form-hint">
            设为 0 将跳过补货巡检
          </p>
        </el-form-item>
        <el-form-item v-if="!editingId" label="当前库存" prop="current_stock">
          <el-input-number v-model="form.current_stock" :min="0" :step="1" step-strictly />
        </el-form-item>
        <el-form-item v-else label="当前库存">
          <span class="consumables__readonly">{{ form.current_stock }} {{ form.unit }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="consumables__dialog-footer">
          <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="handleSave">{{ saveLabel }}</el-button>
        </div>
      </template>
    </el-dialog>

    <RestockDialog
      v-model="receive.visible"
      :consumable="receive.row"
      @completed="handleReceived"
    />

    <LedgerImportDialog
      v-model="ledgerImportVisible"
      @completed="handleLedgerImported"
    />
  </div>
</template>

<style scoped>
.consumables {
  display: flex;
  flex-direction: column;
}

.consumables__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
  padding: 12px 16px;
  margin-bottom: var(--space-4);
}

.consumables__segment {
  padding: 2px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.consumables__segment :deep(.el-radio-button__inner) {
  min-height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: var(--radius-control);
  background: transparent;
  box-shadow: none;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  line-height: 30px;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.consumables__segment :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-left: none;
}

.consumables__segment :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: var(--primary-soft);
  box-shadow: none;
  color: var(--primary);
  font-weight: 600;
}

.consumables__search {
  display: flex;
  flex: 1 1 260px;
  align-items: center;
  gap: 8px;
}

.consumables__search-input {
  max-width: 300px;
}

.consumables__refresh {
  margin-left: auto;
  color: var(--text-secondary);
}

.consumables__row-actions {
  display: flex;
  align-items: center;
}

.consumables__row-actions :deep(.el-button + .el-dropdown) {
  margin-left: 8px;
}

.consumables__more-icon {
  margin-left: 2px;
}

.consumables__banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  margin-bottom: var(--space-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  font-size: 14px;
  line-height: 22px;
}

.consumables__banner--warning {
  border-color: var(--warning-line);
  background: var(--warning-soft);
  color: var(--warning);
}

.consumables__banner--info {
  border-color: var(--info-line);
  background: var(--info-soft);
  color: var(--info);
}

.consumables__placeholder {
  padding: var(--space-5);
}

.consumables__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
}

.consumables__error-title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.consumables__error-desc {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.consumables__refreshing {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.consumables__table {
  overflow: hidden;
}

.cell-main {
  color: var(--text);
  font-weight: 500;
}

.cell-code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-muted);
}

.cell-sub {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.cell-sub--danger {
  color: var(--danger);
}

.cell-sub--warning {
  color: var(--warning);
}

.cell-number {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.cell-number--out {
  color: var(--danger);
  font-weight: 600;
}

.consumables__form-error {
  margin-bottom: var(--space-4);
}

.consumables__form-hint {
  width: 100%;
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.consumables__readonly {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.consumables__unit {
  width: 100%;
}

.consumables__dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .consumables__toolbar {
    gap: 8px;
    padding: 12px;
  }

  .consumables__segment {
    flex: 1 1 100%;
    display: flex;
  }

  .consumables__segment :deep(.el-radio-button) {
    flex: 1;
  }

  .consumables__segment :deep(.el-radio-button__inner) {
    width: 100%;
    min-height: 38px;
    line-height: 38px;
  }

  .consumables__search {
    flex: 1 1 100%;
  }

  .consumables__search-input {
    max-width: none;
    flex: 1;
  }

  .consumables__refresh {
    margin-left: 0;
    min-height: 44px;
  }
}
</style>

<style>
.consumables__dialog {
  --el-dialog-padding-primary: 24px;
}

.confirm-body p {
  margin: 0 0 6px;
  line-height: 22px;
}

.confirm-body p:last-child {
  margin-bottom: 0;
}

.confirm-body__title {
  font-weight: 600;
  color: var(--text);
}

.confirm-body__warn {
  color: var(--warning);
}

.consumables__row--focus td.el-table__cell {
  background: var(--primary-soft);
}

.el-dropdown-menu__item.consumables__danger-item {
  color: var(--danger);
}

.el-dropdown-menu__item.consumables__danger-item:not(.is-disabled):hover {
  background: var(--danger-soft);
  color: var(--danger);
}
</style>