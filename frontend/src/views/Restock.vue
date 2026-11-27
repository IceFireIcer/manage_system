<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'

const TIER_META = {
  urgent: { label: '紧急', type: 'danger' },
  moderate: { label: '一般', type: 'warning' },
  observe: { label: '观察', type: 'primary' },
}

const userStore = useUserStore()
const isAdmin = computed(() => userStore.user?.role === 'admin')

const items = ref([])
const loading = ref(false)
const generatedAt = ref('')

const restockDialog = reactive({
  visible: false,
  item: null,
  quantity: 1,
  remark: '',
  submitting: false,
})

function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatDays(value) {
  if (value === null || value === undefined) return '∞'
  return `${value} 天`
}

function consumableLabel(item) {
  return item.spec ? `${item.name}（${item.spec}）` : item.name
}

async function fetchSuggestions(force = false) {
  loading.value = true
  try {
    const { data } = force
      ? await api.post('/inventory/suggestions/refresh')
      : await api.get('/inventory/suggestions')
    items.value = data.items
    generatedAt.value = formatTime(data.generated_at)
    if (force) {
      ElMessage.success('已重新巡检')
    }
  } catch {
    ElMessage.error(force ? '重新巡检失败，请稍后重试' : '加载补货建议失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function openRestock(item) {
  restockDialog.item = item
  restockDialog.quantity = item.recommended_quantity > 0 ? item.recommended_quantity : 1
  restockDialog.remark = ''
  restockDialog.visible = true
}

async function submitRestock() {
  const quantity = Number(restockDialog.quantity)
  if (!Number.isInteger(quantity) || quantity <= 0) {
    ElMessage.warning('入库数量必须为正整数')
    return
  }
  restockDialog.submitting = true
  try {
    const { data } = await api.post('/inventory/restock', {
      consumable_id: restockDialog.item.consumable_id,
      quantity,
      remark: restockDialog.remark.trim() || null,
    })
    items.value = items.value.filter((row) => row.consumable_id !== data.consumable_id)
    restockDialog.visible = false
    ElMessage.success(
      `已入库 ${data.quantity}${data.unit || ''}，当前库存 ${data.stock_after}${data.unit || ''}`,
    )
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '入库失败，请稍后重试')
  } finally {
    restockDialog.submitting = false
  }
}

onMounted(fetchSuggestions)
</script>

<template>
  <div class="restock">
    <el-card class="restock__toolbar">
      <div class="restock__toolbar-main">
        <div>
          <h2 class="restock__title">补货建议</h2>
          <p class="restock__subtitle">
            巡检时间：{{ generatedAt || '—' }}；规则判定 + AI 润色，结果缓存 10 分钟。
          </p>
        </div>
        <el-button type="primary" :loading="loading" @click="fetchSuggestions(true)">
          重新巡检
        </el-button>
      </div>
    </el-card>

    <el-empty
      v-if="!loading && items.length === 0"
      description="当前库存状态良好，暂无补货建议"
    />

    <div v-loading="loading" class="restock__list">
      <el-card
        v-for="item in items"
        :key="item.consumable_id"
        class="restock__card"
        :class="`restock__card--${item.tier}`"
      >
        <div class="restock__card-head">
          <div class="restock__card-title">
            <span class="restock__card-name">{{ consumableLabel(item) }}</span>
            <el-tag :type="TIER_META[item.tier].type" effect="dark" size="small">
              {{ TIER_META[item.tier].label }}
            </el-tag>
          </div>
          <div class="restock__card-actions">
            <span class="restock__amount-label">建议补货</span>
            <span class="restock__amount">{{ item.recommended_quantity }}{{ item.unit }}</span>
            <el-button v-if="isAdmin" type="primary" @click="openRestock(item)">
              处置入库
            </el-button>
          </div>
        </div>

        <el-descriptions :column="3" size="small" class="restock__metrics">
          <el-descriptions-item label="当前库存">
            {{ item.current_stock }}{{ item.unit }}
          </el-descriptions-item>
          <el-descriptions-item label="安全库存线">
            {{ item.safety_stock }}{{ item.unit }}
          </el-descriptions-item>
          <el-descriptions-item label="近 7 天净消耗">
            {{ item.q7 }}{{ item.unit }}
          </el-descriptions-item>
          <el-descriptions-item label="日均速率">
            {{ item.rate }}{{ item.unit }}/天
          </el-descriptions-item>
          <el-descriptions-item label="可支撑天数">
            {{ formatDays(item.days_of_supply) }}
          </el-descriptions-item>
          <el-descriptions-item label="库存缺口">
            {{ item.gap }}{{ item.unit }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="restock__rationale">
          <el-tag size="small" type="info">规则判定 + AI 润色</el-tag>
          <span>{{ item.rationale }}</span>
        </div>
      </el-card>
    </div>

    <el-dialog v-model="restockDialog.visible" title="处置入库" width="440px">
      <el-form label-width="96px">
        <el-form-item label="耗材">
          <span>{{ restockDialog.item ? consumableLabel(restockDialog.item) : '' }}</span>
        </el-form-item>
        <el-form-item label="入库数量" required>
          <el-input-number
            v-model="restockDialog.quantity"
            :min="1"
            :precision="0"
            :step="1"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="restockDialog.remark"
            maxlength="255"
            show-word-limit
            placeholder="可选，如采购回库"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="restockDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="restockDialog.submitting"
          @click="submitRestock"
        >
          确认入库
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.restock__toolbar {
  margin-bottom: 16px;
}

.restock__toolbar-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.restock__title {
  margin: 0;
  font-size: 16px;
}

.restock__subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: #909399;
}

.restock__list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
  gap: 16px;
  min-height: 80px;
}

.restock__card {
  border-left: 4px solid #909399;
}

.restock__card--urgent {
  border-left-color: #f56c6c;
}

.restock__card--moderate {
  border-left-color: #e6a23c;
}

.restock__card--observe {
  border-left-color: #409eff;
}

.restock__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.restock__card-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.restock__card-name {
  font-size: 16px;
  font-weight: 600;
}

.restock__card-actions {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.restock__amount-label {
  font-size: 12px;
  color: #909399;
}

.restock__amount {
  font-size: 24px;
  font-weight: 700;
  color: #f56c6c;
}

.restock__metrics {
  margin-bottom: 12px;
}

.restock__rationale {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  color: #606266;
  background-color: #f5f7fa;
  border-radius: 4px;
}
</style>