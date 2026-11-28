import { defineStore } from 'pinia'
import api from '../api'

export const useAlertsStore = defineStore('alerts', {
  state: () => ({
    items: [],
    activeCount: 0,
    loaded: false,
    loading: false,
    showCompleted: false,
  }),
  getters: {
    activeItems: (state) => state.items.filter((item) => item.status !== 'completed'),
    visibleItems: (state) =>
      state.showCompleted ? state.items : state.items.filter((item) => item.status !== 'completed'),
  },
  actions: {
    async fetch({ includeCompleted = this.showCompleted, silent = false } = {}) {
      if (this.loading && !silent) return
      this.loading = true
      try {
        const { data } = await api.get('/inventory/alerts', {
          params: { include_completed: includeCompleted },
        })
        this.items = Array.isArray(data.items) ? data.items : []
        this.activeCount = data.active_count ?? 0
        this.loaded = true
      } catch {
        this.loaded = this.loaded || false
      } finally {
        this.loading = false
      }
    },
    async setShowCompleted(value) {
      this.showCompleted = value
      await this.fetch({ includeCompleted: value })
    },
    async act(consumableId, action, note = '') {
      const { data } = await api.patch(`/inventory/alerts/${consumableId}`, {
        action,
        note: note || null,
      })
      await this.fetch()
      return data
    },
  },
})