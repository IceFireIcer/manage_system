<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const loading = ref(false)
const items = ref([])
const statusFilter = ref('enabled')

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const formRef = ref()

const emptyForm = {
  name: '',
  spec: '',
  unit: '',
  location: '',
  safety_stock: 0,
  current_stock: 0,
}
const form = reactive({ ...emptyForm })

const integerRule = (message) => [
  { required: true, message, trigger: 'blur' },
  {
    validator: (_rule, value, callback) => {
      if (!Number.isInteger(Number(value)) || Number(value) < 0) {
        callback(new Error('必须为非负整数'))
      } else {
        callback()
      }
    },
    trigger: 'blur',
  },
]

const rules = {
  name: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        if (!String(value ?? '').trim()) callback(new Error('请输入耗材名称'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  spec: [{ required: true, message: '请输入规格', trigger: 'blur' }],
  unit: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        if (!String(value ?? '').trim()) callback(new Error('请输入计量单位'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  location: [{ required: true, message: '请输入存放位置', trigger: 'blur' }],
  safety_stock: integerRule('请输入安全库存线'),
  current_stock: integerRule('请输入当前库存'),
}

const dialogTitle = computed(() => (editingId.value ? '编辑耗材' : '新增耗材'))

async function fetchList() {
  loading.value = true
  try {
    const { data } = await api.get('/consumables', {
      params: { status: statusFilter.value },
    })
    items.value = data
  } catch {
    // 拦截器已统一提示 401/403，其余错误在此兜底
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, emptyForm)
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    spec: row.spec,
    unit: row.unit,
    location: row.location,
    safety_stock: row.safety_stock,
    current_stock: row.current_stock,
  })
  dialogVisible.value = true
}

async function submitForm() {
  await formRef.value.validate()
  submitting.value = true
  try {
    if (editingId.value) {
      await api.put(`/consumables/${editingId.value}`, {
        name: form.name,
        spec: form.spec,
        unit: form.unit,
        location: form.location,
        safety_stock: form.safety_stock,
      })
      ElMessage.success('耗材已更新')
    } else {
      await api.post('/consumables', { ...form })
      ElMessage.success('耗材已创建')
    }
    dialogVisible.value = false
    await fetchList()
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail) {
      const message = Array.isArray(detail)
        ? detail.map((item) => item.msg).join('；')
        : String(detail)
      ElMessage.error(message)
    }
  } finally {
    submitting.value = false
  }
}

async function disableConsumable(row) {
  try {
    await ElMessageBox.confirm(
      `确定停用「${row.name}」吗？建议先盘点库存再停用：停用后不再出现在领用与巡检中，历史流水保留。`,
      '停用耗材',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await api.patch(`/consumables/${row.id}/status`, { status: 'disabled' })
    ElMessage.success('耗材已停用')
    await fetchList()
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail) ElMessage.error(String(detail))
  }
}

async function enableConsumable(row) {
  try {
    await api.patch(`/consumables/${row.id}/status`, { status: 'enabled' })
    ElMessage.success('耗材已启用')
    await fetchList()
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail) ElMessage.error(String(detail))
  }
}

onMounted(fetchList)
</script>

<template>
  <el-card>
    <div class="toolbar">
      <div class="toolbar__title">
        <h2>耗材档案</h2>
        <p>维护班级耗材的名称、规格、单位、存放位置与安全库存线。</p>
      </div>
      <div class="toolbar__actions">
        <el-radio-group v-model="statusFilter" @change="fetchList">
          <el-radio-button value="enabled">启用中</el-radio-button>
          <el-radio-button value="all">全部</el-radio-button>
        </el-radio-group>
        <el-button type="primary" @click="openCreate">新增耗材</el-button>
      </div>
    </div>

    <el-table :data="items" v-loading="loading" border stripe>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="spec" label="规格" min-width="110" />
      <el-table-column prop="unit" label="单位" width="70" />
      <el-table-column prop="location" label="存放位置" min-width="130" />
      <el-table-column prop="current_stock" label="当前库存" width="100" sortable />
      <el-table-column prop="safety_stock" label="安全库存线" width="110" sortable />
      <el-table-column label="库存状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'disabled'" type="info" size="small">已停用</el-tag>
          <el-tag v-else-if="row.low_stock" type="danger" size="small">低库存</el-tag>
          <el-tag v-else type="success" size="small">正常</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            v-if="row.status === 'enabled'"
            link
            type="danger"
            size="small"
            @click="disableConsumable(row)"
          >
            停用
          </el-button>
          <el-button v-else link type="success" size="small" @click="enableConsumable(row)">
            启用
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="128" placeholder="如：焊丝" />
        </el-form-item>
        <el-form-item label="规格" prop="spec">
          <el-input v-model="form.spec" maxlength="128" placeholder="如：φ1.0mm" />
        </el-form-item>
        <el-form-item label="单位" prop="unit">
          <el-input v-model="form.unit" maxlength="16" placeholder="如：卷" />
        </el-form-item>
        <el-form-item label="存放位置" prop="location">
          <el-input v-model="form.location" maxlength="64" placeholder="如：实训室一号柜" />
        </el-form-item>
        <el-form-item label="安全库存线" prop="safety_stock">
          <el-input-number v-model="form.safety_stock" :min="0" :step="1" step-strictly />
        </el-form-item>
        <el-form-item v-if="!editingId" label="当前库存" prop="current_stock">
          <el-input-number v-model="form.current_stock" :min="0" :step="1" step-strictly />
          <div class="form-hint">保存后当前库存仅能经领用 / 撤销 / 补货变更</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.toolbar__title h2 {
  margin: 0 0 4px;
}

.toolbar__title p {
  margin: 0;
  color: #909399;
  font-size: 13px;
}

.toolbar__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.form-hint {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
</style>