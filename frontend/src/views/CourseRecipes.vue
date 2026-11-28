<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const route = useRoute()

const PAGE_SIZE = 9
const SESSION_PAGE_SIZE = 10
const MAX_RECIPE_ITEMS = 3
const WEEK_PAGE_SIZE = 10
const WEEK_DAYS_OPTIONS = [7, 14, 30]

const SESSION_STATUS = {
  planned: { label: '待上课', tag: 'status-tag--info' },
  completed: { label: '已完成', tag: 'status-tag--success' },
  cancelled: { label: '已取消', tag: 'status-tag--neutral' },
}

const RISK_META = {
  sufficient: { label: '充足', tag: 'status-tag--success' },
  critical: { label: '临界', tag: 'status-tag--warning' },
  insufficient: { label: '不足', tag: 'status-tag--danger' },
  unconfigured: { label: '未配置', tag: 'status-tag--neutral' },
}

const courses = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const hasLoaded = ref(false)
const failed = ref(false)
const statusFilter = ref('enabled')
const keywordDraft = ref('')
const keyword = ref('')

const viewportWidth = ref(window.innerWidth)
const drawerSize = computed(() =>
  viewportWidth.value < 768 ? '100%' : '560px',
)

const courseDialogVisible = ref(false)
const courseSaving = ref(false)
const courseError = ref('')
const courseFormRef = ref()
const editingCourse = ref(null)
const courseForm = reactive({
  name: '',
  class_label: '',
  semester: '',
  status: 'enabled',
})

const courseRules = {
  name: [{ required: true, message: '请输入课程名称', trigger: 'blur' }],
  class_label: [{ required: true, message: '请输入班级标签', trigger: 'blur' }],
  semester: [{ required: true, message: '请输入学期', trigger: 'blur' }],
}

const activeCourse = ref(null)

const sessionDrawerOpen = ref(false)
const sessions = ref([])
const sessionTotal = ref(0)
const sessionPage = ref(1)
const sessionsLoading = ref(false)
const sessionError = ref('')
const sessionRisks = ref({})
const riskLoading = ref(false)
const riskError = ref(false)
const sessionDialogVisible = ref(false)
const sessionSaving = ref(false)
const sessionDialogError = ref('')
const editingSession = ref(null)
const sessionForm = reactive({
  seq: 1,
  session_date: '',
  headcount: 30,
  class_label: '',
  status: 'planned',
})

const recipeDrawerOpen = ref(false)
const recipeLoading = ref(false)
const recipeSaving = ref(false)
const recipeError = ref('')
const recipeItems = ref([])
const currentRecipe = ref(null)
const consumables = ref([])
const consumablesLoaded = ref(false)

const consumableOptions = computed(() =>
  consumables.value.map((item) => ({
    ...item,
    label: item.spec ? `${item.name} / ${item.spec}` : item.name,
  })),
)

const weekDrawerOpen = ref(false)
const weekDays = ref(7)
const weekLeadDays = ref(1)
const weekLoading = ref(false)
const weekError = ref('')
const weekData = ref(null)
const weekPage = ref(1)

const reviewDrawerOpen = ref(false)
const reviewSession = ref(null)
const reviewList = ref([])
const reviewSelected = ref(null)
const reviewCandidates = ref([])
const reviewLoading = ref(false)
const reviewGenerating = ref(false)
const reviewSaving = ref(false)
const reviewError = ref('')
const reviewForm = reactive({ reason_tags: [], teacher_note: '' })

const reviewTitle = computed(() =>
  reviewSession.value ? `第 ${reviewSession.value.seq} 次课 · 复盘` : '课次复盘',
)

const isEmpty = computed(() => hasLoaded.value && !courses.value.length)

function errorMessage(error, fallback) {
  if (!error?.response) return '暂未确认提交结果，请刷新后核对'
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean)
    return messages.length ? messages.join('；') : fallback
  }
  if (detail && typeof detail === 'object') {
    return detail.message || fallback
  }
  if (detail) return String(detail)
  return fallback
}

async function fetchWeekRisk() {
  weekLoading.value = true
  weekError.value = ''
  try {
    const { data } = await api.get('/courses/risk/week', {
      params: {
        days: weekDays.value,
        lead_days: weekLeadDays.value,
        page: weekPage.value,
        page_size: WEEK_PAGE_SIZE,
      },
    })
    weekData.value = data
  } catch (error) {
    weekError.value = errorMessage(error, '实训周聚合读取失败，请稍后重试')
    weekData.value = null
  } finally {
    weekLoading.value = false
  }
}

function openWeekRisk() {
  weekDrawerOpen.value = true
  weekPage.value = 1
  fetchWeekRisk()
}

function refreshWeek() {
  weekPage.value = 1
  fetchWeekRisk()
}

function handleWeekPageChange(value) {
  weekPage.value = value
  fetchWeekRisk()
}

function reviewDeviationText(review) {
  if (!review || !review.deviation_available) return '不计算'
  const ratio = review.deviation_ratio ?? 0
  const sign = ratio > 0 ? '+' : ''
  return `${sign}${(ratio * 100).toFixed(1)}%`
}

function reviewDeviationClass(review) {
  if (!review || !review.deviation_available || review.deviation_ratio === 0) {
    return ''
  }
  return review.deviation_ratio > 0 ? 'is-high' : 'is-low'
}

