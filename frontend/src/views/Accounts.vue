<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const userStore = useUserStore()

const ROLE_LABELS = {
  super_admin: '超级管理员',
  teacher: '教师',
  admin: '班委',
  student: '学生',
}
const ROLE_CLASSES = {
  super_admin: 'role-tag--admin',
  teacher: 'role-tag--teacher',
  admin: 'role-tag--admin',
  student: 'role-tag--student',
}

const actorRole = computed(() => userStore.activeRole ?? userStore.user?.role)
const isSuperAdmin = computed(() => actorRole.value === 'super_admin')
const assignableRoles = computed(() =>
  isSuperAdmin.value
    ? ['super_admin', 'teacher', 'admin', 'student']
    : ['admin', 'student'],
)

const accounts = ref([])
const classes = ref([])
const loading = ref(false)
const loaded = ref(false)
const filters = reactive({ role: '', status: '', keyword: '' })

const createVisible = ref(false)
const creating = ref(false)
const createError = ref('')
const createForm = reactive({ account: '', name: '', password: '', role: 'student', class_id: null })

const editVisible = ref(false)
const editing = ref(false)
const editError = ref('')
const editForm = reactive({ id: null, account: '', name: '', class_id: null })

const rolesVisible = ref(false)
const rolesSaving = ref(false)
const rolesError = ref('')
const rolesForm = reactive({ id: null, account: '', name: '', roles: [] })

const resetVisible = ref(false)
const resetResult = ref(null)

async function fetchClasses() {
  try {
    const { data } = await api.get('/classes')
    classes.value = Array.isArray(data) ? data : []
  } catch {
    classes.value = []
  }
}

