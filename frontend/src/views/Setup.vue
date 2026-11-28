<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

import api from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(true)
const status = ref(null)

const isSuperAdmin = computed(() => userStore.user?.role === 'super_admin')
const mustChange = computed(() => userStore.mustChangePassword)
const hasTeacher = computed(() => Boolean(status.value?.has_enabled_teacher))

const passwordForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const passwordSubmitting = ref(false)
const passwordError = ref('')

const teacherForm = reactive({ account: '', name: '', password: '' })
const teacherSubmitting = ref(false)
const teacherError = ref('')
const createdTeachers = ref([])

const completing = ref(false)
const completeError = ref('')

async function loadStatus() {
  loading.value = true
  try {
    const { data } = await api.get('/setup/status', { skipAuthRedirect: true })
    status.value = data
    userStore.setupStatus = data
    userStore.setupRequired = Boolean(data.needs_setup)
  } catch {
    status.value = null
  } finally {
    loading.value = false
  }
}

async function submitPassword() {
  if (passwordSubmitting.value) return
  passwordError.value = ''
  if (!passwordForm.oldPassword) {
    passwordError.value = '请输入当前密码'
    return
  }
  if (passwordForm.newPassword.length < 8) {
    passwordError.value = '新密码至少 8 位'
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordSubmitting.value = true
  try {
    await userStore.changePassword({
      old_password: passwordForm.oldPassword,
      new_password: passwordForm.newPassword,
    })
    passwordForm.oldPassword = ''
    passwordForm.newPassword = ''
    passwordForm.confirmPassword = ''
    ElMessage.success('密码已更新')
  } catch (error) {
    passwordError.value = error.response?.data?.detail || '修改密码失败，请重试'
  } finally {
    passwordSubmitting.value = false
  }
}

async function submitTeacher() {
  if (teacherSubmitting.value) return
  teacherError.value = ''
  teacherForm.account = teacherForm.account.trim()
  teacherForm.name = teacherForm.name.trim()
  if (!teacherForm.account) {
    teacherError.value = '请输入教师账号'
    return
  }
  if (!teacherForm.name) {
    teacherError.value = '请输入教师姓名'
    return
  }
  teacherSubmitting.value = true
  try {
    const payload = { account: teacherForm.account, name: teacherForm.name }
    if (teacherForm.password) payload.password = teacherForm.password
    const { data } = await api.post('/setup/teachers', payload)
    createdTeachers.value.push(data)
    ElMessage.success(`已创建教师 ${data.name}`)
    teacherForm.account = ''
    teacherForm.name = ''
    teacherForm.password = ''
    await loadStatus()
  } catch (error) {
    teacherError.value = error.response?.data?.detail || '创建教师失败，请重试'
  } finally {
    teacherSubmitting.value = false
  }
}

async function completeSetup() {
  if (completing.value) return
  completeError.value = ''
  completing.value = true
  try {
    await api.post('/setup/complete')
    userStore.setupRequired = false
    userStore.setupStatus = null
    ElMessage.success('初始化完成')
    await router.replace('/dashboard')
  } catch (error) {
    completeError.value = error.response?.data?.detail || '完成初始化失败，请重试'
  } finally {
    completing.value = false
  }
}

async function logout() {
  await userStore.logout()
  router.replace('/login')
}

onMounted(loadStatus)
</script>

<template>
  <div class="setup">
    <main class="panel setup__card">
      <header class="setup__brand">
        <span class="setup__mark">
          <el-icon :size="22"><Setting /></el-icon>
        </span>
        <h1 class="setup__title">系统初始化</h1>
      </header>

      <p v-if="loading" class="setup__hint" role="status">正在加载…</p>

      <template v-else-if="!isSuperAdmin">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="系统尚未完成初始化"
          description="请联系超级管理员完成初始化后再使用。"
        />
        <el-button class="setup__logout" @click="logout">退出登录</el-button>
      </template>

      <template v-else>
        <section class="setup__section">
          <h2 class="setup__step">1. 修改超级管理员密码</h2>
          <template v-if="mustChange">
            <el-form label-position="top" @submit.prevent>
              <el-form-item label="当前密码">
                <el-input
                  v-model="passwordForm.oldPassword"
                  type="password"
                  autocomplete="current-password"
                />
              </el-form-item>
              <el-form-item label="新密码">
                <el-input
                  v-model="passwordForm.newPassword"
                  type="password"
                  autocomplete="new-password"
                />
              </el-form-item>
              <el-form-item label="确认新密码">
                <el-input
                  v-model="passwordForm.confirmPassword"
                  type="password"
                  autocomplete="new-password"
                />
              </el-form-item>
            </el-form>
            <p v-if="passwordError" class="setup__error" role="alert">{{ passwordError }}</p>
            <el-button type="primary" :loading="passwordSubmitting" @click="submitPassword">
              保存密码
            </el-button>
          </template>
          <p v-else class="setup__done">密码已更新</p>
        </section>

        <section class="setup__section" :class="{ 'is-locked': mustChange }">
          <h2 class="setup__step">2. 创建教师账号</h2>
          <el-form label-position="top" @submit.prevent>
            <el-form-item label="教师账号">
              <el-input v-model="teacherForm.account" :disabled="mustChange" />
            </el-form-item>
            <el-form-item label="教师姓名">
              <el-input v-model="teacherForm.name" :disabled="mustChange" />
            </el-form-item>
            <el-form-item label="临时密码（留空自动生成）">
              <el-input v-model="teacherForm.password" :disabled="mustChange" />
            </el-form-item>
          </el-form>
          <p v-if="teacherError" class="setup__error" role="alert">{{ teacherError }}</p>
          <el-button :disabled="mustChange" :loading="teacherSubmitting" @click="submitTeacher">
            添加教师
          </el-button>

          <ul v-if="createdTeachers.length" class="setup__teachers">
            <li v-for="item in createdTeachers" :key="item.user_id">
              {{ item.name }}（{{ item.account }}）临时密码：<code>{{ item.temporary_password }}</code>
            </li>
          </ul>
          <p v-else-if="hasTeacher" class="setup__done">已存在启用教师</p>
        </section>

        <section class="setup__section">
          <h2 class="setup__step">3. 完成初始化</h2>
          <p v-if="completeError" class="setup__error" role="alert">{{ completeError }}</p>
          <el-button
            type="primary"
            :disabled="mustChange || !hasTeacher"
            :loading="completing"
            @click="completeSetup"
          >
            完成初始化
          </el-button>
          <p v-if="!hasTeacher" class="setup__hint">至少创建一名启用教师后可完成</p>
        </section>

        <button type="button" class="setup__logout" @click="logout">退出登录</button>
      </template>
    </main>
  </div>
</template>

<style scoped>
.setup {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 100vh;
  padding: 32px 24px 48px;
  background: var(--page);
}

.setup__card {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 520px;
  max-width: 100%;
  padding: 28px;
  border-color: var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow-md);
}

.setup__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.setup__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--brand-mark-bg);
  color: #ffffff;
}

.setup__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
}

.setup__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

.setup__section.is-locked {
  opacity: 0.7;
}

.setup__step {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.setup__hint {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.setup__done {
  margin: 0;
  font-size: 13px;
  color: var(--success, #2f9e44);
}

.setup__error {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--danger);
}

.setup__teachers {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 22px;
  color: var(--text-secondary);
}

.setup__teachers code {
  padding: 1px 6px;
  border-radius: var(--radius-xs);
  background: var(--surface-muted);
  color: var(--text);
  user-select: all;
}

.setup__logout {
  align-self: flex-start;
  min-height: 36px;
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}

@media (max-width: 767px) {
  .setup {
    padding: 20px 12px 40px;
  }

  .setup__card {
    padding: 22px 18px;
  }
}
</style>