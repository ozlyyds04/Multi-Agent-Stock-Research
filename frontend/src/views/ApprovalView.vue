<template>
  <el-card>
    <h3 class="sub">待审批任务</h3>
    <el-table :data="pending" :loading="loading" empty-text="暂无待审批任务">
      <el-table-column prop="run_id" label="run_id" width="120" />
      <el-table-column prop="symbol" label="代码" width="120" />
      <el-table-column prop="status" label="状态" width="140" />
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button size="small" @click="review(row)">去审阅</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { useResearchStore } from "../stores/research";

const router = useRouter();
const store = useResearchStore();
const pending = ref<any[]>([]);
const loading = ref(false);
const loadError = ref("");

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const d = await api.list(100);
    pending.value = d.runs.filter((r: any) => r.status === "awaiting_approval");
  } catch (e: any) {
    loadError.value = e?.message || "加载待审批任务失败。";
    ElMessage.error(loadError.value);
  } finally {
    loading.value = false;
  }
}

async function review(row: any) {
  // 通过 URL 传 runId，由研报页 onMounted 精确 adopt，避免显示 store 里上一个待审批
  router.push({ path: "/", query: { runId: row.run_id } });
}

onMounted(load);
</script>
<style scoped>
.sub { margin: 0 0 12px; }
</style>
