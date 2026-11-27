<script setup>
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const userStore = useUserStore()
const route = useRoute()

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }
const ROLE_CLASSES = {
  teacher: 'role-tag--teacher',
  admin: 'role-tag--admin',
  student: 'role-tag--student',
}

const DEFAULT_FILTERS = { role: 'student', status: '' }
const ROLE_VALUES = ['', 'student', 'teacher', 'admin']
const STATUS_VALUES = ['', 'enabled', 'disabled']

const users = ref([])
const loading = ref(false)
const hasLoaded = ref(false)
const failed = ref(false)
const refreshFailed = ref(false)
const refreshFailReason = ref('')
const lastLoadedAt = ref(null)

const filters = reactive({ ...DEFAULT_FILTERS })
const keywordDraft = ref('')
const keyword = ref('')

const viewportWidth = ref(window.innerWidth)

const createVisible = ref(false)
const creating = ref(false)
const formRef = ref()
const formError = ref('')
const accountError = ref('')

const form = reactive({
  account: '',
  name: '',
  password: '',
})

const editVisible = ref(false)
const editSaving = ref(false)
const editError = ref('')
const editForm = reactive({ id: null, account: '', name: '' })

const resetVisible = ref(false)
const resetResult = ref(null)

const importVisible = ref(false)
const importFileName = ref('')
const importSource = ref('')
const importRows = ref([])
const importPreview = ref(null)
const importOnExisting = ref('skip')
const importRequestId = ref('')
const importError = ref('')
const importParsing = ref(false)
const importSubmitting = ref(false)
const importReceipt = ref(null)

const IMPORT_STATUS_NAMES = {
  ready: '可创建',
  existing: '已存在',
  update: '已存在·更正',
  error: '错误',
}

const IMPORT_ACTION_NAMES = {
  created: '已创建',
  updated: '已更正',
  unchanged: '无变化',
  skipped: '已跳过',
}

const dialogWidth = computed(() =>
  viewportWidth.value < 552 ? `${viewportWidth.value - 32}px` : '520px',
)

const importDialogWidth = computed(() =>
  viewportWidth.value < 860 ? `${viewportWidth.value - 32}px` : '820px',
)

const lastLoadedLabel = computed(() => {
  if (!lastLoadedAt.value) return '—'
  const value = lastLoadedAt.value
  const pad = (number) => String(number).padStart(2, '0')
  return `${pad(value.getHours())}:${pad(value.getMinutes())}`
})

const appliedKeyword = computed(() => keyword.value.trim().toLowerCase())

const visibleUsers = computed(() => {
  if (!appliedKeyword.value) return users.value
  return users.value.filter((user) =>
    [user.account, user.name].some((field) =>
      String(field ?? '')
        .toLowerCase()
        .includes(appliedKeyword.value),
    ),
  )
})

const isDefaultView = computed(
  () => filters.role === 'student' && !filters.status && !appliedKeyword.value,
)

const emptyTitle = computed(() =>
  !users.value.length && isDefaultView.value ? '还没有学生账号' : '没有符合条件的账号',
)

const emptyDescription = computed(() => {
  if (!users.value.length && isDefaultView.value) {
    return '创建学生账号后，学生即可使用分配的账号登记本人领用。'
  }
  const parts = []
  if (filters.role) parts.push(`角色：${ROLE_NAMES[filters.role] ?? filters.role}`)
  if (filters.status) parts.push(`状态：${filters.status === 'enabled' ? '启用' : '停用'}`)
  if (appliedKeyword.value) parts.push(`关键词：${keyword.value.trim()}`)
  return parts.length ? `当前条件为 ${parts.join('，')}，没有符合条件的账号。` : '没有符合条件的账号。'
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

const rules = {
  account: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        if (accountError.value) {
          callback(new Error(accountError.value))
          return
        }
        textValidator('请输入账号', 64)(_rule, value, callback)
      },
      trigger: 'blur',
    },
  ],
  name: [{ required: true, validator: textValidator('请输入姓名', 64), trigger: 'blur' }],
  password: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        const text = String(value ?? '')
        if (!text) {
          callback(new Error('请输入初始密码'))
          return
        }
        if (text.length < 6) {
          callback(new Error('初始密码至少 6 位'))
          return
        }
        if (text.length > 64) {
          callback(new Error('初始密码不能超过 64 位'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

watch(
  () => form.account,
  () => {
    if (accountError.value) accountError.value = ''
  },
)

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  return matched ? `${matched[1]}-${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}` : text
}

function errorMessage(
  error,
  fallback = '操作失败，请稍后重试',
  unconfirmed = '暂未确认操作结果，请刷新名单核对后再决定是否重试',
) {
  if (!error?.response) return unconfirmed
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean)
    return messages.length ? messages.join('；') : fallback
  }
  if (detail) return String(detail)
  return fallback
}

