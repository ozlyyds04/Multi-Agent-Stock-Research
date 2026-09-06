<template>
  <el-container class="root">
    <el-aside width="200px">
      <div class="logo">多智能体股票研究</div>
      <el-menu :default-active="$route.path" router>
        <el-menu-item index="/">研报生成</el-menu-item>
        <el-menu-item index="/approval">审批中心</el-menu-item>
        <el-menu-item index="/history">历史记录</el-menu-item>
        <el-menu-item index="/dashboard">监控面板</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<style>
#app, .root { height: 100vh; }
.root { background: #f7f8fa; }
.el-aside { background: #fff; border-right: 1px solid #e2e8f0; }
.logo { font-weight: 700; padding: 18px 16px; font-size: 0.95rem; color: #0f172a; }
.el-main { padding: 20px; }
</style>
