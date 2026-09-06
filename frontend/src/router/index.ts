import { createRouter, createWebHistory } from "vue-router";

const routes = [
  { path: "/", name: "research", component: () => import("../views/ResearchView.vue") },
  { path: "/approval", name: "approval", component: () => import("../views/ApprovalView.vue") },
  { path: "/history", name: "history", component: () => import("../views/HistoryView.vue") },
  { path: "/dashboard", name: "dashboard", component: () => import("../views/DashboardView.vue") },
];

export default createRouter({ history: createWebHistory(), routes });
