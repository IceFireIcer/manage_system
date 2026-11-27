<script setup>
defineProps({
  title: {
    type: String,
    default: '暂无数据',
  },
  description: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <div class="empty-state" role="status">
    <div class="empty-state__icon">
      <el-icon :size="20"><Box /></el-icon>
    </div>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="description" class="empty-state__desc">{{ description }}</p>
    <div v-if="$slots.action" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>