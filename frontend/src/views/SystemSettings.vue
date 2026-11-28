<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const isSuper = computed(
  () => (userStore.activeRole ?? userStore.user?.role) === 'super_admin',
)

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const days = ref(90)
const savedDays = ref(90)

async function fetchSettings() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get('/settings/system')
    days.value = data.assistant_retention_days
    savedDays.value = data.assistant_retention_days
  } catch {
    error.value = '系统参数读取失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    const { data } = await api.put('/settings/system', {
      assistant_retention_days: Number(days.value),
    })
    days.value = data.assistant_retention_days
    savedDays.value = data.assistant_retention_days
    ElMessage.success('聊天留存天数已保存')
  } catch (err) {
    error.value = err.response?.data?.detail || '保存失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="system-settings">
    <PageHeader title="系统参数" :breadcrumb="['设置', '系统参数']" />
    <section class="panel system-settings__panel">
      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
      <el-skeleton v-if="loading" :rows="3" animated />
      <template v-else>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="聊天留存天数">
            <el-input-number
              v-model="days"
              :min="0"
              :max="3650"
              :step="30"
              :disabled="!isSuper"
            />
            <p class="system-settings__hint">
              0 表示长期保留，默认 90 天；仅超级管理员可修改。
            </p>
          </el-form-item>
        </el-form>
        <div v-if="isSuper" class="system-settings__actions">
          <el-button
            type="primary"
            :loading="saving"
            :disabled="Number(days) === savedDays"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.system-settings__panel {
  padding: 24px;
  max-width: 560px;
}

.system-settings__hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 18px;
}

.system-settings__actions {
  margin-top: 16px;
}
</style>