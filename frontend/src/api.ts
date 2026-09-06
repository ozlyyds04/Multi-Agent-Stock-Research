const BASE = (import.meta.env.VITE_API_URL as string) || "";
// 安全提示：VITE_ 前缀变量会被 Vite 内联进客户端 bundle，任何人在 DevTools 里都能看到。
// 它只能防误触，不是安全边界；真正的鉴权应放在反向代理/网关层。
const API_KEY = (import.meta.env.VITE_API_KEY as string) || "";

async function req(path: string, opts: RequestInit = {}) {
  const r = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json", ...(API_KEY ? { "X-API-Key": API_KEY } : {}) },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.status === "error") {
    throw new Error(data.reason || `HTTP ${r.status}`);
  }
  return data;
}

export const api = {
  submit: (symbol: string, days: number, human: boolean) =>
    req("/api/research", { method: "POST", body: JSON.stringify({ symbol, days, human }) }),
  list: (limit = 20, offset = 0) => req(`/api/research?limit=${limit}&offset=${offset}`),
  get: (id: string) => req(`/api/research/${id}`).then((d) => d.run),
  decide: (id: string, action: string, comment: string, edited: string) =>
    req(`/api/research/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ action, comment, edited_text: edited }),
    }),
};
