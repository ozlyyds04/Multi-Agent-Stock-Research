export function formatTime(ts?: string | number | null): string {
  if (ts == null) return "";
  // 内存模式的后端返回秒级 float 时间戳，而 new Date() 按毫秒解析；
  // 不补会显示成 1970 年。1e12（约 2001-09）之前的数值视为秒。
  let v: number | string = ts;
  if (typeof ts === "number" && ts > 0 && ts < 1e12) v = ts * 1000;
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
