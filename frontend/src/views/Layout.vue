<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const menus = [
  { path: '/overview', title: '库存总览' },
  { path: '/requisition', title: '领用登记' },
  { path: '/records', title: '记录查询' },
  { path: '/consumables', title: '耗材档案', roles: ['admin'] },
  { path: '/users', title: '用户管理', roles: ['admin'] },
  { path: '/restock', title: '补货建议', roles: ['teacher', 'admin'] },
  { path: '/consumption', title: '消耗汇总', roles: ['teacher', 'admin'] },
  { path: '/logs', title: '操作日志', roles: ['teacher', 'admin'] },
]

const visibleMenus = computed(() =>
  menus.filter((menu) => !menu.roles || menu.roles.includes(userStore.user?.role)),
)

const activeMenu = computed(() => route.path)

const roleTagType = computed(() =>
  ({ teacher: 'danger', admin: 'warning', student: 'success' })[userStore.user?.role] ?? 'info',
)

async function handleLogout() {
  await userStore.logout()
  ElMessage.success('已退出登录')
  router.push('/login')
}
</script>

<template>
  <el-container class="layout">
    <el-aside class="layout__aside" width="220px">
      <div class="layout__brand">耗材管理系统</div>
      <el-menu class="layout__menu" :default-active="activeMenu" router>
        <el-menu-item v-for="menu in visibleMenus" :key="menu.path" :index="menu.path">
          {{ menu.title }}
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="layout__header">
        <span class="layout__header-title">{{ route.meta.title }}</span>
        <div class="layout__header-user">
          <span class="layout__user-name">{{ userStore.user?.name }}</span>
          <el-tag :type="roleTagType" size="small">{{ userStore.roleName }}</el-tag>
          <el-button size="small" @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="layout__main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}

.layout__aside {
  background-color: #304156;
}

.layout__brand {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: #fff;
  font-size: 18px;
  font-weight: 600;
}

.layout__menu {
  border-right: none;
  background-color: #304156;
}

.layout__menu :deep(.el-menu-item) {
  color: #bfcbd9;
}

.layout__menu :deep(.el-menu-item.is-active) {
  color: #409eff;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e6e6e6;
}

.layout__header-title {
  font-size: 16px;
  font-weight: 600;
}

.layout__header-user {
  display: flex;
  align-items: center;
  gap: 12px;
}

.layout__user-name {
  font-size: 14px;
}

.layout__main {
  background-color: #f0f2f5;
}
</style>