function selectReview(review) {
  reviewSelected.value = review
  Object.assign(reviewForm, {
    reason_tags: [...(review?.reason_tags ?? [])],
    teacher_note: review?.teacher_note ?? '',
  })
}

async function fetchReviews() {
  if (!activeCourse.value || !reviewSession.value) return
  reviewLoading.value = true
  reviewError.value = ''
  try {
    const { data } = await api.get(
      `/courses/${activeCourse.value.id}/sessions/${reviewSession.value.id}/reviews`,
    )
    reviewList.value = data.items ?? []
    reviewCandidates.value = data.reason_candidates ?? []
    selectReview(reviewList.value[0] ?? null)
  } catch (error) {
    reviewError.value = errorMessage(error, '复盘读取失败，请稍后重试')
  } finally {
    reviewLoading.value = false
  }
}

function openReview(session) {
  reviewSession.value = session
  reviewDrawerOpen.value = true
  reviewList.value = []
  reviewSelected.value = null
  reviewCandidates.value = []
  reviewError.value = ''
  Object.assign(reviewForm, { reason_tags: [], teacher_note: '' })
  fetchReviews()
}

async function generateReview() {
  if (reviewGenerating.value || !activeCourse.value || !reviewSession.value) return
  reviewGenerating.value = true
  reviewError.value = ''
  try {
    await api.post(
      `/courses/${activeCourse.value.id}/sessions/${reviewSession.value.id}/reviews`,
      {
        reason_tags: reviewForm.reason_tags,
        teacher_note: reviewForm.teacher_note.trim() || null,
      },
    )
    ElMessage.success('已生成复盘版本')
    await fetchReviews()
  } catch (error) {
    reviewError.value = errorMessage(error, '生成复盘失败，请稍后重试')
  } finally {
    reviewGenerating.value = false
  }
}

async function saveReview() {
  if (!reviewSelected.value || reviewSaving.value) return
  reviewSaving.value = true
  reviewError.value = ''
  try {
    await api.patch(`/courses/reviews/${reviewSelected.value.id}`, {
      reason_tags: reviewForm.reason_tags,
      teacher_note: reviewForm.teacher_note.trim() || null,
    })
    ElMessage.success('复盘已更新')
    await fetchReviews()
  } catch (error) {
    reviewError.value = errorMessage(error, '保存失败，请稍后重试')
  } finally {
    reviewSaving.value = false
  }
}

function weekItemKey(item) {
  return `${item.session_date}-${item.consumable_id}`
}

function weekLevelMeta(level) {
  return RISK_META[level] ?? { label: level, tag: 'status-tag--neutral' }
}

async function fetchCourses() {
  loading.value = true
  try {
    const { data } = await api.get('/courses', {
      params: {
        page: page.value,
        page_size: PAGE_SIZE,
        status: statusFilter.value,
        keyword: keyword.value || undefined,
      },
    })
    courses.value = data.items ?? []
    total.value = data.total ?? 0
    hasLoaded.value = true
    failed.value = false
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

function applySearch() {
  keyword.value = keywordDraft.value.trim()
  page.value = 1
  fetchCourses()
}

function clearFilters() {
  keywordDraft.value = ''
  keyword.value = ''
  statusFilter.value = 'enabled'
  page.value = 1
  fetchCourses()
}

function handleStatusChange() {
  page.value = 1
  fetchCourses()
}

function handlePageChange(value) {
  page.value = value
  fetchCourses()
}

function resetCourseForm() {
  Object.assign(courseForm, {
    name: '',
    class_label: '',
    semester: '',
    status: 'enabled',
  })
}

function openCreateCourse() {
  editingCourse.value = null
  resetCourseForm()
  courseError.value = ''
  courseDialogVisible.value = true
}

function openEditCourse(course) {
  editingCourse.value = course
  Object.assign(courseForm, {
    name: course.name,
    class_label: course.class_label,
    semester: course.semester,
    status: course.status,
  })
  courseError.value = ''
  courseDialogVisible.value = true
}

async function submitCourse() {
  if (courseSaving.value) return
  const valid = await courseFormRef.value?.validate().catch(() => false)
  if (!valid) return
  courseSaving.value = true
  courseError.value = ''
  const payload = {
    name: courseForm.name.trim(),
    class_label: courseForm.class_label.trim(),
    semester: courseForm.semester.trim(),
  }
  try {
    if (editingCourse.value) {
      await api.put(`/courses/${editingCourse.value.id}`, {
        ...payload,
        status: courseForm.status,
        expected_version: editingCourse.value.version,
      })
    } else {
      await api.post('/courses', payload)
    }
    courseDialogVisible.value = false
    ElMessage.success(editingCourse.value ? '课程已更新' : '课程已创建')
    await fetchCourses()
  } catch (error) {
    courseError.value = errorMessage(error, '保存失败，请稍后重试')
  } finally {
    courseSaving.value = false
  }
}

function todayValue() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function sessionStatusLabel(status) {
  return SESSION_STATUS[status]?.label ?? status
}

function sessionStatusTag(status) {
  return SESSION_STATUS[status]?.tag ?? 'status-tag--neutral'
}

function riskMeta(sessionId) {
  const risk = sessionRisks.value[sessionId]
  if (!risk) return null
  return RISK_META[risk.level] ?? { label: risk.level, tag: 'status-tag--neutral' }
}

function riskDetail(sessionId) {
  const risk = sessionRisks.value[sessionId]
  if (!risk || risk.level === 'sufficient' || risk.level === 'unconfigured') return ''
  const parts = []
  if (risk.gap > 0) {
    parts.push(
      `缺口 ${risk.gap}${risk.gap_consumable_name ? `（${risk.gap_consumable_name}）` : ''}`,
    )
  } else if (risk.level === 'critical' && risk.gap_consumable_name) {
    parts.push(`${risk.gap_consumable_name} 低于安全线`)
  }
  if (risk.arrival_deadline) parts.push(`${risk.arrival_deadline.slice(5)} 前到货`)
  return parts.join(' · ')
}

function riskPlaceholder(sessionId) {
  if (sessionRisks.value[sessionId]) return ''
  if (riskLoading.value) return '计算中'
  if (riskError.value) return '读取失败'
  return '—'
}

async function loadSessionRisks() {
  const course = activeCourse.value
  if (!course || !sessions.value.length) {
    sessionRisks.value = {}
    riskError.value = false
    return
  }
  const rows = [...sessions.value]
  riskLoading.value = true
  riskError.value = false
  const results = await Promise.allSettled(
    rows.map((row) =>
      api
        .get(`/courses/${course.id}/sessions/${row.id}/risk`)
        .then(({ data }) => data),
    ),
  )
  const next = {}
  let failed = false
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') next[rows[index].id] = result.value
    else failed = true
  })
  sessionRisks.value = next
  riskError.value = failed
  riskLoading.value = false
}

