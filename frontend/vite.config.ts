import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      // 报告预览/PDF/图表走 /artifacts，不代理的话开发模式永远 404
      "/artifacts": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
