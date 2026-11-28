<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'

const userStore = useUserStore()

const actorRole = computed(() => userStore.activeRole ?? userStore.user?.role)
const isSuperAdmin = computed(() => actorRole.value === 'super_admin')
const visibleRoles = computed(() =>
  isSuperAdmin.value ? ['teacher', 'admin', 'student'] : ['admin', 'student'],
)

const loading = ref(false)
const saving = ref(false)
const matrix = ref(null)
const activeRole = ref(isSuperAdmin.value ? 'teacher' : 'admin')

const currentData = computed(() => {
  const roles = matrix.value?.roles ?? []
  return roles.find((item) => item.role === activeRole.value) ?? null
})

const entries = computed(() => currentData.value?.entries ?? [])

function roleLabel(role) {
  const found = (matrix.value?.roles ?? []).find((item) => item.role === role)
  return found?.label ?? role
}

async function fetchMatrix() {
  loading.value = true
  try {
    const { data } = await api.get('/permissions/matrix')
    matrix.value = data
    if (!visibleRoles.value.includes(activeRole.value)) {
      activeRole.value = visibleRoles.value[0]
    }
  } catch {
    ElMessage.error('读取权限矩阵失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function updateRoleField(entry, field, value) {
  if (saving.value) return
  const payload = {
    role: activeRole.value,
    permission_key: entry.key,
    allowed: entry.allowed,
    default_enabled: entry.default_enabled,
    toggleable_by_teacher: entry.toggleable_by_teacher,
    locked: entry.locked,
    [field]: value,
  }
  saving.value = true
  try {
    await api.put('/permissions/roles', payload)
    ElMessage.success('角色权限已更新')
    await fetchMatrix()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '更新失败，请稍后重试')
    await fetchMatrix()
  } finally {
    saving.value = false
  }
}

async function updateOverride(entry, value) {
  if (saving.value) return
  saving.value = true
  try {
    await api.put('/permissions/overrides', {
      role: activeRole.value,
      updates: { [entry.key]: value },
    })
    ElMessage.success('班级权限已更新')
    await fetchMatrix()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '更新失败，请稍后重试')
    await fetchMatrix()
  } finally {
    saving.value = false
  }
}

function overrideDisabled(entry) {
  return saving.value || entry.locked || !entry.toggleable_by_teacher
}

onMounted(fetchMatrix)
</script>

<template>
  <div class="perm">
    <PageHeader
      title="权限与页面"
      description="按角色查看与调整页面/动作权限；系统锁定项不可解除。"
      :breadcrumb="['设置', '权限与页面']"
    />

    <div class="perm__toolbar panel">
      <span class="perm__label">角色</span>
      <el-radio-group v-model="activeRole" size="small">
        <el-radio-button v-for="role in visibleRoles" :key="role" :value="role">
          {{ roleLabel(role) }}
        </el-radio-button>
      </el-radio-group>
      <span class="perm__hint">
        {{ isSuperAdmin ? '超管可编辑角色上限、默认开关、可调整项与锁定' : '教师仅可开关本班学生/班委的允许项' }}
      </span>
    </div>

    <div class="perm__table panel">
      <el-table v-loading="loading" :data="entries" row-key="key" class="table-dense">
        <el-table-column prop="label" label="页面 / 动作" min-width="150" />
        <el-table-column prop="category_label" label="分类" width="100" />
        <el-table-column v-if="isSuperAdmin" label="角色上限" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.allowed"
              :disabled="saving"
              @update:model-value="(value) => updateRoleField(row, 'allowed', value)"
            />
          </template>
        </el-table-column>
        <el-table-column v-if="isSuperAdmin" label="默认开启" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.default_enabled"
              :disabled="saving || !row.allowed"
              @update:model-value="(value) => updateRoleField(row, 'default_enabled', value)"
            />
          </template>
        </el-table-column>
        <el-table-column v-if="isSuperAdmin" label="可调" width="90">
          <template #default="{ row }">
            <el-switch
              :model-value="row.toggleable_by_teacher"
              :disabled="saving || !row.allowed || row.scope !== 'class'"
              @update:model-value="(value) => updateRoleField(row, 'toggleable_by_teacher', value)"
            />
          </template>
        </el-table-column>
        <el-table-column v-if="isSuperAdmin" label="锁定" width="90">
          <template #default="{ row }">
            <el-switch
              :model-value="row.locked"
              :disabled="saving || row.hard_locked"
              @update:model-value="(value) => updateRoleField(row, 'locked', value)"
            />
          </template>
        </el-table-column>
        <el-table-column v-else label="本班启用" width="110">
          <template #default="{ row }">
            <el-switch
              :model-value="row.effective_enabled"
              :disabled="overrideDisabled(row)"
              @update:model-value="(value) => updateOverride(row, value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span v-if="row.locked" class="status-tag status-tag--neutral">锁定</span>
            <span v-else-if="!row.allowed" class="status-tag status-tag--out">角色不可用</span>
            <span v-else-if="row.overridden" class="status-tag status-tag--success">已覆盖</span>
            <span v-else class="status-tag status-tag--neutral">默认</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.perm {
  display: flex;
  flex-direction: column;
}

.perm__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

.perm__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.perm__hint {
  font-size: 12px;
  color: var(--text-muted);
}

.perm__table {
  overflow: hidden;
}
</style>