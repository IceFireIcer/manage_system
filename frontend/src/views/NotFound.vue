<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const isLoggedIn = computed(() => userStore.isLoggedIn)
</script>

<template>
  <div class="exception exception--full" aria-labelledby="not-found-title">
    <div class="exception__brand">
      <span class="exception__mark">
        <el-icon :size="24"><Box /></el-icon>
      </span>
      <span>班级实训耗材智能管理系统</span>
    </div>
    <h1 id="not-found-title" class="exception__title">页面不存在</h1>
    <p class="exception__desc">你访问的地址不存在或已被移除，请检查链接是否正确。</p>
    <div class="exception__actions">
      <el-button v-if="isLoggedIn" type="primary" @click="router.push('/overview')">
        返回库存总览
      </el-button>
      <el-button v-else type="primary" @click="router.push('/login')">去登录</el-button>
    </div>
  </div>
</template>