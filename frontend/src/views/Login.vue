<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

import api from '../api'
import { useUserStore } from '../stores/user'
import HelpDrawer from '../components/HelpDrawer.vue'
import { announce } from '../utils/announcer'
import { keepFocusedFieldVisible } from '../utils/mobileFocus'
import { copyRequestId, recordRequestId, requestIdOf } from '../utils/requestTrace'

const router = useRouter()
const userStore = useUserStore()
const helpOpen = ref(false)
const lastRequestId = ref('')

const form = reactive({
  account: '',
  password: '',
})

const formRef = ref()
const accountRef = ref()
const passwordRef = ref()

const submitting = ref(false)
const checking = ref(userStore.status !== 'ready')
const connectionError = ref(false)
const passwordVisible = ref(false)
const formError = ref('')
const formErrorHint = ref('')
const bootstrapState = ref('')
const bootstrapMessage = ref('')

const bootstrapNotice = computed(
  () =>
    bootstrapState.value === 'needs_initialization' ||
    bootstrapState.value === 'needs_recovery',
)

const bootstrapTitle = computed(() =>
  bootstrapState.value === 'needs_recovery'
    ? '系统需恢复：暂无可用管理员'
    : '系统需初始化：尚无任何账号',
)

const rules = {
  account: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const passwordType = computed(() => (passwordVisible.value ? 'text' : 'password'))

const toggleLabel = computed(() => (passwordVisible.value ? '隐藏密码' : '显示密码'))

const locked = computed(() => submitting.value || checking.value)

function isValidInternalPath(path) {
  return (
    typeof path === 'string' &&
    path.startsWith('/') &&
    !path.startsWith('//') &&
    path !== '/login'
  )
}

function resolveRedirect() {
  const raw = router.currentRoute.value.query.redirect
  const candidate = Array.isArray(raw) ? raw[0] : raw
  if (isValidInternalPath(candidate)) {
    return candidate
  }
  return userStore.takeRedirect()
}

async function checkSession() {
  checking.value = true
  connectionError.value = false
  const user = await userStore.init()
  checking.value = false
  if (userStore.status === 'error') {
    connectionError.value = true
    return
  }
  if (user) {
    await router.replace(resolveRedirect())
  }
}

async function loadBootstrapStatus() {
  try {
    const { data } = await api.get('/bootstrap/status', { skipAuthRedirect: true })
    bootstrapState.value = data.state ?? ''
    bootstrapMessage.value = data.message ?? ''
  } catch {
    bootstrapState.value = ''
    bootstrapMessage.value = ''
  }
}

function focusFirstInvalid() {
  if (!form.account) {
    accountRef.value?.focus()
    return
  }
  passwordRef.value?.focus()
}

function clearFormError() {
  formError.value = ''
  formErrorHint.value = ''
  lastRequestId.value = ''
}

async function copyLoginRequest() {
  if (!lastRequestId.value) return
  const ok = await copyRequestId(lastRequestId.value)
  if (ok) ElMessage.success('请求编号已复制')
  else ElMessage.error('复制失败，请手动选择请求编号')
}

async function handleSubmit() {
  if (locked.value) return
  clearFormError()
  form.account = form.account.trim()
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) {
    focusFirstInvalid()
    announce('请填写账号和密码后再登录', { assertive: true })
    return
  }
  submitting.value = true
  try {
    const data = await userStore.login({
      account: form.account,
      password: form.password,
    })
    announce(`登录成功，欢迎 ${data.name}`)
    ElMessage.success(`欢迎，${data.name}`)
    await router.replace(resolveRedirect())
  } catch (error) {
    const status = error.response?.status
    lastRequestId.value = recordRequestId(requestIdOf(error))
    if (status === 401) {
      formError.value = '账号或密码错误'
      formErrorHint.value = '如账号已停用，请联系班委，或打开页面下方“使用帮助”核对账号状态'
      await nextTick()
      passwordRef.value?.focus()
      passwordRef.value?.select()
      announce(`${formError.value}。${formErrorHint.value}`, { assertive: true })
    } else {
      formError.value = '暂时无法登录，请检查连接后重试'
      announce(
        `${formError.value}${
          lastRequestId.value ? `。请求编号 ${lastRequestId.value}` : ''
        }`,
        { assertive: true },
      )
    }
  } finally {
    submitting.value = false
  }
}


onMounted(() => {
  loadBootstrapStatus()
  if (userStore.status === 'ready') {
    checking.value = false
    if (userStore.isLoggedIn) {
      router.replace(resolveRedirect())
    }
    return
  }
  checkSession()
})
</script>

