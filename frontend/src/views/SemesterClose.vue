<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import { businessDate, validateDateRange } from '../utils/businessDate'

const CSV_FILES = [
  'key-events.csv',
  'incidents.csv',
  'handover.csv',
  'count-differences.csv',
  'review-summary.csv',
]

const FILE_LABELS = {
  'key-events.csv': '关键变动',
  'incidents.csv': '异常处理',
  'handover.csv': '交接清单',
  'count-differences.csv': '盘点差异',
  'review-summary.csv': '复盘摘要',
  'summary.json': '汇总',
}

const METRICS = [
  { key: 'key_events', label: '关键变动', unit: '条' },
  { key: 'incidents', label: '异常记录', unit: '条' },
  { key: 'handover_checklists', label: '交接清单', unit: '份' },
  { key: 'count_differences', label: '盘点差异', unit: '项' },
  { key: 'reviews', label: '课次复盘', unit: '次' },
]

const today = businessDate()
const dateRange = ref([`${today.slice(0, 4)}-01-01`, today])
const preview = ref(null)
const loadingPreview = ref(false)
const exporting = ref(false)
const error = ref('')
const filterError = ref('')

const rangeText = computed(() => {
  if (!preview.value) return ''
  return `${preview.value.range.start} ~ ${preview.value.range.end}（${preview.value.range.days} 天）`
})

const fileRows = computed(() => {
  const files = preview.value?.files ?? {}
  return CSV_FILES.map((name) => ({
    name,
    label: FILE_LABELS[name] ?? name,
    rows: files[name]?.rows ?? 0,
  }))
})

function buildParams() {
  return { start: dateRange.value[0], end: dateRange.value[1] }
}

function validate() {
  if (!Array.isArray(dateRange.value) || dateRange.value.length !== 2) {
    filterError.value = '请选择起止日期'
    return false
  }
  const message = validateDateRange(dateRange.value)
  filterError.value = message
  return !message
}

async function loadPreview() {
  if (!validate() || loadingPreview.value) return
  loadingPreview.value = true
  error.value = ''
  try {
    const { data } = await api.get('/ops/semester-close/preview', {
      params: buildParams(),
    })
    preview.value = data
  } catch {
    error.value = '结账摘要加载失败，请稍后重试。'
  } finally {
    loadingPreview.value = false
  }
}

async function handleExport() {
  if (!validate() || exporting.value) return
  exporting.value = true
  error.value = ''
  try {
    const response = await api.get('/ops/semester-close', {
      params: buildParams(),
      responseType: 'blob',
      timeout: 60000,
    })
    const disposition = response.headers?.['content-disposition'] || ''
    const matched = /filename="?([^";]+)"?/.exec(disposition)
    const filename = matched?.[1] || 'semester_close.zip'
    const url = window.URL.createObjectURL(
      new Blob([response.data], { type: 'application/zip' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    ElMessage.success('结账包已生成')
  } catch {
    error.value = '结账包生成失败，请稍后重试。'
  } finally {
    exporting.value = false
  }
}

onMounted(loadPreview)
</script>

<template>
  <div class="semester-close">
    <PageHeader title="学期结账" :breadcrumb="['分析', '学期结账']">
      <template #actions>
        <el-button type="primary" :loading="exporting" @click="handleExport">
          生成下载
        </el-button>
      </template>
    </PageHeader>

    <section class="panel semester-close__panel">
      <div class="semester-close__controls">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          class="semester-close__date"
        />
        <el-button :loading="loadingPreview" @click="loadPreview">预览</el-button>
      </div>
      <p v-if="filterError" class="semester-close__error" role="alert">
        {{ filterError }}
      </p>
    </section>

    <p v-if="error" class="semester-close__notice" role="alert">{{ error }}</p>

    <template v-if="preview">
      <section class="panel semester-close__panel">
        <div class="semester-close__summary">
          <div>
            <p class="semester-close__label">范围</p>
            <p class="semester-close__value">{{ rangeText }}</p>
          </div>
          <div>
            <p class="semester-close__label">库存净变动</p>
            <p class="semester-close__value">{{ preview.totals.key_event_delta_sum }}</p>
          </div>
        </div>

        <dl class="semester-close__metrics">
          <div v-for="metric in METRICS" :key="metric.key" class="semester-close__metric">
            <dt class="semester-close__label">{{ metric.label }}</dt>
            <dd class="semester-close__metric-value">
              {{ preview.totals[metric.key] ?? 0 }}
              <span class="semester-close__unit">{{ metric.unit }}</span>
            </dd>
          </div>
        </dl>
      </section>

      <section class="panel semester-close__panel">
        <h2 class="semester-close__heading">文件行数</h2>
        <div class="semester-close__files">
          <div v-for="file in fileRows" :key="file.name" class="semester-close__file">
            <span class="semester-close__file-label">{{ file.label }}</span>
            <span class="semester-close__file-name">{{ file.name }}</span>
            <span class="semester-close__file-count">{{ file.rows }} 行</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.semester-close {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.semester-close__panel {
  padding: 20px;
}

.semester-close__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.semester-close__date {
  flex: 1 1 280px;
  max-width: 360px;
}

.semester-close__controls :deep(.el-date-editor) {
  width: 100%;
}

.semester-close__error,
.semester-close__notice {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--danger);
}

.semester-close__notice {
  margin: 0;
}

.semester-close__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
}

.semester-close__label {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.semester-close__value {
  margin: 4px 0 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.semester-close__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  margin: 16px 0 0;
}

.semester-close__metric {
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-subtle);
}

.semester-close__metric-value {
  margin: 4px 0 0;
  font-size: 22px;
  line-height: 28px;
  font-weight: 600;
  color: var(--text);
}

.semester-close__unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted);
}

.semester-close__heading {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
}

.semester-close__files {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--line-soft);
}

.semester-close__file {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: baseline;
  gap: 4px 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}

.semester-close__file-label {
  font-size: 14px;
  line-height: 20px;
  color: var(--text);
}

.semester-close__file-name {
  grid-column: 1;
  font-size: 12px;
  line-height: 16px;
  color: var(--text-muted);
}

.semester-close__file-count {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--success);
}

@media (max-width: 767px) {
  .semester-close__panel {
    padding: 16px;
  }

  .semester-close__date {
    flex-basis: 100%;
    max-width: none;
  }

  .semester-close__controls .el-button {
    width: 100%;
    min-height: 44px;
    margin-left: 0;
  }

  .semester-close__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>