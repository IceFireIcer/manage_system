<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  Bell,
  Box,
  Expand,
  Fold,
  Odometer,
  QuestionFilled,
  Reading,
  School,
  Setting,
  Tickets,
  TrendCharts,
} from '@element-plus/icons-vue'
import { useUserStore } from '../stores/user'
import { useAlertsStore } from '../stores/alerts'
import HelpDrawer from '../components/HelpDrawer.vue'

const GROUP_META = {
  inventory: { label: '库存总览', icon: Box, order: 1 },
  requisition: { label: '领用管理', icon: Tickets, order: 2 },
  teaching: { label: '教学计划', icon: Reading, order: 2.5 },
  analysis: { label: '库存分析', icon: TrendCharts, order: 3 },
  daily: { label: '日常管理', icon: Setting, order: 4 },
}

const ROLE_TAG_CLASS = {
  super_admin: 'role-tag--admin',
  teacher: 'role-tag--teacher',
  class_committee: 'role-tag--admin',
  admin: 'role-tag--admin',
  student: 'role-tag--student',
  super_admin: 'role-tag--admin',
}

const ROLE_LABEL = {
  super_admin: '超级管理员',
  teacher: '教师',
  class_committee: '班委',
  admin: '班委',
  student: '学生',
  super_admin: '超级管理员',
}

const AVATAR_ROLE = {
  super_admin: 'admin',
}

const userInitial = computed(() => {
  const name = userStore.user?.name?.trim()
  return name ? name.slice(0, 1) : '·'
})

const effectiveRole = computed(() => userStore.activeRole ?? userStore.user?.role)

const avatarClass = computed(() => {
  const role = effectiveRole.value
  if (!role) return ''
  return `layout__avatar--${AVATAR_ROLE[role] ?? role}`
})

const accountRole = computed(
  () => ROLE_LABEL[effectiveRole.value] ?? userStore.roleName ?? '',
)

const switchableRoles = computed(() =>
  (userStore.roles ?? []).filter((role) => role !== userStore.activeRole),
)

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const alertsStore = useAlertsStore()

const viewportWidth = ref(window.innerWidth)
const collapsed = ref(window.innerWidth < 1280)
const drawerOpen = ref(false)
const drawerNavigation = ref(false)
const helpOpen = ref(false)

const toggleButtonRef = ref(null)
const userTriggerRef = ref(null)
const mainRef = ref(null)

const isMobile = computed(() => viewportWidth.value < 768)
const canSeeAlerts = computed(() =>
  ['super_admin', 'teacher', 'admin'].includes(effectiveRole.value),
)

const currentTitle = computed(() => {
  const nav = route.meta?.nav
  if (nav?.studentTitle && effectiveRole.value === 'student') {
    return nav.studentTitle
  }
  return route.meta?.title ?? ''
})

const toggleIcon = computed(() => {
  if (isMobile.value) {
    return drawerOpen.value ? Fold : Expand
  }
  return collapsed.value ? Expand : Fold
})

const toggleLabel = computed(() => {
  if (isMobile.value) {
    return drawerOpen.value ? '关闭导航菜单' : '打开导航菜单'
  }
  return collapsed.value ? '展开导航菜单' : '收起导航菜单'
})

const navGroups = computed(() => {
  const role = effectiveRole.value
  const groups = new Map()

  for (const record of router.getRoutes()) {
    const nav = record.meta?.nav
    if (!nav) continue
    if (nav.group === 'dashboard') continue
    const allowedRoles = record.meta.roles
    if (allowedRoles && role !== 'super_admin' && !allowedRoles.includes(role)) continue
    const groupMeta = GROUP_META[nav.group]
    if (!groupMeta) continue

    if (!groups.has(nav.group)) {
      groups.set(nav.group, { key: nav.group, ...groupMeta, items: [] })
    }

    const title =
      nav.studentTitle && role === 'student' ? nav.studentTitle : record.meta.title
    groups.get(nav.group).items.push({ path: record.path, title, order: nav.order })
  }

  return [...groups.values()]
    .sort((left, right) => left.order - right.order)
    .map((group) => ({
      ...group,
      items: group.items.sort((left, right) => left.order - right.order),
    }))
})

const dashboardItem = computed(() => {
  const record = router
    .getRoutes()
    .find((item) => item.meta?.nav?.group === 'dashboard')
  if (!record) return null
  return { path: record.path, title: record.meta?.title ?? '工作台' }
})

