<script setup>
import { computed } from 'vue'
import { ArrowRight, Box, User } from '@element-plus/icons-vue'

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
        title: '添加第一项实训耗材',
        action: 'go-archive',
      })
    } else {
      list.push({
        key: 'consumable',
        icon: Box,
        title: '启用耗材档案',
        action: 'go-archive',
      })
    }
  }
  if (props.enabledStudents === 0) {
    if (props.disabledStudents > 0) {
      list.push({
        key: 'student',
        icon: User,
        title: '启用已停用的学生账号',
        action: 'go-users',
        query: { role: 'student', status: 'disabled' },
      })
    } else {
      list.push({
        key: 'student',
        icon: User,
        title: '为学生分配账号',
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
      <span class="overview-setup__count">{{ steps.length }} 项待完成</span>
    </div>

    <ul class="overview-setup__list">
      <li v-for="step in steps" :key="step.key">
        <button
          type="button"
          class="overview-setup__step"
          @click="emit(step.action, step.query)"
        >
          <span class="overview-setup__icon">
            <el-icon :size="16"><component :is="step.icon" /></el-icon>
          </span>
          <span class="overview-setup__label">{{ step.title }}</span>
          <el-icon :size="14" class="overview-setup__chevron" aria-hidden="true">
            <ArrowRight />
          </el-icon>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.overview-setup {
  margin-bottom: 24px;
  padding: 8px 0;
}

.overview-setup__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 24px 10px;
}

.overview-setup__title {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.overview-setup__count {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.overview-setup__list {
  margin: 0;
  padding: 0 8px 8px;
  list-style: none;
}

.overview-setup__step {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 48px;
  padding: 8px 16px;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration-hover) var(--ease-out);
}

.overview-setup__step:hover {
  background: var(--primary-50);
}

.overview-setup__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--text-secondary);
}

.overview-setup__label {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.overview-setup__chevron {
  flex: 0 0 auto;
  color: var(--text-muted);
}

@media (max-width: 767px) {
  .overview-setup__head {
    padding: 12px 16px 8px;
  }

  .overview-setup__list {
    padding: 0 4px 4px;
  }

  .overview-setup__step {
    min-height: 56px;
    padding: 8px 12px;
  }
}
</style>