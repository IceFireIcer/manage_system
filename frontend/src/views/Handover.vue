<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const KIND_LABELS = {
  stock: '库存异常',
  restock: '入库单',
  requisition: '领用单',
  alert: '预警',
  incident: '异常单',
  suggestion: '补货建议',
  count: '盘点',
}
const STATUS_LABELS = { pending: '待确认', confirmed: '已确认' }
const PAGE_SIZE = 20

const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref('')

const createVisible = ref(false)
const creating = ref(false)
const form = reactive({ incomingAccount: '', incomingName: '' })

const detailVisible = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const confirmingItemId = ref(null)
const confirmingAll = ref(false)

const isNarrow = ref(window.innerWidth < 600)
const drawerSize = computed(() => (isNarrow.value ? '92%' : '520px'))
const allConfirmed = computed(
  () => Boolean(detail.value) && detail.value.confirmed_count >= detail.value.item_count,
)

function onResize() {
  isNarrow.value = window.innerWidth < 600
}

function formatTime(value) {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
}

function statusLabel(status) {
  return STATUS_LABELS[status] ?? status
}

function kindLabel(kind) {
  return KIND_LABELS[kind] ?? kind
}

function statusTagType(status) {
  return status === 'confirmed' ? 'success' : 'warning'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get('/handover/checklists', {
      params: { page: page.value, page_size: PAGE_SIZE },
    })
    items.value = data.items
    total.value = data.total
  } catch (requestError) {
    error.value = requestError.response?.data?.detail ?? '加载交接清单失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.incomingAccount = ''
  form.incomingName = ''
  createVisible.value = true
}

async function submitCreate() {
  if (creating.value) return
  creating.value = true
  try {
    const payload = {
      incoming_account: form.incomingAccount.trim() || null,
      incoming_name: form.incomingName.trim() || null,
    }
    const { data } = await api.post('/handover/checklists', payload)
    createVisible.value = false
    page.value = 1
    await load()
    openDetail(data)
    ElMessage.success(`已生成交接清单 V${data.version}`)
  } catch (requestError) {
    ElMessage.error(requestError.response?.data?.detail ?? '生成交接清单失败')
  } finally {
    creating.value = false
  }
}

