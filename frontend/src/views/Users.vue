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

const dialogWidth = computed(() =>
  viewportWidth.value < 552 ? `${viewportWidth.value - 32}px` : '520px',
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
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="{ row }">
              <span v-if="row.id === userStore.user?.id" class="users__readonly">当前账号</span>
              <span v-else-if="row.role !== 'student'" class="users__readonly">只读</span>
              <el-button
                v-else-if="row.status === 'enabled'"
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
</style>