<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../stores/user'
import PageHeader from '../components/PageHeader.vue'

const userStore = useUserStore()

const ROLE_LABEL = {
  super_admin: '超级管理员',
  teacher: '教师',
  class_committee: '班委',
  admin: '班委',
  student: '学生',
}

const isSuperAdmin = computed(() => userStore.user?.role === 'super_admin')

const classes = ref([])
const loading = ref(false)
const activeClassId = ref(null)
const classDetail = ref(null)
const teachers = ref([])
const members = ref([])
const teacherCandidates = ref([])

const classForm = reactive({ visible: false, saving: false, id: null, name: '', code: '', status: 'enabled' })
const termForm = reactive({
  visible: false,
  saving: false,
  id: null,
  name: '',
  start_date: '',
  end_date: '',
  status: 'active',
})

const activeClass = computed(
  () => classes.value.find((item) => item.id === activeClassId.value) ?? null,
)

async function loadClasses() {
  loading.value = true
  try {
    const { data } = await api.get('/classes')
    classes.value = data
    if (!data.length) {
      activeClassId.value = null
      classDetail.value = null
      return
    }
    const exists = data.some((item) => item.id === activeClassId.value)
    if (!exists) activeClassId.value = userStore.activeClass?.id ?? data[0].id
    if (!data.some((item) => item.id === activeClassId.value)) activeClassId.value = data[0].id
    await loadClassDetail(activeClassId.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '加载班级失败')
  } finally {
    loading.value = false
  }
}

async function loadClassDetail(classId) {
  if (!classId) return
  try {
    const [detail, teacherRes, memberRes] = await Promise.all([
      api.get(`/classes/${classId}`),
      api.get(`/classes/${classId}/teachers`),
      api.get(`/classes/${classId}/members`),
    ])
    classDetail.value = detail.data
    teachers.value = teacherRes.data
    members.value = memberRes.data
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '加载班级详情失败')
  }
}

async function selectClass(classId) {
  activeClassId.value = classId
  await loadClassDetail(classId)
}

async function loadTeacherCandidates() {
  if (!isSuperAdmin.value) return
  try {
    const { data } = await api.get('/users', { params: { role: 'teacher' } })
    teacherCandidates.value = data
  } catch {
    teacherCandidates.value = []
  }
}

function openCreateClass() {
  classForm.visible = true
  classForm.id = null
  classForm.name = ''
  classForm.code = ''
  classForm.status = 'enabled'
}

function openEditClass() {
  if (!activeClass.value) return
  classForm.visible = true
  classForm.id = activeClass.value.id
  classForm.name = activeClass.value.name
  classForm.code = activeClass.value.code
  classForm.status = activeClass.value.status
}

async function submitClass() {
  if (!classForm.name.trim()) {
    ElMessage.warning('请填写班级名称')
    return
  }
  classForm.saving = true
  try {
    if (classForm.id) {
      await api.patch(`/classes/${classForm.id}`, {
        name: classForm.name.trim(),
        code: classForm.code,
        status: classForm.status,
      })
      ElMessage.success('班级已更新')
    } else {
      const { data } = await api.post('/classes', {
        name: classForm.name.trim(),
        code: classForm.code,
        status: classForm.status,
      })
      activeClassId.value = data.id
      ElMessage.success('班级已创建')
    }
    classForm.visible = false
    await loadClasses()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存班级失败')
  } finally {
    classForm.saving = false
  }
}

async function removeClass() {
  if (!activeClass.value) return
  const confirmed = await ElMessageBox.confirm(
    `确认删除班级「${activeClass.value.name}」？该操作不可恢复。`,
    '删除班级',
    { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) return
  try {
    await api.delete(`/classes/${activeClass.value.id}`)
    ElMessage.success('班级已删除')
    activeClassId.value = null
    await loadClasses()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '删除班级失败')
  }
}

function openCreateTerm() {
  termForm.visible = true
  termForm.id = null
  termForm.name = ''
  termForm.start_date = ''
  termForm.end_date = ''
  termForm.status = 'active'
}

