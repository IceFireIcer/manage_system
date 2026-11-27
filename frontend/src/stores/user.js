import { defineStore } from 'pinia'
import api from '../api'

const ROLE_NAMES = {
  teacher: '教师',
  admin: '班委',
  student: '学生',
}

const REDIRECT_KEY = 'login-redirect'

let initTask = null

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null,
    fetched: false,
    status: 'checking',
    sessionExpired: false,
  }),
  getters: {
    isLoggedIn: (state) => state.user !== null,
    roleName: (state) => ROLE_NAMES[state.user?.role] ?? '未知',
  },
  actions: {
    init(force = false) {
      if (!force && this.status === 'ready') {
        return Promise.resolve(this.user)
      }
      if (initTask) {
        return initTask
      }
      this.status = 'checking'
      initTask = api
        .get('/auth/me', { skipAuthRedirect: true })
        .then(({ data }) => {
          this.user = data
          this.fetched = true
          this.status = 'ready'
          this.sessionExpired = false
          return this.user
        })
        .catch((error) => {
          const status = error.response?.status
          if (status === 401 || status === 403) {
            this.user = null
            this.fetched = true
            this.status = 'ready'
          } else {
            this.status = 'error'
          }
          return this.user
        })
        .finally(() => {
          initTask = null
        })
      return initTask
    },
    async fetchMe() {
      await this.init()
      return this.user
    },
    async login(payload) {
      const { data } = await api.post('/auth/login', payload)
      this.user = data
      this.fetched = true
      this.status = 'ready'
      this.sessionExpired = false
      return data
    },
    async logout() {
      try {
        await api.post('/auth/logout')
      } catch (error) {
        if (error.response?.status === 401) {
          this.clearUser()
          return true
        }
        return false
      }
      this.clearUser()
      return true
    },
    clearUser() {
      this.user = null
      this.fetched = true
      this.status = 'ready'
    },
    rememberRedirect(path) {
      if (!path || path === '/login') return
      sessionStorage.setItem(REDIRECT_KEY, path)
    },
    takeRedirect() {
      const path = sessionStorage.getItem(REDIRECT_KEY)
      sessionStorage.removeItem(REDIRECT_KEY)
      return path || '/overview'
    },
  },
})