async function fetchSessions() {
  if (!activeCourse.value) return
  sessionsLoading.value = true
  sessionError.value = ''
  try {
    const { data } = await api.get(
      `/courses/${activeCourse.value.id}/sessions`,
      { params: { page: sessionPage.value, page_size: SESSION_PAGE_SIZE } },
    )
    sessions.value = data.items ?? []
    sessionTotal.value = data.total ?? 0
  } catch (error) {
    sessionError.value = errorMessage(error, '课次读取失败，请稍后重试')
  } finally {
    sessionsLoading.value = false
  }
  await loadSessionRisks()
}

function openSessions(course) {
  activeCourse.value = course
  sessionPage.value = 1
  sessionDrawerOpen.value = true
  fetchSessions()
}

function openCreateSession() {
  editingSession.value = null
  Object.assign(sessionForm, {
    seq: sessionTotal.value + 1,
    session_date: todayValue(),
    headcount: 30,
    class_label: activeCourse.value?.class_label ?? '',
    status: 'planned',
  })
  sessionDialogError.value = ''
  sessionDialogVisible.value = true
}

function openEditSession(session) {
  editingSession.value = session
  Object.assign(sessionForm, {
    seq: session.seq,
    session_date: session.session_date,
    headcount: session.headcount,
    class_label: session.class_label,
    status: session.status,
  })
  sessionDialogError.value = ''
  sessionDialogVisible.value = true
}

async function submitSession() {
  if (sessionSaving.value) return
  if (!sessionForm.session_date) {
    sessionDialogError.value = '请选择上课日期'
    return
  }
  sessionSaving.value = true
  sessionDialogError.value = ''
  const payload = {
    seq: Number(sessionForm.seq),
    session_date: sessionForm.session_date,
    headcount: Number(sessionForm.headcount),
    class_label: sessionForm.class_label.trim(),
    status: sessionForm.status,
  }
  try {
    if (editingSession.value) {
      await api.put(
        `/courses/${activeCourse.value.id}/sessions/${editingSession.value.id}`,
        payload,
      )
    } else {
      await api.post(`/courses/${activeCourse.value.id}/sessions`, payload)
    }
    sessionDialogVisible.value = false
    ElMessage.success(editingSession.value ? '课次已更新' : '课次已创建')
    await fetchSessions()
    await fetchCourses()
  } catch (error) {
    sessionDialogError.value = errorMessage(error, '保存失败，请稍后重试')
  } finally {
    sessionSaving.value = false
  }
}

function handleSessionPageChange(value) {
  sessionPage.value = value
  fetchSessions()
}

async function loadConsumables() {
  if (consumablesLoaded.value) return
  const { data } = await api.get('/consumables', {
    params: { status: 'enabled' },
  })
  consumables.value = Array.isArray(data) ? data : []
  consumablesLoaded.value = true
}

function emptyRecipeItem() {
  return { consumable_id: null, quantity_per_person: 1 }
}

async function fetchRecipe() {
  if (!activeCourse.value) return
  recipeLoading.value = true
  recipeError.value = ''
  try {
    const { data } = await api.get(
      `/courses/${activeCourse.value.id}/recipes/current`,
    )
    currentRecipe.value = data.configured ? data.snapshot : null
    recipeItems.value = data.configured
      ? data.snapshot.items.map((item) => ({
          consumable_id: item.consumable_id,
          quantity_per_person: item.quantity_per_person,
        }))
      : [emptyRecipeItem()]
  } catch (error) {
    recipeError.value = errorMessage(error, '配方读取失败，请稍后重试')
  } finally {
    recipeLoading.value = false
  }
}

