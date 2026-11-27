<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <section class="exception" aria-labelledby="forbidden-title">
    <h1 id="forbidden-title" class="exception__title">无权访问此页面</h1>
    <p class="exception__desc">
      当前账号没有查看该页面的权限，返回库存总览继续使用，或重新登录后再试。
    </p>
    <div class="exception__actions">
      <el-button type="primary" @click="router.push('/overview')">返回库存总览</el-button>
    </div>
  </section>
</template>