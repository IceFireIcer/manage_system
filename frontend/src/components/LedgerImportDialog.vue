<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'completed'])

const MAX_ROWS = 500
const HEADER_ALIASES = {
  code: ['编码', '耗材编码', 'code'],
  name: ['名称', '耗材名称', 'name'],
  spec: ['规格', '型号', 'spec'],
  unit: ['单位', '计量单位', 'unit'],
  location: ['位置', '存放位置', '地点', '柜位', 'location'],
  safety_stock: ['安全库存', '安全库存线', 'safety_stock'],
  opening_stock: ['期初库存', '期初数量', '库存', 'opening_stock'],
}

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const source = ref('')
const countedAt = ref('')
const csvText = ref('')
const fileName = ref('')
const rows = ref([])
const parseMessage = ref('')
const parseError = ref('')
const preview = ref(null)
const receipt = ref(null)
const previewing = ref(false)
const importing = ref(false)
const errorMessage = ref('')
const fileInput = ref(null)
let requestId = ''
let lastSignature = ''

const canPreview = computed(
  () => Boolean(source.value.trim()) && Boolean(countedAt.value) && rows.value.length > 0,
)
const canImport = computed(() => Boolean(preview.value?.valid) && !importing.value)
const statusLabel = {
  ready: '可导入',
  existing: '已存在，跳过',
  error: '有错误',
}

function pad(value) {
  return String(value).padStart(2, '0')
}

function defaultCountedAt() {
  const now = new Date()
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(
    now.getHours(),
  )}:${pad(now.getMinutes())}:00`
}

function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll('-', '')
  }
  return `imp-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    source.value = ''
    countedAt.value = defaultCountedAt()
    csvText.value = ''
    fileName.value = ''
    rows.value = []
    parseMessage.value = ''
    parseError.value = ''
    preview.value = null
    receipt.value = null
    previewing.value = false
    importing.value = false
    errorMessage.value = ''
    requestId = newRequestId()
    lastSignature = ''
  },
)

function detectDelimiter(line) {
  let best = ','
  let bestCount = -1
  for (const delimiter of [',', '\t', ';']) {
    const count = line.split(delimiter).length - 1
    if (count > bestCount) {
      best = delimiter
      bestCount = count
    }
  }
  return best
}

function splitLine(line, delimiter) {
  const cells = []
  let current = ''
  let quoted = false
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index]
    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"'
        index += 1
      } else {
        quoted = !quoted
      }
      continue
    }
    if (character === delimiter && !quoted) {
      cells.push(current)
      current = ''
      continue
    }
    current += character
  }
  cells.push(current)
  return cells.map((cell) => cell.trim())
}

function buildMapping(headerCells) {
  const mapping = {}
  headerCells.forEach((cell, index) => {
    const normalized = cell.toLowerCase()
    for (const [field, aliases] of Object.entries(HEADER_ALIASES)) {
      if (aliases.some((alias) => alias.toLowerCase() === normalized)) {
        mapping[field] = index
      }
    }
  })
  return mapping
}

function defaultMapping(width) {
  if (width >= 7) {
    return {
      code: 0,
      name: 1,
      spec: 2,
      unit: 3,
      location: 4,
      safety_stock: 5,
      opening_stock: 6,
    }
  }
  if (width === 6) {
    return {
      name: 0,
      spec: 1,
      unit: 2,
      location: 3,
      safety_stock: 4,
      opening_stock: 5,
    }
  }
  throw new Error(
    '未识别列名，请使用表头：编码,名称,规格,单位,位置,安全库存,期初库存',
  )
}

function parseLedger(text) {
  const lines = text
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)
    .filter((line) => line.trim())
  if (!lines.length) throw new Error('文件内没有可识别的数据行')
  const delimiter = detectDelimiter(lines[0])
  const headerCells = splitLine(lines[0], delimiter)
  const headerMapping = buildMapping(headerCells)
  const hasHeader = Object.keys(headerMapping).length >= 3
  const mapping = hasHeader ? headerMapping : defaultMapping(headerCells.length)
  const dataLines = hasHeader ? lines.slice(1) : lines
  if (!dataLines.length) throw new Error('表头之后没有数据行')
  if (dataLines.length > MAX_ROWS) {
    throw new Error(`一次最多导入 ${MAX_ROWS} 行，请拆分台账`)
  }
  return dataLines.map((line, index) => {
    const cells = splitLine(line, delimiter)
    const pick = (field) =>
      mapping[field] === undefined ? null : (cells[mapping[field]] ?? '')
    return {
      line_no: index + 1,
      code: pick('code') || null,
      name: pick('name'),
      spec: pick('spec'),
      unit: pick('unit'),
      location: pick('location'),
      safety_stock: pick('safety_stock'),
      opening_stock: pick('opening_stock'),
    }
  })
}

function syncRows() {
  if (!csvText.value.trim()) return rows.value.length > 0
  try {
    rows.value = parseLedger(csvText.value)
    parseError.value = ''
    parseMessage.value = `已解析 ${rows.value.length} 行`
    return true
  } catch (error) {
    rows.value = []
    parseError.value = error.message
    return false
  }
}

