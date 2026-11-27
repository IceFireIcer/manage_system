<script setup>
import { computed } from 'vue'
import { Box, User } from '@element-plus/icons-vue'

const props = defineProps({
  loaded: {
    type: Boolean,
    default: false,
  },
  totalKinds: {
    type: Number,
    default: 0,
  },
  enabledKinds: {
    type: Number,
    default: 0,
  },
  enabledStudents: {
    type: Number,
    default: 0,
  },
  disabledStudents: {
    type: Number,
    default: 0,
  },
})

const emit = defineEmits(['go-archive', 'go-users'])

const steps = computed(() => {
  if (!props.loaded) return []
  const list = []
  if (props.enabledKinds === 0) {
    if (props.totalKinds === 0) {
      list.push({
        key: 'consumable',
        icon: Box,
        title: '先添加一项实训耗材',
        description: '建立耗材档案并填写期初数量和安全库存线，保存后总览会显示可用库存。',
        label: '去建档',
        action: 'go-archive',
      })
    } else {
      list.push({
        key: 'consumable',
        icon: Box,
        title: '当前没有启用耗材',
        description: '同类别的耗材已全部停用，启用后总览会重新显示库存。',
        label: '查看耗材档案',
        action: 'go-archive',
      })
    }
  }
  if (props.enabledStudents === 0) {
    if (props.disabledStudents > 0) {
      list.push({
        key: 'student',
        icon: User,
        title: '已有学生账号全部停用',
        description: '在用户管理中按已停用状态查看账号，启用后学生可以重新登录。',
        label: '查看已停用账号',
        action: 'go-users',
        query: { role: 'student', status: 'disabled' },
      })
    } else {
      list.push({
        key: 'student',
        icon: User,
        title: '给参加实训的学生分配账号',
        description: '学生用分配的账号登记本人用料，保存后可在用户管理核对账号状态。',
        label: '去用户管理',
        action: 'go-users',
        query: { role: 'student' },
      })
    }
  }
  return list
})
</script>

<template>
  <section v-if="steps.length" class="panel overview-setup" aria-label="开始使用">
    <div class="overview-setup__head">
      <h2 class="overview-setup__title">开始使用</h2>
      <p class="overview-setup__desc">完成下面未完成的步骤后，学生就能用自己的账号登记领用。</p>
    </div>

    <div v-for="step in steps" :key="step.key" class="overview-setup__step">
      <span class="overview-setup__icon">
        <el-icon :size="18"><component :is="step.icon" /></el-icon>
      </span>
      <div class="overview-setup__body">
        <p class="overview-setup__step-title">{{ step.title }}</p>
        <p class="overview-setup__step-desc">{{ step.description }}</p>
      </div>
      <el-button type="primary" plain class="overview-setup__action" @click="emit(step.action, step.query)">
        {{ step.label }}
      </el-button>
    </div>
  </section>
</template>

<style scoped>
.overview-setup {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 24px;
  padding: 24px;
}

.overview-setup__head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.overview-setup__title {
  margin: 0;
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
  color: var(--text);
}

.overview-setup__desc {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview-setup__step {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
}

.overview-setup__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-control);
  background: var(--primary-soft);
  color: var(--primary);
}

.overview-setup__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.overview-setup__step-title {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.overview-setup__step-desc {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.overview-setup__action {
  flex: 0 0 auto;
}

@media (max-width: 767px) {
  .overview-setup {
    padding: 16px;
  }

  .overview-setup__step {
    flex-wrap: wrap;
  }

  .overview-setup__action {
    width: 100%;
  }
}
</style>