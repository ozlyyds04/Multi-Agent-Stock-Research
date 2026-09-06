<template>
  <div>
    <el-card>
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="股票代码">
          <el-input v-model="symbol" placeholder="如 AAPL / 600519 / 00700" clearable />
        </el-form-item>
        <el-form-item label="天数">
          <el-input-number v-model="days" :min="5" :max="15" />
        </el-form-item>
        <el-form-item label="人工审批">
          <el-switch v-model="human" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="busy" @click="go">生成报告</el-button>
        </el-form-item>
      </el-form>
      <el-alert v-if="store.error" :title="store.error" type="error" show-icon closable @close="store.error=''" />
    </el-card>

    <el-card v-if="store.status === 'running'" class="mt">
      <el-progress :percentage="store.progress" :status="store.progress ? '' : 'active'">
        <span>阶段：{{ store.phase || "…" }}</span>
      </el-progress>
    </el-card>

    <el-card v-if="store.status === 'pending'" class="mt">
      <h3 class="sub">人工审批</h3>
      <pre class="draft">{{ store.draft }}</pre>
      <el-radio-group v-model="action" class="mb">
        <el-radio label="approve">批准</el-radio>
        <el-radio label="reject">驳回并反馈</el-radio>
        <el-radio label="edit">修改后发布</el-radio>
      </el-radio-group>
      <el-input
        type="textarea"
        v-model="comment"
        :rows="6"
        placeholder="驳回时填写修改意见；修改时直接粘贴修改后的正文"
      />
      <div class="mt">
        <el-button type="primary" :loading="busy" @click="submitDecision">提交审批</el-button>
      </div>
    </el-card>

    <el-card v-if="store.status === 'success'" class="mt">      <el-result icon="success" title="报告生成成功" />
      <el-tabs v-model="activeTab" @tab-click="onTabClick">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="股票代码">{{ store.result?.symbol }}</el-descriptions-item>
            <el-descriptions-item label="PDF">
              <el-link type="primary" :href="dlUrl(store.result?.pdf)" target="_blank">打开 PDF</el-link>
            </el-descriptions-item>
            <el-descriptions-item label="Markdown">
              <el-link type="primary" :href="dlUrl(store.result?.report)" target="_blank">打开报告</el-link>
            </el-descriptions-item>
            <el-descriptions-item label="图表">
              <el-link type="primary" :href="dlUrl(store.result?.plot)" target="_blank">打开图表</el-link>
            </el-descriptions-item>
            <el-descriptions-item label="原始JSON">
              <el-link type="primary" :href="dlUrl(store.result?.raw)" target="_blank">打开 JSON</el-link>
            </el-descriptions-item>
            <el-descriptions-item label="长期记忆">
              召回 {{ (store.result?.memory_read || []).length }} 条 · 写入
              {{ store.result?.memory_written || 0 }} · 压缩 {{ store.result?.memory_compacted || 0 }}
            </el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
        <el-tab-pane label="报告预览" name="report">
          <div v-if="reportLoading" class="muted">报告加载中…</div>
          <div v-else-if="reportHtml" class="report" v-html="reportHtml"></div>
          <div v-else class="muted">暂无报告内容（可点击“打开报告”下载）。</div>
        </el-tab-pane>
      </el-tabs>
      <div class="mt"><el-button @click="store.resetState()">清除结果</el-button></div>
    </el-card>

    <el-card v-if="store.status === 'failed'" class="mt">
      <el-result icon="error" title="报告生成失败">
        <template #sub-title>
          <p>{{ store.error || "生成过程中出现错误，请稍后重试。" }}</p>
        </template>
      </el-result>
      <div class="mt">
        <el-button @click="store.resetState()">重新开始</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { useRoute } from "vue-router";
import { useResearchStore } from "../stores/research";

const store = useResearchStore();
const route = useRoute();
const symbol = ref("");
const days = ref(10);
const human = ref(false);
const action = ref("approve");
const comment = ref("");
const busy = ref(false);
const activeTab = ref("overview");
const reportHtml = ref("");
const reportLoading = ref(false);

function dlUrl(p?: string) {
  if (!p) return "";
  return "/" + String(p).replace(/\\/g, "/");
}

async function loadReport() {
  const p = store.result?.report;
  if (!p) return;
  reportLoading.value = true;
  try {
    const res = await fetch(dlUrl(p));
    if (!res.ok) {
      reportHtml.value = "";
      return;
    }
    const md = await res.text();
    const fixed = md.replace(/src="([^"]*)[\\/]artifacts[\\/]([^"]*)"/g, 'src="/artifacts/$2"');
    reportHtml.value = DOMPurify.sanitize(marked.parse(fixed) as string);
  } catch (e) {
    reportHtml.value = "";
  } finally {
    reportLoading.value = false;
  }
}

function onTabClick(pane: any) {
  if (pane?.name === "report") loadReport();
}

watch(
  () => store.status,
  async (s) => {
    if (s === "success") await loadReport();
  }
);

async function go() {
  busy.value = true;
  store.error = "";
  try {
    await store.submit(symbol.value, days.value, human.value);
  } catch (e: any) {
    store.error = e.message;
  }
  busy.value = false;
}

async function submitDecision() {
  busy.value = true;
  store.error = "";
  try {
    await store.decide(action.value, comment.value);
  } catch (e: any) {
    store.error = e.message;
  }
  busy.value = false;
}

onMounted(async () => {
  const runId = route.query.runId as string;
  if (runId) {
    store.resetState();
    store.status = "running";
    store.phase = "加载中…";
    store.progress = 0;
    try {
      await store.adopt(runId);
    } catch (e: any) {
      // run 可能已被淘汰/不存在：明确报错，而不是永远停在"加载中"
      store.status = "failed";
      store.error = e?.message || "任务不存在或已过期。";
    }
  }
});
</script>

<style scoped>
.mt { margin-top: 16px; }
.mb { margin-bottom: 12px; }
.sub { margin: 0 0 10px; }
.draft { white-space: pre-wrap; background: #f7f8fa; padding: 12px; border-radius: 6px; }
.report { line-height: 1.7; font-size: 0.95rem; }
.report :deep(h1) { font-size: 1.4rem; margin: 0.6rem 0; }
.report :deep(h2) { font-size: 1.2rem; margin: 0.6rem 0 0.3rem; border-bottom: 1px solid #eef1f6; padding-bottom: 4px; }
.report :deep(h3) { font-size: 1.05rem; margin: 0.5rem 0 0.2rem; }
.report :deep(h4) { font-size: 1rem; margin: 0.4rem 0 0.2rem; }
.report :deep(p) { margin: 0.4rem 0; }
.report :deep(ul), .report :deep(ol) { padding-left: 1.4rem; margin: 0.4rem 0; }
.report :deep(img) { max-width: 90%; display: block; margin: 12px auto; }
.report :deep(blockquote) { margin: 0.5rem 0; padding: 0.3rem 0.8rem; border-left: 3px solid #cbd5e1; background: #f8fafc; }
.muted { color: #94a3b8; }
</style>
