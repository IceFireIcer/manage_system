<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, Box, Expand, Fold, Setting, Tickets, TrendCharts } from '@element-plus/icons-vue'
import { useUserStore } from '../stores/user'

const GROUP_META = {
  inventory: { label: '库存总览', icon: Box, order: 1 },
  requisition: { label: '领用管理', icon: Tickets, order: 2 },
  analysis: { label: '库存分析', icon: TrendCharts, order: 3 },
  daily: { label: '日常管理', icon: Setting, order: 4 },
}

const ROLE_TAG_CLASS = {
  teacher: 'role-tag--teacher',
  admin: 'role-tag--admin',
  student: 'role-tag--student',
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const viewportWidth = ref(window.innerWidth)
const collapsed = ref(window.innerWidth < 1280)
const drawerOpen = ref(false)

const isMobile = computed(() => viewportWidth.value < 768)

const currentTitle = computed(() => {
  const nav = route.meta?.nav
  if (nav?.studentTitle && userStore.user?.role === 'student') {
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
  const role = userStore.user?.role
  const groups = new Map()

  for (const record of router.getRoutes()) {
    const nav = record.meta?.nav
    if (!nav) continue
    const allowedRoles = record.meta.roles
    if (allowedRoles && !allowedRoles.includes(role)) continue
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

function handleResize() {
  const wasMobile = viewportWidth.value < 768
  viewportWidth.value = window.innerWidth
  if (viewportWidth.value < 768) {
    drawerOpen.value = false
  } else if (wasMobile) {
    collapsed.value = viewportWidth.value < 1280
  }
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
    drawerOpen.value = false
  }
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

function handleUserCommand(command) {
  if (command === 'logout') {
    handleLogout()
  } else if (command === 'logout-all') {
    handleLogoutAll()
  } else if (command === 'change-password') {
    openPasswordDialog()
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
  () => {
    drawerOpen.value = false
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
  expiryTimer = window.setInterval(checkExpiry, 30000)
  checkExpiry()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (expiryTimer) {
    window.clearInterval(expiryTimer)
    expiryTimer = null
  }
})
</script>

<template>
  <div class="layout">
    <header class="layout__header">
      <button
        class="layout__toggle"
        type="button"
        :aria-label="toggleLabel"
        @click="toggleSidebar"
      >
        <el-icon :size="18"><component :is="toggleIcon" /></el-icon>
      </button>

      <div class="layout__brand">
        <el-icon :size="18" class="layout__brand-icon"><Box /></el-icon>
        <span class="layout__brand-name">班级实训耗材智能管理系统</span>
      </div>

      <span v-if="isMobile" class="layout__current">{{ currentTitle }}</span>

      <div class="layout__spacer" />

      <div class="layout__account">
        <span class="layout__user-name" :title="userStore.user?.name">
          {{ userStore.user?.name }}
        </span>
        <span class="status-tag" :class="ROLE_TAG_CLASS[userStore.user?.role]">
          {{ userStore.roleName }}
        </span>
        <el-dropdown trigger="click" @command="handleUserCommand">
          <button class="layout__user-trigger" type="button" aria-label="账号菜单">
            <el-icon :size="16"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                <span class="layout__dropdown-name">{{ userStore.user?.name }}</span>
                <span class="layout__dropdown-role">{{ userStore.roleName }}</span>
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
        size="208px"
        class="layout__drawer"
      >
        <nav class="layout__drawer-nav" aria-label="主导航">
          <el-menu
            class="layout__menu"
            :default-active="route.path"
            router
            @select="handleMenuSelect"
          >
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

      <main class="layout__main">
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
  </div>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  background: var(--page);
}

.layout__header {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--topbar-height);
  padding: 0 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.layout__toggle,
.layout__user-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.layout__toggle:hover,
.layout__user-trigger:hover {
  background: var(--surface-muted);
  color: var(--text);
}

.layout__brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
}

.layout__brand-icon {
  color: var(--primary);
}

.layout__spacer {
  flex: 1;
}

.layout__account {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.layout__user-name {
  max-width: 160px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  color: var(--text);
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
  border-right: 1px solid var(--line);
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

.layout__menu {
  border-right: none;
  background: var(--nav);
  padding: 8px 0;
}

.layout__menu :deep(.el-menu-item),
.layout__menu :deep(.el-sub-menu__title) {
  position: relative;
  height: 40px;
  line-height: 40px;
  margin: 2px 8px;
  border-radius: var(--radius-control);
  color: var(--text-secondary);
  font-size: 14px;
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

.layout__menu :deep(.el-menu-item.is-active)::before {
  position: absolute;
  left: -8px;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--primary);
  content: "";
}

.layout__menu :deep(.el-sub-menu__title .el-icon) {
  margin-right: 8px;
  font-size: 18px;
}

.layout__menu :deep(.el-menu--inline) {
  background: var(--nav);
}

.layout__menu :deep(.el-menu--inline .el-menu-item) {
  padding-left: 44px;
}

.layout__drawer-nav {
  padding: 0;
}

.layout__current {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.route-fade-enter-active {
  transition: opacity var(--duration-route) ease;
}

.route-fade-enter-from {
  opacity: 0;
}

@media (max-width: 767px) {
  .layout__header {
    gap: 8px;
    padding: 0 8px;
  }

  .layout__brand-name {
    display: none;
  }

  .layout__user-name {
    max-width: 96px;
  }

  .layout__account .status-tag {
    display: none;
  }
}
</style>

<style>
.layout__drawer .el-drawer__body {
  padding: 0;
}
</style>