<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from './stores/user'
import ScreenReaderAnnouncer from './components/ScreenReaderAnnouncer.vue'

const userStore = useUserStore()
const router = useRouter()

const checking = computed(() => userStore.status === 'checking')
const failed = computed(() => userStore.status === 'error')

onMounted(() => userStore.init())

async function retry() {
  await userStore.init(true)
  if (userStore.status === 'error') return
  if (!userStore.isLoggedIn) {
    const current = router.currentRoute.value
    const query = current.meta?.public ? {} : { redirect: current.fullPath }
    router.replace({ path: '/login', query })
  }
}
</script>

<template>
  <ScreenReaderAnnouncer />

  <div v-if="checking" class="startup" role="status" aria-live="polite">
    <div class="startup__brand">
      <span class="startup__mark">
        <el-icon :size="24"><Box /></el-icon>
      </span>
      <span class="startup__name">班级实训耗材智能管理系统</span>
    </div>
    <p class="startup__text">正在连接系统…</p>
  </div>

  <div v-else-if="failed" class="startup" role="alert">
    <div class="startup__brand">
      <span class="startup__mark">
        <el-icon :size="24"><Box /></el-icon>
      </span>
      <span class="startup__name">班级实训耗材智能管理系统</span>
    </div>
    <h1 class="startup__title">暂时无法连接系统</h1>
    <p class="startup__desc">
      请检查网络连接或稍后再试。服务恢复后，登录状态有效时会回到原来的页面。
    </p>
    <el-button type="primary" @click="retry">重试</el-button>
  </div>

  <router-view v-else />
</template>

<style scoped>
.startup {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  min-height: 100vh;
  padding: 24px;
  background: var(--page);
  text-align: center;
}

.startup__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.startup__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-panel);
  background: var(--primary-soft);
  color: var(--primary);
}

.startup__name {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text);
}

.startup__text {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}

.startup__title {
  margin: 0;
  font-size: 24px;
  line-height: 32px;
  font-weight: 600;
  color: var(--text);
}

.startup__desc {
  max-width: 420px;
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
}
</style>