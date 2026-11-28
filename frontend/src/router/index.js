import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const Layout = () => import('../views/Layout.vue')

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
          nav: { group: 'dashboard', icon: 'Odometer', order: 1 },
        },
      },
      {
        path: 'overview',
        name: 'overview',
        component: () => import('../views/Overview.vue'),
        meta: { title: '库存总览', nav: { group: 'inventory', icon: 'Box', order: 1 } },
      },
      {
        path: 'requisition',
        name: 'requisition',
        component: () => import('../views/Requisition.vue'),
        meta: { title: '领用登记', nav: { group: 'requisition', icon: 'Tickets', order: 1 } },
      },
      {
        path: 'records',
        name: 'records',
        component: () => import('../views/Records.vue'),
        meta: {
          title: '记录查询',
          nav: { group: 'requisition', icon: 'Tickets', order: 2, studentTitle: '我的记录' },
        },
      },
      {
        path: 'incidents',
        name: 'incidents',
        component: () => import('../views/Incidents.vue'),
        meta: {
          title: '异常上报',
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
          nav: { group: 'daily', icon: 'Setting', order: 2 },
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
          nav: { group: 'daily', icon: 'Setting', order: 3 },
        },
      },
      {
        path: 'settings/classes',
        name: 'settings-classes',
        component: () => import('../views/Classes.vue'),
        meta: {
          title: '班级与学期',
          roles: ['super_admin', 'teacher'],
          nav: { group: 'daily', icon: 'School', order: 4 },
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
  const allowedRoles = to.meta.roles
  if (allowedRoles && activeRole !== 'super_admin' && !allowedRoles.includes(activeRole)) {
    return { path: '/403', query: { from: to.fullPath } }
  }

  return true
})

export default router