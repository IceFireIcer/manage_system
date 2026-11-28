<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Clock, Close, Delete, Plus } from '@element-plus/icons-vue'
import { useUserStore } from '../stores/user'
import { renderMarkdown } from '../utils/markdown'
import {
  cancelAssistantAction,
  confirmAssistantAction,
  createThread,
  deleteThread,
  fetchAssistantStatus,
  fetchThreadDetail,
  fetchThreads,
  sendThreadMessage,
} from '../api/assistant'

const OPEN_KEY = 'assistant-open'
const ACTION_STATUS = {
  pending: { label: '待确认', type: 'warning' },
  prefilled: { label: '可前往', type: 'info' },
  confirmed: { label: '已执行', type: 'success' },
  cancelled: { label: '已取消', type: 'info' },
  failed: { label: '执行失败', type: 'danger' },
}
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const open = ref(false)
const isMobile = ref(window.innerWidth < 768)
const configured = ref(true)
const statusMessage = ref('')
const threads = ref([])
const activeThreadId = ref(null)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const showHistory = ref(false)
const scrollRef = ref(null)
const actions = ref([])
const confirmingToken = ref('')
const cancellingToken = ref('')

const roleName = computed(() => userStore.roleName ?? '')
const className = computed(() => userStore.activeClass?.name ?? '未选择班级')
const currentPageTitle = computed(() => route.meta?.title ?? '当前页面')

function persistOpen(value) {
  try {
    localStorage.setItem(OPEN_KEY, value ? '1' : '0')
  } catch {
    /* 忽略存储异常 */
  }
}

function togglePanel() {
  open.value = !open.value
  persistOpen(open.value)
}

function closePanel() {
  open.value = false
  persistOpen(false)
}

function handleResize() {
  isMobile.value = window.innerWidth < 768
}

async function scrollToBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadStatus() {
  try {
    const { data } = await fetchAssistantStatus()
    configured.value = Boolean(data.configured)
    statusMessage.value = data.message ?? ''
  } catch {
    configured.value = true
  }
}

async function loadThreads() {
  try {
    const { data } = await fetchThreads()
    threads.value = Array.isArray(data) ? data : []
  } catch {
    threads.value = []
  }
}

async function selectThread(threadId) {
  showHistory.value = false
  try {
    const { data } = await fetchThreadDetail(threadId)
    activeThreadId.value = data.thread.id
    messages.value = (data.messages ?? []).map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      toolCalls: item.tool_calls ?? [],
    }))
    actions.value = Array.isArray(data.actions) ? data.actions : []
    await scrollToBottom()
  } catch {
    messages.value = []
    actions.value = []
  }
}

function startNewChat() {
  activeThreadId.value = null
  messages.value = []
  actions.value = []
  showHistory.value = false
  draft.value = ''
}

function mergeActions(list) {
  if (!Array.isArray(list) || !list.length) return
  const merged = [...actions.value]
  for (const item of list) {
    if (!item?.token) continue
    const index = merged.findIndex((action) => action.token === item.token)
    if (index === -1) merged.push(item)
    else merged[index] = item
  }
  merged.sort((a, b) => String(a.created_at ?? '').localeCompare(String(b.created_at ?? '')))
  actions.value = merged
}

function upsertAction(action) {
  if (!action?.token) return
  mergeActions([action])
}

function actionStatus(action) {
  return ACTION_STATUS[action.status] ?? { label: action.status, type: 'info' }
}

function actionResultText(action) {
  if (action.status === 'failed') return action.error || '执行失败'
  if (action.status === 'cancelled') return '已取消'
  if (action.status !== 'confirmed') return ''
  const result = action.result ?? {}
  if (result.incident_id) return `已提交异常单 #${result.incident_id}`
  if (result.changed === false) return '资料没有变化'
  if (Array.isArray(result.fields) && result.fields.length) return '资料已更新'
  return '已执行'
}

async function confirmAction(action) {
  if (confirmingToken.value || cancellingToken.value) return
  confirmingToken.value = action.token
  try {
    const { data } = await confirmAssistantAction(action.token)
    upsertAction(data)
    if (data.status === 'confirmed') ElMessage.success('操作已执行')
    else if (data.status === 'failed') ElMessage.error(data.error || '操作执行失败')
    await scrollToBottom()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '确认失败，请稍后重试')
  } finally {
    confirmingToken.value = ''
  }
}

