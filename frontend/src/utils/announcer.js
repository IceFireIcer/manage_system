import { reactive } from 'vue'

export const announcer = reactive({
  polite: '',
  assertive: '',
})

const timers = { polite: null, assertive: null }

export function announce(message, options = {}) {
  if (!message) return
  const key = options.assertive ? 'assertive' : 'polite'
  if (timers[key]) window.clearTimeout(timers[key])
  announcer[key] = ''
  timers[key] = window.setTimeout(() => {
    announcer[key] = String(message)
    timers[key] = null
  }, 60)
}