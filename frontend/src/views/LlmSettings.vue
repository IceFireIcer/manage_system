<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'

const loading = ref(false)
const features = ref([])
const providers = ref([])
const prompts = ref([])
const bindings = ref([])

const defaultProviderForm = () => ({
  name: '',
  base_url: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  api_key: '',
  timeout: 30,
  temperature: 0.3,
  enabled: true,
  is_default: false,
  supports_tools: false,
})

const providerDialog = reactive({
  visible: false,
  saving: false,
  testing: false,
  id: null,
  form: defaultProviderForm(),
})

const promptDialog = reactive({
  visible: false,
  saving: false,
  id: null,
  feature: '',
  enabled: true,
  system_prompt: '',
})

const activePromptFeature = ref('restock_rationale')
const bindingDrafts = reactive({})

const featureMap = computed(() => {
  const map = {}
  for (const item of features.value) map[item.key] = item
  return map
})

const providerOptions = computed(() => providers.value.filter((p) => p.enabled))

const featurePrompts = computed(() =>
  prompts.value
    .filter((p) => p.feature === activePromptFeature.value)
    .sort((a, b) => b.version - a.version),
)

function promptsForFeature(feature) {
  return prompts.value
    .filter((p) => p.feature === feature)
    .sort((a, b) => b.version - a.version)
}

function emptyBinding(feature) {
  return { provider_id: null, prompt_id: null, enabled: false }
}

function syncBindingDrafts() {
  const byFeature = {}
  for (const row of bindings.value) byFeature[row.feature] = row
  for (const item of features.value) {
    const row = byFeature[item.key]
    bindingDrafts[item.key] = row
      ? {
          provider_id: row.provider_id,
          prompt_id: row.prompt_id,
          enabled: row.enabled,
        }
      : emptyBinding(item.key)
  }
}

function formatTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

async function loadAll() {
  loading.value = true
  try {
    const [featureRes, providerRes, promptRes, bindingRes] = await Promise.all([
      api.get('/llm/features'),
      api.get('/llm/providers'),
      api.get('/llm/prompts'),
      api.get('/llm/bindings'),
    ])
    features.value = featureRes.data
    providers.value = providerRes.data
    prompts.value = promptRes.data
    bindings.value = bindingRes.data
    if (features.value.length && !featureMap.value[activePromptFeature.value]) {
      activePromptFeature.value = features.value[0].key
    }
    syncBindingDrafts()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '加载 LLM 配置失败')
  } finally {
    loading.value = false
  }
}

function openCreateProvider() {
  providerDialog.visible = true
  providerDialog.id = null
  providerDialog.form = defaultProviderForm()
}

function openEditProvider(row) {
  providerDialog.visible = true
  providerDialog.id = row.id
  providerDialog.form = {
    name: row.name,
    base_url: row.base_url,
    model: row.model,
    api_key: '',
    timeout: row.timeout,
    temperature: row.temperature,
    enabled: row.enabled,
    is_default: row.is_default,
    supports_tools: row.supports_tools,
  }
}

function providerPayload() {
  const form = providerDialog.form
  return {
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    timeout: Number(form.timeout),
    temperature: Number(form.temperature),
    enabled: form.enabled,
    is_default: form.is_default,
    supports_tools: form.supports_tools,
  }
}

async function submitProvider() {
  const form = providerDialog.form
  if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) {
    ElMessage.warning('请填写名称、Base URL 与模型')
    return
  }
  providerDialog.saving = true
  try {
    const payload = providerPayload()
    if (form.api_key.trim()) payload.api_key = form.api_key.trim()
    if (providerDialog.id) {
      await api.patch(`/llm/providers/${providerDialog.id}`, payload)
      ElMessage.success('供应商已更新')
    } else {
      await api.post('/llm/providers', payload)
      ElMessage.success('供应商已创建')
    }
    providerDialog.visible = false
    await loadAll()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存供应商失败')
  } finally {
    providerDialog.saving = false
  }
}

async function deleteProvider(row) {
  try {
    await ElMessageBox.confirm(`确认删除供应商「${row.name}」？`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await api.delete(`/llm/providers/${row.id}`)
    ElMessage.success('供应商已删除')
    await loadAll()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '删除供应商失败')
  }
}

async function testProviderById(row) {
  await runTest(
    () => api.post(`/llm/providers/${row.id}/test`, {}),
    row.name,
  )
}