function handleResize() {
  const wasMobile = viewportWidth.value < 768
  viewportWidth.value = window.innerWidth
  if (viewportWidth.value < 768) {
    drawerOpen.value = false
  } else if (wasMobile) {
    collapsed.value = viewportWidth.value < 1280
  }
}

function syncKeyboardInset() {
  const viewport = window.visualViewport
  if (!viewport) {
    document.documentElement.style.setProperty('--keyboard-inset', '0px')
    return
  }
  const inset = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
  document.documentElement.style.setProperty('--keyboard-inset', `${Math.round(inset)}px`)
}

function toggleSidebar() {
  if (isMobile.value) {
    drawerOpen.value = !drawerOpen.value
    return
  }
  collapsed.value = !collapsed.value
}

function handleMenuSelect() {
  if (isMobile.value) {
    drawerNavigation.value = true
    drawerOpen.value = false
  }
}

function goAlerts() {
  if (route.path === '/overview') {
    document
      .getElementById('stock-alerts')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  router.push({ path: '/overview', query: { focus: 'alerts' } })
}

async function focusMain() {
  await nextTick()
  window.scrollTo({ top: 0 })
  mainRef.value?.focus?.({ preventScroll: true })
}

function handleDrawerClosed() {
  if (drawerNavigation.value) {
    drawerNavigation.value = false
    nextTick(() => {
      window.scrollTo({ top: 0 })
      mainRef.value?.focus?.({ preventScroll: true })
    })
    return
  }
  nextTick(() => toggleButtonRef.value?.focus?.())
}

async function handleLogout() {
  const confirmed = await ElMessageBox.confirm(
    '退出当前会话后，本机需要重新登录才能继续使用，其它设备登录状态保留。',
    '退出当前会话',
    {
      confirmButtonText: '确认退出',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
    .then(() => true)
    .catch(() => false)

  if (!confirmed) return

  const success = await userStore.logout()
  if (!success) {
    ElMessage.error('退出失败，请重试')
    return
  }

  ElMessage.success('已退出当前会话')
  router.push('/login')
}

async function handleLogoutAll() {
  const confirmed = await ElMessageBox.confirm(
    '撤销全部会话后，所有设备都需要重新登录。',
    '撤销全部会话',
    {
      confirmButtonText: '确认撤销',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
    .then(() => true)
    .catch(() => false)

  if (!confirmed) return

  const success = await userStore.logoutAll()
  if (!success) {
    ElMessage.error('撤销失败，请重试')
    return
  }

  ElMessage.success('已撤销全部会话')
  router.push('/login')
}

const passwordDialogOpen = ref(false)
const passwordSubmitting = ref(false)
const passwordError = ref('')
const passwordForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const expiryNotified = ref(false)
let expiryTimer = null
let alertsTimer = null

function openPasswordDialog() {
  passwordError.value = ''
  passwordForm.oldPassword = ''
  passwordForm.newPassword = ''
  passwordForm.confirmPassword = ''
  passwordDialogOpen.value = true
}

function handlePasswordClosed() {
  if (userStore.mustChangePassword) {
    passwordDialogOpen.value = true
    return
  }
  nextTick(() => userTriggerRef.value?.focus?.())
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
    passwordDialogOpen.value = false
    ElMessage.success('密码已更新，初始凭据与旧会话已失效')
  } catch (error) {
    passwordError.value = error.response?.data?.detail || '修改密码失败，请重试'
  } finally {
    passwordSubmitting.value = false
  }
}

function checkExpiry() {
  if (!userStore.sessionExpiresAt || !userStore.isLoggedIn) return
  const remaining = new Date(userStore.sessionExpiresAt).getTime() - Date.now()
  if (remaining <= 0) {
    userStore.sessionExpired = true
    userStore.clearUser()
    router.replace('/login')
    return
  }
  if (remaining <= 5 * 60 * 1000 && !expiryNotified.value) {
    expiryNotified.value = true
    ElMessage.warning('登录将在 5 分钟内过期，请及时保存当前操作')
  }
}

async function handleRoleSwitch(nextRole) {
  if (!nextRole || nextRole === effectiveRole.value) return
  try {
    await userStore.switchRole(nextRole)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '角色切换失败，请重试')
    return
  }
  ElMessage.success(`已切换为${ROLE_LABEL[nextRole] ?? nextRole}视角`)
  const allowedRoles = route.meta?.roles
  if (allowedRoles && nextRole !== 'super_admin' && !allowedRoles.includes(nextRole)) {
    router.push('/dashboard')
    return
  }
  router.go(0)
}

function handleUserCommand(command) {
  if (command === 'logout') {
    handleLogout()
  } else if (command === 'logout-all') {
    handleLogoutAll()
  } else if (command === 'change-password') {
    openPasswordDialog()
  } else if (typeof command === 'string' && command.startsWith('switch-role:')) {
    handleRoleSwitch(command.slice('switch-role:'.length))
  }
}

const activeClassName = computed(() => userStore.activeClass?.name ?? '未选择班级')
const activeClassTerm = computed(() => userStore.activeClass?.term_name ?? '')

async function handleClassCommand(classId) {
  const target = userStore.availableClasses.find((item) => item.id === classId)
  if (!target || classId === userStore.activeClass?.id) return
  try {
    await userStore.switchClass(classId)
    ElMessage.success(`已切换到 ${target.name}`)
    window.location.reload()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '切换班级失败，请重试')
  }
}

watch(
  () => userStore.mustChangePassword,
  (value) => {
    if (value) openPasswordDialog()
  },
  { immediate: true },
)

watch(
  () => route.path,
  async () => {
    const drawerWasOpen = drawerOpen.value
    const viaDrawer = isMobile.value && (drawerNavigation.value || drawerWasOpen)
    if (drawerWasOpen) drawerNavigation.value = true
    drawerOpen.value = false
    await nextTick()
    if (viaDrawer) return
    window.scrollTo({ top: 0 })
    mainRef.value?.focus?.({ preventScroll: true })
  },
)

watch(
  () => userStore.identityLocked,
  (value) => {
    if (value) {
      ElMessage.warning('账号已在其它标签页切换或退出，请重新登录')
      router.replace({ path: '/login', query: { redirect: route.fullPath } })
    }
  },
)

onMounted(() => {
  window.addEventListener('resize', handleResize)
  window.visualViewport?.addEventListener('resize', syncKeyboardInset)
  window.visualViewport?.addEventListener('scroll', syncKeyboardInset)
  syncKeyboardInset()
  expiryTimer = window.setInterval(checkExpiry, 30000)
  checkExpiry()
  if (canSeeAlerts.value) {
    alertsStore.fetch()
    alertsTimer = window.setInterval(() => alertsStore.fetch({ silent: true }), 30000)
  }
  if (!userStore.mustChangePassword) {
    nextTick(() => mainRef.value?.focus?.({ preventScroll: true }))
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  window.visualViewport?.removeEventListener('resize', syncKeyboardInset)
  window.visualViewport?.removeEventListener('scroll', syncKeyboardInset)
  document.documentElement.style.removeProperty('--keyboard-inset')
  if (expiryTimer) {
    window.clearInterval(expiryTimer)
    expiryTimer = null
  }
  if (alertsTimer) {
    window.clearInterval(alertsTimer)
    alertsTimer = null
  }
})
</script>

<template>
  <div class="layout">
    <a class="layout__skip" href="#main-content" @click.prevent="focusMain">跳到主要内容</a>

    <header class="layout__header">
      <button
        ref="toggleButtonRef"
        class="layout__icon-button"
        type="button"
        :aria-label="toggleLabel"
        @click="toggleSidebar"
      >
        <el-icon :size="18"><component :is="toggleIcon" /></el-icon>
      </button>

      <div class="layout__brand">
        <span class="layout__brand-mark" aria-hidden="true">
          <el-icon :size="16"><Box /></el-icon>
        </span>
        <span class="layout__brand-name">班级实训耗材智能管理系统</span>
      </div>

      <span v-if="isMobile" class="layout__current">{{ currentTitle }}</span>

      <div class="layout__spacer" />

      <div class="layout__tools">
        <el-badge
          v-if="canSeeAlerts"
          :value="alertsStore.activeCount"
          :max="99"
          :hidden="!alertsStore.activeCount"
          class="layout__alert"
        >
          <button
            class="layout__icon-button"
            type="button"
            aria-label="站内待办"
            @click="goAlerts"
          >
            <el-icon :size="18"><Bell /></el-icon>
          </button>
        </el-badge>

        <button
          class="layout__icon-button layout__help"
          type="button"
          aria-label="使用帮助与故障联络"
          @click="helpOpen = true"
        >
          <el-icon :size="18"><QuestionFilled /></el-icon>
          <span class="layout__help-text">帮助</span>
        </button>

        <el-dropdown
          v-if="userStore.canSwitchClass"
          trigger="click"
          class="layout__class"
          @command="handleClassCommand"
        >
          <button
            class="layout__class-button"
            type="button"
            aria-label="切换班级"
            aria-haspopup="menu"
          >
            <el-icon :size="16"><School /></el-icon>
            <span class="layout__class-text">
              <span class="layout__class-name" :title="activeClassName">{{ activeClassName }}</span>
              <span v-if="activeClassTerm" class="layout__class-term">{{ activeClassTerm }}</span>
            </span>
            <el-icon :size="12" class="layout__chevron"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                v-for="item in userStore.availableClasses"
                :key="item.id"
                :command="item.id"
                :disabled="item.id === userStore.activeClass?.id"
              >
                <span>{{ item.name }}</span>
                <span v-if="item.term_name" class="layout__class-term">
                  （{{ item.term_name }}）
                </span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <span class="layout__divider" aria-hidden="true" />

        <el-dropdown trigger="click" @command="handleUserCommand">
          <button
            ref="userTriggerRef"
            class="layout__account"
            type="button"
            aria-label="账号菜单"
            aria-haspopup="menu"
          >
            <span class="layout__avatar" :class="avatarClass" aria-hidden="true">
              {{ userInitial }}
            </span>
            <span class="layout__account-text">
              <span class="layout__user-name" :title="userStore.user?.name">
                {{ userStore.user?.name }}
              </span>
              <span class="layout__user-role">{{ accountRole }}</span>
            </span>
            <el-icon :size="14" class="layout__chevron"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                <span class="layout__dropdown-name">{{ userStore.user?.name }}</span>
                <span class="layout__dropdown-role">{{ userStore.roleName }}</span>
              </el-dropdown-item>
              <el-dropdown-item
                v-for="(role, index) in switchableRoles"
                :key="`role-${role}`"
                :divided="index === 0"
                :command="`switch-role:${role}`"
              >
                切换为{{ ROLE_LABEL[role] ?? role }}视角
              </el-dropdown-item>
              <el-dropdown-item divided command="change-password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout">退出当前会话</el-dropdown-item>
              <el-dropdown-item command="logout-all">撤销全部会话</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div class="layout__body">
      <aside
        v-if="!isMobile"
        class="layout__aside"
        :class="{ 'layout__aside--collapsed': collapsed }"
      >
        <el-menu
          class="layout__menu"
          :default-active="route.path"
          :collapse="collapsed"
          :collapse-transition="false"
          router
          @select="handleMenuSelect"
        >
          <el-menu-item v-if="dashboardItem" :index="dashboardItem.path">
            <el-icon :size="18"><Odometer /></el-icon>
            <span>{{ dashboardItem.title }}</span>
          </el-menu-item>
          <el-sub-menu
            v-for="group in navGroups"
            :key="group.key"
            :index="`group:${group.key}`"
          >
            <template #title>
              <el-icon :size="18">
                <component :is="GROUP_META[group.key].icon" />
              </el-icon>
              <span>{{ group.label }}</span>
            </template>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              {{ item.title }}
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </aside>

      <el-drawer
        v-if="isMobile"
        v-model="drawerOpen"
        direction="ltr"
        :with-header="false"
        size="248px"
        class="layout__drawer"
        @closed="handleDrawerClosed"
      >
        <nav class="layout__drawer-nav" aria-label="主导航">
          <div class="layout__drawer-head">
            <span class="layout__brand-mark" aria-hidden="true">
              <el-icon :size="16"><Box /></el-icon>
            </span>
            <span>班级实训耗材智能管理系统</span>
          </div>
          <el-menu
            class="layout__menu"
            :default-active="route.path"
            router
            @select="handleMenuSelect"
          >
            <el-menu-item v-if="dashboardItem" :index="dashboardItem.path">
              <el-icon :size="18"><Odometer /></el-icon>
              <span>{{ dashboardItem.title }}</span>
            </el-menu-item>
            <el-sub-menu
              v-for="group in navGroups"
              :key="group.key"
              :index="`group:${group.key}`"
            >
              <template #title>
                <el-icon :size="18">
                  <component :is="GROUP_META[group.key].icon" />
                </el-icon>
                <span>{{ group.label }}</span>
              </template>
              <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
                {{ item.title }}
              </el-menu-item>
            </el-sub-menu>
          </el-menu>
        </nav>
      </el-drawer>

      <main
        id="main-content"
        ref="mainRef"
        class="layout__main"
        tabindex="-1"
        aria-label="主要内容"
      >
        <div class="page">
          <router-view v-slot="{ Component }">
            <transition name="route-fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </main>
    </div>

    <el-dialog
      v-model="passwordDialogOpen"
      :title="userStore.mustChangePassword ? '首次登录请修改密码' : '修改密码'"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="!userStore.mustChangePassword"
      :show-close="!userStore.mustChangePassword"
      @closed="handlePasswordClosed"
    >
      <div class="layout__password">
        <el-alert
          v-if="userStore.mustChangePassword"
          type="info"
          :closable="false"
          show-icon
          title="初始密码仅可使用一次，请设置新密码后继续使用。"
          class="layout__password-alert"
        />
        <el-alert
          v-if="passwordError"
          type="error"
          :closable="false"
          show-icon
          :title="passwordError"
          class="layout__password-alert"
        />
        <el-form label-position="top" @submit.prevent="submitPassword">
          <el-form-item label="当前密码">
            <el-input
              v-model="passwordForm.oldPassword"
              type="password"
              autocomplete="current-password"
              :disabled="passwordSubmitting"
            />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input
              v-model="passwordForm.newPassword"
              type="password"
              autocomplete="new-password"
              :disabled="passwordSubmitting"
            />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input
              v-model="passwordForm.confirmPassword"
              type="password"
              autocomplete="new-password"
              :disabled="passwordSubmitting"
              @keyup.enter="submitPassword"
            />
          </el-form-item>
        </el-form>
        <p class="layout__password-hint">新密码至少 8 位。修改后其它设备需要重新登录。</p>
      </div>
      <template #footer>
        <el-button
          v-if="!userStore.mustChangePassword"
          :disabled="passwordSubmitting"
          @click="passwordDialogOpen = false"
        >
          取消
        </el-button>
        <el-button type="primary" :loading="passwordSubmitting" @click="submitPassword">
          保存新密码
        </el-button>
      </template>
    </el-dialog>

    <HelpDrawer v-model="helpOpen" />
  </div>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  background: var(--page);
}

.layout__skip {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 60;
  padding: 8px 12px;
  border-radius: var(--radius-control);
  background: var(--primary);
  color: #FFFFFF;
  font-size: 14px;
  line-height: 20px;
  text-decoration: none;
  transform: translateY(-200%);
  transition: transform var(--duration-hover) ease;
}

.layout__skip:focus-visible {
  transform: translateY(0);
}

.layout__header {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--topbar-height);
  padding: 0 16px 0 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--line-soft);
  box-shadow: 0 1px 2px rgb(23 38 60 / 3%);
}