async function openRecipe(course) {
  activeCourse.value = course
  recipeDrawerOpen.value = true
  recipeItems.value = [emptyRecipeItem()]
  currentRecipe.value = null
  try {
    await loadConsumables()
  } catch {
    recipeError.value = '耗材列表读取失败，请稍后重试'
  }
  await fetchRecipe()
}

function unitOf(consumableId) {
  const found = consumables.value.find((item) => item.id === consumableId)
  return found?.unit ?? ''
}

function addRecipeItem() {
  if (recipeItems.value.length >= MAX_RECIPE_ITEMS) return
  recipeItems.value.push(emptyRecipeItem())
}

function removeRecipeItem(index) {
  recipeItems.value.splice(index, 1)
  if (!recipeItems.value.length) recipeItems.value.push(emptyRecipeItem())
}

async function saveRecipe() {
  if (recipeSaving.value) return
  recipeError.value = ''
  const filled = recipeItems.value.filter((item) => item.consumable_id)
  if (!filled.length) {
    recipeError.value = '请至少选择一种耗材'
    return
  }
  const ids = filled.map((item) => item.consumable_id)
  if (new Set(ids).size !== ids.length) {
    recipeError.value = '配方中耗材不可重复'
    return
  }
  recipeSaving.value = true
  try {
    await api.post(`/courses/${activeCourse.value.id}/recipes`, {
      items: filled.map((item) => ({
        consumable_id: item.consumable_id,
        quantity_per_person: Number(item.quantity_per_person),
        unit: unitOf(item.consumable_id),
      })),
    })
    ElMessage.success('已保存配方快照')
    await fetchRecipe()
    await fetchCourses()
  } catch (error) {
    recipeError.value = errorMessage(error, '保存失败，请稍后重试')
  } finally {
    recipeSaving.value = false
  }
}

function handleResize() {
  viewportWidth.value = window.innerWidth
}

async function applyRouteQuery() {
  const courseId = Number(route.query.course_id)
  if (!Number.isInteger(courseId) || courseId <= 0) return
  let course = courses.value.find((item) => item.id === courseId)
  if (!course) {
    statusFilter.value = 'all'
    await fetchCourses()
    course = courses.value.find((item) => item.id === courseId)
  }
  if (!course) return
  await openRecipe(course)
  const consumableId = Number(route.query.consumable_id)
  if (
    Number.isInteger(consumableId) &&
    consumableId > 0 &&
    !recipeItems.value.some((item) => item.consumable_id === consumableId) &&
    recipeItems.value.length < MAX_RECIPE_ITEMS
  ) {
    recipeItems.value.push({
      consumable_id: consumableId,
      quantity_per_person: 1,
    })
  }
}