async function cancelAction(action) {
  if (confirmingToken.value || cancellingToken.value) return
  cancellingToken.value = action.token
  try {
    const { data } = await cancelAssistantAction(action.token)
    upsertAction(data)
    ElMessage.success('操作已取消')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '取消失败，请稍后重试')
  } finally {
    cancellingToken.value = ''
  }
}

function goAction(action) {
  if (!action.route) return
  router.push({ path: action.route, query: action.query ?? {} })
  if (isMobile.value) closePanel()
}

async function removeThread(thread) {
  try {
    await deleteThread(thread.id)
  } catch {
    return
  }
  threads.value = threads.value.filter((item) => item.id !== thread.id)
  if (activeThreadId.value === thread.id) startNewChat()
}

function buildContext() {
  return {
    route: route.path,
    page: route.meta?.title ?? null,
  }
}

async function send() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  sending.value = true
  draft.value = ''
  const optimisticId = `local-${Date.now()}`
  messages.value.push({ id: optimisticId, role: 'user', content })
  await scrollToBottom()
  try {
    let threadId = activeThreadId.value
    if (!threadId) {
      const { data: thread } = await createThread()
      threadId = thread.id
      activeThreadId.value = threadId
    }
    const { data } = await sendThreadMessage(threadId, {
      content,
      context: buildContext(),
    })
    messages.value = messages.value.filter((item) => item.id !== optimisticId)
    messages.value.push({
      id: data.user_message.id,
      role: 'user',
      content: data.user_message.content,
      toolCalls: [],
    })
    messages.value.push({
      id: data.assistant_message.id,
      role: 'assistant',
      content: data.assistant_message.content,
      toolCalls: data.assistant_message.tool_calls ?? data.tool_calls ?? [],
    })
    configured.value = Boolean(data.configured)
    mergeActions(data.actions ?? [])
    await loadThreads()
    await scrollToBottom()
  } catch (error) {
    messages.value = messages.value.filter((item) => item.id !== optimisticId)
    draft.value = content
    messages.value.push({
      id: `error-${Date.now()}`,
      role: 'assistant',
      content: '消息发送失败，请稍后重试。',
      toolCalls: [],
    })
    await scrollToBottom()
  } finally {
    sending.value = false
  }
}

function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) loadThreads()
}

watch(
  () => open.value,
  (value) => {
    if (value) {
      loadThreads()
      scrollToBottom()
    }
  },
)

