import { defineStore } from "pinia";
import { api } from "../api";

export const useResearchStore = defineStore("research", {
  state: () => ({
    runId: "",
    status: "idle" as "idle" | "running" | "pending" | "success" | "failed",
    phase: "",
    progress: 0,
    result: null as any,
    error: "",
    draft: "",
    _pollTimer: null as any,
    _es: null as EventSource | null,
  }),
  actions: {
    _closeStream() {
      if (this._es) {
        this._es.close();
        this._es = null;
      }
    },
    _stopPoll() {
      if (this._pollTimer) clearInterval(this._pollTimer);
      this._pollTimer = null;
    },
    async submit(symbol: string, days: number, human: boolean) {
      // 新任务开始前必须清掉上一个任务的连接/计时器，
      // 否则旧 SSE 的事件会把新任务的状态拉回去（串扰）
      this._closeStream();
      this._stopPoll();
      this.runId = "";
      this.status = "running";
      this.phase = "accepted";
      this.progress = 1;
      this.result = null;
      this.error = "";
      this.draft = "";
      const r = await api.submit(symbol, days, human);
      this.runId = r.run_id;
      this.listen(r.run_id);
    },
    async adopt(runId: string) {
      const r = await api.get(runId);
      this.runId = runId;
      // 后端 "pending" 表示排队/初始化，不是待审批——映射成 running，
      // 避免对一个还没开始的任务渲染审批卡片
      this.status = r.status === "awaiting_approval" ? "pending" : r.status === "pending" ? "running" : r.status;
      this.draft = r.draft || "";
      this.phase = r.phase || "";
      this.progress = r.progress || 0;
      this.error = r.error || "";
      if (r.result) this.result = r.result;
      // 进行中的任务恢复实时更新（否则进度条永远停在 adopt 时的快照）
      if (this.status === "running") this.listen(runId);
      else if (this.status === "pending") this._poll(runId);
    },
    listen(runId: string) {
      this._closeStream();
      const es = new EventSource(`/api/research/${runId}/stream`);
      this._es = es;
      es.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === "node") {
          this.phase = ev.phase || "";
          this.progress = ev.progress || 0;
        } else if (ev.type === "awaiting_approval") {
          this.status = "pending";
          this.phase = "approval";
          this.draft = ev.draft || "";
          es.close();
          if (this._es === es) this._es = null;
        } else if (ev.type === "done") {
          this.status = ev.status === "success" ? "success" : "failed";
          if (ev.status === "success") this.result = ev.result;
          this.error = ev.error || this.error;
          es.close();
          if (this._es === es) this._es = null;
        }
      };
      es.onerror = () => {
        // SSE 断连（网络问题/任务被逐出）：关闭连接并降级为轮询，
        // 避免 EventSource 自动重连后重放历史 awaiting 造成状态回跳
        es.close();
        if (this._es === es) this._es = null;
        if (this.status === "running") this._poll(runId);
      };
    },
    async decide(action: string, text: string) {
      // 兜底：提交前先查最新状态；若已不是 awaiting，说明已被处理/过期，提示并清掉过期待审批
      let cur: any = null;
      try {
        cur = await api.get(this.runId);
      } catch (e) {
        cur = null;
      }
      if (cur && cur.status !== "awaiting_approval") {
        if (cur.status === "success") {
          this.status = "success";
          this.result = cur.result || null;
        } else if (cur.status === "failed") {
          this.status = "failed";
          this.error = cur.error || "生成失败。";
        } else {
          this.status = "running";
          this.phase = cur.phase || "";
          this.progress = cur.progress || 0;
        }
        throw new Error("这条任务已被处理，请到“历史记录”查看结果。");
      }
      await api.decide(this.runId, action, text, text);
      this.status = "running";
      this.phase = "resume";
      this.progress = 80;
      // 审批后不再重连 SSE（会重放历史 awaiting），改为轮询状态到终态，
      // 避免：point 后面板又回到 pending，导致二次提交 409。
      this._poll(this.runId);
    },
    _poll(runId: string) {
      this._stopPoll();
      let n = 0;
      this._pollTimer = setInterval(async () => {
        n += 1;
        if (n > 120) {
          // 轮询超限：明确置为失败并提示，而不是让进度条无限悬挂
          this._stopPoll();
          this.status = "failed";
          this.error = "任务状态更新超时，请到“历史记录”查看最终结果。";
          return;
        }
        try {
          const r = await api.get(runId);
          this.phase = r.phase || "";
          this.progress = r.progress || 0;
          if (r.status === "awaiting_approval") {
            this.status = "pending";
            this.phase = "approval";
            this.draft = r.draft || "";
            this._stopPoll();
          } else if (r.status === "success") {
            this.status = "success";
            this.result = r.result || null;
            this._stopPoll();
          } else if (r.status === "failed") {
            this.status = "failed";
            this.error = r.error || "报告生成失败。";
            this._stopPoll();
          } else {
            this.status = "running";
          }
        } catch (e) {
          // 忽略单次轮询失败
        }
      }, 1000);
    },
    resetState() {
      this._closeStream();
      this._stopPoll();
      this.runId = "";
      this.status = "idle";
      this.phase = "";
      this.progress = 0;
      this.result = null;
      this.error = "";
      this.draft = "";
    },
  },
});