<template>
  <div class="login" @focusin="keepFocusedFieldVisible">
    <div class="login__ambient" aria-hidden="true" />
    <main class="panel login__card" :aria-busy="checking">
      <header class="login__brand">
        <span class="login__mark">
          <el-icon :size="24"><Box /></el-icon>
        </span>
        <h1 class="login__title">班级实训耗材智能管理系统</h1>

      </header>

      <p v-if="checking" class="login__status" role="status">正在连接系统…</p>

      <div v-else-if="connectionError" class="login__notice" role="alert">
        <span>暂时无法连接系统</span>
        <el-button link type="primary" @click="checkSession">重试</el-button>
      </div>

      <el-alert
        v-if="bootstrapNotice"
        :title="bootstrapTitle"
        :description="bootstrapMessage"
        type="warning"
        :closable="false"
        show-icon
        class="login__bootstrap"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        class="login__form"

      >
        <el-alert
          v-if="formError"
          :title="formError"
          :description="formErrorHint"
          type="error"
          :closable="false"
          show-icon
          class="login__error"
          role="alert"
        >
          <template #default>
            <p v-if="lastRequestId" class="login__error-trace">
              请求编号
              <code class="login__request-id" tabindex="0">{{ lastRequestId }}</code>
              <el-button link type="primary" @click="copyLoginRequest">复制请求编号</el-button>
            </p>
          </template>
        </el-alert>

        <el-form-item label="账号" prop="account">
          <el-input
            ref="accountRef"
            v-model="form.account"
            placeholder="请输入账号"
            autocomplete="username"
            :disabled="submitting"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            ref="passwordRef"
            v-model="form.password"
            :type="passwordType"
            placeholder="请输入密码"
            autocomplete="current-password"
            :disabled="submitting"
            class="login__password"
            @keyup.enter="handleSubmit"
          >
            <template #suffix>
              <button
                type="button"
                class="login__password-toggle"
                :aria-label="toggleLabel"
                :aria-pressed="passwordVisible"
                @click="passwordVisible = !passwordVisible"

              >
                {{ toggleLabel }}
              </button>
            </template>
          </el-input>
        </el-form-item>

        <el-button
          type="primary"
          class="login__submit"
          :loading="submitting"
          :disabled="checking"
          @click="handleSubmit"
        >
          {{ submitting ? '登录中…' : '登录' }}
        </el-button>
      </el-form>

      <p class="login__footnote">账号由部署方或班委分配</p>
      <button type="button" class="login__help" @click="helpOpen = true">
        使用帮助
      </button>
    </main>

    <HelpDrawer v-model="helpOpen" />
  </div>
</template>

<style scoped>
.login {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 32px 24px;
  overflow: hidden;
  background: var(--page);
}

.login__ambient {
  position: absolute;
  inset: 0;
  background: radial-gradient(720px 400px at 50% -6%, var(--brand-glow), transparent 68%);
  pointer-events: none;
}

.login__card {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 22px;
  width: 400px;
  max-width: 100%;
  padding: 32px;
  border-color: var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow-md);
}

.login__brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.login__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--brand-mark-bg);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 14%);
  color: #FFFFFF;
}

.login__title {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--text);
  text-align: center;
  text-wrap: balance;
}

.login__status {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  text-align: center;
}

.login__notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-control);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 13px;
  line-height: 20px;
}

.login__form {
  display: flex;
  flex-direction: column;
}

.login__bootstrap {
  margin-bottom: 0;
}

.login__form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.login__form :deep(.el-form-item:last-of-type) {
  margin-bottom: 20px;
}

.login__form :deep(.el-form-item__label) {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.login__form :deep(.el-input__wrapper) {
  min-height: 40px;
}

.login__error {
  margin-bottom: 12px;
}

.login__error-trace {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
}

.login__request-id {
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--surface-muted);
  color: var(--text);
  font-size: 12px;
  line-height: 18px;
  word-break: break-all;
  user-select: all;
}

.login__password :deep(.el-input__inner) {
  padding-right: 76px;
}

.login__password :deep(.el-input__suffix) {
  right: 8px;
}

.login__password-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 0 4px;
  border: none;
  background: transparent;
  color: var(--primary);
  font-family: inherit;
  font-size: 12px;
  line-height: 18px;
  cursor: pointer;
}

.login__password-toggle:hover {
  color: var(--primary-hover);
}

.login__submit {
  width: 100%;
  height: 44px;
  font-size: 15px;
}

.login__footnote {
  margin: 0;
  padding-top: 18px;
  border-top: 1px solid var(--line-soft);
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  text-align: center;
}

.login__help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  line-height: 20px;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    border-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.login__help:hover {
  border-color: var(--primary-200);
  background: var(--primary-50);
  color: var(--primary);
}

@media (max-width: 767px) {
  .login {
    align-items: flex-start;
    padding: 24px 12px 48px;
  }

  .login__card {
    gap: 18px;
    padding: 24px 20px 20px;
  }

  .login__password-toggle {
    min-height: 44px;
  }

  .login__help {
    width: 100%;
    min-height: 44px;
  }

  .login__submit {
    position: sticky;
    bottom: calc(var(--keyboard-inset, 0px) + 4px);
    z-index: 1;
  }
}
</style>