.layout__icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease,
    border-color var(--duration-hover) ease;
}

.layout__icon-button:hover {
  background: var(--surface-muted);
  color: var(--text);
}

.layout__icon-button:active {
  background: var(--surface-sunken);
}

.layout__help {
  width: auto;
  padding: 0 10px;
}

.layout__brand {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  color: var(--text);
}

.layout__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: var(--brand-mark-bg);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 14%);
  color: #FFFFFF;
}

.layout__brand-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.layout__tools {
  display: flex;
  align-items: center;
  gap: 6px;
}

.layout__divider {
  width: 1px;
  height: 20px;
  margin: 0 4px;
  background: var(--line);
}

.layout__alert :deep(.el-badge__content) {
  font-size: 11px;
  font-weight: 600;
}

.layout__spacer {
  flex: 1;
}

.layout__class-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 9px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-family: inherit;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.layout__class-button:hover {
  background: var(--surface-sunken);
  color: var(--text);
}

.layout__class-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
}

.layout__class-name {
  max-width: 130px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
  line-height: 16px;
  font-weight: 600;
  color: var(--text);
}

.layout__class-term {
  font-size: 11px;
  line-height: 14px;
  color: var(--text-muted);
}

.layout__account {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 260px;
  height: 38px;
  padding: 4px 6px 4px 4px;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  font-family: inherit;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    border-color var(--duration-hover) ease;
}