async function fetchAccounts() {
  loading.value = true
  try {
    const params = {}
    if (filters.role) params.role = filters.role
    if (filters.status) params.status = filters.status
    if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
    const { data } = await api.get('/accounts', { params })
    accounts.value = Array.isArray(data) ? data : []
    loaded.value = true
  } catch {
    ElMessage.error('读取账号列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.role = ''
  filters.status = ''
  filters.keyword = ''
  fetchAccounts()
}

function openCreate() {
  Object.assign(createForm, {
    account: '',
    name: '',
    password: '',
    role: isSuperAdmin.value ? 'student' : 'student',
    class_id: userStore.activeClass?.id ?? classes.value[0]?.id ?? null,
  })
  createError.value = ''
  createVisible.value = true
}

async function submitCreate() {
  if (creating.value) return
  createError.value = ''
  if (!createForm.account.trim() || !createForm.name.trim()) {
    createError.value = '请填写账号与姓名'
    return
  }
  if (createForm.password.length < 6) {
    createError.value = '初始密码至少 6 位'
    return
  }
  creating.value = true
  try {
    await api.post('/accounts', {
      account: createForm.account.trim(),
      name: createForm.name.trim(),
      password: createForm.password,
      role: createForm.role,
      class_id: isSuperAdmin.value ? createForm.class_id : undefined,
    })
    ElMessage.success('账号已创建，初始密码仅本次设置')
    createVisible.value = false
    await fetchAccounts()
  } catch (error) {
    createError.value = error.response?.data?.detail || '创建失败，请稍后重试'
  } finally {
    creating.value = false
  }
}

function openEdit(row) {
  Object.assign(editForm, {
    id: row.id,
    account: row.account,
    name: row.name,
    class_id: row.class_id,
  })
  editError.value = ''
  editVisible.value = true
}

async function submitEdit() {
  if (editing.value) return
  editError.value = ''
  editing.value = true
  try {
    await api.patch(`/accounts/${editForm.id}`, {
      account: editForm.account.trim(),
      name: editForm.name.trim(),
      class_id: isSuperAdmin.value ? editForm.class_id : undefined,
    })
    ElMessage.success('账号已更新')
    editVisible.value = false
    await fetchAccounts()
  } catch (error) {
    editError.value = error.response?.data?.detail || '更新失败，请稍后重试'
  } finally {
    editing.value = false
  }
}

function openRoles(row) {
  rolesForm.id = row.id
  rolesForm.account = row.account
  rolesForm.name = row.name
  rolesForm.roles = assignableRoles.value.filter((role) => row.roles.includes(role))
  rolesError.value = ''
  rolesVisible.value = true
}

async function submitRoles() {
  if (rolesSaving.value) return
  rolesError.value = ''
  if (!rolesForm.roles.length) {
    rolesError.value = '至少保留一个角色'
    return
  }
  rolesSaving.value = true
  try {
    await api.put(`/accounts/${rolesForm.id}/roles`, { roles: rolesForm.roles })
    ElMessage.success('角色已更新，相关会话需重新登录后生效')
    rolesVisible.value = false
    await fetchAccounts()
  } catch (error) {
    rolesError.value = error.response?.data?.detail || '更新失败，请稍后重试'
  } finally {
    rolesSaving.value = false
  }
}

async function toggleStatus(row, next) {
  try {
    await ElMessageBox.confirm(
      `${row.name}（${row.account}）将被${next === 'enabled' ? '启用' : '停用'}。`,
      next === 'enabled' ? '启用账号' : '停用账号',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await api.patch(`/accounts/${row.id}/status`, { status: next })
    ElMessage.success('账号状态已更新')
    await fetchAccounts()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败，请稍后重试')
  }
}

async function resetPassword(row) {
  try {
    await ElMessageBox.confirm(
      `重置后 ${row.name}（${row.account}）的旧会话立即失效，需使用一次性临时密码登录并改密。`,
      '重置密码',
      { type: 'warning', confirmButtonText: '确认重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const { data } = await api.post(`/accounts/${row.id}/reset-password`)
    resetResult.value = data
    resetVisible.value = true
    await fetchAccounts()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '重置失败，请稍后重试')
  }
}

function closeReset() {
  resetVisible.value = false
  resetResult.value = null
}

onMounted(async () => {
  await fetchClasses()
  await fetchAccounts()
})
</script>

<template>
  <div class="accounts">
    <PageHeader
      title="账号与角色"
      description="创建、编辑、停用账号，授予或撤销角色，分配主班。"
      :breadcrumb="['设置', '账号与角色']"
    >
      <template #actions>
        <el-button type="primary" @click="openCreate">新增账号</el-button>
      </template>
    </PageHeader>

    <div class="accounts__toolbar panel">
      <el-select v-model="filters.role" placeholder="全部角色" class="accounts__select" @change="fetchAccounts">
        <el-option label="全部角色" value="" />
        <el-option v-for="role in assignableRoles" :key="role" :label="ROLE_LABELS[role]" :value="role" />
      </el-select>
      <el-radio-group v-model="filters.status" @change="fetchAccounts">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="enabled">启用</el-radio-button>
        <el-radio-button value="disabled">停用</el-radio-button>
      </el-radio-group>
      <el-input
        v-model="filters.keyword"
        placeholder="搜索账号或姓名"
        clearable
        class="accounts__search"
        @keyup.enter="fetchAccounts"
        @clear="fetchAccounts"
      />
      <el-button @click="fetchAccounts">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </div>

    <div class="accounts__table panel">
      <el-table v-loading="loading" :data="accounts" row-key="id" class="table-dense">
        <el-table-column prop="account" label="账号" min-width="120" />
        <el-table-column prop="name" label="姓名" min-width="100" />
        <el-table-column label="角色" min-width="120">
          <template #default="{ row }">
            <span
              class="status-tag"
              :class="ROLE_CLASSES[row.role] ?? 'status-tag--neutral'"
            >
              {{ ROLE_LABELS[row.role] ?? row.role }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="class_name" label="主班" min-width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span
              class="status-tag"
              :class="row.status === 'enabled' ? 'status-tag--success' : 'status-tag--neutral'"
            >
              {{ row.status === 'enabled' ? '启用' : '停用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="首登改密" width="100">
          <template #default="{ row }">
            <span v-if="row.must_change_password" class="status-tag status-tag--warning">待改密</span>
            <span v-else class="accounts__muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <span v-if="row.id === userStore.user?.id" class="accounts__muted">当前账号</span>
            <template v-else>
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" size="small" @click="openRoles(row)">角色</el-button>
              <el-dropdown trigger="click">
                <el-button link type="primary" size="small">更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="resetPassword(row)">重置密码</el-dropdown-item>
                    <el-dropdown-item
                      v-if="row.status === 'enabled'"
                      divided
                      @click="toggleStatus(row, 'disabled')"
                    >
                      停用
                    </el-dropdown-item>
                    <el-dropdown-item v-else divided @click="toggleStatus(row, 'enabled')">
                      启用
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState title="没有符合条件的账号" description="调整筛选条件或新增账号。" />
        </template>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="新增账号" width="480px" :close-on-click-modal="false">
      <el-alert v-if="createError" type="error" :closable="false" show-icon :title="createError" class="accounts__alert" />
      <el-form label-position="top">
        <el-form-item label="账号">
          <el-input v-model="createForm.account" maxlength="64" autocomplete="off" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="createForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="createForm.password" type="password" show-password maxlength="64" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" class="accounts__full">
            <el-option v-for="role in assignableRoles" :key="role" :label="ROLE_LABELS[role]" :value="role" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="isSuperAdmin" label="主班">
          <el-select v-model="createForm.class_id" class="accounts__full">
            <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="主班">
          <span class="accounts__muted">{{ userStore.activeClass?.name ?? '当前班级' }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="creating" @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑账号" width="480px" :close-on-click-modal="false">
      <el-alert v-if="editError" type="error" :closable="false" show-icon :title="editError" class="accounts__alert" />
      <el-form label-position="top">
        <el-form-item label="账号">
          <el-input v-model="editForm.account" maxlength="64" autocomplete="off" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="editForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="主班">
          <el-select v-model="editForm.class_id" class="accounts__full" :disabled="!isSuperAdmin">
            <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="editing" @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rolesVisible" title="角色授予" width="420px" :close-on-click-modal="false">
      <el-alert v-if="rolesError" type="error" :closable="false" show-icon :title="rolesError" class="accounts__alert" />
      <p class="accounts__muted">{{ rolesForm.name }}（{{ rolesForm.account }}）</p>
      <el-checkbox-group v-model="rolesForm.roles">
        <el-checkbox v-for="role in assignableRoles" :key="role" :value="role" :label="role">
          {{ ROLE_LABELS[role] }}
        </el-checkbox>
      </el-checkbox-group>
      <p class="accounts__hint">撤销当前生效角色后，该账号需重新登录以同步权限。</p>
      <template #footer>
        <el-button :disabled="rolesSaving" @click="rolesVisible = false">取消</el-button>
        <el-button type="primary" :loading="rolesSaving" @click="submitRoles">保存角色</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetVisible" title="一次性临时密码" width="420px" @closed="closeReset">
      <template v-if="resetResult">
        <p class="accounts__muted">{{ resetResult.user.name }}（{{ resetResult.user.account }}）</p>
        <div class="accounts__temp">{{ resetResult.temporary_password }}</div>
        <p class="accounts__hint">临时密码仅本次显示，首次登录后必须改密。</p>
      </template>
      <template #footer>
        <el-button type="primary" @click="closeReset">我已记录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.accounts {
  display: flex;
  flex-direction: column;
}

.accounts__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

.accounts__select {
  width: 150px;
}

.accounts__search {
  flex: 1 1 220px;
  max-width: 280px;
}

.accounts__table {
  overflow: hidden;
}

.accounts__muted {
  color: var(--text-muted);
  font-size: 13px;
}

.accounts__alert {
  margin-bottom: 12px;
}

.accounts__full {
  width: 100%;
}

.accounts__hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.accounts__temp {
  padding: 12px 16px;
  margin: 12px 0;
  font-family: var(--font-mono);
  font-size: 18px;
  letter-spacing: 0.04em;
  background: var(--surface-muted);
  border-radius: var(--radius-control);
  word-break: break-all;
}
</style>