async function testProviderInDialog() {
  const form = providerDialog.form
  if (!providerDialog.id && !form.api_key.trim()) {
    ElMessage.warning('请先填写 API Key 再测试')
    return
  }
  providerDialog.testing = true
  try {
    if (providerDialog.id && !form.api_key.trim()) {
      await runTest(() => api.post(`/llm/providers/${providerDialog.id}/test`, {}), form.name)
    } else {
      await runTest(() => api.post('/llm/providers/test', { ...providerPayload(), api_key: form.api_key.trim() }), form.name)
    }
  } finally {
    providerDialog.testing = false
  }
}

async function runTest(request, label) {
  try {
    const { data } = await request()
    if (data.ok) {
      ElMessage.success(`${label || '供应商'}连接成功（${data.latency_ms ?? '-'} ms）`)
    } else {
      ElMessage.error(`连接失败：${data.message}`)
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '测试连接失败')
  }
}

function openCreatePrompt() {
  promptDialog.visible = true
  promptDialog.id = null
  promptDialog.feature = activePromptFeature.value
  promptDialog.enabled = true
  promptDialog.system_prompt = ''
}

function openEditPrompt(row) {
  promptDialog.visible = true
  promptDialog.id = row.id
  promptDialog.feature = row.feature
  promptDialog.enabled = row.enabled
  promptDialog.system_prompt = row.system_prompt
}

async function submitPrompt() {
  if (!promptDialog.system_prompt.trim()) {
    ElMessage.warning('请填写系统提示词')
    return
  }
  promptDialog.saving = true
  try {
    if (promptDialog.id) {
      await api.patch(`/llm/prompts/${promptDialog.id}`, {
        system_prompt: promptDialog.system_prompt.trim(),
        enabled: promptDialog.enabled,
      })
      ElMessage.success('提示词已更新')
    } else {
      await api.post('/llm/prompts', {
        feature: promptDialog.feature,
        system_prompt: promptDialog.system_prompt.trim(),
        enabled: promptDialog.enabled,
      })
      ElMessage.success('已创建新版本')
    }
    promptDialog.visible = false
    await loadAll()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存提示词失败')
  } finally {
    promptDialog.saving = false
  }
}

async function activatePrompt(row, rollback = false) {
  try {
    const path = rollback ? 'rollback' : 'activate'
    await api.post(`/llm/prompts/${row.id}/${path}`)
    ElMessage.success(rollback ? `已回滚到 v${row.version}` : `已激活 v${row.version}`)
    await loadAll()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  }
}

