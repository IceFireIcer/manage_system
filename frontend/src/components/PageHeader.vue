<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: '',
  },
  breadcrumb: {
    type: Array,
    default: () => [],
  },
})
</script>

<template>
  <header class="page-header">
    <div class="page-header__main">
      <el-breadcrumb v-if="breadcrumb.length" class="page-header__breadcrumb" separator="/">
        <el-breadcrumb-item v-for="(item, index) in breadcrumb" :key="`${item}-${index}`">
          {{ item }}
        </el-breadcrumb-item>
      </el-breadcrumb>
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="description" class="page-header__desc">{{ description }}</p>
      <slot name="description" />
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>