watch(
  () => [route.query.course_id, route.query.consumable_id],
  () => applyRouteQuery(),
)

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  await fetchCourses()
  await applyRouteQuery()
})

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<template>
  <div class="courses">
    <PageHeader title="课程配方" :breadcrumb="['教学计划', '课程配方']">
      <template #actions>
        <el-button type="primary" @click="openCreateCourse">新增课程</el-button>
      </template>
    </PageHeader>

    <div class="courses__toolbar panel">
      <el-radio-group
        v-model="statusFilter"
        class="courses__segment"
        aria-label="课程状态"
        @change="handleStatusChange"
      >
        <el-radio-button value="enabled">启用中</el-radio-button>
        <el-radio-button value="disabled">已停用</el-radio-button>
        <el-radio-button value="all">全部</el-radio-button>
      </el-radio-group>
      <div class="courses__search">
        <el-input
          v-model="keywordDraft"
          class="courses__search-input"
          placeholder="搜索课程、班级或学期"
          maxlength="64"
          clearable
          aria-label="搜索课程"
          @keyup.enter="applySearch"
          @clear="applySearch"
        />
        <el-button @click="applySearch">查询</el-button>
      </div>
      <el-button class="courses__week" @click="openWeekRisk">实训周聚合</el-button>
      <el-button text class="courses__refresh" :loading="loading" @click="fetchCourses">
        刷新
      </el-button>
    </div>

    <div v-if="!hasLoaded" class="courses__placeholder panel" :aria-busy="!failed">
      <el-skeleton v-if="!failed" :rows="4" animated />
      <div v-else class="courses__error" role="alert">
        <p class="courses__error-title">课程读取失败</p>
        <el-button @click="fetchCourses">重试</el-button>
      </div>
    </div>

    <template v-else>
      <EmptyState
        v-if="isEmpty"
        title="还没有课程"
        description="先创建课程，再维护课次与配方。"
      >
        <template #action>
          <el-button v-if="keyword || statusFilter !== 'enabled'" @click="clearFilters">
            清除筛选
          </el-button>
          <el-button type="primary" @click="openCreateCourse">新增课程</el-button>
        </template>
      </EmptyState>

      <div v-else class="courses__grid">
        <article v-for="course in courses" :key="course.id" class="course-card panel">
          <header class="course-card__head">
            <div class="course-card__title">
              <h2>{{ course.name }}</h2>
              <p>{{ course.class_label || '未填班级' }} · {{ course.semester || '未填学期' }}</p>
            </div>
            <span
              class="status-tag"
              :class="course.status === 'enabled' ? 'status-tag--success' : 'status-tag--neutral'"
            >
              {{ course.status === 'enabled' ? '启用' : '停用' }}
            </span>
          </header>
          <dl class="course-card__meta">
            <div>
              <dt>课次</dt>
              <dd>{{ course.session_count }}</dd>
            </div>
            <div>
              <dt>配方</dt>
              <dd>{{ course.recipe_version ? `v${course.recipe_version}` : '未配置' }}</dd>
            </div>
            <div>
              <dt>创建人</dt>
              <dd>{{ course.created_by_name }}</dd>
            </div>
          </dl>
          <footer class="course-card__actions">
            <el-button size="small" @click="openSessions(course)">课次</el-button>
            <el-button size="small" @click="openRecipe(course)">配方</el-button>
            <el-button size="small" text type="primary" @click="openEditCourse(course)">
              编辑
            </el-button>
          </footer>
        </article>
      </div>

      <div v-if="total > PAGE_SIZE" class="courses__pagination">
        <el-pagination
          layout="prev, pager, next"
          :total="total"
          :page-size="PAGE_SIZE"
          :current-page="page"
          background
          @current-change="handlePageChange"
        />
      </div>
    </template>

    <el-dialog
      v-model="courseDialogVisible"
      :title="editingCourse ? '编辑课程' : '新增课程'"
      :width="viewportWidth < 560 ? `${viewportWidth - 32}px` : '480px'"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-alert
        v-if="courseError"
        type="error"
        :closable="false"
        show-icon
        :title="courseError"
        class="courses__alert"
      />
      <el-form
        ref="courseFormRef"
        :model="courseForm"
        :rules="courseRules"
        label-position="top"
        @submit.prevent="submitCourse"
      >
        <el-form-item label="课程名称" prop="name">
          <el-input v-model="courseForm.name" maxlength="128" placeholder="如：焊接实训" />
        </el-form-item>
        <el-form-item label="班级标签" prop="class_label">
          <el-input v-model="courseForm.class_label" maxlength="64" placeholder="如：计算机 2301" />
        </el-form-item>
        <el-form-item label="学期" prop="semester">
          <el-input v-model="courseForm.semester" maxlength="32" placeholder="如：2026 秋" />
        </el-form-item>
        <el-form-item v-if="editingCourse" label="状态">
          <el-radio-group v-model="courseForm.status">
            <el-radio value="enabled">启用</el-radio>
            <el-radio value="disabled">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="courseSaving" @click="courseDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="courseSaving" @click="submitCourse">
          {{ editingCourse ? '保存修改' : '创建课程' }}
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="sessionDrawerOpen"
      :size="drawerSize"
      :title="activeCourse ? `${activeCourse.name} · 课次` : '课次'"
      destroy-on-close
    >
      <div class="drawer-body">
        <div class="drawer-head">
          <p class="drawer-head__hint">新题次默认使用当前最新配方快照</p>
          <el-button type="primary" size="small" @click="openCreateSession">新增课次</el-button>
        </div>

        <el-alert
          v-if="sessionError"
          type="error"
          :closable="false"
          show-icon
          :title="sessionError"
          class="courses__alert"
        />

        <el-alert
          v-if="riskError"
          type="warning"
          :closable="false"
          show-icon
          title="部分课次风险读取失败，其余信息不受影响"
          class="courses__alert"
        />

        <el-skeleton v-if="sessionsLoading && !sessions.length" :rows="4" animated />
        <EmptyState
          v-else-if="!sessions.length"
          title="暂无课次"
          description="添加课次后可绑定配方快照。"
        >
          <template #action>
            <el-button @click="openCreateSession">新增课次</el-button>
          </template>
        </EmptyState>
        <el-table v-else :data="sessions" row-key="id" class="table-dense">
          <el-table-column label="课次" width="64">
            <template #default="{ row }">第 {{ row.seq }} 次</template>
          </el-table-column>
          <el-table-column prop="session_date" label="日期" width="104" />
          <el-table-column label="人数" width="64" align="right">
            <template #default="{ row }">{{ row.headcount }} 人</template>
          </el-table-column>
          <el-table-column label="配方" width="72">
            <template #default="{ row }">
              {{ row.recipe_version ? `v${row.recipe_version}` : '未配置' }}
            </template>
          </el-table-column>
          <el-table-column label="风险" width="176">
            <template #default="{ row }">
              <div class="session-risk">
                <span
                  v-if="riskMeta(row.id)"
                  class="status-tag"
                  :class="riskMeta(row.id).tag"
                >
                  {{ riskMeta(row.id).label }}
                </span>
                <span v-else class="session-risk__placeholder">
                  {{ riskPlaceholder(row.id) }}
                </span>
                <span v-if="riskDetail(row.id)" class="session-risk__detail">
                  {{ riskDetail(row.id) }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="84">
            <template #default="{ row }">
              <span class="status-tag" :class="sessionStatusTag(row.status)">
                {{ sessionStatusLabel(row.status) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="128" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openEditSession(row)">
                编辑
              </el-button>
              <el-button link type="primary" size="small" @click="openReview(row)">
                复盘
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="sessionTotal > SESSION_PAGE_SIZE" class="drawer-pagination">
          <el-pagination
            layout="prev, pager, next"
            :total="sessionTotal"
            :page-size="SESSION_PAGE_SIZE"
            :current-page="sessionPage"
            small
            background
            @current-change="handleSessionPageChange"
          />
        </div>
      </div>
    </el-drawer>

    <el-dialog
      v-model="sessionDialogVisible"
      :title="editingSession ? '编辑课次' : '新增课次'"
      :width="viewportWidth < 560 ? `${viewportWidth - 32}px` : '440px'"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-alert
        v-if="sessionDialogError"
        type="error"
        :closable="false"
        show-icon
        :title="sessionDialogError"
        class="courses__alert"
      />
      <el-form label-position="top" @submit.prevent="submitSession">
        <el-form-item label="序号">
          <el-input-number v-model="sessionForm.seq" :min="1" :step="1" step-strictly />
        </el-form-item>
        <el-form-item label="上课日期">
          <el-date-picker
            v-model="sessionForm.session_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            class="courses__date"
          />
        </el-form-item>
        <el-form-item label="人数">
          <el-input-number v-model="sessionForm.headcount" :min="0" :step="1" step-strictly />
        </el-form-item>
        <el-form-item label="班级标签">
          <el-input v-model="sessionForm.class_label" maxlength="64" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="sessionForm.status">
            <el-option label="待上课" value="planned" />
            <el-option label="已完成" value="completed" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="sessionSaving" @click="sessionDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="sessionSaving" @click="submitSession">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="recipeDrawerOpen"
      :size="drawerSize"
      :title="activeCourse ? `${activeCourse.name} · 配方` : '配方'"
      destroy-on-close
    >
      <div class="drawer-body">
        <div class="recipe-meta">
          <span v-if="currentRecipe" class="status-tag status-tag--success">
            当前 v{{ currentRecipe.version }}
          </span>
          <span v-else class="status-tag status-tag--neutral">未配置</span>
          <span v-if="currentRecipe" class="recipe-meta__text">
            {{ currentRecipe.created_by_name }} · {{ currentRecipe.created_at }}
          </span>
        </div>

        <el-alert
          v-if="recipeError"
          type="error"
          :closable="false"
          show-icon
          :title="recipeError"
          class="courses__alert"
        />

        <el-skeleton v-if="recipeLoading" :rows="3" animated />
        <template v-else>
          <div v-for="(item, index) in recipeItems" :key="index" class="recipe-row">
            <el-select
              v-model="item.consumable_id"
              filterable
              placeholder="选择耗材"
              class="recipe-row__select"
            >
              <el-option
                v-for="option in consumableOptions"
                :key="option.id"
                :label="option.label"
                :value="option.id"
              />
            </el-select>
            <el-input-number
              v-model="item.quantity_per_person"
              :min="1"
              :step="1"
              step-strictly
              class="recipe-row__qty"
              aria-label="每人用量"
            />
            <el-input
              :model-value="unitOf(item.consumable_id)"
              class="recipe-row__unit"
              placeholder="单位"
              disabled
            />
            <el-button
              text
              type="danger"
              :disabled="recipeItems.length === 1"
              @click="removeRecipeItem(index)"
            >
              移除
            </el-button>
          </div>

          <div class="recipe-actions">
            <el-button :disabled="recipeItems.length >= MAX_RECIPE_ITEMS" @click="addRecipeItem">
              添加耗材
            </el-button>
            <el-button type="primary" :loading="recipeSaving" @click="saveRecipe">
              保存为新版本
            </el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <el-drawer
      v-model="weekDrawerOpen"
      :size="drawerSize"
      title="实训周聚合"
      destroy-on-close
    >
      <div class="drawer-body">
        <div class="week-toolbar">
          <el-radio-group v-model="weekDays" size="small" @change="refreshWeek">
            <el-radio-button
              v-for="option in WEEK_DAYS_OPTIONS"
              :key="option"
              :value="option"
            >
              {{ option }} 天
            </el-radio-button>
          </el-radio-group>
          <el-button text :loading="weekLoading" @click="refreshWeek">刷新</el-button>
        </div>
        <p class="week-hint">
          同班同日多课次对同一耗材合并判读；预计到货与在途不计入可用库存。
        </p>

        <el-alert
          v-if="weekError"
          type="error"
          :closable="false"
          show-icon
          :title="weekError"
          class="courses__alert"
        />

        <el-skeleton v-if="weekLoading && !weekData" :rows="4" animated />
        <template v-else-if="weekData">
          <EmptyState
            v-if="!weekData.items.length && !weekData.unconfigured.length"
            title="实训周内没有待上课次"
            description="仅统计未来窗口内状态为待上课的课次。"
          />
          <template v-else>
            <p v-if="!weekData.items.length" class="week-empty">
              本周没有可聚合的已配置课次。
            </p>
            <article
              v-for="item in weekData.items"
              :key="weekItemKey(item)"
              class="week-card panel"
            >
              <header class="week-card__head">
                <div class="week-card__title">
                  <h3>{{ item.session_date }} · {{ item.consumable_name }}</h3>
                  <p>
                    {{ item.class_label || '未填班级' }} · 合并需求
                    {{ item.required_quantity }}{{ item.unit }} / 可用
                    {{ item.available_quantity }}{{ item.unit }}
                  </p>
                </div>
                <span class="status-tag" :class="weekLevelMeta(item.level).tag">
                  {{ weekLevelMeta(item.level).label }}
                </span>
              </header>
              <p v-if="item.gap > 0" class="week-card__gap">
                合并缺口 {{ item.gap }}{{ item.unit }}
              </p>
              <p v-else-if="item.level === 'critical'" class="week-card__gap">
                低于安全库存 {{ item.safety_stock }}{{ item.unit }}
              </p>
              <p v-if="item.arrival_deadline" class="week-card__deadline">
                {{ item.arrival_deadline.slice(5) }} 前到货
              </p>
              <ul class="week-card__sessions">
                <li v-for="session in item.sessions" :key="session.session_id">
                  {{ session.course_name }} · 第 {{ session.seq }} 次
                  <span>
                    {{ session.headcount }} 人，需
                    {{ session.required_quantity }}{{ item.unit }}
                  </span>
                </li>
              </ul>
            </article>
            <div v-if="weekData.total > WEEK_PAGE_SIZE" class="drawer-pagination">
              <el-pagination
                layout="prev, pager, next"
                :total="weekData.total"
                :page-size="WEEK_PAGE_SIZE"
                :current-page="weekPage"
                small
                background
                @current-change="handleWeekPageChange"
              />
            </div>
          </template>

          <section v-if="weekData.unconfigured.length" class="week-unconfigured">
            <h3>未计入聚合的课次</h3>
            <ul>
              <li v-for="row in weekData.unconfigured" :key="row.session_id">
                {{ row.course_name }} · 第 {{ row.seq }} 次（{{ row.session_date }}）
                <span>{{ row.message }}</span>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </el-drawer>

    <el-drawer
      v-model="reviewDrawerOpen"
      :size="drawerSize"
      :title="reviewTitle"
      destroy-on-close
    >
      <div class="drawer-body">
        <div class="review-head">
          <p v-if="reviewSession" class="review-head__meta">
            {{ reviewSession.session_date }} · {{ reviewSession.headcount }} 人
          </p>
          <el-button
            type="primary"
            size="small"
            :loading="reviewGenerating"
            @click="generateReview"
          >
            生成复盘
          </el-button>
        </div>

        <el-alert
          v-if="reviewError"
          type="error"
          :closable="false"
          show-icon
          :title="reviewError"
          class="courses__alert"
        />

        <el-skeleton v-if="reviewLoading && !reviewList.length" :rows="4" animated />
        <EmptyState
          v-else-if="!reviewList.length"
          title="暂无复盘"
          description="生成后可对比实际净消耗与配方标准量。"
        >
          <template #action>
            <el-button type="primary" :loading="reviewGenerating" @click="generateReview">
              生成复盘
            </el-button>
          </template>
        </EmptyState>
        <template v-else-if="reviewSelected">
          <div class="review-metrics">
            <div class="review-metric">
              <span>实际净消耗</span>
              <strong>{{ reviewSelected.actual_net_quantity }}</strong>
            </div>
            <div class="review-metric">
              <span>配方标准量</span>
              <strong>{{ reviewSelected.standard_quantity }}</strong>
            </div>
            <div class="review-metric">
              <span>偏差</span>
              <strong :class="reviewDeviationClass(reviewSelected)">
                {{ reviewDeviationText(reviewSelected) }}
              </strong>
            </div>
          </div>
          <p v-if="!reviewSelected.deviation_available" class="review-note">
            标准量为 0，仅展示实际值。
          </p>
          <p class="review-note">
            v{{ reviewSelected.version }} · 配方 v{{ reviewSelected.recipe_version }} ·
            {{ reviewSelected.created_by_name }} · {{ reviewSelected.created_at }}
          </p>

          <ul class="review-items">
            <li v-for="item in reviewSelected.items" :key="item.consumable_id">
              <span>{{ item.consumable_name }}</span>
              <span>
                {{ item.quantity_per_person }} × {{ reviewSelected.headcount }} 人 =
                {{ item.standard_quantity }}{{ item.unit }}
              </span>
            </li>
          </ul>

          <el-form label-position="top">
            <el-form-item label="原因标签">
              <el-select
                v-model="reviewForm.reason_tags"
                class="review-tags"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="选择或补充原因"
              >
                <el-option
                  v-for="tag in reviewCandidates"
                  :key="tag"
                  :label="tag"
                  :value="tag"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="教师备注">
              <el-input
                v-model="reviewForm.teacher_note"
                type="textarea"
                :rows="2"
                maxlength="255"
                show-word-limit
              />
            </el-form-item>
          </el-form>
          <div class="review-actions">
            <el-button type="primary" :loading="reviewSaving" @click="saveReview">
              保存修改
            </el-button>
            <el-button text :loading="reviewGenerating" @click="generateReview">
              生成新版本
            </el-button>
          </div>

          <section class="review-history">
            <h3>历史版本</h3>
            <button
              v-for="row in reviewList"
              :key="row.id"
              type="button"
              class="review-history__item"
              :class="{ 'is-active': reviewSelected.id === row.id }"
              @click="selectReview(row)"
            >
              <span>v{{ row.version }}</span>
              <span>{{ row.created_at }}</span>
              <span>{{ row.actual_net_quantity }} / {{ row.standard_quantity }}</span>
            </button>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.courses {
  display: flex;
  flex-direction: column;
}

.courses__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
  padding: 12px 16px;
  margin-bottom: var(--space-4);
}

.courses__segment {
  padding: 2px;
  border-radius: var(--radius-control);
  background: var(--surface-muted);
}

.courses__segment :deep(.el-radio-button__inner) {
  min-height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: var(--radius-control);
  background: transparent;
  box-shadow: none;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  line-height: 30px;
}

.courses__segment :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: var(--primary-soft);
  box-shadow: none;
  color: var(--primary);
  font-weight: 600;
}

.courses__search {
  display: flex;
  flex: 1 1 260px;
  align-items: center;
  gap: 8px;
}

.courses__search-input {
  max-width: 300px;
}

.courses__refresh {
  margin-left: auto;
  color: var(--text-secondary);
}

.courses__placeholder {
  padding: var(--space-5);
}

.courses__error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.courses__error-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.courses__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}