function openEditTerm(term) {
  termForm.visible = true
  termForm.id = term.id
  termForm.name = term.name
  termForm.start_date = term.start_date || ''
  termForm.end_date = term.end_date || ''
  termForm.status = term.status
}

async function submitTerm() {
  if (!activeClassId.value) return
  if (!termForm.name.trim()) {
    ElMessage.warning('请填写学期名称')
    return
  }
  termForm.saving = true
  const payload = {
    name: termForm.name.trim(),
    start_date: termForm.start_date || null,
    end_date: termForm.end_date || null,
    status: termForm.status,
  }
  try {
    if (termForm.id) {
      await api.patch(`/classes/${activeClassId.value}/terms/${termForm.id}`, payload)
      ElMessage.success('学期已更新')
    } else {
      await api.post(`/classes/${activeClassId.value}/terms`, payload)
      ElMessage.success('学期已创建')
    }
    termForm.visible = false
    await loadClassDetail(activeClassId.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '保存学期失败')
  } finally {
    termForm.saving = false
  }
}

async function removeTerm(term) {
  const confirmed = await ElMessageBox.confirm(
    `确认删除学期「${term.name}」？`,
    '删除学期',
    { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) return
  try {
    await api.delete(`/classes/${activeClassId.value}/terms/${term.id}`)
    ElMessage.success('学期已删除')
    await loadClassDetail(activeClassId.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '删除学期失败')
  }
}

const assignUserId = ref(null)

async function addTeacher() {
  if (!assignUserId.value || !activeClassId.value) {
    ElMessage.warning('请选择教师')
    return
  }
  try {
    await api.post(`/classes/${activeClassId.value}/teachers`, { user_id: assignUserId.value })
    ElMessage.success('教师已分配')
    assignUserId.value = null
    await loadClassDetail(activeClassId.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '分配教师失败')
  }
}

async function removeTeacher(teacher) {
  const confirmed = await ElMessageBox.confirm(
    `确认将「${teacher.name}」移出本班？`,
    '移除教师',
    { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
  )
    .then(() => true)
    .catch(() => false)
  if (!confirmed) return
  try {
    await api.delete(`/classes/${activeClassId.value}/teachers/${teacher.user_id}`)
    ElMessage.success('教师已移除')
    await loadClassDetail(activeClassId.value)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '移除教师失败')
  }
}

onMounted(async () => {
  await loadClasses()
  await loadTeacherCandidates()
})
</script>

<template>
  <div class="classes-page">
    <PageHeader title="班级与学期" description="维护班级、学期与教师分配，切换顶栏班级后业务数据随之切换。" />

    <el-row :gutter="16">
      <el-col :xs="24" :md="8">
        <el-card shadow="never" class="classes-page__panel">
          <template #header>
            <div class="classes-page__panel-head">
              <span>班级列表</span>
              <el-button v-if="isSuperAdmin" type="primary" size="small" @click="openCreateClass">
                新建班级
              </el-button>
            </div>
          </template>
          <el-skeleton v-if="loading" :rows="4" animated />
          <el-empty v-else-if="!classes.length" description="暂无班级" />
          <ul v-else class="classes-page__list">
            <li
              v-for="item in classes"
              :key="item.id"
              class="classes-page__item"
              :class="{ 'classes-page__item--active': item.id === activeClassId }"
              @click="selectClass(item.id)"
            >
              <span class="classes-page__name">{{ item.name }}</span>
              <el-tag :type="item.status === 'enabled' ? 'success' : 'info'" size="small">
                {{ item.status === 'enabled' ? '启用' : '停用' }}
              </el-tag>
            </li>
          </ul>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="16">
        <el-card v-if="activeClass" shadow="never" class="classes-page__panel">
          <template #header>
            <div class="classes-page__panel-head">
              <span>{{ activeClass.name }}</span>
              <div class="classes-page__actions">
                <el-button size="small" @click="openEditClass">编辑</el-button>
                <el-button v-if="isSuperAdmin" size="small" type="danger" @click="removeClass">
                  删除
                </el-button>
              </div>
            </div>
          </template>

          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="班级编码">{{ activeClass.code || '—' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              {{ activeClass.status === 'enabled' ? '启用' : '停用' }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="classes-page__section">
            <div class="classes-page__section-head">
              <span>学期</span>
              <el-button size="small" type="primary" @click="openCreateTerm">新增学期</el-button>
            </div>
            <el-table :data="classDetail?.terms ?? []" size="small" empty-text="暂无学期">
              <el-table-column prop="name" label="名称" min-width="120" />
              <el-table-column prop="start_date" label="开始日期" width="120" />
              <el-table-column prop="end_date" label="结束日期" width="120" />
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
                    {{ row.status === 'active' ? '当前' : row.status === 'planned' ? '计划' : '归档' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="130">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openEditTerm(row)">编辑</el-button>
                  <el-button link type="danger" size="small" @click="removeTerm(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <div class="classes-page__section">
            <div class="classes-page__section-head">
              <span>任课教师</span>
            </div>
            <div v-if="isSuperAdmin" class="classes-page__assign">
              <el-select v-model="assignUserId" placeholder="选择教师" size="small" style="width: 200px">
                <el-option
                  v-for="candidate in teacherCandidates"
                  :key="candidate.id"
                  :label="`${candidate.name}（${candidate.account}）`"
                  :value="candidate.id"
                />
              </el-select>
              <el-button size="small" type="primary" @click="addTeacher">分配</el-button>
            </div>
            <el-table :data="teachers" size="small" empty-text="暂无分配教师">
              <el-table-column prop="name" label="姓名" min-width="100" />
              <el-table-column prop="account" label="账号" min-width="120" />
              <el-table-column v-if="isSuperAdmin" label="操作" width="100">
                <template #default="{ row }">
                  <el-button link type="danger" size="small" @click="removeTeacher(row)">移除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <div class="classes-page__section">
            <div class="classes-page__section-head">
              <span>班级成员</span>
            </div>
            <el-table :data="members" size="small" empty-text="暂无成员">
              <el-table-column prop="name" label="姓名" min-width="100" />
              <el-table-column prop="account" label="账号" min-width="120" />
              <el-table-column label="角色" width="100">
                <template #default="{ row }">{{ ROLE_LABEL[row.role] ?? row.role }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.status === 'enabled' ? 'success' : 'info'">
                    {{ row.status === 'enabled' ? '启用' : '停用' }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>
        <el-card v-else shadow="never" class="classes-page__panel">
          <el-empty description="请选择或创建一个班级" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="classForm.visible" :title="classForm.id ? '编辑班级' : '新建班级'" width="420px">
      <el-form label-position="top">
        <el-form-item label="班级名称">
          <el-input v-model="classForm.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="班级编码">
          <el-input v-model="classForm.code" maxlength="32" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="classForm.status" style="width: 100%">
            <el-option label="启用" value="enabled" />
            <el-option label="停用" value="disabled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="classForm.visible = false">取消</el-button>
        <el-button type="primary" :loading="classForm.saving" @click="submitClass">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="termForm.visible" :title="termForm.id ? '编辑学期' : '新增学期'" width="420px">
      <el-form label-position="top">
        <el-form-item label="学期名称">
          <el-input v-model="termForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="开始日期">
          <el-date-picker v-model="termForm.start_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="termForm.end_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="termForm.status" style="width: 100%">
            <el-option label="当前" value="active" />
            <el-option label="计划" value="planned" />
            <el-option label="归档" value="archived" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="termForm.visible = false">取消</el-button>
        <el-button type="primary" :loading="termForm.saving" @click="submitTerm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.classes-page__panel {
  border-radius: var(--radius-card);
}

.classes-page__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.classes-page__actions {
  display: flex;
  gap: 8px;
}

.classes-page__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.classes-page__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 6px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  cursor: pointer;
  transition: background-color var(--duration-hover) ease;
}

.classes-page__item:hover {
  background: var(--surface-muted);
}

.classes-page__item--active {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.classes-page__name {
  font-weight: 600;
  color: var(--text);
}

.classes-page__section {
  margin-top: 20px;
}

.classes-page__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-weight: 600;
}

.classes-page__assign {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
</style>