function invalidatePreview() {
  preview.value = null
  receipt.value = null
  errorMessage.value = ''
  if (csvText.value.trim()) parseMessage.value = '内容已修改，请重新预览'
}

async function handleFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > 1024 * 1024) {
    parseError.value = '文件不能超过 1 MB'
    return
  }
  fileName.value = file.name
  csvText.value = await file.text()
  invalidatePreview()
  syncRows()
}

function errorText(error) {
  if (!error?.response) {
    return '暂未确认提交结果，请重新预览后再决定是否提交'
  }
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean)
    return messages.length ? messages.join('；') : '操作失败，请稍后重试'
  }
  return '操作失败，请稍后重试'
}

function requestPayload() {
  return {
    source: source.value.trim(),
    counted_at: countedAt.value,
    rows: rows.value,
  }
}

function refreshRequestId() {
  const signature = JSON.stringify([source.value.trim(), countedAt.value, rows.value])
  if (signature !== lastSignature) {
    requestId = newRequestId()
    lastSignature = signature
  }
}

async function runPreview() {
  if (previewing.value || importing.value) return
  errorMessage.value = ''
  receipt.value = null
  if (!source.value.trim()) {
    errorMessage.value = '请填写台账来源'
    return
  }
  if (!countedAt.value) {
    errorMessage.value = '请选择初始计数时点'
    return
  }
  if (!syncRows()) {
    if (!parseError.value) parseError.value = '请粘贴台账内容或选择 CSV 文件'
    return
  }
  previewing.value = true
  refreshRequestId()
  try {
    const { data } = await api.post('/consumables/import/preview', requestPayload())
    preview.value = data
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    previewing.value = false
  }
}