.course-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.course-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.course-card__title h2 {
  margin: 0 0 4px;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.course-card__title p {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.course-card__meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 10px 0;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
}

.course-card__meta div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.course-card__meta dt {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.course-card__meta dd {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.course-card__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.courses__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-4);
}

.courses__alert {
  margin-bottom: var(--space-4);
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.drawer-head__hint {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.drawer-pagination {
  display: flex;
  justify-content: flex-end;
}

.session-risk {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.session-risk__placeholder {
  color: var(--text-muted);
  font-size: 13px;
  line-height: 18px;
}

.session-risk__detail {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 18px;
  font-variant-numeric: tabular-nums;
}

.recipe-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.recipe-meta__text {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.recipe-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px 80px auto;
  align-items: center;
  gap: 8px;
}

.recipe-row__select {
  width: 100%;
}

.recipe-row__unit {
  width: 100%;
}

.recipe-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: var(--space-2);
}

.courses__date {
  width: 100%;
}

.courses__week {
  min-height: 32px;
}

.week-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.week-hint {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.week-empty {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.week-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
}

.week-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.week-card__title h3 {
  margin: 0 0 4px;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.week-card__title p {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.week-card__gap {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.week-card__deadline {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.week-card__sessions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 8px 0 0;
  border-top: 1px solid var(--line-soft);
  list-style: none;
}

.week-card__sessions li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text);
}

.week-card__sessions span {
  color: var(--text-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.week-unconfigured {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: var(--space-2);
  border-top: 1px solid var(--line-soft);
}

.week-unconfigured h3 {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
}

.week-unconfigured ul {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.week-unconfigured span {
  display: block;
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.review-head__meta {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.review-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
}

.review-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.review-metric span {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
}

.review-metric strong {
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.review-metric strong.is-high {
  color: var(--danger);
}

.review-metric strong.is-low {
  color: var(--warning);
}

.review-note {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.review-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 8px 0;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  list-style: none;
}

.review-items li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text);
}

.review-items li span:last-child {
  color: var(--text-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.review-tags {
  width: 100%;
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.review-history {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: var(--space-2);
  border-top: 1px solid var(--line-soft);
}

.review-history h3 {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--text);
}

.review-history__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  line-height: 18px;
  font-variant-numeric: tabular-nums;
  text-align: left;
  cursor: pointer;
}

.review-history__item.is-active {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.review-history__item span:last-child {
  color: var(--text-muted);
}

.review-history__item.is-active span:last-child {
  color: var(--primary);
}

@media (max-width: 767px) {
  .courses__toolbar {
    gap: 8px;
    padding: 12px;
  }

  .courses__segment {
    flex: 1 1 100%;
    display: flex;
  }

  .courses__segment :deep(.el-radio-button) {
    flex: 1;
  }

  .courses__segment :deep(.el-radio-button__inner) {
    width: 100%;
    min-height: 38px;
    line-height: 38px;
    padding: 0 6px;
  }

  .courses__search {
    flex: 1 1 100%;
  }

  .courses__search-input {
    max-width: none;
    flex: 1;
  }

  .courses__refresh {
    margin-left: 0;
    min-height: 44px;
  }

  .course-card__actions :deep(.el-button) {
    min-height: 44px;
  }

  .courses__grid {
    grid-template-columns: 1fr;
  }

  .courses__week {
    min-height: 44px;
  }

  .week-toolbar :deep(.el-button) {
    min-height: 40px;
  }

  .week-card__sessions li {
    flex-direction: column;
    gap: 2px;
  }

  .week-card__sessions span {
    text-align: left;
  }

  .recipe-row {
    grid-template-columns: minmax(0, 1fr) 96px;
    row-gap: 8px;
  }

  .recipe-row__unit {
    grid-column: 1 / 2;
  }

  .recipe-actions :deep(.el-button) {
    min-height: 44px;
  }

  .review-head :deep(.el-button),
  .review-actions :deep(.el-button) {
    min-height: 44px;
  }

  .review-history__item {
    min-height: 44px;
  }

  .review-items li {
    flex-direction: column;
    gap: 2px;
  }

  .review-items li span:last-child {
    text-align: left;
  }
}
</style>