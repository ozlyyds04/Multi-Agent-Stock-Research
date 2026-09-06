<template>
  <el-card>
    <div class="toolbar">
      <h3 class="sub">历史记录</h3>
      <el-select v-model="statusFilter" placeholder="状态筛选" clearable style="width: 180px">
        <el-option v-for="s in ['success', 'failed', 'awaiting_approval', 'running']" :key="s" :label="s" :value="s" />
      </el-select>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error=''" class="mb" />
    <el-table :data="filtered" :loading="loading">
      <el-table-column label="时间" width="200">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="symbol" label="代码" width="120" />
      <el-table-column prop="status" label="状态" width="150" />
      <el-table-column label="记忆" width="160">
        <template #default="{ row }">召回 {{ (row.result?.memory_read || []).length }} / 写入 {{ row.result?.memory_written || 0 }}</template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }"><el-button size="small" @click="view(row)">查看</el-button></template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import { formatTime } from "../utils/time";

const router = useRouter();
const runs = ref<any[]>([]);
const statusFilter = ref("");
const loading = ref(false);
const error = ref("");
const filtered = computed(() =>
  statusFilter.value ? runs.value.filter((r) => r.status === statusFilter.value) : runs.value
);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const d = await api.list(100);
    runs.value = d.runs;
  } catch (e: any) {
    error.value = e?.message || "加载历史记录失败。";
  } finally {
    loading.value = false;
  }
}

function view(row: any) {
  router.push({ path: "/", query: { runId: row.run_id } });
}

onMounted(load);
</script>
<style scoped>
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
.sub { margin: 0; flex: 1; }
.mb { margin-bottom: 12px; }
</style>