async function confirmImport() {
  if (!canImport.value) return
  importing.value = true
  errorMessage.value = ''
  try {
    const { data } = await api.post('/consumables/import', {
      ...requestPayload(),
      request_id: requestId,
    })
    receipt.value = data
    preview.value = null
    ElMessage.success('台账导入完成')
    emit('completed', data)
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object' && Array.isArray(detail.rows)) {
      preview.value = detail
    }
    errorMessage.value = errorText(error)
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="导入纸质台账"
    width="min(920px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!importing"
    :show-close="!importing"
    destroy-on-close
    class="ledger-import"
  >
    <template v-if="!receipt">
      <div class="ledger-import__grid">
        <label class="ledger-import__field">
          <span>台账来源</span>
          <el-input
            v-model="source"
            maxlength="128"
            placeholder="如：2026 年秋季实训室纸质台账"
            @input="invalidatePreview"
          />
        </label>
        <label class="ledger-import__field">
          <span>初始计数时点</span>
          <el-date-picker
            v-model="countedAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择清点时间"
            :clearable="false"
            @change="invalidatePreview"
          />
        </label>
      </div>

      <div class="ledger-import__upload">
        <el-button @click="fileInput?.click()">选择 CSV 文件</el-button>
        <span class="ledger-import__filename">{{ fileName || '未选择文件' }}</span>
        <input
          ref="fileInput"
          type="file"
          accept=".csv,.txt,text/csv,text/plain"
          hidden
          @change="handleFile"
        />
      </div>

      <el-input
        v-model="csvText"
        type="textarea"
        :rows="8"
        placeholder="粘贴台账内容，第一行为表头：编码,名称,规格,单位,位置,安全库存,期初库存（编码可留空）"
        @input="invalidatePreview"
      />

      <p v-if="parseError" class="ledger-import__error">{{ parseError }}</p>
      <p v-else-if="parseMessage" class="ledger-import__hint">{{ parseMessage }}</p>

      <el-alert
        v-if="errorMessage"
        type="error"
        :closable="false"
        show-icon
        :title="errorMessage"
        class="ledger-import__alert"
      />

      <template v-if="preview">
        <dl class="ledger-import__summary">
          <div class="ledger-import__summary-item">
            <dt>总行数</dt>
            <dd>{{ preview.summary.total_rows }}</dd>
          </div>
          <div class="ledger-import__summary-item">
            <dt>可导入</dt>
            <dd>{{ preview.summary.ready_rows }} 类</dd>
          </div>
          <div class="ledger-import__summary-item">
            <dt>已存在跳过</dt>
            <dd>{{ preview.summary.existing_rows }} 行</dd>
          </div>
          <div class="ledger-import__summary-item">
            <dt>错误</dt>
            <dd>{{ preview.summary.error_rows }} 行</dd>
          </div>
          <div class="ledger-import__summary-item">
            <dt>期初合计</dt>
            <dd>{{ preview.summary.opening_total }}</dd>
          </div>
        </dl>
        <div class="ledger-import__units">
          <el-tag
            v-for="item in preview.summary.by_unit"
            :key="item.unit"
            effect="plain"
            type="info"
          >
            {{ item.unit || '未填单位' }}：{{ item.kinds }} 类 / 期初 {{ item.opening_stock }}
          </el-tag>
        </div>

        <el-table :data="preview.rows" max-height="320" class="table-dense" row-key="line_no">
          <el-table-column prop="line_no" label="行" width="64" />
          <el-table-column prop="code" label="编码" width="120">
            <template #default="{ row }">{{ row.code || '自动生成' }}</template>
          </el-table-column>
          <el-table-column label="名称／规格" min-width="160">
            <template #default="{ row }">
              <div>{{ row.name || '—' }}</div>
              <div class="ledger-import__sub">{{ row.spec || '—' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="unit" label="单位" width="80" />
          <el-table-column label="期初" width="80" align="right">
            <template #default="{ row }">{{ row.opening_stock }}</template>
          </el-table-column>
          <el-table-column label="预览结果" min-width="220">
            <template #default="{ row }">
              <el-tag
                :type="row.status === 'ready' ? 'success' : row.status === 'existing' ? 'info' : 'danger'"
                effect="plain"
                size="small"
              >
                {{ statusLabel[row.status] || row.status }}
              </el-tag>
              <div v-if="row.message" class="ledger-import__sub">{{ row.message }}</div>
              <div
                v-for="error in row.errors"
                :key="`${row.line_no}-${error.field}-${error.code}`"
                class="ledger-import__sub ledger-import__sub--error"
              >
                {{ error.reason }}
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </template>

    <template v-else>
      <el-result
        icon="success"
        title="台账已导入"
        :sub-title="`批次 ${receipt.batch_id} · ${receipt.source}`"
      />
      <dl class="ledger-import__summary">
        <div class="ledger-import__summary-item">
          <dt>总类数</dt>
          <dd>{{ receipt.summary.total_kinds }}</dd>
        </div>
        <div class="ledger-import__summary-item">
          <dt>期初合计</dt>
          <dd>{{ receipt.summary.opening_total }}</dd>
        </div>
        <div class="ledger-import__summary-item">
          <dt>已存在跳过</dt>
          <dd>{{ receipt.summary.existing_rows }} 行</dd>
        </div>
        <div class="ledger-import__summary-item">
          <dt>计数时点</dt>
          <dd>{{ receipt.counted_at.replace('T', ' ') }}</dd>
        </div>
      </dl>
      <div class="ledger-import__units">
        <el-tag
          v-for="item in receipt.summary.by_unit"
          :key="item.unit"
          effect="plain"
          type="info"
        >
          {{ item.unit || '未填单位' }}：{{ item.kinds }} 类 / 期初 {{ item.opening_stock }}
        </el-tag>
      </div>

      <el-table :data="receipt.rows" max-height="360" class="table-dense" row-key="line_no">
        <el-table-column prop="line_no" label="行" width="64" />
        <el-table-column prop="code" label="编码" width="120" />
        <el-table-column label="名称／规格" min-width="160">
          <template #default="{ row }">
            <div>{{ row.name }}</div>
            <div class="ledger-import__sub">{{ row.spec }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="unit" label="单位" width="80" />
        <el-table-column label="期初" width="90" align="right">
          <template #default="{ row }">{{ row.opening_stock }}</template>
        </el-table-column>
        <el-table-column label="账本核对" width="140">
          <template #default="{ row }">
            <el-tag :type="row.reconciled ? 'success' : 'danger'" effect="plain" size="small">
              {{ row.reconciled ? '期初一致' : '期初不一致' }}
            </el-tag>
            <div class="ledger-import__sub">
              {{ row.event_id ? `事件 #${row.event_id}` : '无期初事件' }}
            </div>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template #footer>
      <div class="ledger-import__footer">
        <el-button :disabled="importing" @click="visible = false">
          {{ receipt ? '完成' : '取消' }}
        </el-button>
        <template v-if="!receipt">
          <el-button :loading="previewing" @click="runPreview">预览核对</el-button>
          <el-button type="primary" :disabled="!canImport" :loading="importing" @click="confirmImport">
            确认导入
          </el-button>
        </template>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.ledger-import__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-3);
}

.ledger-import__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ledger-import__field span {
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text-muted);
}

.ledger-import__upload {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.ledger-import__filename {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.ledger-import__hint,
.ledger-import__error {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
}

.ledger-import__hint {
  color: var(--text-muted);
}

.ledger-import__error {
  color: var(--danger);
}

.ledger-import__alert {
  margin-top: var(--space-3);
}

.ledger-import__summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
  margin: var(--space-4) 0 var(--space-2);
  padding: 12px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.ledger-import__summary-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.ledger-import__summary-item dt {
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text-muted);
}

.ledger-import__summary-item dd {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.ledger-import__units {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: var(--space-3);
}

.ledger-import__sub {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.ledger-import__sub--error {
  color: var(--danger);
}

.ledger-import__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 640px) {
  .ledger-import__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .ledger-import__upload {
    flex-wrap: wrap;
  }
}

@media (max-width: 767px) {
  .ledger-import .el-dialog__footer .el-button {
    min-height: 44px;
  }
}
</style>