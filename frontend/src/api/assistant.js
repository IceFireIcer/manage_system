import api from './index'

export function fetchAssistantStatus() {
  return api.get('/assistant/status')
}

export function fetchThreads(includeArchived = false) {
  return api.get('/assistant/threads', {
    params: { include_archived: includeArchived },
  })
}

export function createThread(title = null) {
  return api.post('/assistant/threads', title ? { title } : {})
}

export function fetchThreadDetail(threadId) {
  return api.get(`/assistant/threads/${threadId}`)
}

export function deleteThread(threadId) {
  return api.delete(`/assistant/threads/${threadId}`)
}

export function sendThreadMessage(threadId, payload) {
  return api.post(`/assistant/threads/${threadId}/messages`, payload, {
    timeout: 60000,
  })
}
export function confirmAssistantAction(token) {
  return api.post(`/assistant/actions/${token}/confirm`, {}, { timeout: 30000 })
}

export function cancelAssistantAction(token) {
  return api.post(`/assistant/actions/${token}/cancel`, {}, { timeout: 30000 })
}