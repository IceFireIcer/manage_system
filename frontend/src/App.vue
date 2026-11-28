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
    <p class="startup__text">
      <span class="startup__spinner" aria-hidden="true" />
      正在连接系统…
    </p>
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
  background:
    radial-gradient(700px 380px at 50% -6%, var(--brand-glow), transparent 70%),
    var(--page);
  text-align: center;
}

.startup__brand {
  display: flex;
  align-items: center;
  gap: 11px;
}

.startup__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--brand-mark-bg);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 14%);
  color: #FFFFFF;
}

.startup__name {
  font-size: 15px;
  line-height: 24px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--text);
}

.startup__text {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
}

.startup__spinner {
  width: 14px;
  height: 14px;
  border: 1.5px solid var(--line-strong);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: startup-spin 0.8s linear infinite;
}

@keyframes startup-spin {
  to {
    transform: rotate(360deg);
  }
}

.startup__title {
  margin: 0;
  font-size: 24px;
  line-height: 32px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--text);
}

.startup__desc {
  max-width: 44ch;
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--text-muted);
  text-wrap: pretty;
}
</style>