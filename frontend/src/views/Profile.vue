<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'

const MAX_SIDE = 256
const MAX_BYTES = 200 * 1024
const ROLE_LABEL = {
  super_admin: '超级管理员',
  teacher: '教师',
  admin: '班委',
  class_committee: '班委',
  student: '学生',
}

const userStore = useUserStore()
const loading = ref(true)
const saving = ref(false)
const profile = ref(null)
const avatarPreview = ref(null)
const avatarChanged = ref(false)
const fileInputRef = ref(null)

const form = reactive({
  name: '',
  phone: '',
  email: '',
  wechat: '',
  qq: '',
  github: '',
})

const roleLabel = computed(
  () => ROLE_LABEL[profile.value?.role] ?? profile.value?.role ?? '',
)
const avatarFallback = computed(() => (form.name?.trim()?.slice(0, 1) || '·'))

function applyProfile(data) {
  profile.value = data
  form.name = data.name ?? ''
  form.phone = data.phone ?? ''
  form.email = data.email ?? ''
  form.wechat = data.wechat ?? ''
  form.qq = data.qq ?? ''
  form.github = data.github ?? ''
  avatarPreview.value = data.avatar_base64 || null
  avatarChanged.value = false
}

async function loadProfile() {
  loading.value = true
  try {
    const { data } = await api.get('/profile')
    applyProfile(data)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '资料加载失败')
  } finally {
    loading.value = false
  }
}

function pickFile() {
  fileInputRef.value?.click()
}

function drawSquare(source, size) {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(0, 0, size, size)
  const side = Math.min(source.width, source.height)
  const sx = (source.width - side) / 2
  const sy = (source.height - side) / 2
  ctx.drawImage(source, sx, sy, side, side, 0, 0, size, size)
  return canvas
}

function encodeWithinLimit(canvas) {
  let quality = 0.9
  let dataUrl = canvas.toDataURL('image/jpeg', quality)
  while (dataUrl.length * 0.75 > MAX_BYTES && quality > 0.4) {
    quality -= 0.1
    dataUrl = canvas.toDataURL('image/jpeg', quality)
  }
  return dataUrl
}

function handleFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.error('请选择图片文件')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    const image = new Image()
    image.onload = () => {
      const canvas = drawSquare(image, MAX_SIDE)
      avatarPreview.value = encodeWithinLimit(canvas)
      avatarChanged.value = true
    }
    image.onerror = () => ElMessage.error('图片解析失败，请更换图片')
    image.src = reader.result
  }
  reader.onerror = () => ElMessage.error('图片读取失败，请重试')
  reader.readAsDataURL(file)
}

function clearAvatar() {
  avatarPreview.value = null
  avatarChanged.value = true
}

async function save() {
  if (saving.value) return
  if (!form.name.trim()) {
    ElMessage.error('姓名不能为空')
    return
  }
  saving.value = true
  try {
    if (avatarChanged.value) {
      const { data } = await api.put('/profile/avatar', {
        avatar_base64: avatarPreview.value || '',
      })
      avatarPreview.value = data.avatar_base64 || null
    }
    const { data } = await api.patch('/profile', {
      name: form.name,
      phone: form.phone,
      email: form.email,
      wechat: form.wechat,
      qq: form.qq,
      github: form.github,
    })
    applyProfile(data)
    if (userStore.user) {
      userStore.user.name = data.name
    }
    ElMessage.success('资料已保存')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存失败，请重试')
  } finally {
    saving.value = false
  }
}

onMounted(loadProfile)
</script>

<template>
  <div class="profile">
    <PageHeader title="个人资料" />

    <div v-loading="loading" class="profile__grid">
      <section class="panel profile__avatar-panel">
        <div class="profile__avatar">
          <img
            v-if="avatarPreview"
            :src="avatarPreview"
            alt="头像预览"
            class="profile__avatar-image"
          />
          <span v-else class="profile__avatar-fallback" aria-hidden="true">
            {{ avatarFallback }}
          </span>
        </div>
        <div class="profile__avatar-actions">
          <el-button size="small" @click="pickFile">选择图片</el-button>
          <el-button v-if="avatarPreview" size="small" text @click="clearAvatar">
            移除
          </el-button>
        </div>
        <input
          ref="fileInputRef"
          type="file"
          accept="image/*"
          class="profile__file"
          @change="handleFileChange"
        />
        <dl class="profile__meta">
          <div class="profile__meta-row">
            <dt>账号</dt>
            <dd>{{ profile?.account }}</dd>
          </div>
          <div class="profile__meta-row">
            <dt>角色</dt>
            <dd>{{ roleLabel }}</dd>
          </div>
          <div class="profile__meta-row">
            <dt>班级</dt>
            <dd>{{ profile?.class_name }}</dd>
          </div>
          <div class="profile__meta-row">
            <dt>学号</dt>
            <dd>{{ profile?.student_no || '未设置' }}</dd>
          </div>
          <div class="profile__meta-row">
            <dt>工号</dt>
            <dd>{{ profile?.employee_no || '未设置' }}</dd>
          </div>
        </dl>
      </section>

      <section class="panel profile__form-panel">
        <el-form label-position="top" class="profile__form" @submit.prevent="save">
          <div class="profile__fields">
            <el-form-item label="姓名">
              <el-input v-model="form.name" maxlength="64" />
            </el-form-item>
            <el-form-item label="手机号">
              <el-input v-model="form.phone" maxlength="32" placeholder="11 位手机号" />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input v-model="form.email" maxlength="128" />
            </el-form-item>
            <el-form-item label="微信">
              <el-input v-model="form.wechat" maxlength="64" />
            </el-form-item>
            <el-form-item label="QQ">
              <el-input v-model="form.qq" maxlength="32" />
            </el-form-item>
            <el-form-item label="GitHub">
              <el-input v-model="form.github" maxlength="64" />
            </el-form-item>
          </div>
          <div class="profile__footer">
            <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          </div>
        </el-form>
      </section>
    </div>
  </div>
</template>

<style scoped>
.profile__grid {
  display: grid;
  grid-template-columns: minmax(220px, 280px) 1fr;
  gap: 16px;
  align-items: start;
}

.profile__avatar-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 24px 20px;
}

.profile__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 112px;
  height: 112px;
  overflow: hidden;
  border-radius: 50%;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 40px;
  font-weight: 600;
}

.profile__avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.profile__avatar-actions {
  display: flex;
  gap: 4px;
}

.profile__file {
  display: none;
}

.profile__meta {
  width: 100%;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--line-soft);
  padding-top: 14px;
}

.profile__meta-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}

.profile__meta-row dt {
  color: var(--text-muted);
}

.profile__meta-row dd {
  margin: 0;
  color: var(--text);
  font-weight: 500;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile__form-panel {
  padding: 20px;
}

.profile__fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0 16px;
}

.profile__footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

@media (max-width: 767px) {
  .profile__grid {
    grid-template-columns: 1fr;
  }
}
</style>