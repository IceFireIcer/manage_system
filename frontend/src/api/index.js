import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import { useUserStore } from '../stores/user'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  withCredentials: true,
})

let lastForbiddenAt = 0
let lastServerErrorAt = 0

function requestIdOf(error) {
  const fromHeader = error.response?.headers?.['x-request-id']
  const fromBody = error.response?.data?.request_id
  const value = fromHeader || fromBody
  return value ? `（请求编号 ${value}）` : ''
}

api.interceptors.request.use((config) => {
  if (config.skipAuthRedirect) return config
  const userStore = useUserStore()
  if (userStore.identityLocked) {
    return Promise.reject(new axios.CanceledError('session-locked'))
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const requestIdSuffix = requestIdOf(error)

    if (status === 401 && !error.config?.skipAuthRedirect) {
      const userStore = useUserStore()
      const current = router.currentRoute.value
      if (!userStore.sessionExpired) {
        userStore.sessionExpired = true
        userStore.rememberRedirect(current.fullPath)
        userStore.clearUser()
        if (current.path !== '/login') {
          ElMessage.warning('登录已失效，请重新登录')
        }
      }
      if (current.path !== '/login') {
        router.replace('/login')
      }
    } else if (status === 403) {
      const detail = error.response?.data?.detail
      if (detail === '请先修改初始密码') {
        const userStore = useUserStore()
        userStore.mustChangePassword = true
        return Promise.reject(error)
      }
      const now = Date.now()
      if (now - lastForbiddenAt > 800) {
        lastForbiddenAt = now
        ElMessage.error(`你没有权限执行此操作${requestIdSuffix}`)
      }
    } else if (status >= 500 && !error.config?.skipAuthRedirect) {
      const now = Date.now()
      if (now - lastServerErrorAt > 800) {
        lastServerErrorAt = now
        ElMessage.error(`服务暂时不可用，请稍后重试${requestIdSuffix}`)
      }
    }

    return Promise.reject(error)
  },
)

export default api