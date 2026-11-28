<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const WINDOWS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
  { label: '近 90 天', value: 90 },
]

const REASON_TEXT = {
  no_recipe: '暂无课程配方',
  no_headcount: '课程未填写人数',
  zero_demand: '配方用量为 0',
}

const windowDays = ref(90)
const appliedWindow = ref(90)
const items = ref([])
const unitTotals = ref([])
const totalCount = ref(0)
const loading = ref(false)
const error = ref('')
let requestSeq = 0

function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}` : text
}

function hasSupport(support) {
  return support?.supported_sessions !== null && support?.supported_sessions !== undefined
}

function supportText(support) {
  if (hasSupport(support)) return `约 ${support.supported_sessions} 次同类实训`
  return REASON_TEXT[support?.reason_code] ?? '暂无法估算'
}

function supportMeta(support) {
  if (!hasSupport(support) || !support.course_name) return ''
  return `${support.course_name} · ${support.headcount} 人`
}

async function fetchFootprint() {
  const seq = ++requestSeq
  const requested = windowDays.value
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get('/requisitions/my-footprint', {
      params: { window: requested },
    })
    if (seq !== requestSeq) return
    items.value = Array.isArray(data.items) ? data.items : []
    unitTotals.value = Array.isArray(data.unit_totals) ? data.unit_totals : []
    totalCount.value = Number(data.requisition_count) || 0
    appliedWindow.value = requested
  } catch {
    if (seq !== requestSeq) return
    error.value = '足迹读取失败，请稍后重试。'
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function switchWindow(value) {
  windowDays.value = value
  fetchFootprint()
}

onMounted(fetchFootprint)
</script>

<template>
  <div class="footprint">
    <PageHeader title="我的耗材足迹" :breadcrumb="['领用管理', '我的耗材足迹']">
      <template #actions>
        <el-radio-group
          v-model="windowDays"
          class="footprint__segmented"
          @change="switchWindow"
        >
          <el-radio-button
            v-for="option in WINDOWS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </el-radio-button>
        </el-radio-group>
      </template>
    </PageHeader>

    <section v-if="!items.length" class="panel footprint__state">
      <div v-if="loading" class="footprint__skeleton" aria-busy="true">
        <p class="footprint__placeholder">正在汇总本人足迹…</p>
        <el-skeleton :rows="5" animated />
      </div>
      <EmptyState
        v-else-if="error"
        title="本人足迹暂时不可用"
        :description="error"
      >
        <template #action>
          <el-button type="primary" @click="fetchFootprint">重试</el-button>
        </template>
      </EmptyState>
      <EmptyState
        v-else
        title="所选时间段暂无领用记录"
        description="完成一次领用后会显示在这里。"
      >
        <template #action>
          <el-button
            type="primary"
            @click="switchWindow(appliedWindow === 90 ? 7 : 90)"
          >
            查看{{ appliedWindow === 90 ? '近 7 天' : '近 90 天' }}
          </el-button>
        </template>
      </EmptyState>
    </section>

    <div v-else class="footprint__body" :aria-busy="loading">
      <div class="footprint__stats">
        <div class="panel footprint__stat">
          <span class="footprint__stat-label">累计领用</span>
          <span class="footprint__stat-value">{{ totalCount }}<small>次</small></span>
        </div>
        <div class="panel footprint__stat">
          <span class="footprint__stat-label">涉及耗材</span>
          <span class="footprint__stat-value">{{ items.length }}<small>种</small></span>
        </div>
        <div class="panel footprint__stat">
          <span class="footprint__stat-label">计量单位</span>
          <span class="footprint__stat-value">{{ unitTotals.length }}<small>种</small></span>
        </div>
      </div>

      <section v-if="unitTotals.length" class="panel footprint__block">
        <div class="footprint__block-head">
          <h2 class="footprint__block-title">分单位累计</h2>
          <span class="footprint__block-meta">近 {{ appliedWindow }} 天</span>
        </div>
        <div class="footprint__unit-grid">
          <div v-for="row in unitTotals" :key="row.unit" class="footprint__unit">
            <span class="footprint__unit-name">{{ row.unit }}</span>
            <span class="footprint__unit-value">
              {{ row.net_quantity }}<small>{{ row.unit }}</small>
            </span>
            <span class="footprint__unit-meta">
              {{ row.requisition_count }} 次 · {{ row.consumables }} 种耗材
            </span>
          </div>
        </div>
      </section>

      <section class="panel footprint__block">
        <div class="footprint__block-head">
          <h2 class="footprint__block-title">按耗材</h2>
          <span class="footprint__block-meta">
            近 {{ appliedWindow }} 天 · {{ items.length }} 种
          </span>
        </div>
        <ul class="footprint__list">
          <li v-for="row in items" :key="row.consumable_id" class="footprint__item">
            <div class="footprint__item-main">
              <span class="footprint__item-name">{{ row.name }}</span>
              <span v-if="row.spec" class="footprint__item-spec">{{ row.spec }}</span>
            </div>
            <div class="footprint__item-figures">
              <div class="footprint__figure">
                <span class="footprint__figure-label">领用</span>
                <span class="footprint__figure-value">{{ row.requisition_count }} 次</span>
              </div>
              <div class="footprint__figure">
                <span class="footprint__figure-label">净量</span>
                <span class="footprint__figure-value">
                  {{ row.net_quantity }} {{ row.unit }}
                </span>
              </div>
              <div class="footprint__figure footprint__figure--wide">
                <span class="footprint__figure-label">最近</span>
                <span class="footprint__figure-value">{{ formatTime(row.last_at) }}</span>
              </div>
            </div>
            <div
              class="footprint__support"
              :class="{ 'footprint__support--pending': !hasSupport(row.support) }"
            >
              <span class="footprint__support-label">可支撑</span>
              <span class="footprint__support-value">{{ supportText(row.support) }}</span>
              <span v-if="supportMeta(row.support)" class="footprint__support-meta">
                {{ supportMeta(row.support) }}
              </span>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.footprint__segmented {
  display: inline-flex;
}

.footprint__segmented :deep(.el-radio-button__inner) {
  height: 44px;
  padding: 0 16px;
  border-color: var(--line);
  border-radius: 0;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  box-shadow: none;
  transition:
    background-color var(--duration-hover) ease,
    color var(--duration-hover) ease,
    border-color var(--duration-hover) ease;
}

.footprint__segmented :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-radius: var(--radius-control) 0 0 var(--radius-control);
}

.footprint__segmented :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-radius: 0 var(--radius-control) var(--radius-control) 0;
}

.footprint__segmented :deep(.el-radio-button__inner:hover) {
  background: var(--surface-subtle);
  color: var(--text);
}

.footprint__segmented :deep(.el-radio-button.is-active .el-radio-button__inner) {
  border-color: var(--primary-200);
  background: var(--primary-soft);
  color: var(--primary);
  box-shadow: none;
}

.footprint__state {
  padding: 24px;
}

.footprint__state :deep(.el-button) {
  min-height: 44px;
}

.footprint__skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.footprint__placeholder {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.footprint__body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.footprint__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.footprint__stat {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 18px;
}

.footprint__stat-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.footprint__stat-value {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-size: 24px;
  line-height: 30px;
  font-weight: 650;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}

.footprint__stat-value small {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--text-muted);
}

.footprint__block {
  padding: 20px;
}

.footprint__block-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;
}

.footprint__block-title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.footprint__block-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.footprint__unit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.footprint__unit {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.footprint__unit-name {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text-secondary);
}

.footprint__unit-value {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-size: 20px;
  line-height: 26px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}

.footprint__unit-value small {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
}

.footprint__unit-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.footprint__list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.footprint__item {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface);
}

.footprint__item-main {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.footprint__item-name {
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.footprint__item-spec {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.footprint__item-figures {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.footprint__figure {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.footprint__figure-label {
  font-size: 11px;
  line-height: 15px;
  color: var(--text-muted);
}

.footprint__figure-value {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.footprint__figure--wide {
  grid-column: span 1;
}

.footprint__support {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  padding: 10px 12px;
  border-radius: var(--radius-control);
  background: var(--primary-soft);
}

.footprint__support--pending {
  background: var(--surface-subtle);
}

.footprint__support-label {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-muted);
}

.footprint__support-value {
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--primary);
}

.footprint__support--pending .footprint__support-value {
  font-weight: 500;
  color: var(--text-secondary);
}

.footprint__support-meta {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-muted);
}

@media (max-width: 767px) {
  .footprint__stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .footprint__stat {
    padding: 12px;
  }

  .footprint__stat-value {
    font-size: 20px;
    line-height: 26px;
  }

  .footprint__block {
    padding: 16px;
  }

  .footprint__list {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 390px) {
  .footprint__stats {
    grid-template-columns: minmax(0, 1fr);
  }

  .footprint__item-figures {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .footprint__figure--wide {
    grid-column: span 2;
  }
}
</style>