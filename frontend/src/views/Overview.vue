<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import InventoryAuditDrawer from '../components/InventoryAuditDrawer.vue'
import InventoryCountDialog from '../components/InventoryCountDialog.vue'
import RestockDialog from '../components/RestockDialog.vue'
import OverviewGettingStarted from '../components/OverviewGettingStarted.vue'
import { useAlertsStore } from '../stores/alerts'
import { useUserStore } from '../stores/user'
import { businessToday } from '../utils/businessDate'

const POLL_INTERVAL_MS = 30000
const SEARCH_DEBOUNCE_MS = 300

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const alertsStore = useAlertsStore()

const isStudent = computed(() => userStore.user?.role === 'student')
const isAdmin = computed(() => userStore.user?.role === 'admin')
const canAudit = computed(() => ['teacher', 'admin'].includes(userStore.user?.role))
const canManageAlerts = computed(() => isAdmin.value)

const ALERT_STATUS_META = {
  open: { label: '待处理', tagClass: 'status-tag--warning' },
  acknowledged: { label: '已确认', tagClass: 'status-tag--info' },
  completed: { label: '已完成', tagClass: 'status-tag--success' },
}

const summary = ref({ total_kinds: 0, low_stock_count: 0, today_requisitions: 0 })
const items = ref([])

const initialLoading = ref(true)
const loadError = ref(false)
const refreshing = ref(false)
const pollFailed = ref(false)
const lastUpdated = ref(null)

const searchInput = ref('')
const appliedSearch = ref('')
const statusFilter = ref('all')

const setup = reactive({
  loaded: false,
  failed: false,
  totalKinds: 0,
  enabledKinds: 0,
  enabledStudents: 0,
  disabledStudents: 0,
})

const audit = reactive({
  visible: false,
  consumableId: null,
  consumableName: '',
})

const receive = reactive({
  visible: false,
  row: null,
})

const inventoryCount = reactive({
  visible: false,
})

const viewportWidth = ref(window.innerWidth)
const isMobile = computed(() => viewportWidth.value < 768)

let pollTimer = null
let searchTimer = null

const statusOptions = [
  { value: 'all', label: '全部' },
  { value: 'out', label: '缺货' },
  { value: 'low', label: '低于安全库存线' },
  { value: 'uncovered', label: '未设预警线' },
]

const hasFilters = computed(() => Boolean(appliedSearch.value.trim()) || statusFilter.value !== 'all')

const filteredItems = computed(() => {
  const keyword = appliedSearch.value.trim().toLowerCase()
  return items.value.filter((row) => {
    if (statusFilter.value === 'out' && !row.is_out) return false
    if (statusFilter.value === 'low' && !row.low_stock) return false
    if (statusFilter.value === 'uncovered' && row.safety_stock > 0) return false
    if (!keyword) return true
    return [row.name, row.spec, row.location].some((field) =>
      String(field ?? '')
        .toLowerCase()
        .includes(keyword),
    )
  })
})

const safetyCoverage = computed(() => {
  const total = items.value.length
  const covered = items.value.filter((row) => row.safety_stock > 0).length
  return { total, covered, missing: total - covered }
})

const hasItems = computed(() => items.value.length > 0)

const currentStatusLabel = computed(
  () => statusOptions.find((item) => item.value === statusFilter.value)?.label ?? '全部',
)

const emptyState = computed(() => {
  if (filteredItems.value.length > 0) return null

  if (!hasItems.value) {
    if (isAdmin.value && !setup.loaded && !setup.failed) {
      return null
    }
    if (isAdmin.value && setup.loaded) {
      if (setup.totalKinds === 0) {
        return {
          title: '还没有耗材档案',
          description: '先建立耗材档案并填写期初数量，保存后这里会显示库存。',
          actions: [{ label: '去建档', handler: goArchive }],
        }
      }
      if (setup.enabledKinds === 0) {
        return {
          title: '当前没有启用耗材',
          description: '同类别的耗材档案已全部停用，启用后总览会重新显示库存。',
          actions: [{ label: '查看耗材档案', handler: goArchive }],
        }
      }
    }
    if (isAdmin.value) {
      return {
        title: '暂时无法读取耗材状态',
        description: '请检查网络连接后重试。',
        actions: [{ label: '重试', handler: retry }],
      }
    }
    return {
      title: '当前没有启用耗材',
      description: '请联系班委确认是否有可领用的耗材。',
      actions: [],
    }
  }

  if (appliedSearch.value.trim() && statusFilter.value !== 'all') {
    return {
      title: '没有符合条件的耗材',
      description: `关键词「${appliedSearch.value.trim()}」，状态「${currentStatusLabel.value}」。`,
      actions: [{ label: '清除筛选', handler: clearFilters }],
    }
  }

  if (appliedSearch.value.trim()) {
    return {
      title: '没有找到匹配的耗材',
      description: `没有名称、规格或位置包含「${appliedSearch.value.trim()}」的耗材。`,
      actions: [{ label: '清除筛选', handler: clearFilters }],
    }
  }

  return {
    title: '当前没有符合该状态的耗材',
    description: '可以切回全部，继续查看其他库存。',
    actions: [{ label: '查看全部', handler: clearFilters }],
  }
})