async function openDetail(row) {
  detailVisible.value = true
  detail.value = row.items ? row : null
  detailLoading.value = !row.items
  try {
    const { data } = await api.get(`/handover/checklists/${row.id}`)
    detail.value = data
  } catch (requestError) {
    ElMessage.error(requestError.response?.data?.detail ?? '加载清单详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function confirmItem(item) {
  if (!detail.value || item.confirmed || confirmingItemId.value) return
  confirmingItemId.value = item.id
  try {
    await api.post(
      `/handover/checklists/${detail.value.id}/items/${item.id}/confirm`,
    )
    await refreshDetail()
  } catch (requestError) {
    ElMessage.error(requestError.response?.data?.detail ?? '确认失败')
  } finally {
    confirmingItemId.value = null
  }
}

async function confirmAll() {
  if (!detail.value || confirmingAll.value) return
  if (!allConfirmed.value) {
    ElMessage.warning('请先逐项确认')
    return
  }
  confirmingAll.value = true
  try {
    await api.post(`/handover/checklists/${detail.value.id}/confirm`)
    await refreshDetail()
    await load()
    ElMessage.success('交接清单已确认')
  } catch (requestError) {
    ElMessage.error(requestError.response?.data?.detail ?? '确认清单失败')
  } finally {
    confirmingAll.value = false
  }
}

async function refreshDetail() {
  if (!detail.value) return
  const { data } = await api.get(`/handover/checklists/${detail.value.id}`)
  detail.value = data
}

function changePage(nextPage) {
  page.value = nextPage
  load()
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
  load()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <section class="handover">
    <PageHeader title="班委交接">
      <template #actions>
        <el-button type="primary" @click="openCreate">生成交接清单</el-button>
      </template>
    </PageHeader>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="handover__alert"
    />

    <div v-loading="loading" class="handover__list">
      <article v-for="row in items" :key="row.id" class="handover__card">
        <div class="handover__card-head">
          <span class="handover__version">V{{ row.version }}</span>
          <el-tag :type="statusTagType(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </div>
        <p class="handover__party">
          {{ row.outgoing_name }}（{{ row.outgoing_account }}）
          <span class="handover__arrow">→</span>
          {{ row.incoming_name || '未指定接班人' }}
          <span v-if="row.incoming_account" class="handover__muted">
            （{{ row.incoming_account }}）
          </span>
        </p>
        <p class="handover__meta">
          生成 {{ formatTime(row.snapshot_at) }} · 已确认
          {{ row.confirmed_count }}/{{ row.item_count }}
        </p>
        <p class="handover__meta">
          确认时间 {{ formatTime(row.confirmed_at) }}
          <span v-if="row.confirmed_by_name">· {{ row.confirmed_by_name }}</span>
        </p>
        <el-button size="small" @click="openDetail(row)">查看</el-button>
      </article>

      <EmptyState
        v-if="!loading && !items.length && !error"
        title="暂无交接清单"
        description="生成清单后会在此处显示"
      />
    </div>

    <el-pagination
      v-if="total > PAGE_SIZE"
      class="handover__pager"
      small
      layout="prev, pager, next"
      :current-page="page"
      :page-size="PAGE_SIZE"
      :total="total"
      @current-change="changePage"
    />

    <el-dialog v-model="createVisible" title="生成交接清单" width="min(360px, 92%)">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="接班账号">
          <el-input v-model="form.incomingAccount" placeholder="可留空" clearable />
        </el-form-item>
        <el-form-item label="接班人姓名">
          <el-input v-model="form.incomingName" placeholder="可留空" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">
          生成
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="detailVisible"
      title="交接清单详情"
      :size="drawerSize"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="handover-detail">
        <template v-if="detail">
          <div class="handover-detail__summary">
            <span class="handover__version">V{{ detail.version }}</span>
            <el-tag :type="statusTagType(detail.status)" size="small">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </div>
          <dl class="handover-detail__facts">
            <div>
              <dt>交班人</dt>
              <dd>{{ detail.outgoing_name }}（{{ detail.outgoing_account }}）</dd>
            </div>
            <div>
              <dt>接班人</dt>
              <dd>
                {{ detail.incoming_name || '未指定' }}
                <template v-if="detail.incoming_account">
                  （{{ detail.incoming_account }}）
                </template>
              </dd>
            </div>
            <div>
              <dt>生成时间</dt>
              <dd>{{ formatTime(detail.snapshot_at) }}</dd>
            </div>
            <div>
              <dt>确认时间</dt>
              <dd>
                {{ formatTime(detail.confirmed_at) }}
                <template v-if="detail.confirmed_by_name">
                  · {{ detail.confirmed_by_name }}
                </template>
              </dd>
            </div>
            <div>
              <dt>确认进度</dt>
              <dd>{{ detail.confirmed_count }}/{{ detail.item_count }}</dd>
            </div>
          </dl>

          <ul class="handover-detail__items">
            <li
              v-for="item in detail.items"
              :key="item.id"
              class="handover-detail__item"
            >
              <div class="handover-detail__item-main">
                <el-tag size="small" effect="plain">{{ kindLabel(item.kind) }}</el-tag>
                <span class="handover-detail__label">{{ item.label }}</span>
              </div>
              <div class="handover-detail__item-side">
                <span v-if="item.confirmed" class="handover-detail__done">
                  {{ item.confirmed_by_name }} {{ formatTime(item.confirmed_at) }}
                </span>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  plain
                  :disabled="detail.status === 'confirmed'"
                  :loading="confirmingItemId === item.id"
                  @click="confirmItem(item)"
                >
                  确认
                </el-button>
              </div>
            </li>
          </ul>

          <EmptyState
            v-if="!detail.items.length"
            title="本次交接无待办"
            description="清单已固化，可直接确认"
          />
        </template>
      </div>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :disabled="detail?.status === 'confirmed'"
          :loading="confirmingAll"
          @click="confirmAll"
        >
          确认整份清单
        </el-button>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.handover {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.handover__alert {
  margin: 0;
}

.handover__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 120px;
}

.handover__card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}

.handover__card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.handover__version {
  font-weight: 600;
}

.handover__party,
.handover__meta {
  margin: 0;
  font-size: 13px;
}

.handover__arrow {
  margin: 0 4px;
  color: var(--el-text-color-secondary);
}

.handover__muted {
  color: var(--el-text-color-secondary);
}

.handover__card .el-button {
  align-self: flex-start;
}

.handover__pager {
  align-self: center;
}

.handover-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 120px;
}

.handover-detail__summary {
  display: flex;
  align-items: center;
  gap: 8px;
}

.handover-detail__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin: 0;
}

.handover-detail__facts div {
  min-width: 0;
}

.handover-detail__facts dt {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.handover-detail__facts dd {
  margin: 2px 0 0;
  font-size: 13px;
  word-break: break-all;
}

.handover-detail__items {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.handover-detail__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.handover-detail__item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.handover-detail__label {
  font-size: 13px;
  word-break: break-all;
}

.handover-detail__item-side {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.handover-detail__done {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 600px) {
  .handover-detail__facts {
    grid-template-columns: minmax(0, 1fr);
  }

  .handover-detail__item {
    flex-direction: column;
    align-items: flex-start;
  }

  .handover-detail__item-side {
    align-self: stretch;
    justify-content: space-between;
  }
}
</style>