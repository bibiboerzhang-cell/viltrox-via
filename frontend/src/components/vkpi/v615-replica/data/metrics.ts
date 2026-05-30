// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { Eye, RadioTower, ShoppingCart, Sparkles, Target, Users } from "lucide-react";

export const METRICS = [
  // ─── Card 1: 合作 KOL / 矩阵账号(合同总数) ───
  {
    id: "kol-count",
    label: "Active Roster",
    sub: "签约 / 矩阵 总数",
    icon: Users,
    data: {
      all:     { value: 1041,  trend: "+12 this week",      source: "real",    color: "#a855f7", spark: [1020,1022,1024,1027,1028,1031,1033,1035,1037,1038,1039,1040,1041,1041] },
      kol:     { value: 1023,  trend: "+12 this week",      source: "real",    color: "#ec4899", spark: [988,992,998,1001,1003,1006,1009,1011,1014,1017,1019,1020,1022,1023] },
      company: { value: 18,    trend: "+0 this week",       source: "real",    color: "#06b6d4", spark: [17,17,17,18,18,18,18,18,18,18,18,18,18,18] },
    },
    format: "number",
  },
  // ─── Card 2: 月活 ───
  {
    id: "active-30d",
    label: "Active 30D",
    sub: "本月有发帖",
    icon: RadioTower,
    data: {
      all:     { value: 301,   trend: "+28 vs last 30d",    source: "real",    color: "#a855f7", spark: [250,255,260,265,272,278,282,286,290,294,297,299,300,301] },
      kol:     { value: 287,   trend: "+24 vs last 30d",    source: "real",    color: "#ec4899", spark: [240,244,250,255,261,268,272,276,280,284,285,286,287,287] },
      company: { value: 14,    trend: "+4 vs last 30d",     source: "real",    color: "#06b6d4", spark: [10,10,11,11,12,12,13,13,13,14,14,14,14,14] },
    },
    format: "number",
  },
  // ─── Card 3: 总曝光 ───
  {
    id: "exposure",
    label: "Total Exposure",
    sub: "30天累计",
    icon: Eye,
    data: {
      all:     { value: 367610000,  trend: "+18.4% vs last 30d", source: "real",    color: "#a855f7", spark: [298,305,312,318,326,333,341,348,354,358,362,365,366,367] },
      kol:     { value: 287610000,  trend: "+22.1% vs last 30d", source: "real",    color: "#ec4899", spark: [222,229,238,246,253,260,267,272,277,281,284,286,287,287] },
      company: { value: 80000000,   trend: "+5.2% vs last 30d",  source: "real",    color: "#06b6d4", spark: [76,76,77,77,78,78,79,79,79,79,79,80,80,80] },
    },
    format: "compact",
  },
  // ─── Card 4: 内容互动率 ───
  {
    id: "engagement",
    label: "Engagement Rate",
    sub: "likes + comments / views",
    icon: Sparkles,
    data: {
      all:     { value: 3.94,  trend: "+0.18% vs last 30d", source: "real",    color: "#a855f7", spark: [3.6,3.65,3.7,3.72,3.75,3.78,3.81,3.84,3.87,3.89,3.91,3.92,3.93,3.94] },
      kol:     { value: 4.21,  trend: "+0.24% vs last 30d", source: "real",    color: "#ec4899", spark: [3.85,3.9,3.95,3.98,4.02,4.05,4.08,4.11,4.14,4.16,4.18,4.19,4.20,4.21] },
      company: { value: 2.87,  trend: "+0.05% vs last 30d", source: "real",    color: "#06b6d4", spark: [2.78,2.79,2.80,2.81,2.82,2.83,2.84,2.85,2.85,2.86,2.86,2.86,2.87,2.87] },
    },
    format: "percent",
  },
  // ─── Card 5: GMV / 订单 ───
  {
    id: "gmv",
    label: "Attributed GMV",
    sub: "Shopify 归因订单",
    icon: ShoppingCart,
    data: {
      all:     { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待 Shopify 订单接入" },
      kol:     { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待 Shopify 订单 + KOL 归因" },
      company: { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待 Shopify 订单 webhook" },
    },
    format: "money",
  },
  // ─── Card 6: ROI ───
  {
    id: "roi",
    label: "Avg ROI",
    sub: "GMV / Spend",
    icon: Target,
    data: {
      all:     { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待成本与订单接入" },
      kol:     { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待 KOL 成本接入" },
      company: { value: null,  trend: "Awaiting integration", source: "pending", color: "#fbbf24", spark: null, waiting: "待成本接入" },
    },
    format: "multiplier",
  },
];
