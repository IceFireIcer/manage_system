import { reactive } from 'vue'

export const requestTrace = reactive({
  id: '',
  at: '',
})

export function requestIdOf(error) {
  const fromHeader = error?.response?.headers?.['x-request-id']
  const fromBody = error?.response?.data?.request_id
  const value = fromHeader || fromBody
  return value ? String(value) : ''
}

export function recordRequestId(value) {
  const id = value ? String(value) : ''
  if (!id) return ''
  requestTrace.id = id
  requestTrace.at = new Date().toISOString()
  return id
}

export async function copyRequestId(value = requestTrace.id) {
  if (!value) return false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch (error) {
    void error
  }
  try {
    const area = document.createElement('textarea')
    area.value = value
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const copied = document.execCommand('copy')
    document.body.removeChild(area)
    return copied
  } catch {
    return false
  }
}