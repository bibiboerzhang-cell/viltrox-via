// KPI 卡片的「外壳」定义:仅 id / label / sub / icon / format 会被消费。
// 每张卡的真实数值与 scope 数据由 normalizeDashboardMetrics() 用后端
// summary.evidence_metrics 覆写(见 normalizers.ts:line ~324 `values`)。
// 这里的 data 只是覆写失败时的「诚实兜底」——一律 value:null / source:"pending",
// 绝不放任何写死的假数字(历史上这里曾是 1041 / 367M 假值并误标 source:"real")。

import { Eye, RadioTower, ShoppingCart, Sparkles, Target, Users } from "lucide-react";

// 兜底:三个 scope 都标 pending、空值。真值永远来自 evidence_metrics 覆写。
const PENDING = {
  all: { value: null, source: "pending", color: "#a855f7", spark: null },
  kol: { value: null, source: "pending", color: "#ec4899", spark: null },
  company: { value: null, source: "pending", color: "#06b6d4", spark: null },
};

export const METRICS = [
  { id: "kol-count",  label: "Active Roster",    sub: "签约 / 矩阵 总数",        icon: Users,        data: PENDING, format: "number" },
  { id: "active-30d", label: "Active 30D",       sub: "本月有发帖",              icon: RadioTower,   data: PENDING, format: "number" },
  { id: "exposure",   label: "Total Exposure",   sub: "30d 增量，不取 lifetime", icon: Eye,          data: PENDING, format: "compact" },
  { id: "engagement", label: "Engagement Rate",  sub: "likes + comments / views", icon: Sparkles,    data: PENDING, format: "percent" },
  { id: "gmv",        label: "Attributed GMV",   sub: "Shopify 归因订单",        icon: ShoppingCart, data: PENDING, format: "money" },
  { id: "roi",        label: "Avg ROI",          sub: "GMV / Spend",             icon: Target,       data: PENDING, format: "multiplier" },
];
