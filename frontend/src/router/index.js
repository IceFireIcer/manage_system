import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const Layout = () => import('../views/Layout.vue')
const SettingsPlaceholder = () => import('../views/SettingsPlaceholder.vue')

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('../views/Setup.vue'),
    meta: { title: '系统初始化' },
  },
  {
    path: '/',
    component: Layout,
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/Dashboard.vue'),
        meta: {
          title: '工作台',
          permission: 'dashboard.view',
          nav: { group: 'dashboard', icon: 'Odometer', order: 1 },
        },
      },
      {
        path: 'overview',
        name: 'overview',
        component: () => import('../views/Overview.vue'),
        meta: {
          title: '库存总览',
          permission: 'inventory.view',
          nav: { group: 'inventory', icon: 'Box', order: 1 },
        },
      },
      {
        path: 'requisition',
        name: 'requisition',
        component: () => import('../views/Requisition.vue'),
        meta: {
          title: '领用登记',
          permission: 'requisition.view',
          nav: { group: 'requisition', icon: 'Tickets', order: 1 },
        },
      },
      {
        path: 'records',
        name: 'records',
        component: () => import('../views/Records.vue'),
        meta: {
          title: '记录查询',
          permission: 'record.view',
          nav: { group: 'requisition', icon: 'Tickets', order: 2, studentTitle: '我的记录' },
        },
      },
      {
        path: 'incidents',
        name: 'incidents',
        component: () => import('../views/Incidents.vue'),
        meta: {
          title: '异常上报',
          permission: 'incident.view',
          nav: { group: 'requisition', icon: 'Warning', order: 3 },
        },
      },
      {
        path: 'footprint',
        name: 'footprint',
        component: () => import('../views/MyFootprint.vue'),
        meta: {
          title: '我的耗材足迹',
          nav: { group: 'requisition', icon: 'DataLine', order: 4 },
        },
      },
      {
        path: 'courses',
        name: 'courses',
        component: () => import('../views/CourseRecipes.vue'),
        meta: {
          title: '课程配方',
          roles: ['teacher', 'admin'],
          permission: 'course.view',
          nav: { group: 'teaching', icon: 'Reading', order: 1 },
        },
      },
      {
        path: 'handover',
        name: 'handover',
        component: () => import('../views/Handover.vue'),
        meta: {
          title: '班委交接',
          roles: ['teacher', 'admin'],
          permission: 'handover.view',
          nav: { group: 'daily', icon: 'Switch', order: 0 },
        },
      },
      {
        path: 'consumables',
        name: 'consumables',
        component: () => import('../views/Consumables.vue'),
        meta: {
          title: '耗材档案',
          roles: ['admin'],
          permission: 'consumable.view',
          nav: { group: 'daily', icon: 'Setting', order: 1 },
        },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('../views/Users.vue'),
        meta: {
          title: '用户管理',
          roles: ['admin'],
          permission: 'account.view',
        },
      },
      {
        path: 'profile',
        name: 'profile',
        component: () => import('../views/Profile.vue'),
        meta: { title: '个人资料' },
      },
      {
        path: 'restock',
        name: 'restock',
        component: () => import('../views/Restock.vue'),
        meta: {
          title: '补货建议',
          roles: ['teacher', 'admin'],
          permission: 'restock.view',
          nav: { group: 'analysis', icon: 'TrendCharts', order: 1 },
        },
      },
      {
        path: 'consumption',
        name: 'consumption',
        component: () => import('../views/Consumption.vue'),
        meta: {
          title: '消耗汇总',
          roles: ['teacher', 'admin'],
          permission: 'consumption.view',
          nav: { group: 'analysis', icon: 'TrendCharts', order: 2 },
        },
      },
      {
        path: 'semester-close',
        name: 'semester-close',
        component: () => import('../views/SemesterClose.vue'),
        meta: {
          title: '学期结账',
          roles: ['teacher', 'admin'],
          permission: 'report.view',
          nav: { group: 'analysis', icon: 'Files', order: 3 },
        },
      },
      {
        path: 'logs',
        name: 'logs',
        component: () => import('../views/Logs.vue'),
        meta: {
          title: '操作日志',
          roles: ['teacher', 'admin'],
          permission: 'log.view',
          nav: { group: 'daily', icon: 'Setting', order: 3 },
        },
      },
      {
        path: 'settings/accounts',
        name: 'settings-accounts',
        component: SettingsPlaceholder,
        meta: {
          title: '账号与角色',
          roles: ['super_admin', 'teacher'],
          permission: 'account.manage',
          nav: { group: 'settings', icon: 'User', order: 1 },
        },
      },
      {
        path: 'settings/classes',
        name: 'settings-classes',
        component: () => import('../views/Classes.vue'),
        meta: {
          title: '班级与学期',
          roles: ['super_admin', 'teacher'],
          permission: 'settings.manage',
          nav: { group: 'settings', icon: 'School', order: 2 },
        },
      },
      {
        path: 'settings/permissions',
        name: 'settings-permissions',
        component: () => import('../views/Permissions.vue'),
        meta: {
          title: '权限与页面',
          roles: ['super_admin', 'teacher'],
          permission: 'permission.manage',
          nav: { group: 'settings', icon: 'Lock', order: 3 },
        },
      },
      {
        path: 'settings/llm',
        name: 'settings-llm',
        component: () => import('../views/LlmSettings.vue'),
        meta: {
          title: 'LLM 配置',
          roles: ['super_admin'],
          permission: 'llm.manage',
          nav: { group: 'settings', icon: 'MagicStick', order: 4 },
        },
      },
      {
        path: 'settings/prompts',
        name: 'settings-prompts',
        component: SettingsPlaceholder,
        meta: {
          title: '提示词与工具',
          roles: ['super_admin'],
          permission: 'llm.manage',
          nav: { group: 'settings', icon: 'ChatDotRound', order: 5 },
        },
      },
      {
        path: 'settings/system',
        name: 'settings-system',
        component: SettingsPlaceholder,
        meta: {
          title: '系统参数',
          roles: ['super_admin'],
          permission: 'settings.manage',
          nav: { group: 'settings', icon: 'SetUp', order: 6 },
        },
      },
      {
        path: 'settings/audit',
        name: 'settings-audit',
        component: SettingsPlaceholder,
        meta: {
          title: '审计',
          roles: ['super_admin', 'teacher'],
          permission: 'audit.view',
          nav: { group: 'settings', icon: 'Document', order: 7 },
        },
      },
      {
        path: '403',
        name: 'forbidden',
        component: () => import('../views/Forbidden.vue'),
        meta: { title: '无权访问此页面' },
      },
    ],
  },
  {
    path: '/404',
    name: 'not-found',
    component: () => import('../views/NotFound.vue'),
    meta: { public: true, title: '页面不存在' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found-fallback',
    component: () => import('../views/NotFound.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const userStore = useUserStore()

  if (to.meta.public) {
    if (to.name === 'login' && userStore.isLoggedIn) {
      return { path: userStore.setupRequired ? '/setup' : '/dashboard' }
    }
    return true
  }

  if (userStore.status !== 'ready') {
    await userStore.init()
  }

  if (userStore.status === 'error') {
    return true
  }

  if (!userStore.isLoggedIn) {
    return {
      path: '/login',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : {},
    }
  }

await userStore.ensureSetupStatus()

  if (userStore.setupRequired) {
    if (to.name !== 'setup') {
      return { path: '/setup' }
    }
    return true
  }

  if (to.name === 'setup') {
    return { path: '/dashboard' }
  }

  const activeRole = userStore.activeRole ?? userStore.user.role
  const permissions = userStore.permissions ?? []

  if (
    to.meta.permission &&
    activeRole !== 'super_admin' &&
    !permissions.includes(to.meta.permission)
  ) {
    return { path: '/403', query: { from: to.fullPath } }
  }

  const allowedRoles = to.meta.roles
  if (allowedRoles && activeRole !== 'super_admin' && !allowedRoles.includes(activeRole)) {
    return { path: '/403', query: { from: to.fullPath } }
  }

  return true
})

export default router