.layout__account:hover {
  background: var(--surface-muted);
}

.layout__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.layout__avatar--admin {
  background: var(--success-soft);
  color: var(--success);
}

.layout__avatar--teacher {
  background: var(--info-soft);
  color: var(--info);
}

.layout__avatar--student {
  background: var(--surface-muted);
  color: var(--text-secondary);
}

.layout__avatar--super_admin {
  background: var(--info-soft);
  color: var(--info);
}

.layout__avatar--class_committee {
  background: var(--success-soft);
  color: var(--success);
}

.layout__account-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
}

.layout__user-name {
  max-width: 150px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text);
}

.layout__user-role {
  font-size: 11px;
  line-height: 15px;
  letter-spacing: 0.02em;
  color: var(--text-muted);
}

.layout__chevron {
  flex: 0 0 auto;
  color: var(--text-muted);
}

.layout__dropdown-name {
  color: var(--text);
  font-weight: 600;
}

.layout__dropdown-role {
  margin-left: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

.layout__body {
  display: flex;
  align-items: flex-start;
}

.layout__aside {
  position: sticky;
  top: var(--topbar-height);
  flex: 0 0 var(--sidebar-width);
  width: var(--sidebar-width);
  height: calc(100vh - var(--topbar-height));
  overflow-y: auto;
  background: var(--nav);
  border-right: 1px solid var(--line-soft);
  transition:
    width var(--duration-hover) ease,
    flex-basis var(--duration-hover) ease;
}

.layout__aside--collapsed {
  flex-basis: var(--sidebar-collapsed);
  width: var(--sidebar-collapsed);
}

.layout__main {
  flex: 1;
  min-width: 0;
}

.layout__main:focus {
  outline: none;
}

.layout__menu {
  border-right: none;
  background: var(--nav);
  padding: 10px 0 16px;
}

.layout__menu :deep(.el-menu-item),
.layout__menu :deep(.el-sub-menu__title) {
  position: relative;
  height: 38px;
  line-height: 38px;
  margin: 2px 8px;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.layout__menu :deep(.el-sub-menu__title) {
  margin-bottom: 4px;
  color: var(--text);
  font-weight: 600;
}

.layout__menu :deep(.el-menu-item:hover),
.layout__menu :deep(.el-sub-menu__title:hover) {
  background: var(--surface-muted);
  color: var(--text);
}

.layout__menu :deep(.el-menu-item.is-active) {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}

.layout__menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: var(--primary);
}

.layout__menu :deep(.el-menu-item.is-active)::before {
  position: absolute;
  left: -8px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--primary);
  content: "";
}

