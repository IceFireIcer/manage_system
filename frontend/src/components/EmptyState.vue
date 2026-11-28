<script setup>
import { Box } from '@element-plus/icons-vue'

defineProps({
  title: {
    type: String,
    default: '暂无数据',
  },
  description: {
    type: String,
    default: '',
  },
  icon: {
    type: [Object, Function],
    default: () => Box,
  },
  tone: {
    type: String,
    default: 'neutral',
  },
})
</script>

<template>
  <div class="empty-state" :class="`empty-state--${tone}`" role="status">
    <div class="empty-state__icon">
      <el-icon :size="22"><component :is="icon" /></el-icon>
    </div>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="description" class="empty-state__desc">{{ description }}</p>
    <div v-if="$slots.action" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>