function validateForm() {
  if (!formRef.value) return Promise.resolve(false)
  return formRef.value.validate().catch(() => {
    formRef.value?.$el?.querySelector('.is-error input')?.focus()
    return false
  })
}

let requestSeq = 0

async function fetchUsers(failReason = '') {
  const seq = ++requestSeq
  loading.value = true
  try {
    const params = {}
    if (filters.role) params.role = filters.role
    if (filters.status) params.status = filters.status
    const { data } = await api.get('/users', { params })
    if (seq !== requestSeq) return
    users.value = Array.isArray(data) ? data : []
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

function applySearch() {
  keyword.value = keywordDraft.value
}

function applyRouteQuery() {
  const { role, status } = route.query
  if (typeof role === 'string') {
    filters.role = ROLE_VALUES.includes(role) ? role : DEFAULT_FILTERS.role
  }
  if (typeof status === 'string') {
    filters.status = STATUS_VALUES.includes(status) ? status : DEFAULT_FILTERS.status
  }
}

function resetFilters() {
  filters.role = DEFAULT_FILTERS.role
  filters.status = DEFAULT_FILTERS.status
  keywordDraft.value = ''
  keyword.value = ''
  fetchUsers()
}

function handleFilterChange() {
  fetchUsers()
}

function openCreateDialog() {
  Object.assign(form, { account: '', name: '', password: '' })
  formError.value = ''
  accountError.value = ''
  createVisible.value = true
}

async function handleCreate() {
  if (creating.value) return
  const valid = await validateForm()
  if (!valid) return
  creating.value = true
  formError.value = ''
  accountError.value = ''
  const account = form.account.trim()
  try {
    await api.post('/users', {
      account,
      name: form.name.trim(),
      password: form.password,
      role: 'student',
    })
    ElMessage.success(`已创建学生账号 ${account}`)
    createVisible.value = false
    form.password = ''
    await fetchUsers('账号已创建，暂时无法刷新列表')
  } catch (error) {
    const reason = errorMessage(
      error,
      '创建失败，请稍后重试',
      '暂未确认创建结果，请刷新名单核对后再决定是否重试',
    )
    if (reason.includes('已存在') || reason.includes('账号')) {
      accountError.value = reason
      formRef.value?.validateField('account').catch(() => undefined)
    }
    formError.value = reason
  } finally {
    creating.value = false
  }
}

function statusConfirmMessage(row, nextStatus) {
  const blocks = [h('p', { class: 'confirm-body__title' }, `${row.name}（${row.account}）`)]
  if (nextStatus === 'disabled') {
    blocks.push(h('p', null, '停用后该账号无法登录，历史领用记录保留。'))
  } else {
    blocks.push(h('p', null, '启用后该账号可以登录并登记本人领用。'))
  }
  return h('div', { class: 'confirm-body' }, blocks)
}

async function changeStatus(row, nextStatus) {
  const disabling = nextStatus === 'disabled'
  try {
    await ElMessageBox.confirm(
      statusConfirmMessage(row, nextStatus),
      disabling ? '停用账号' : '启用账号',
      {
        type: 'warning',
        confirmButtonText: disabling ? '确认停用' : '确认启用',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  try {
    await api.patch(`/users/${row.id}/status`, { status: nextStatus })
    ElMessage.success(disabling ? `账号 ${row.account} 已停用` : `账号 ${row.account} 已启用`)
    await fetchUsers(disabling ? '账号已停用，暂时无法刷新列表' : '账号已启用，暂时无法刷新列表')
  } catch (error) {
    ElMessage.error(errorMessage(error))
    if (error.response?.status === 404 || error.response?.status === 400) {
      await fetchUsers()
    }
  }
}

function handleDisable(row) {
  changeStatus(row, 'disabled')
}

function handleEnable(row) {
  changeStatus(row, 'enabled')
}

function openEditDialog(row) {
  editForm.id = row.id
  editForm.account = row.account
  editForm.name = row.name
  editError.value = ''
  editVisible.value = true
}

async function handleEdit() {
  if (editSaving.value) return
  editSaving.value = true
  editError.value = ''
  try {
    await api.patch(`/users/${editForm.id}`, {
      account: editForm.account.trim(),
      name: editForm.name.trim(),
    })
    ElMessage.success('人员信息已更正，历史记录仍归属同一人员')
    editVisible.value = false
    await fetchUsers('人员信息已更正，暂时无法刷新列表')
  } catch (error) {
    editError.value = errorMessage(
      error,
      '更正失败，请稍后重试',
      '暂未确认更正结果，请刷新名单核对后再决定是否重试',
    )
  } finally {
    editSaving.value = false
  }
}

async function handleResetPassword(row) {
  try {
    await ElMessageBox.confirm(
      h('div', { class: 'confirm-body' }, [
        h('p', { class: 'confirm-body__title' }, `${row.name}（${row.account}）`),
        h('p', null, '重置后旧密码与旧登录会话立即失效，需将一次性临时密码交给该学生。'),
      ]),
      '重置学生密码',
      {
        type: 'warning',
        confirmButtonText: '确认重置',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  try {
    const { data } = await api.post(`/users/${row.id}/reset-password`)
    resetResult.value = data
    resetVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error, '重置失败，请稍后重试'))
  }
}

function closeResetResult() {
  resetVisible.value = false
  resetResult.value = null
}

function parseRosterCsv(text) {
  const rows = []
  const lines = String(text ?? '')
    .replace(/^\ufeff/, '')
    .split(/\r?\n/)
  lines.forEach((raw, index) => {
    if (!raw.trim()) return
    const cells = raw.split(',').map((cell) => cell.trim())
    if (index === 0 && (cells[0]?.includes('学号') || cells[1]?.includes('姓名'))) return
    rows.push({ line_no: index + 1, account: cells[0] ?? '', name: cells[1] ?? '' })
  })
  return rows
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `roster-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function openImportDialog() {
  importFileName.value = ''
  importSource.value = ''
  importRows.value = []
  importPreview.value = null
  importOnExisting.value = 'skip'
  importRequestId.value = ''
  importError.value = ''
  importParsing.value = false
  importSubmitting.value = false
  importReceipt.value = null
  importVisible.value = true
}

function closeImportDialog() {
  if (importSubmitting.value) return
  importVisible.value = false
}

async function handleTemplateDownload() {
  try {
    const response = await api.get('/users/import/template', { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'student_roster_template.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('模板下载失败，请稍后重试')
  }
}

async function runPreview() {
  importError.value = ''
  importPreview.value = null
  if (!importRows.value.length) {
    importError.value = '名单为空，请检查文件是否为「学号,姓名」两列 CSV'
    return
  }
  importParsing.value = true
  try {
    const { data } = await api.post('/users/import/preview', {
      source: importSource.value || '花名册导入',
      rows: importRows.value,
      on_existing: importOnExisting.value,
    })
    importPreview.value = data
  } catch (error) {
    importError.value = errorMessage(
      error,
      '预检失败，请稍后重试',
      '暂未确认预检结果，请重试',
    )
  } finally {
    importParsing.value = false
  }
}

async function handleRosterFile(uploadFile) {
  const file = uploadFile?.raw
  if (!file || importSubmitting.value) return
  importFileName.value = file.name
  importSource.value = file.name.replace(/\.csv$/i, '') || '花名册导入'
  importReceipt.value = null
  importRequestId.value = newRequestId()
  importError.value = ''
  try {
    importRows.value = parseRosterCsv(await file.text())
  } catch {
    importRows.value = []
    importError.value = '名单读取失败，请确认文件为 CSV 编码'
    return
  }
  await runPreview()
}

function handleOnExistingChange() {
  if (importRows.value.length) runPreview()
}

async function handleImport() {
  if (!importPreview.value?.valid || importSubmitting.value) return
  importSubmitting.value = true
  importError.value = ''
  try {
    const { data } = await api.post('/users/import', {
      source: importSource.value || '花名册导入',
      rows: importRows.value,
      on_existing: importOnExisting.value,
      request_id: importRequestId.value,
    })
    importReceipt.value = data
    ElMessage.success('花名册已导入，请立即转交一次性临时密码')
    await fetchUsers('花名册已导入，暂时无法刷新列表')
  } catch (error) {
    importError.value = errorMessage(
      error,
      '导入失败，请稍后重试',
      '暂未确认导入结果，请先查询批次回执再决定是否重试',
    )
  } finally {
    importSubmitting.value = false
  }
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  applyRouteQuery()
  fetchUsers()
})

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<template>
  <div class="users">
    <PageHeader title="用户管理" :breadcrumb="['日常管理', '用户管理']">
      <template #actions>
        <el-button @click="openImportDialog">导入花名册</el-button>
        <el-button type="primary" @click="openCreateDialog">新增学生账号</el-button>
      </template>
    </PageHeader>

    <div class="users__toolbar panel">
      <el-select
        v-model="filters.role"
        class="users__role"
        placeholder="全部角色"
        @change="handleFilterChange"
      >
        <el-option label="全部角色" value="" />
        <el-option label="学生" value="student" />
        <el-option label="教师" value="teacher" />
        <el-option label="班委" value="admin" />
      </el-select>
      <el-radio-group v-model="filters.status" @change="handleFilterChange">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="enabled">启用</el-radio-button>
        <el-radio-button value="disabled">停用</el-radio-button>
      </el-radio-group>
      <div class="users__search">
        <el-input
          v-model="keywordDraft"
          class="users__search-input"
          placeholder="搜索账号或姓名"
          maxlength="64"
          clearable
          @keyup.enter="applySearch"
          @clear="applySearch"
        />
        <el-button @click="applySearch">查询</el-button>
      </div>
      <el-button class="users__reset" @click="resetFilters">重置</el-button>
    </div>

    <div
      v-if="refreshFailed"
      class="users__banner users__banner--warning"
      role="status"
    >
      <span>{{ refreshFailReason }}，显示上次结果（{{ lastLoadedLabel }}）。</span>
      <el-button link type="primary" @click="fetchUsers()">重试读取</el-button>
    </div>

    <div v-if="!hasLoaded" class="users__placeholder panel" :aria-busy="!failed">
      <el-skeleton v-if="!failed" :rows="5" animated />
      <div v-else class="users__error" role="alert">
        <p class="users__error-title">用户列表读取失败</p>
        <p class="users__error-desc">请检查网络连接后重试；重新读取成功后会展示最新名单。</p>
        <el-button type="primary" @click="fetchUsers()">重试</el-button>
      </div>
    </div>

    <template v-else>
      <p v-if="loading" class="users__refreshing" role="status">正在刷新…</p>
      <div class="users__table panel">
        <el-table :data="visibleUsers" class="table-dense" row-key="id" :aria-busy="loading">
          <el-table-column prop="account" label="账号" min-width="120" />
          <el-table-column prop="name" label="姓名" min-width="100" />
          <el-table-column label="角色" width="90">
            <template #default="{ row }">
              <span class="status-tag" :class="ROLE_CLASSES[row.role] ?? 'status-tag--neutral'">
                {{ ROLE_NAMES[row.role] ?? row.role }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <span
                class="status-tag"
                :class="row.status === 'enabled' ? 'status-tag--success' : 'status-tag--neutral'"
              >
                {{ row.status === 'enabled' ? '启用' : '停用' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="210" fixed="right">
            <template #default="{ row }">
              <span v-if="row.id === userStore.user?.id" class="users__readonly">当前账号</span>
              <span v-else-if="row.role !== 'student'" class="users__readonly">只读</span>
              <template v-else>
                <el-button
                  link
                  type="primary"
                  size="small"
                  :aria-label="`更正 ${row.name}`"
                  @click="openEditDialog(row)"
                >
                  更正
                </el-button>
                <el-button
                  link
                  type="primary"
                  size="small"
                  :aria-label="`重置 ${row.name} 的密码`"
                  @click="handleResetPassword(row)"
                >
                  重置密码
                </el-button>
                <el-button
                  v-if="row.status === 'enabled'"
                  link
                  type="danger"
                  size="small"
                  :aria-label="`停用 ${row.name}`"
                  @click="handleDisable(row)"
                >
                  停用
                </el-button>
                <el-button
                  v-else
                  link
                  type="primary"
                  size="small"
                  :aria-label="`启用 ${row.name}`"
                  @click="handleEnable(row)"
                >
                  启用
                </el-button>
              </template>
            </template>
          </el-table-column>

          <template #empty>
            <EmptyState :title="emptyTitle" :description="emptyDescription">
              <template #action>
                <el-button v-if="isDefaultView" type="primary" @click="openCreateDialog">
                  新增学生账号
                </el-button>
                <el-button v-else @click="resetFilters">清除筛选</el-button>
              </template>
            </EmptyState>
          </template>
        </el-table>
      </div>
    </template>

    <el-dialog
      v-model="createVisible"
      title="新增学生账号"
      :width="dialogWidth"
      :close-on-click-modal="false"
      :close-on-press-escape="!creating"
      :show-close="!creating"
      destroy-on-close
      class="users__dialog"
    >
      <el-alert
        v-if="formError"
        type="error"
        :closable="false"
        show-icon
        :title="formError"
        class="users__form-error"
      />
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        scroll-to-error
      >
        <el-form-item label="账号" prop="account">
          <el-input v-model="form.account" maxlength="64" placeholder="请输入账号" autocomplete="off" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" maxlength="64" placeholder="请输入姓名" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            maxlength="64"
            placeholder="6–64 位"
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="角色">
          <span class="users__readonly">学生</span>
        </el-form-item>
      </el-form>
      <p class="users__form-hint">请将账号和初始密码交给该学生；初始密码不做保存，关闭后无法再次查看。</p>
      <template #footer>
        <div class="users__dialog-footer">
          <el-button :disabled="creating" @click="createVisible = false">取消</el-button>
          <el-button type="primary" :loading="creating" @click="handleCreate">创建账号</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="editVisible"
      title="更正人员信息"
      :width="dialogWidth"
      :close-on-click-modal="false"
      :close-on-press-escape="!editSaving"
      :show-close="!editSaving"
      destroy-on-close
      class="users__dialog"
    >
      <el-alert
        v-if="editError"
        type="error"
        :closable="false"
        show-icon
        :title="editError"
        class="users__form-error"
      />
      <el-form label-position="top">
        <el-form-item label="姓名">
          <el-input v-model="editForm.name" maxlength="64" placeholder="请输入姓名" />
        </el-form-item>
        <el-form-item label="学号">
          <el-input
            v-model="editForm.account"
            maxlength="64"
            placeholder="请输入学号"
            autocomplete="off"
          />
        </el-form-item>
      </el-form>
      <p class="users__form-hint">
        按稳定用户 ID 更正，历史领用与日志仍归属该人员；同名人员请以学号区分。
      </p>
      <template #footer>
        <div class="users__dialog-footer">
          <el-button :disabled="editSaving" @click="editVisible = false">取消</el-button>
          <el-button type="primary" :loading="editSaving" @click="handleEdit">保存更正</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="resetVisible"
      title="一次性临时密码"
      :width="dialogWidth"
      :close-on-click-modal="false"
      @closed="closeResetResult"
      class="users__dialog"
    >
      <template v-if="resetResult">
        <p class="users__reset-title">
          已重置 {{ resetResult.user.name }}（{{ resetResult.user.account }}）的密码。
        </p>
        <div class="users__temp-password">{{ resetResult.temporary_password }}</div>
        <p class="users__form-hint">
          临时密码仅本次显示，关闭后无法再次查看；该学生下次登录须立即修改密码，旧密码与旧会话已失效。
        </p>
      </template>
      <template #footer>
        <el-button type="primary" @click="closeResetResult">我已记录</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="importVisible"
      title="导入花名册"
      :width="importDialogWidth"
      :close-on-click-modal="false"
      :close-on-press-escape="!importSubmitting"
      :show-close="!importSubmitting"
      @closed="closeImportDialog"
      class="users__dialog"
    >
      <template v-if="!importReceipt">
        <div class="users__import-actions">
          <el-button @click="handleTemplateDownload">下载模板</el-button>
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".csv,text/csv"
            :on-change="handleRosterFile"
          >
            <el-button type="primary" :loading="importParsing">选择名单 CSV</el-button>
          </el-upload>
          <span v-if="importFileName" class="users__import-file">{{ importFileName }}</span>
        </div>
        <el-alert
          v-if="importError"
          type="error"
          :closable="false"
          show-icon
          :title="importError"
          class="users__form-error"
        />
        <div v-if="importRows.length" class="users__import-mode">
          <span>已存在学号：</span>
          <el-radio-group v-model="importOnExisting" @change="handleOnExistingChange">
            <el-radio-button value="skip">跳过</el-radio-button>
            <el-radio-button value="update">按学号更正姓名</el-radio-button>
          </el-radio-group>
        </div>
        <template v-if="importPreview">
          <p class="users__import-summary">
            共 {{ importPreview.summary.total_rows }} 行：可创建
            {{ importPreview.summary.ready_rows }}，已存在
            {{ importPreview.summary.existing_rows }}，更正
            {{ importPreview.summary.update_rows }}，错误
            {{ importPreview.summary.error_rows }}
          </p>
          <el-table
            :data="importPreview.rows"
            row-key="line_no"
            max-height="360"
            class="table-dense"
          >
            <el-table-column prop="line_no" label="行" width="60" />
            <el-table-column prop="account" label="学号" min-width="130" />
            <el-table-column prop="name" label="姓名" min-width="100" />
            <el-table-column label="预检" width="120">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.status === 'ready' ? 'success' : row.status === 'error' ? 'danger' : 'info'"
                >
                  {{ IMPORT_STATUS_NAMES[row.status] ?? row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="260">
              <template #default="{ row }">
                <span v-if="row.errors.length" class="users__import-error">
                  {{ row.errors.map((item) => `${item.field}：${item.reason}`).join('；') }}
                </span>
                <span v-else>{{ row.message || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </template>
      <template v-else>
        <p class="users__import-receipt">
          批次 #{{ importReceipt.batch_id }} 已处理：创建
          {{ importReceipt.summary.ready_rows }} 人，更正
          {{ importReceipt.summary.update_rows }} 人，跳过
          {{ importReceipt.summary.existing_rows }} 人。
        </p>
        <el-table :data="importReceipt.rows" row-key="line_no" max-height="360" class="table-dense">
          <el-table-column prop="line_no" label="行" width="60" />
          <el-table-column prop="account" label="学号" min-width="130" />
          <el-table-column prop="name" label="姓名" min-width="100" />
          <el-table-column label="结果" width="100">
            <template #default="{ row }">
              {{ IMPORT_ACTION_NAMES[row.action] ?? row.action }}
            </template>
          </el-table-column>
          <el-table-column label="临时密码" min-width="180">
            <template #default="{ row }">
              <span class="users__receipt-password">{{ row.temporary_password || '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
        <p class="users__form-hint">
          临时密码仅本次显示，关闭后无法再次查看；批次 #{{ importReceipt.batch_id }}
          的回执可再次查询，但不包含临时密码。学生首次登录须立即修改密码。
        </p>
      </template>
      <template #footer>
        <div class="users__dialog-footer">
          <el-button :disabled="importSubmitting" @click="closeImportDialog">
            {{ importReceipt ? '完成' : '取消' }}
          </el-button>
          <el-button
            v-if="!importReceipt"
            type="primary"
            :loading="importSubmitting"
            :disabled="!importPreview?.valid"
            @click="handleImport"
          >
            确认导入
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.users {
  display: flex;
  flex-direction: column;
}

.users__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

.users__role {
  width: 140px;
}

.users__search {
  display: flex;
  flex: 1 1 220px;
  align-items: center;
  gap: 8px;
}

.users__search-input {
  max-width: 280px;
}

.users__reset {
  margin-left: auto;
}

.users__banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  margin-bottom: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  font-size: 14px;
  line-height: 22px;
}

.users__banner--warning {
  border-color: var(--warning);
  background: var(--warning-soft);
  color: var(--warning);
}

.users__placeholder {
  padding: 24px;
}

.users__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
}

.users__error-title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.users__error-desc {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.users__refreshing {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.users__table {
  overflow: hidden;
}

.users__readonly {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.users__form-error {
  margin-bottom: 16px;
}

.users__form-hint {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.users__dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.users__import-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.users__import-file {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  word-break: break-all;
}

.users__import-mode {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
  font-size: 13px;
  line-height: 20px;
}

.users__import-summary {
  margin: 12px 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.users__import-error {
  color: var(--danger, #f56c6c);
}

.users__import-receipt {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.users__receipt-password {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

.users__reset-title {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
}

.users__temp-password {
  padding: 12px 16px;
  margin: 12px 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 18px;
  line-height: 26px;
  letter-spacing: 0.04em;
  background: var(--surface-muted);
  border-radius: var(--radius-panel);
  word-break: break-all;
}
</style>