onMounted(async () => {
  try {
    open.value = localStorage.getItem(OPEN_KEY) === '1'
  } catch {
    open.value = false
  }
  window.addEventListener('resize', handleResize)
  await loadStatus()
  await loadThreads()
  if (open.value && threads.value.length) {
    await selectThread(threads.value[0].id)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="assistant">
    <button
      v-if="!open"
      class="assistant__launcher"
      type="button"
      aria-label="打开 AI 助手"
      @click="togglePanel"
    >
      <el-icon :size="24"><ChatDotRound /></el-icon>
    </button>

    <section
      v-else
      class="assistant__panel"
      :class="{ 'assistant__panel--full': isMobile }"
      aria-label="AI 助手"
    >
      <header class="assistant__header">
        <div class="assistant__title">
          <el-icon :size="16"><ChatDotRound /></el-icon>
          <span>AI 助手</span>
          <el-tag v-if="!configured" size="small" type="warning">未配置</el-tag>
        </div>
        <div class="assistant__tools">
          <button type="button" title="新对话" aria-label="新对话" @click="startNewChat">
            <el-icon :size="16"><Plus /></el-icon>
          </button>
          <button type="button" title="历史会话" aria-label="历史会话" @click="toggleHistory">
            <el-icon :size="16"><Clock /></el-icon>
          </button>
          <button type="button" title="收起" aria-label="收起" @click="closePanel">
            <el-icon :size="16"><Close /></el-icon>
          </button>
        </div>
      </header>

      <div class="assistant__context">
        <span class="assistant__context-page">{{ currentPageTitle }}</span>
        <span class="assistant__context-sep">·</span>
        <span>{{ roleName }}</span>
        <span class="assistant__context-sep">·</span>
        <span>{{ className }}</span>
      </div>

      <div v-if="showHistory" class="assistant__history">
        <div class="assistant__history-head">历史会话</div>
        <div v-if="!threads.length" class="assistant__history-empty">暂无会话</div>
        <button
          v-for="thread in threads"
          :key="thread.id"
          type="button"
          class="assistant__history-item"
          :class="{ 'is-active': thread.id === activeThreadId }"
          @click="selectThread(thread.id)"
        >
          <span class="assistant__history-title">{{ thread.title }}</span>
          <el-icon class="assistant__history-del" @click.stop="removeThread(thread)">
            <Delete />
          </el-icon>
        </button>
      </div>

      <div ref="scrollRef" class="assistant__messages">
        <div v-if="!messages.length && !sending && !actions.length" class="assistant__hello">
          <p>你好，我是 AI 助手。</p>
          <p>
            我可以基于当前页面与班级的聚合数据回答问题；涉及修改或领用等操作时，
            只会生成待确认卡片或预填链接，不会自动提交。
          </p>
        </div>
        <div
          v-for="message in messages"
          :key="message.id"
          class="assistant__msg"
          :class="`assistant__msg--${message.role}`"
        >
          <div class="assistant__stack">
            <div class="assistant__bubble" v-html="renderMarkdown(message.content)" />
            <div v-if="message.toolCalls?.length" class="assistant__toolcalls">
              <span
                v-for="call in message.toolCalls"
                :key="`${message.id}-${call.name}`"
                class="assistant__toolcall"
                :class="{ 'is-error': !call.ok }"
              >
                只读工具 · {{ call.label || call.name }}{{ call.ok ? '' : '（失败）' }}
              </span>
            </div>
          </div>
        </div>
        <div v-if="sending" class="assistant__msg assistant__msg--assistant">
          <div class="assistant__bubble assistant__typing">思考中…</div>
        </div>

        <div v-if="actions.length" class="assistant__actions">
          <div class="assistant__actions-head">操作</div>
          <div
            v-for="action in actions"
            :key="action.token"
            class="assistant__action"
            :class="`is-${action.status}`"
          >
            <div class="assistant__action-top">
              <span class="assistant__action-label">{{ action.label }}</span>
              <el-tag size="small" :type="actionStatus(action).type">
                {{ actionStatus(action).label }}
              </el-tag>
            </div>
            <p v-if="action.summary" class="assistant__action-summary">
              {{ action.summary }}
            </p>
            <p v-if="action.status === 'failed'" class="assistant__action-error">
              {{ actionResultText(action) }}
            </p>
            <p v-else-if="action.status === 'confirmed'" class="assistant__action-done">
              {{ actionResultText(action) }}
            </p>
            <div
              v-if="
                (action.status === 'pending' && action.requires_confirmation) ||
                (action.status === 'prefilled' && action.route)
              "
              class="assistant__action-btns"
            >
              <template v-if="action.status === 'pending' && action.requires_confirmation">
                <el-button
                  size="small"
                  type="primary"
                  :loading="confirmingToken === action.token"
                  :disabled="cancellingToken !== ''"
                  @click="confirmAction(action)"
                >
                  确认执行
                </el-button>
                <el-button
                  size="small"
                  :loading="cancellingToken === action.token"
                  :disabled="confirmingToken !== ''"
                  @click="cancelAction(action)"
                >
                  取消
                </el-button>
              </template>
              <el-button v-else size="small" type="primary" plain @click="goAction(action)">
                前往填写
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="assistant__input">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          maxlength="4000"
          placeholder="询问当前页面的聚合数据…"
          @keydown.enter.exact.prevent="send"
        />
        <el-button
          type="primary"
          :loading="sending"
          :disabled="!draft.trim()"
          @click="send"
        >
          发送
        </el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.assistant {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 40;
}

.assistant__launcher {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border: none;
  border-radius: 16px;
  background: var(--primary);
  color: #ffffff;
  cursor: pointer;
  box-shadow: 0 8px 24px rgb(23 38 60 / 22%);
  transition: transform var(--duration-hover) ease, box-shadow var(--duration-hover) ease;
}

.assistant__launcher:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgb(23 38 60 / 26%);
}

.assistant__panel {
  display: flex;
  flex-direction: column;
  width: 380px;
  height: 560px;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 16px;
  background: var(--surface);
  box-shadow: 0 18px 48px rgb(23 38 60 / 22%);
}

.assistant__panel--full {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.assistant__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line-soft);
}

.assistant__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.assistant__tools {
  display: flex;
  align-items: center;
  gap: 4px;
}

.assistant__tools button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.assistant__tools button:hover {
  background: var(--surface-muted);
  color: var(--text);
}

.assistant__context {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-muted);
  font-size: 12px;
  color: var(--text-muted);
}