const updatedLabel = computed(() => {
  if (!lastUpdated.value) return ''
  return formatClock(lastUpdated.value)
})

const summaryReady = computed(
  () => !initialLoading.value && (!loadError.value || Boolean(lastUpdated.value)),
)

let overviewSeq = 0
let active = true

async function fetchOverview({ silent = false } = {}) {
  const seq = ++overviewSeq
  if (!silent) {
    refreshing.value = true
  }
  try {
    const { data } = await api.get('/inventory/overview')
    if (seq !== overviewSeq || !active) return
    summary.value = {
      total_kinds: data.summary?.total_kinds ?? 0,
      low_stock_count: data.summary?.low_stock_count ?? 0,
      today_requisitions: data.summary?.today_requisitions ?? 0,
    }
    items.value = Array.isArray(data.items) ? data.items : []
    lastUpdated.value = new Date()
    loadError.value = false
    pollFailed.value = false
  } catch {
    if (seq !== overviewSeq || !active) return
    if (lastUpdated.value) {
      pollFailed.value = true
    } else {
      loadError.value = true
    }
  } finally {
    if (seq === overviewSeq) {
      initialLoading.value = false
      refreshing.value = false
    }
  }
}

async function fetchSetup() {
  if (!isAdmin.value) return
  setup.failed = false
  try {
    const [consumablesResponse, enabledStudentsResponse, disabledStudentsResponse] =
      await Promise.all([
        api.get('/consumables', { params: { status: 'all' } }),
        api.get('/users', { params: { role: 'student', status: 'enabled' } }),
        api.get('/users', { params: { role: 'student', status: 'disabled' } }),
      ])
    const consumablesList = Array.isArray(consumablesResponse.data) ? consumablesResponse.data : []
    const enabledStudentsList = Array.isArray(enabledStudentsResponse.data)
      ? enabledStudentsResponse.data
      : []
    const disabledStudentsList = Array.isArray(disabledStudentsResponse.data)
      ? disabledStudentsResponse.data
      : []
    setup.totalKinds = consumablesList.length
    setup.enabledKinds = consumablesList.filter((item) => item.status === 'enabled').length
    setup.enabledStudents = enabledStudentsList.length
    setup.disabledStudents = disabledStudentsList.length
    setup.loaded = true
  } catch {
    setup.loaded = false
    setup.failed = true
  }
}

function applySearch() {
  appliedSearch.value = searchInput.value
}

function handleSearchInput() {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(applySearch, SEARCH_DEBOUNCE_MS)
}

function setStatus(value) {
  statusFilter.value = value
}

function clearFilters() {
  searchInput.value = ''
  appliedSearch.value = ''
  statusFilter.value = 'all'
}


function showLowStock() {
  searchInput.value = ''
  appliedSearch.value = ''
  statusFilter.value = 'low'
}

function goArchive() {
  router.push('/consumables')
}

function goSuggestion(row) {
  router.push({ path: '/restock', query: { consumable_id: String(row.id) } })
}

function goAlertRequisition(alert) {
  router.push({
    path: '/requisition',
    query: { consumable_id: String(alert.consumable_id) },
  })
}

function goAlertSuggestion(alert) {
  router.push({ path: '/restock', query: { consumable_id: String(alert.consumable_id) } })
}

function alertStatusMeta(alert) {
  return ALERT_STATUS_META[alert.status] ?? ALERT_STATUS_META.open
}

function formatAlertTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(5, 16)
}

async function actOnAlert(alert, action) {
  try {
    await alertsStore.act(alert.consumable_id, action)
    const messages = {
      acknowledge: '已确认待办',
      complete: '待办已完成',
      retrigger: '已重新触发待办',
    }
    ElMessage.success(messages[action] || '操作完成')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败，请重试')
  }
}

