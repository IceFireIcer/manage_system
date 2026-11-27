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
    path: '/',
    component: Layout,
    children: [
      { path: '', redirect: '/overview' },
      { path: 'overview', name: 'overview', component: () => import('../views/Overview.vue'), meta: { title: '库存总览' } },
      { path: 'requisition', name: 'requisition', component: () => import('../views/Requisition.vue'), meta: { title: '领用登记' } },
      { path: 'records', name: 'records', component: () => import('../views/Records.vue'), meta: { title: '记录查询' } },
      { path: 'consumables', name: 'consumables', component: () => import('../views/Consumables.vue'), meta: { title: '耗材档案', roles: ['teacher', 'admin'] } },
      { path: 'users', name: 'users', component: () => import('../views/Users.vue'), meta: { title: '用户管理', roles: ['admin'] } },
      { path: 'restock', name: 'restock', component: () => import('../views/Restock.vue'), meta: { title: '补货建议', roles: ['teacher', 'admin'] } },
      { path: 'logs', name: 'logs', component: () => import('../views/Logs.vue'), meta: { title: '操作日志', roles: ['teacher', 'admin'] } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const userStore = useUserStore()

  if (!userStore.fetched && !to.meta.public) {
    await userStore.fetchMe()
  }

  if (to.meta.public) {
    if (userStore.isLoggedIn) {
      return { path: '/' }
    }
    return true
  }

  if (!userStore.isLoggedIn) {
    return { path: '/login' }
  }

  const allowedRoles = to.meta.roles
  if (allowedRoles && !allowedRoles.includes(userStore.user.role)) {
    return { path: '/overview' }
  }

  return true
})

export default router