.assistant__context-page {
  font-weight: 600;
  color: var(--text-secondary);
}

.assistant__context-sep {
  opacity: 0.5;
}

.assistant__history {
  max-height: 240px;
  overflow-y: auto;
  padding: 8px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-muted);
}

.assistant__history-head {
  padding: 4px 6px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.assistant__history-empty {
  padding: 8px 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.assistant__history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.assistant__history-item:hover,
.assistant__history-item.is-active {
  background: var(--primary-soft);
  color: var(--primary);
}

.assistant__history-title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.assistant__history-del {
  flex: 0 0 auto;
  color: var(--text-muted);
}

.assistant__history-del:hover {
  color: var(--danger, #e45656);
}

.assistant__messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.assistant__hello {
  margin: auto 0;
  padding: 0 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  text-align: center;
}

.assistant__msg {
  display: flex;
}

.assistant__msg--user {
  justify-content: flex-end;
}

.assistant__stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 84%;
}

.assistant__msg--user .assistant__stack {
  align-items: flex-end;
}

.assistant__toolcalls {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.assistant__toolcall {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 11px;
}

.assistant__toolcall.is-error {
  background: rgb(228 86 86 / 12%);
  color: #c0392b;
}

.assistant__bubble {
  max-width: 100%;
  padding: 9px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 20px;
  word-break: break-word;
}

.assistant__msg--assistant .assistant__bubble {
  background: var(--surface-muted);
  color: var(--text);
  border-top-left-radius: 4px;
}

.assistant__msg--user .assistant__bubble {
  background: var(--primary);
  color: #ffffff;
  border-top-right-radius: 4px;
}

.assistant__typing {
  color: var(--text-muted);
}

.assistant__actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}

.assistant__actions-head {
  font-size: 11px;
  color: var(--text-muted);
}

.assistant__action {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-muted);
}

.assistant__action.is-pending {
  border-color: var(--primary);
}

.assistant__action.is-failed {
  border-color: rgb(228 86 86 / 45%);
}

.assistant__action-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.assistant__action-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
}

.assistant__action-summary {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.assistant__action-error {
  margin: 0;
  font-size: 12px;
  color: #c0392b;
}

.assistant__action-done {
  margin: 0;
  font-size: 12px;
  color: var(--primary);
}

.assistant__action-btns {
  display: flex;
  gap: 6px;
}

.assistant__input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--line-soft);
}

.assistant__input :deep(.el-textarea__inner) {
  font-size: 13px;
}

.assistant__panel--full .assistant__messages {
  padding: 16px;
}

@media (max-width: 767px) {
  .assistant {
    right: 16px;
    bottom: calc(16px + var(--keyboard-inset, 0px));
  }

  .assistant__panel--full .assistant__input {
    padding-bottom: calc(10px + var(--keyboard-inset, 0px));
  }
}
</style>

<style>
.assistant-md__p {
  margin: 0 0 6px;
}

.assistant-md__p:last-child {
  margin-bottom: 0;
}

.assistant-md__list {
  margin: 4px 0;
  padding-left: 18px;
}

.assistant-md__pre {
  margin: 6px 0;
  padding: 8px 10px;
  overflow-x: auto;
  border-radius: 8px;
  background: var(--surface-sunken, #f2f4f8);
  font-size: 12px;
}

.assistant-md__pre code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.assistant__bubble code {
  padding: 1px 4px;
  border-radius: 4px;
  background: rgb(23 38 60 / 8%);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
</style>