.layout__menu :deep(.el-sub-menu__title .el-icon) {
  margin-right: 9px;
  font-size: 17px;
}

.layout__menu :deep(.el-menu-item .el-icon) {
  margin-right: 9px;
  font-size: 17px;
}

.layout__menu :deep(.el-menu--inline) {
  background: transparent;
}

.layout__menu :deep(.el-menu--inline .el-menu-item) {
  padding-left: 40px;
  font-size: 13px;
}

.layout__aside--collapsed .layout__menu :deep(.el-sub-menu__title) {
  justify-content: center;
  padding: 0;
}

.layout__aside--collapsed .layout__menu :deep(.el-sub-menu__title .el-icon) {
  margin-right: 0;
}

.layout__aside--collapsed .layout__menu :deep(.el-menu-item) {
  justify-content: center;
  padding: 0;
}

.layout__aside--collapsed .layout__menu :deep(.el-menu-item .el-icon) {
  margin-right: 0;
}

.layout__drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--topbar-height);
  padding: 0 16px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.layout__drawer-nav {
  padding: 0;
}

.layout__drawer-nav .layout__menu {
  background: var(--nav);
  min-height: calc(100vh - var(--topbar-height));
}

.layout__current {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.route-fade-enter-active {
  transition:
    opacity var(--duration-route) var(--ease-out),
    transform var(--duration-route) var(--ease-out);
}

.route-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

@media (max-width: 767px) {
  .layout__header {
    gap: 6px;
    padding: 0 8px;
  }

  .layout__brand-name {
    display: none;
  }

  .layout__icon-button {
    width: 44px;
    height: 44px;
  }

  .layout__help {
    width: 44px;
    padding: 0;
  }

  .layout__help-text {
    display: none;
  }

  .layout__divider {
    margin: 0;
  }

  .layout__account {
    height: 44px;
    padding: 4px;
  }

  .layout__account-text,
  .layout__chevron {
    display: none;
  }
}
</style>

<style>
.layout__drawer .el-drawer__body {
  padding: 0;
}

.el-menu--popup {
  min-width: 168px;
  padding: 6px;
  border-radius: 10px;
  box-shadow: var(--shadow-popover);
}

.el-menu--popup .el-menu-item {
  height: 34px;
  line-height: 34px;
  margin: 2px 0;
  border-radius: 6px;
  font-size: 13px;
}
</style>