async function saveBinding(feature) {
  const draft = bindingDrafts[feature]
  try {
    await api.put(`/llm/bindings/${feature}`, {
      provider_id: draft.provider_id ?? null,
      prompt_id: draft.prompt_id ?? null,
      enabled: draft.enabled,
    })
    ElMessage.success('功能绑定已保存')
    await loadAll()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存功能绑定失败')
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="llm-settings" v-loading="loading">
    <PageHeader title="LLM 配置" description="管理供应商、提示词版本与功能绑定。" />

    <el-card class="llm-card" shadow="never">
      <template #header>
        <div class="llm-card__header">
          <span>供应商</span>
          <el-button type="primary" size="small" @click="openCreateProvider">新增供应商</el-button>
        </div>
      </template>
      <el-table :data="providers" size="small" empty-text="暂无供应商">
        <el-table-column prop="name" label="名称" min-width="120">
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.is_default" size="small" type="success" class="ml-4">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" min-width="120" />
        <el-table-column prop="base_url" label="Base URL" min-width="200" show-overflow-tooltip />
        <el-table-column label="API Key" min-width="110">
          <template #default="{ row }">
            <span v-if="row.has_api_key">{{ row.api_key_masked || '••••' }}</span>
            <span v-else class="text-muted">未配置</span>
          </template>
        </el-table-column>
        <el-table-column label="参数" min-width="120">
          <template #default="{ row }">{{ row.timeout }}s / T={{ row.temperature }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="工具" width="70">
          <template #default="{ row }">{{ row.supports_tools ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="testProviderById(row)">测试</el-button>
            <el-button link type="primary" size="small" @click="openEditProvider(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="deleteProvider(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="llm-card" shadow="never">
      <template #header>
        <div class="llm-card__header">
          <span>提示词版本</span>
          <div class="llm-card__actions">
            <el-select v-model="activePromptFeature" size="small" style="width: 160px">
              <el-option
                v-for="item in features"
                :key="item.key"
                :label="item.label"
                :value="item.key"
              />
            </el-select>
            <el-button type="primary" size="small" @click="openCreatePrompt">新建版本</el-button>
          </div>
        </div>
      </template>
      <el-table :data="featurePrompts" size="small" empty-text="暂无提示词版本">
        <el-table-column prop="version" label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.active" size="small" type="success">使用中</el-tag>
            <el-tag v-else size="small" type="info">未激活</el-tag>
            <el-tag v-if="!row.enabled" size="small" type="warning" class="ml-4">已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="system_prompt" label="提示词" min-width="260" show-overflow-tooltip />
        <el-table-column prop="created_by" label="创建人" width="110">
          <template #default="{ row }">{{ row.created_by || '—' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.active"
              link
              type="primary"
              size="small"
              @click="activatePrompt(row, false)"
            >激活</el-button>
            <el-button
              v-if="!row.active"
              link
              type="warning"
              size="small"
              @click="activatePrompt(row, true)"
            >回滚到此版本</el-button>
            <el-button link type="primary" size="small" @click="openEditPrompt(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="llm-card" shadow="never">
      <template #header>
        <div class="llm-card__header">
          <span>功能绑定</span>
        </div>
      </template>
      <el-table :data="features" size="small" empty-text="暂无功能">
        <el-table-column label="功能" min-width="150">
          <template #default="{ row }">
            <div class="feature-name">{{ row.label }}</div>
            <div class="feature-desc">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="供应商" min-width="200">
          <template #default="{ row }">
            <el-select
              v-model="bindingDrafts[row.key].provider_id"
              clearable
              placeholder="未绑定"
              size="small"
              style="width: 100%"
            >
              <el-option
                v-for="provider in providerOptions"
                :key="provider.id"
                :label="provider.name"
                :value="provider.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="提示词" min-width="200">
          <template #default="{ row }">
            <el-select
              v-model="bindingDrafts[row.key].prompt_id"
              clearable
              placeholder="未绑定"
              size="small"
              style="width: 100%"
            >
              <el-option
                v-for="prompt in promptsForFeature(row.key)"
                :key="prompt.id"
                :label="`v${prompt.version}${prompt.active ? '（使用中）' : ''}`"
                :value="prompt.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch v-model="bindingDrafts[row.key].enabled" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="saveBinding(row.key)">保存</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="providerDialog.visible"
      :title="providerDialog.id ? '编辑供应商' : '新增供应商'"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form label-width="96px" label-position="left">
        <el-form-item label="名称" required>
          <el-input v-model="providerDialog.form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="Base URL" required>
          <el-input v-model="providerDialog.form.base_url" maxlength="255" />
        </el-form-item>
        <el-form-item label="模型" required>
          <el-input v-model="providerDialog.form.model" maxlength="128" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="providerDialog.form.api_key"
            type="password"
            show-password
            :placeholder="providerDialog.id ? '留空则保持不变' : 'sk-...'"
          />
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="providerDialog.form.timeout" :min="1" :max="300" />
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="providerDialog.form.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
        <el-form-item label="选项">
          <div class="option-row">
            <el-checkbox v-model="providerDialog.form.enabled">启用</el-checkbox>
            <el-checkbox v-model="providerDialog.form.is_default">设为默认</el-checkbox>
            <el-checkbox v-model="providerDialog.form.supports_tools">支持工具</el-checkbox>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="providerDialog.testing" @click="testProviderInDialog">测试连接</el-button>
        <el-button @click="providerDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="providerDialog.saving" @click="submitProvider">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="promptDialog.visible"
      :title="promptDialog.id ? '编辑提示词' : `新增提示词版本 · ${featureMap[promptDialog.feature]?.label || ''}`"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-width="96px" label-position="left">
        <el-form-item label="系统提示词" required>
          <el-input
            v-model="promptDialog.system_prompt"
            type="textarea"
            :rows="8"
            maxlength="4000"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="promptDialog.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="promptDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="promptDialog.saving" @click="submitPrompt">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.llm-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.llm-card {
  border-radius: 10px;
}

.llm-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.llm-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.feature-name {
  font-weight: 500;
}

.feature-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.option-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.text-muted {
  color: var(--el-text-color-secondary);
}

.ml-4 {
  margin-left: 4px;
}

@media (max-width: 768px) {
  .llm-card__header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .llm-card__actions {
    width: 100%;
    flex-wrap: wrap;
  }
}
</style>