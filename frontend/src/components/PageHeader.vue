<script setup>
defineProps({
  title: {
    type: String,
    required: true,
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
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>