function scrollToAlerts() {
  document
    .getElementById('stock-alerts')
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function goArchiveItem(row) {
  router.push({ path: '/consumables', query: { consumable_id: String(row.id) } })
}

function showUncovered() {
  searchInput.value = ''
  appliedSearch.value = ''
  statusFilter.value = 'uncovered'
}

function goUsers(query) {
  router.push({ path: '/users', query: query ?? {} })
}

function goRequisition(row) {
  const query = row?.id ? { consumable_id: String(row.id) } : {}
  router.push({ path: '/requisition', query })
}

function goTodayRecords() {
  const today = businessToday()
  router.push({
    path: '/records',
    query: { start_date: today, end_date: today, type: 'normal', status: 'active' },
  })
}

function openAudit(row) {
  audit.consumableId = row.id
  audit.consumableName = row.name
  audit.visible = true
}

function handleAuditSource(event) {
  if (!event) return
  audit.visible = false
  if (event.requisition_id) {
    router.push({
      path: '/records',
      query: { requisition_id: String(event.requisition_id) },
    })
    return
  }
  if (event.order_no) {
    router.push({ path: '/restock', query: { order_no: event.order_no } })
  }
}

function openReceive(row) {
  receive.row = row
  receive.visible = true
}

function handleReceived() {
  fetchOverview()
  alertsStore.fetch()
}

function formatClock(value) {
  const pad = (number) => String(number).padStart(2, '0')
  return `${pad(value.getHours())}:${pad(value.getMinutes())}`
}

function formatQuantity(value, unit) {
  const amount = value ?? 0
  return unit ? `${amount} ${unit}` : String(amount)
}

function statusClass(row) {
  if (row.is_out) return 'status-tag--out'
  if (row.low_stock) return 'status-tag--low'
  return 'status-tag--success'
}

function statusLabel(row) {
  if (row.is_out) return '缺货'
  if (row.low_stock) return '低库存'
  return '正常'
}

function safetyLabel(row) {
  if (row.safety_stock) return ''
  if (row.is_out) return '未设安全库存线，缺货不进入补货建议'
  return '未设安全库存线'
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

function retry() {
  loadError.value = false
  initialLoading.value = true
  fetchOverview()
  if (isAdmin.value) fetchSetup()
}

onMounted(() => {
  fetchOverview()
  fetchSetup()
  if (canAudit.value) {
    alertsStore.fetch()
  }
  pollTimer = setInterval(() => fetchOverview({ silent: true }), POLL_INTERVAL_MS)
  window.addEventListener('resize', handleResize)
  if (route.query.focus === 'alerts') {
    nextTick(() => setTimeout(scrollToAlerts, 200))
  }
})

onBeforeUnmount(() => {
  active = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="overview">
    <PageHeader title="库存总览">
      <template #actions>
        <el-button v-if="isStudent" @click="router.push('/records')">查看我的记录</el-button>
        <el-button :loading="refreshing" @click="fetchOverview()">
          <el-icon :size="16"><Refresh /></el-icon>
          <span class="overview__button-text">刷新</span>
        </el-button>
        <el-button v-if="isAdmin" @click="inventoryCount.visible = true">库存盘点</el-button>
        <el-button type="primary" @click="goRequisition()">登记领用</el-button>
      </template>
    </PageHeader>

    <OverviewGettingStarted
      v-if="isAdmin"
      :loaded="setup.loaded"
      :total-kinds="setup.totalKinds"
      :enabled-kinds="setup.enabledKinds"
      :enabled-students="setup.enabledStudents"
      :disabled-students="setup.disabledStudents"
      @go-archive="goArchive"
      @go-users="goUsers"
    />

    <div
      v-if="!isStudent"
      class="overview__metrics"
      aria-label="班级库存指标"
      :aria-busy="initialLoading"
    >
      <component
        :is="isAdmin ? 'button' : 'div'"
        :type="isAdmin ? 'button' : undefined"
        class="overview__metric overview__metric--kinds"
        :class="{ 'overview__metric--clickable': isAdmin }"
        :aria-label="isAdmin ? '查看耗材档案' : undefined"
        @click="isAdmin && goArchive()"
      >
        <span class="overview__metric-value">
          {{ summaryReady ? summary.total_kinds : '—' }}
        </span>
        <span class="overview__metric-label">耗材总类数</span>
      </component>

      <button
        type="button"
        class="overview__metric overview__metric--warning overview__metric--clickable"
        aria-label="查看低于安全库存线的耗材"
        @click="showLowStock"
      >
        <span class="overview__metric-value">
          {{ summaryReady ? summary.low_stock_count : '—' }}
        </span>
        <span class="overview__metric-label">低库存预警数</span>
      </button>

      <button
        type="button"
        class="overview__metric overview__metric--clickable"
        aria-label="查看未设安全库存线的耗材"
        @click="showUncovered"
      >
        <span class="overview__metric-value">
          {{ summaryReady ? `${safetyCoverage.missing}/${safetyCoverage.total}` : '—' }}
        </span>
        <span class="overview__metric-label">预警配置未覆盖 / 总类数</span>
      </button>

      <button
        type="button"
        class="overview__metric overview__metric--clickable"
        aria-label="查看今天的领用记录"
        @click="goTodayRecords"
      >
        <span class="overview__metric-value">
          {{ summaryReady ? summary.today_requisitions : '—' }}
        </span>
        <span class="overview__metric-label">今日领用笔数</span>
      </button>
    </div>

    <section
      v-if="canAudit"
      id="stock-alerts"
      class="panel overview__alerts"
      aria-label="站内待办"
    >
      <div class="overview__alerts-head">
        <div>
          <h2 class="overview__alerts-title">站内待办</h2>
          <p class="overview__alerts-desc">
            低库存提醒按耗材去重，持续缺货只累计巡检次数；部分到货保留待办，风险解除后自动完成。
          </p>
        </div>
        <div class="overview__alerts-tools">
          <span class="status-tag status-tag--warning">
            待处理 {{ alertsStore.activeCount }}
          </span>
          <label class="overview__alerts-switch">
            <el-switch
              :model-value="alertsStore.showCompleted"
              size="small"
              @change="alertsStore.setShowCompleted"
            />
            显示已完成
          </label>
        </div>
      </div>

      <EmptyState
        v-if="!alertsStore.visibleItems.length"
        title="当前没有待处理提醒"
        description="库存低于安全库存线时会自动生成站内待办。"
      />

      <div v-else class="overview__alert-list">
        <article
          v-for="alert in alertsStore.visibleItems"
          :key="alert.consumable_id"
          class="overview__alert"
        >
          <div class="overview__alert-head">
            <span
              class="status-tag"
              :class="alert.risk_level === 'out' ? 'status-tag--out' : 'status-tag--low'"
            >
              {{ alert.risk_level === 'out' ? '缺货' : '低库存' }}
            </span>
            <strong class="overview__alert-name">{{ alert.consumable_name }}</strong>
            <span class="status-tag" :class="alertStatusMeta(alert).tagClass">
              {{ alertStatusMeta(alert).label }}
            </span>
          </div>
          <p class="overview__alert-line">
            当前 {{ formatQuantity(alert.current_stock, alert.unit) }} / 安全线
            {{ formatQuantity(alert.safety_stock, alert.unit) }}，缺口
            {{ formatQuantity(alert.gap, alert.unit) }}
            <span v-if="alert.partial_arrival">· 部分到货保留待办</span>
            <span v-if="alert.last_order_no">· 入库单 {{ alert.last_order_no }}</span>
          </p>
          <p class="overview__alert-meta">
            第 {{ alert.occurrence }} 次触发 · 已巡检 {{ alert.detection_count }} 次 · 最近
            {{ formatAlertTime(alert.last_detected_at) }}
            <span v-if="alert.acknowledged_by_name">· {{ alert.acknowledged_by_name }} 已确认</span>
          </p>
          <div class="overview__alert-actions">
            <el-button link type="primary" @click="goAlertRequisition(alert)">登记领用</el-button>
            <el-button link type="primary" @click="goAlertSuggestion(alert)">补货建议</el-button>
            <template v-if="canManageAlerts">
              <el-button
                v-if="alert.status === 'open'"
                link
                type="primary"
                @click="actOnAlert(alert, 'acknowledge')"
              >
                确认
              </el-button>
              <el-button
                v-if="alert.status !== 'completed'"
                link
                type="primary"
                @click="actOnAlert(alert, 'complete')"
              >
                完成
              </el-button>
              <el-button
                v-if="alert.status === 'completed'"
                link
                type="primary"
                @click="actOnAlert(alert, 'retrigger')"
              >
                重新触发
              </el-button>
            </template>
          </div>
        </article>
      </div>
    </section>

    <section class="panel overview__inventory">
      <div class="toolbar overview__filters">
        <el-input
          v-model="searchInput"
          class="overview__search"
          placeholder="搜索名称、规格或位置"
          clearable
          aria-label="搜索名称、规格或位置"
          @input="handleSearchInput"
          @keyup.enter="applySearch"
          @clear="applySearch"
        >
          <template #prefix>
            <el-icon :size="16"><Search /></el-icon>
          </template>
        </el-input>

        <div class="overview__segmented" role="group" aria-label="按库存状态筛选">
          <button
            v-for="option in statusOptions"
            :key="option.value"
            type="button"
            class="overview__segment"
            :class="{ 'overview__segment--active': statusFilter === option.value }"
            :aria-pressed="statusFilter === option.value"
            @click="setStatus(option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <el-button v-if="hasFilters" link type="primary" @click="clearFilters">清除筛选</el-button>
      </div>

      <div class="overview__meta">
        <span v-if="refreshing && lastUpdated" class="overview__meta-item">正在刷新…</span>
        <span v-if="updatedLabel" class="overview__meta-item">
          <span v-if="pollFailed" class="status-tag status-tag--warning">显示上次结果</span>
          <span>上次更新 {{ updatedLabel }}</span>
        </span>
        <span v-if="!lastUpdated && !loadError" class="overview__meta-item">正在加载库存…</span>
      </div>

      <div
        v-if="initialLoading || (isAdmin && !setup.loaded && !setup.failed)"
        class="overview__skeleton"
        aria-busy="true"
        aria-label="正在加载库存"
      >
        <el-skeleton :rows="6" animated />
      </div>

      <EmptyState
        v-else-if="loadError && !lastUpdated"
        title="暂时无法读取库存"
        description="请检查网络连接后重试，库存数据以服务端返回为准。"
      >
        <template #action>
          <el-button type="primary" @click="retry">重试</el-button>
        </template>
      </EmptyState>

      <EmptyState
        v-else-if="emptyState"
        :title="emptyState.title"
        :description="emptyState.description"
      >
        <template v-if="emptyState.actions.length" #action>
          <el-button
            v-for="action in emptyState.actions"
            :key="action.label"
            type="primary"
            plain
            @click="action.handler"
          >
            {{ action.label }}
          </el-button>
        </template>
      </EmptyState>

      <div v-else-if="!isMobile" class="overview__table-wrap">
        <el-table :data="filteredItems" class="table-dense overview__table" row-key="id">
          <el-table-column label="状态" width="96">
            <template #default="{ row }">
              <span class="status-tag" :class="statusClass(row)">{{ statusLabel(row) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="耗材 / 规格" min-width="180">
            <template #default="{ row }">
              <div class="overview__name">{{ row.name }}</div>
              <div class="overview__spec">{{ row.spec || '—' }}</div>
            </template>
          </el-table-column>

          <el-table-column label="存放位置" min-width="120">
            <template #default="{ row }">{{ row.location || '—' }}</template>
          </el-table-column>

          <el-table-column label="当前库存" width="120" align="right">
            <template #default="{ row }">{{ formatQuantity(row.current_stock, row.unit) }}</template>
          </el-table-column>

          <el-table-column label="安全库存线" width="160" align="right">
            <template #default="{ row }">
              <div>{{ formatQuantity(row.safety_stock, row.unit) }}</div>
              <div v-if="safetyLabel(row)" class="overview__hint">{{ safetyLabel(row) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="300" align="right">
            <template #default="{ row }">
              <div class="overview__row-actions">
                <el-button
                  link
                  type="primary"
                  :disabled="row.is_out"
                  @click="goRequisition(row)"
                >
                  登记领用
                </el-button>
                <span v-if="row.is_out" class="overview__hint overview__hint--out">当前缺货</span>
                <el-button v-if="isAdmin" link type="primary" @click="openReceive(row)">
                  收货
                </el-button>
                <el-button v-if="row.is_out && canAudit" link type="primary" @click="goSuggestion(row)">
                  补货建议
                </el-button>
                <el-button v-if="row.is_out && isAdmin" link type="primary" @click="goArchiveItem(row)">
                  档案
                </el-button>
                <el-button v-if="canAudit" link type="primary" @click="openAudit(row)">
                  查库存变动
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <ul v-else class="overview__cards">
        <li v-for="row in filteredItems" :key="row.id" class="overview__card">
          <div class="overview__card-head">
            <span class="status-tag" :class="statusClass(row)">{{ statusLabel(row) }}</span>
            <span class="overview__card-name">{{ row.name }}</span>
          </div>
          <div class="overview__card-spec">{{ row.spec || '—' }}</div>
          <div class="overview__card-line">
            <span class="overview__card-label">存放位置</span>
            <span>{{ row.location || '—' }}</span>
          </div>
          <div class="overview__card-line">
            <span class="overview__card-label">当前库存 · 安全库存线</span>
            <span>
              {{ formatQuantity(row.current_stock, row.unit) }} ·
              {{ formatQuantity(row.safety_stock, row.unit) }}
            </span>
          </div>
          <div v-if="safetyLabel(row)" class="overview__hint">{{ safetyLabel(row) }}</div>
          <div class="overview__card-actions">
            <el-button
              type="primary"
              size="small"
              :disabled="row.is_out"
              @click="goRequisition(row)"
            >
              登记领用
            </el-button>
            <span v-if="row.is_out" class="overview__hint overview__hint--out">当前缺货</span>
            <el-button v-if="isAdmin" size="small" @click="openReceive(row)">收货</el-button>
            <el-button v-if="row.is_out && canAudit" size="small" @click="goSuggestion(row)">
              补货建议
            </el-button>
            <el-button v-if="row.is_out && isAdmin" size="small" @click="goArchiveItem(row)">
              档案
            </el-button>
            <el-button v-if="canAudit" size="small" @click="openAudit(row)">查库存变动</el-button>
          </div>
        </li>
      </ul>
    </section>

    <InventoryAuditDrawer
      v-model="audit.visible"
      :consumable-id="audit.consumableId"
      :consumable-name="audit.consumableName"
      @view-source="handleAuditSource"
    />

    <RestockDialog
      v-model="receive.visible"
      :consumable="receive.row"
      @completed="handleReceived"
    />

    <InventoryCountDialog
      v-model="inventoryCount.visible"
      :consumables="items"
      @completed="fetchOverview"
    />
  </div>
</template>

<style scoped>
.overview__button-text {
  margin-left: 4px;
}

.overview__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.overview__metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--surface);
  text-align: left;
  font-family: inherit;
}

.overview__metric--clickable {
  cursor: pointer;
  transition: background-color var(--duration-hover) ease;
}

.overview__metric--clickable:hover {
  background: var(--primary-soft);
}

.overview__metric-value {
  font-size: 28px;
  line-height: 36px;
  font-weight: 700;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.overview__metric--warning .overview__metric-value {
  color: var(--warning);
}

.overview__metric-label {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__alerts {
  margin-bottom: 24px;
  padding: 20px 24px;
}

.overview__alerts-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.overview__alerts-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.overview__alerts-desc {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__alerts-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.overview__alerts-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
}

.overview__alert-list {
  display: grid;
  gap: 12px;
}

.overview__alert {
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.overview__alert-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.overview__alert-name {
  font-size: 14px;
  color: var(--text);
}

.overview__alert-line {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}

.overview__alert-meta {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.overview__alert-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.overview__inventory {
  padding: 24px;
}

.overview__filters {
  align-items: center;
  gap: 16px;
}

.overview__search {
  width: 280px;
  max-width: 100%;
}

.overview__segmented {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 2px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.overview__segment {
  min-height: 36px;
  padding: 4px 12px;
  border: none;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  cursor: pointer;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease;
}

.overview__segment:hover {
  color: var(--text);
}

.overview__segment--active {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}

.overview__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-height: 22px;
  margin: 12px 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__meta-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.overview__skeleton {
  padding: 8px 0;
}

.overview__table-wrap {
  width: 100%;
  overflow-x: auto;
}

.overview__name {
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--text);
}

.overview__spec {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__hint {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__hint--out {
  color: var(--danger);
}

.overview__row-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.overview__cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.overview__card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.overview__card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.overview__card-name {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.overview__card-spec {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview__card-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-secondary);
}

.overview__card-label {
  flex: 0 0 auto;
  font-size: 13px;
  color: var(--text-muted);
}

.overview__card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
}

@media (max-width: 1279px) {
  .overview__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview__search {
    width: 240px;
  }
}

@media (max-width: 767px) {
  .overview__metrics {
    grid-template-columns: minmax(0, 1fr);
  }

  .overview__inventory {
    padding: 16px;
  }

  .overview__filters {
    flex-direction: column;
    align-items: stretch;
  }

  .overview__search {
    width: 100%;
  }

  .overview__segmented {
    width: 100%;
  }

  .overview__segment {
    flex: 1;
  }
}
</style>