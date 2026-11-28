<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const title = computed(() => route.meta?.title ?? '设置')
</script>

<template>
  <div class="placeholder">
    <PageHeader
      :title="title"
      description="该设置页面正在建设中，敬请期待。"
      :breadcrumb="['设置', title]"
    />
    <div class="placeholder__panel panel">
      <el-empty description="功能建设中" />
    </div>
  </div>
</template>

<style scoped>
.placeholder__panel {
  padding: 32px;
}
</style>