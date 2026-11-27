<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../stores/user'
import { copyRequestId, requestTrace } from '../utils/requestTrace'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue'])

const userStore = useUserStore()
const copyState = ref('')
let previousFocus = null

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const role = computed(() => userStore.user?.role ?? 'guest')

const roleName = computed(
  () => ({ student: '学生', teacher: '教师', admin: '班委', guest: '新用户' })[role.value],
)

const studentSteps = [
  '使用班委或教师分配的账号登录；首次登录必须按提示修改初始密码。',
  '进入「领用登记」，选择耗材和数量，领用人会自动填为本人。',
  '提交后页面会显示回执与领用单号，也可到「我的记录」查回执和红冲情况。',
  '账号无法登录或提示已停用时，请联系班委核对账号状态，不要重复注册。',
  '遇到页面报错，请复制请求编号并发给班委或教师。',
]

const staffSteps = [
  '使用分配的账号登录；首次登录必须修改初始密码。',
  '教师可在「记录查询」查看、红冲或处理纠错申请。',
  '班委还可在「耗材档案」「用户管理」「处置入库」「操作日志」维护数据和核查。',
  '学生账号停用或恢复由班委处理；教师账号问题请联系部署方或系统管理员。',
  '遇到页面报错，请复制请求编号，交给能查看日志的班委或部署方。',
]

const startGroups = computed(() => {
  if (role.value === 'student') {
    return [{ key: 'student', title: '学生首次使用', steps: studentSteps }]
  }
  if (role.value === 'teacher' || role.value === 'admin') {
    return [{ key: 'staff', title: `${roleName.value}首次使用`, steps: staffSteps }]
  }
  return [
    { key: 'student', title: '学生首次使用', steps: studentSteps },
    { key: 'staff', title: '教师和班委首次使用', steps: staffSteps },
  ]
})

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      copyState.value = ''
      previousFocus = document.activeElement
    }
  },
)

function handleClosed() {
  const target = previousFocus
  previousFocus = null
  if (target?.isConnected) target.focus?.()
}

async function handleCopy() {
  if (!requestTrace.id) return
  const ok = await copyRequestId(requestTrace.id)
  copyState.value = ok ? '已复制请求编号' : '复制失败，请手动选择编号'
  if (ok) ElMessage.success('请求编号已复制')
  else ElMessage.error('复制失败，请手动选择请求编号')
}

function formatTraceTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part) => String(part).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`
}
</script>

<template>
  <el-drawer
    v-model="visible"
    title="使用帮助与故障联络"
    size="min(420px, 100%)"
    class="help-drawer"
    @closed="handleClosed"
  >
    <div class="help">
      <p class="help__lead">当前身份：{{ roleName }}。下面的步骤可用于首次使用和班委交接。</p>

      <section
        v-for="group in startGroups"
        :key="group.key"
        class="help__section"
        :aria-labelledby="`help-${group.key}-title`"
      >
        <h2 :id="`help-${group.key}-title`" class="help__title">{{ group.title }}</h2>
        <ol class="help__steps">
          <li v-for="step in group.steps" :key="step" class="help__step">{{ step }}</li>
        </ol>
      </section>

      <section class="help__section" aria-labelledby="help-disabled-title">
        <h2 id="help-disabled-title" class="help__title">账号停用</h2>
        <p class="help__text">
          学生账号停用后无法登录，历史领用记录会保留。请联系班委核对状态并恢复；教师或班委账号异常请联系部署方。
        </p>
      </section>

      <section class="help__section" aria-labelledby="help-fault-title">
        <h2 id="help-fault-title" class="help__title">故障联络</h2>
        <p class="help__text">
          页面提示、提交错误或“暂未确认”信息会同时给出请求编号。复制编号并发给班委或教师，可帮助他们定位本次请求。
        </p>
        <div class="help__trace" :class="{ 'help__trace--empty': !requestTrace.id }">
          <template v-if="requestTrace.id">
            <span class="help__trace-label">最近一次请求编号</span>
            <code class="help__trace-code" tabindex="0">{{ requestTrace.id }}</code>
            <span v-if="formatTraceTime(requestTrace.at)" class="help__trace-time">
              记录于 {{ formatTraceTime(requestTrace.at) }}
            </span>
            <el-button type="primary" plain @click="handleCopy">复制请求编号</el-button>
          </template>
          <p v-else class="help__text">
            当前还没有记录到请求编号。出现错误提示后，这里会自动填入可复制的编号。
          </p>
        </div>
        <p class="help__text">
          全站无法打开时，请向部署方说明发生时间，并提供请求编号；可在「操作日志」按时间核对操作。
        </p>
        <p v-if="copyState" class="help__status" role="status">{{ copyState }}</p>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.help {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 0 4px 8px;
}

.help__lead {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.help__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.help__title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.help__steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding-left: 20px;
}

.help__step,
.help__text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.help__trace {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface-muted);
}

.help__trace--empty {
  background: var(--surface);
}

.help__trace-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.help__trace-code {
  max-width: 100%;
  padding: 4px 8px;
  border-radius: var(--radius-control);
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  line-height: 22px;
  word-break: break-all;
  user-select: all;
}

.help__trace-time {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.help__status {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--success);
}

@media (max-width: 767px) {
  .help__trace .el-button {
    width: 100%;
    min-height: 44px;
  }
}
</style>