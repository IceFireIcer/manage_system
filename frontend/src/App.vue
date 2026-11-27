<script setup>
</script>

<template>
  <el-container class="home">
    <el-header class="home__header">
      <h1 class="home__title">班级实训耗材智能管理系统</h1>
    </el-header>
    <el-main class="home__main">
      <el-card class="home__card">
        <p>工程骨架已就绪（T01）。</p>
        <p>后端健康检查：<code>GET /api/v1/health</code></p>
      </el-card>
    </el-main>
  </el-container>
</template>

<style scoped>
.home {
  min-height: 100vh;
}

.home__header {
  display: flex;
  align-items: center;
  background-color: #409eff;
}

.home__title {
  color: #fff;
  font-size: 20px;
  margin: 0;
}

.home__main {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 48px;
}

.home__card {
  width: 480px;
}
</style>