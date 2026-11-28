import { defineStore } from 'pinia'
import api from '../api'

const ROLE_NAMES = {
  teacher: '教师',
  admin: '班委',
  student: '学生',
}

const REDIRECT_KEY = 'login-redirect'
const SESSION_EPOCH_KEY = 'manage-system-session-epoch'
const SESSION_CHANNEL = 'manage-system-session'

let initTask = null
let syncReady = false
let channel = null

function parseEpoch(value) {
  if (!value) return null
  const index = String(value).lastIndexOf(':')
  if (index <= 0) return null
  const userId = Number(value.slice(0, index))
  const stamp = Number(value.slice(index + 1))
  if (!Number.isInteger(userId) || !Number.isInteger(stamp)) return null
  return { userId, epoch: String(value) }
}

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null,
    fetched: false,
    status: 'checking',
    sessionExpired: false,
    sessionExpiresAt: null,
    mustChangePassword: false,
    sessionEpoch: null,
    identityLocked: false,
  }),
  getters: {
    isLoggedIn: (state) => state.user !== null,
    roleName: (state) => ROLE_NAMES[state.user?.role] ?? '未知',
  },
  actions: {
    ensureSessionSync() {
      if (syncReady || typeof window === 'undefined') return
      syncReady = true
      if ('BroadcastChannel' in window) {
        channel = new BroadcastChannel(SESSION_CHANNEL)
        channel.onmessage = (event) => this.applyRemoteSignal(event.data)
      }
      window.addEventListener('storage', (event) => {
        if (event.key === SESSION_EPOCH_KEY && event.newValue) {
          this.applyRemoteSignal({ type: 'epoch', epoch: event.newValue })
        }
      })
    },
    applyRemoteSignal(signal) {
      if (!signal || !this.user) return
      if (signal.type === 'epoch') {
        const remote = parseEpoch(signal.epoch)
        if (!remote) return
        if (remote.userId !== this.user.id) {
          this.lockIdentity()
        }
        return
      }
      if (signal.type === 'logout') {
        this.lockIdentity()
      }
    },
    lockIdentity() {
      if (!this.user) return
      this.user = null
      this.fetched = true
      this.status = 'ready'
      this.identityLocked = true
      this.sessionExpired = true
      this.sessionExpiresAt = null
      this.mustChangePassword = false
    },
    broadcast(signal) {
      if (channel) channel.postMessage(signal)
    },
    publishEpoch(userId) {
      this.sessionEpoch = `${userId}:${Date.now()}`
      if (typeof window !== 'undefined') {
        localStorage.setItem(SESSION_EPOCH_KEY, this.sessionEpoch)
      }
      this.broadcast({ type: 'epoch', epoch: this.sessionEpoch })
    },
    applySession(data) {
      if (!data) return
      this.user = data.user ?? null
      this.mustChangePassword = Boolean(data.must_change_password)
      this.sessionExpiresAt = data.session_expires_at ?? null
    },
    init(force = false) {
      this.ensureSessionSync()
      if (!force && this.status === 'ready') {
        return Promise.resolve(this.user)
      }
      if (initTask) {
        return initTask
      }
      this.status = 'checking'
      initTask = api
        .get('/auth/session', { skipAuthRedirect: true })
        .then(({ data }) => {
          this.applySession(data)
          this.fetched = true
          this.status = 'ready'
          this.sessionExpired = false
          this.identityLocked = false
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
    async refreshSession() {
      const { data } = await api.get('/auth/session', { skipAuthRedirect: true })
      this.applySession(data)
      this.status = 'ready'
      this.fetched = true
      this.sessionExpired = false
      return data
    },
    async login(payload) {
      const { data } = await api.post('/auth/login', payload)
      this.user = data
      this.status = 'ready'
      this.fetched = true
      this.sessionExpired = false
      this.identityLocked = false
      this.publishEpoch(data.id)
      try {
        await this.refreshSession()
      } catch {
        this.mustChangePassword = false
        this.sessionExpiresAt = null
      }
      return data
    },
    async changePassword(payload) {
      const { data } = await api.post('/auth/change-password', payload)
      this.applySession(data)
      this.mustChangePassword = false
      if (this.user?.id) {
        this.publishEpoch(this.user.id)
      }
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
      this.broadcast({ type: 'logout', epoch: this.sessionEpoch })
      this.clearUser()
      return true
    },
    async logoutAll() {
      try {
        await api.post('/auth/logout-all')
      } catch (error) {
        if (error.response?.status === 401) {
          this.clearUser()
          return true
        }
        return false
      }
      this.broadcast({ type: 'logout', epoch: this.sessionEpoch })
      this.clearUser()
      return true
    },
    clearUser() {
      this.user = null
      this.fetched = true
      this.status = 'ready'
      this.identityLocked = false
    },
    rememberRedirect(path) {
      if (!path || path === '/login') return
      sessionStorage.setItem(REDIRECT_KEY, path)
    },
    takeRedirect() {
      const path = sessionStorage.getItem(REDIRECT_KEY)
      sessionStorage.removeItem(REDIRECT_KEY)
      return path || '/dashboard'
    },
  },
})