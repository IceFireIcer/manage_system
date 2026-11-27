import { defineStore } from 'pinia'
import api from '../api'

const ROLE_NAMES = {
  teacher: '教师',
  admin: '班委',
  student: '学生',
}

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null,
    fetched: false,
  }),
  getters: {
    isLoggedIn: (state) => state.user !== null,
    roleName: (state) => ROLE_NAMES[state.user?.role] ?? '未知',
  },
  actions: {
    async fetchMe() {
      try {
        const { data } = await api.get('/auth/me')
        this.user = data
      } catch {
        this.user = null
      } finally {
        this.fetched = true
      }
      return this.user
    },
    async login(payload) {
      const { data } = await api.post('/auth/login', payload)
      this.user = data
      this.fetched = true
      return data
    },
    async logout() {
      try {
        await api.post('/auth/logout')
      } catch {
        // 登出失败也继续清理本地状态
      }
      this.clearUser()
    },
    clearUser() {
      this.user = null
      this.fetched = false
    },
  },
})