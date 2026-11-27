<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const ROLE_NAMES = { teacher: '教师', admin: '班委', student: '学生' }
const ROLE_TAG_TYPES = { teacher: 'danger', admin: 'warning', student: 'success' }
const STATUS_NAMES = { enabled: '启用', disabled: '停用' }

const users = ref([])
const loading = ref(false)

const filters = reactive({
  role: '',
  status: '',
})

const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref()
const form = reactive({
  account: '',
  name: '',
  password: '',
})

const rules = {
  account: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于 6 位', trigger: 'blur' },
  ],
}

function formatTime(value) {
  if (!value) return ''
  return value.slice(0, 16).replace('T', ' ')
}

async function fetchUsers() {
  loading.value = true
  try {
    const params = {}
    if (filters.role) params.role = filters.role
    if (filters.status) params.status = filters.status
    const { data } = await api.get('/users', { params })
    users.value = data
  } catch (error) {
    ElMessage.error('加载用户列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  dialogVisible.value = true
}

function resetForm() {
  formRef.value?.resetFields()
}

async function handleCreate() {
  await formRef.value.validate()
  submitting.value = true
  try {
    await api.post('/users', { ...form, role: 'student' })
    ElMessage.success(`学生账号 ${form.account} 创建成功`)
    dialogVisible.value = false
    fetchUsers()
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(detail || '创建失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

async function handleDisable(row) {
  try {
    await ElMessageBox.confirm(
      '停用后该账号无法登录，历史记录保留。',
      `停用账号 ${row.account}`,
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await changeStatus(row, 'disabled')
}

async function handleEnable(row) {
  await changeStatus(row, 'enabled')
}

async function changeStatus(row, status) {
  try {
    await api.patch(`/users/${row.id}/status`, { status })
    row.status = status
    ElMessage.success(status === 'enabled' ? `账号 ${row.account} 已启用` : `账号 ${row.account} 已停用`)
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(detail || '操作失败，请稍后重试')
  }
}

onMounted(fetchUsers)
</script>

<template>
  <el-card>
    <div class="users__toolbar">
      <div class="users__filters">
        <el-select v-model="filters.role" placeholder="角色：全部" clearable style="width: 140px" @change="fetchUsers">
          <el-option label="学生" value="student" />
          <el-option label="教师" value="teacher" />
          <el-option label="班委" value="admin" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态：全部" clearable style="width: 140px" @change="fetchUsers">
          <el-option label="启用" value="enabled" />
          <el-option label="停用" value="disabled" />
        </el-select>
      </div>
      <el-button type="primary" @click="openCreateDialog">新增学生账号</el-button>
    </div>

    <el-table v-loading="loading" :data="users" class="users__table">
      <el-table-column prop="account" label="账号" min-width="120" />
      <el-table-column prop="name" label="姓名" min-width="100" />
      <el-table-column label="角色" width="90">
        <template #default="{ row }">
          <el-tag :type="ROLE_TAG_TYPES[row.role] ?? 'info'" size="small">
            {{ ROLE_NAMES[row.role] ?? row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'enabled' ? 'success' : 'info'" size="small">
            {{ STATUS_NAMES[row.status] ?? row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="150">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <template v-if="row.id !== userStore.user?.id">
            <el-button
              v-if="row.status === 'enabled'"
              type="danger"
              link
              size="small"
              @click="handleDisable(row)"
            >
              停用
            </el-button>
            <el-button v-else type="primary" link size="small" @click="handleEnable(row)">
              启用
            </el-button>
          </template>
          <span v-else class="users__self">当前账号</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      title="新增学生账号"
      width="440px"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="账号" prop="account">
          <el-input v-model="form.account" placeholder="请输入账号" maxlength="64" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="请输入姓名" maxlength="64" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            show-password
            maxlength="64"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.users__toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.users__filters {
  display: flex;
  gap: 12px;
}

.users__self {
  font-size: 12px;
  color: #909399;
}
</style>