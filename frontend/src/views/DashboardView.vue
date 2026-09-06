<template>
  <div>
    <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error=''" class="mb" />
    <el-row :gutter="16">
      <el-col v-for="card in cards" :key="card.label" :span="6">
        <el-card class="stat">
          <div class="num">{{ count(card.key) }}</div>
          <div class="label">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mt">
      <h3 class="sub">任务列表</h3>
      <el-table :data="runs.slice(0, 20)" :loading="loading">
        <el-table-column prop="symbol" label="代码" width="120" />
        <el-table-column prop="status" label="状态" width="150" />
        <el-table-column label="记忆">
          <template #default="{ row }">
            召回 {{ (row.result?.memory_read || []).length }} / 写入 {{ row.result?.memory_written || 0 }}
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="200">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-alert
      class="mt"
      type="info"
      :closable="false"
      title="LLM 成本 / token / 节点耗时 / 数据源错误等指标：GET /metrics（Prometheus），可导入 docs/grafana_dashboard.json 到 Grafana 查看。"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api } from "../api";
import { formatTime } from "../utils/time";

const runs = ref<any[]>([]);
const loading = ref(false);
const error = ref("");
const cards = [
  { key: "success", label: "成功" },
  { key: "failed", label: "失败" },
  { key: "awaiting_approval", label: "待审批" },
  { key: "running", label: "进行中" },
];
const count = (k: string) => runs.value.filter((r) => r.status === k).length;

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const d = await api.list(100);
    runs.value = d.runs;
  } catch (e: any) {
    error.value = e?.message || "加载监控数据失败。";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>
<style scoped>
.mt { margin-top: 16px; }
.mb { margin-bottom: 16px; }
.sub { margin: 0 0 12px; }
.stat { text-align: center; }
.num { font-size: 2rem; font-weight: 700; color: #0f172a; }
.label { color: #64748b; margin-top: 4px; }
</style>
