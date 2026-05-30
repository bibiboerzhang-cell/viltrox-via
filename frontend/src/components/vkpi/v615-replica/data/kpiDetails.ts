// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { Eye, RadioTower, ShoppingCart, Sparkles, Target, Users } from "lucide-react";

export const KPI_DETAILS = {
  // ─────────────────────────────────────────────────────────────────────
  // V6.9.1: 每个 KPI 三套 scope 数据(all / kol / company)
  // 不变的字段(title/icon/color/metaInfo)在外层
  // 变化的字段(primary/breakdown/movers)在 scopes[scope] 里
  // ─────────────────────────────────────────────────────────────────────
  
  // ─── 1. Active Roster ───
  "kol-count": {
    title: "Active Roster",
    subtitle: "Total contracted KOLs + matrix accounts",
    icon: Users,
    color: "#a855f7",
    metaInfo: [
      { label: "Updated", value: "5 min ago" },
      { label: "Source",  value: "V-KPI Database" },
      { label: "Refresh", value: "Every 1h" },
    ],
    scopes: {
      all: {
        scopeLabel: "All Accounts",
        primary: { value: 1041, label: "Total Active" },
        secondary: [
          { label: "KOL Roster",      value: 1023, color: "#ec4899" },
          { label: "Company Matrix",  value: 18,   color: "#06b6d4" },
        ],
        trend30d: [1013,1015,1017,1018,1020,1022,1024,1025,1027,1029,1030,1032,1033,1034,1035,1036,1037,1038,1039,1040,1040,1040,1041,1041,1041,1041,1041,1041,1041,1041],
        statusBreakdown: [
          { label: "Long-term Partner",  value: 287, pct: 27.6, color: "#10b981", desc: "Active contract · ongoing collab" },
          { label: "In Production",      value: 42,  pct: 4.0,  color: "#f59e0b", desc: "Currently shooting / producing" },
          { label: "One-off Project",    value: 583, pct: 56.0, color: "#3b82f6", desc: "Single campaign · completed or active" },
          { label: "Pending Contract",   value: 129, pct: 12.4, color: "#a855f7", desc: "Negotiating / awaiting signature" },
        ],
        breakdown: [
          { label: "Instagram",   value: 487, pct: 46.8, color: "#ec4899" },
          { label: "YouTube",     value: 312, pct: 30.0, color: "#ef4444" },
          { label: "TikTok",      value: 142, pct: 13.6, color: "#06b6d4" },
          { label: "Weibo",       value: 64,  pct: 6.1,  color: "#f59e0b" },
          { label: "Bilibili",    value: 36,  pct: 3.5,  color: "#22d3ee" },
        ],
        movers: [
          { handle: "@PeterLindgren",    change: "+12.4k followers", direction: "up",   platform: "YouTube" },
          { handle: "@MattiHaapoja",     change: "+8.7k followers",  direction: "up",   platform: "YouTube" },
          { handle: "@DSLRVideoShooter", change: "+5.2k followers",  direction: "up",   platform: "Instagram" },
          { handle: "@TokyoLens",        change: "+3.8k followers",  direction: "up",   platform: "Instagram" },
          { handle: "@CineEule",         change: "-340 followers",   direction: "down", platform: "YouTube" },
        ],
        moversTitle: "Top Movers (Last 30D)",
      },
      kol: {
        scopeLabel: "KOL Only · External Creators",
        primary: { value: 1023, label: "Active KOLs", suffix: "外部签约/合作创作者" },
        secondary: [
          { label: "Signed Contract",    value: 894,  color: "#10b981" },
          { label: "Pending / Trial",    value: 129,  color: "#a855f7" },
        ],
        trend30d: [995,997,999,1000,1002,1004,1006,1007,1009,1011,1012,1014,1015,1016,1017,1018,1019,1020,1021,1022,1022,1022,1023,1023,1023,1023,1023,1023,1023,1023],
        statusBreakdown: [
          { label: "Long-term Partner",  value: 287, pct: 28.1, color: "#10b981", desc: "Active contract · ongoing collab" },
          { label: "In Production",      value: 42,  pct: 4.1,  color: "#f59e0b", desc: "Currently shooting / producing" },
          { label: "One-off Project",    value: 565, pct: 55.2, color: "#3b82f6", desc: "Single campaign · completed or active" },
          { label: "Pending Contract",   value: 129, pct: 12.6, color: "#a855f7", desc: "Negotiating / awaiting signature" },
        ],
        breakdown: [
          { label: "Instagram",   value: 478, pct: 46.7, color: "#ec4899" },
          { label: "YouTube",     value: 305, pct: 29.8, color: "#ef4444" },
          { label: "TikTok",      value: 142, pct: 13.9, color: "#06b6d4" },
          { label: "Weibo",       value: 62,  pct: 6.1,  color: "#f59e0b" },
          { label: "Bilibili",    value: 36,  pct: 3.5,  color: "#22d3ee" },
        ],
        movers: [
          { handle: "@PeterLindgren",    change: "+12.4k followers", direction: "up",   platform: "YouTube" },
          { handle: "@MattiHaapoja",     change: "+8.7k followers",  direction: "up",   platform: "YouTube" },
          { handle: "@DSLRVideoShooter", change: "+5.2k followers",  direction: "up",   platform: "Instagram" },
          { handle: "@TokyoLens",        change: "+3.8k followers",  direction: "up",   platform: "Instagram" },
          { handle: "@CineEule",         change: "-340 followers",   direction: "down", platform: "YouTube" },
        ],
        moversTitle: "Top KOL Movers (Last 30D)",
      },
      company: {
        scopeLabel: "Company Matrix · 18 Accounts",
        primary: { value: 18, label: "Matrix Accounts", suffix: "公司自营矩阵账号" },
        secondary: [
          { label: "Main Brand",         value: 4,  color: "#ec4899" },
          { label: "Product Line",       value: 8,  color: "#a855f7" },
          { label: "Regional",           value: 6,  color: "#06b6d4" },
        ],
        trend30d: [16,16,16,17,17,17,17,17,17,17,17,17,17,17,17,17,17,17,18,18,18,18,18,18,18,18,18,18,18,18],
        // 公司账号不需要 Partnership Status(那是 KOL 才有的)
        statusBreakdown: null,
        breakdown: [
          { label: "Instagram",   value: 9,  pct: 50.0, color: "#ec4899" },
          { label: "YouTube",     value: 7,  pct: 38.9, color: "#ef4444" },
          { label: "TikTok",      value: 0,  pct: 0,    color: "#06b6d4" },
          { label: "Weibo",       value: 2,  pct: 11.1, color: "#f59e0b" },
        ],
        movers: [
          { handle: "Viltrox Official",      change: "+15.2k followers", direction: "up", platform: "Instagram" },
          { handle: "Viltrox Cinema",        change: "+8.4k followers",  direction: "up", platform: "YouTube" },
          { handle: "Viltrox China",         change: "+5.1k followers",  direction: "up", platform: "Weibo" },
          { handle: "Viltrox 135mm LAB",     change: "+3.8k followers",  direction: "up", platform: "Instagram" },
          { handle: "Viltrox EU Distributor", change: "+1.2k followers",  direction: "up", platform: "Instagram" },
        ],
        moversTitle: "Matrix Account Growth (Last 30D)",
      },
    },
  },
  
  // ─── 2. Active 30D ───
  "active-30d": {
    title: "Active in Last 30 Days",
    subtitle: "Accounts that published content this month",
    icon: RadioTower,
    color: "#3b82f6",
    metaInfo: [
      { label: "Window",   value: "Apr 25 – May 24" },
      { label: "Source",   value: "Aspire + Manual" },
      { label: "Refresh",  value: "Every 24h" },
    ],
    scopes: {
      all: {
        scopeLabel: "All Accounts",
        primary: { value: 301, label: "Active This Month", suffix: "/ 1,041 total" },
        secondary: [
          { label: "KOL Active",         value: 287, color: "#ec4899" },
          { label: "Matrix Active",      value: 14,  color: "#06b6d4" },
          { label: "Silent (no posts)",  value: 740, color: "#64748b" },
        ],
        trend30d: [245,250,255,258,261,265,268,270,272,274,277,279,281,282,284,286,288,289,290,292,293,295,296,298,299,300,300,301,301,301],
        breakdown: [
          { label: "Posted 10+ times",   value: 87,  pct: 28.9, color: "#10b981" },
          { label: "Posted 5–9 times",   value: 124, pct: 41.2, color: "#3b82f6" },
          { label: "Posted 1–4 times",   value: 90,  pct: 29.9, color: "#a855f7" },
        ],
        movers: [
          { handle: "@PotatoJet",        change: "First post in 2 months",  direction: "up",   platform: "YouTube" },
          { handle: "@DSLRVideoShooter", change: "23 posts this month",     direction: "up",   platform: "Instagram" },
          { handle: "@MattiHaapoja",     change: "15 posts this month",     direction: "up",   platform: "YouTube" },
          { handle: "@CameraConrad",     change: "Silent for 14 days",      direction: "down", platform: "Instagram" },
          { handle: "@LondonLensman",    change: "Silent for 21 days",      direction: "down", platform: "YouTube" },
        ],
        moversTitle: "Activity Highlights",
      },
      kol: {
        scopeLabel: "KOL Only · External Creators",
        primary: { value: 287, label: "Active KOLs This Month", suffix: "/ 1,023 KOL roster · 28% activity" },
        secondary: [
          { label: "High Activity (10+)", value: 87,  color: "#10b981" },
          { label: "Medium (5–9)",        value: 122, color: "#3b82f6" },
          { label: "Low (1–4)",           value: 78,  color: "#a855f7" },
          { label: "Silent KOLs",         value: 736, color: "#64748b" },
        ],
        trend30d: [232,237,242,245,248,252,255,257,259,261,264,266,268,269,271,273,275,276,277,279,280,282,283,284,285,286,286,287,287,287],
        breakdown: [
          { label: "Posted 10+ times",   value: 87,  pct: 30.3, color: "#10b981" },
          { label: "Posted 5–9 times",   value: 122, pct: 42.5, color: "#3b82f6" },
          { label: "Posted 1–4 times",   value: 78,  pct: 27.2, color: "#a855f7" },
        ],
        movers: [
          { handle: "@PotatoJet",        change: "First post in 2 months",  direction: "up",   platform: "YouTube" },
          { handle: "@DSLRVideoShooter", change: "23 posts this month",     direction: "up",   platform: "Instagram" },
          { handle: "@MattiHaapoja",     change: "15 posts this month",     direction: "up",   platform: "YouTube" },
          { handle: "@CameraConrad",     change: "Silent for 14 days",      direction: "down", platform: "Instagram" },
          { handle: "@LondonLensman",    change: "Silent for 21 days",      direction: "down", platform: "YouTube" },
        ],
        moversTitle: "KOL Activity Highlights",
      },
      company: {
        scopeLabel: "Company Matrix · 18 Accounts",
        primary: { value: 14, label: "Matrix Posting This Month", suffix: "/ 18 matrix · 78% activity" },
        secondary: [
          { label: "Posting Regularly",   value: 11,  color: "#10b981" },
          { label: "Occasional",          value: 3,   color: "#3b82f6" },
          { label: "Dormant",             value: 4,   color: "#64748b" },
        ],
        trend30d: [12,12,12,13,13,13,13,13,13,13,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14,14],
        breakdown: [
          { label: "Daily Posting",      value: 6,  pct: 42.9, color: "#10b981" },
          { label: "Weekly Posting",     value: 5,  pct: 35.7, color: "#3b82f6" },
          { label: "Monthly Posting",    value: 3,  pct: 21.4, color: "#a855f7" },
        ],
        movers: [
          { handle: "Viltrox Official",      change: "32 posts this month",  direction: "up", platform: "Instagram" },
          { handle: "Viltrox Cinema",        change: "18 posts this month",  direction: "up", platform: "YouTube" },
          { handle: "Viltrox China",         change: "15 posts this month",  direction: "up", platform: "Weibo" },
          { handle: "Viltrox EU",            change: "Silent for 7 days",    direction: "down", platform: "Instagram" },
        ],
        moversTitle: "Matrix Activity",
      },
    },
  },
  
  // ─── 3. Total Exposure ───
  "exposure": {
    title: "Total Exposure",
    subtitle: "Aggregate views/impressions across all content (last 30 days)",
    icon: Eye,
    color: "#a855f7",
    metaInfo: [
      { label: "Window",   value: "Apr 25 – May 24" },
      { label: "Source",   value: "Aspire + Platform APIs" },
      { label: "Refresh",  value: "Every 6h" },
    ],
    scopes: {
      all: {
        scopeLabel: "All Accounts",
        primary: { value: "367.61M", label: "Total Impressions" },
        secondary: [
          { label: "KOL Content",     value: "287.61M", color: "#ec4899" },
          { label: "Company Matrix",  value: "80.0M",   color: "#06b6d4" },
        ],
        trend30d: [298,305,312,318,326,333,341,348,354,358,362,365,366,367,368,369,370,371,372,372,373,373,373,372,372,371,370,369,368,368],
        breakdown: [
          { label: "Instagram",      value: "142.3M", pct: 38.7, color: "#ec4899" },
          { label: "YouTube",        value: "118.5M", pct: 32.2, color: "#ef4444" },
          { label: "TikTok",         value: "72.4M",  pct: 19.7, color: "#06b6d4" },
          { label: "Weibo",          value: "22.1M",  pct: 6.0,  color: "#f59e0b" },
          { label: "Other",          value: "12.3M",  pct: 3.4,  color: "#64748b" },
        ],
        movers: [
          { handle: "@PeterLindgren",     change: "+38.4M views (top)",  direction: "up", platform: "YouTube" },
          { handle: "@MattiHaapoja",      change: "+24.8M views",        direction: "up", platform: "YouTube" },
          { handle: "@DSLRVideoShooter",  change: "+18.2M views",        direction: "up", platform: "Instagram" },
          { handle: "@PotatoJet",         change: "+12.7M views",        direction: "up", platform: "YouTube" },
          { handle: "Viltrox Official",   change: "+22.1M views",        direction: "up", platform: "Instagram" },
        ],
        moversTitle: "Top Exposure Contributors",
      },
      kol: {
        scopeLabel: "KOL Only · External Creators",
        primary: { value: "287.61M", label: "KOL Content Impressions" },
        secondary: [
          { label: "Top 10 KOL",      value: "187.2M",  color: "#ec4899" },
          { label: "Mid (11–100)",    value: "82.4M",   color: "#3b82f6" },
          { label: "Long Tail",       value: "18.0M",   color: "#a855f7" },
        ],
        trend30d: [232,238,243,248,254,260,266,272,277,280,283,286,287,287,288,289,290,291,291,291,291,291,291,290,290,289,288,287,287,287],
        breakdown: [
          { label: "Instagram",      value: "108.2M", pct: 37.6, color: "#ec4899" },
          { label: "YouTube",        value: "96.4M",  pct: 33.5, color: "#ef4444" },
          { label: "TikTok",         value: "62.1M",  pct: 21.6, color: "#06b6d4" },
          { label: "Weibo",          value: "12.8M",  pct: 4.5,  color: "#f59e0b" },
          { label: "Other",          value: "8.1M",   pct: 2.8,  color: "#64748b" },
        ],
        movers: [
          { handle: "@PeterLindgren",     change: "+38.4M views (top)",  direction: "up", platform: "YouTube" },
          { handle: "@MattiHaapoja",      change: "+24.8M views",        direction: "up", platform: "YouTube" },
          { handle: "@DSLRVideoShooter",  change: "+18.2M views",        direction: "up", platform: "Instagram" },
          { handle: "@PotatoJet",         change: "+12.7M views",        direction: "up", platform: "YouTube" },
          { handle: "@TokyoLens",         change: "+9.4M views",         direction: "up", platform: "Instagram" },
        ],
        moversTitle: "Top KOL Contributors",
      },
      company: {
        scopeLabel: "Company Matrix · 18 Accounts",
        primary: { value: "80.0M", label: "Matrix Impressions" },
        secondary: [
          { label: "Main Brand",       value: "52.3M",   color: "#ec4899" },
          { label: "Product Line",     value: "18.7M",   color: "#a855f7" },
          { label: "Regional",         value: "9.0M",    color: "#06b6d4" },
        ],
        trend30d: [66,67,69,70,72,73,75,76,77,78,79,79,79,80,80,80,80,80,80,80,81,82,82,82,82,82,82,81,81,81],
        breakdown: [
          { label: "Instagram",      value: "34.1M", pct: 42.6, color: "#ec4899" },
          { label: "YouTube",        value: "22.1M", pct: 27.6, color: "#ef4444" },
          { label: "Weibo",          value: "14.5M", pct: 18.1, color: "#f59e0b" },
          { label: "Other",          value: "9.3M",  pct: 11.7, color: "#64748b" },
        ],
        movers: [
          { handle: "Viltrox Official",      change: "+22.1M views",  direction: "up", platform: "Instagram" },
          { handle: "Viltrox Cinema",        change: "+18.4M views",  direction: "up", platform: "YouTube" },
          { handle: "Viltrox China",         change: "+14.5M views",  direction: "up", platform: "Weibo" },
          { handle: "Viltrox 135mm LAB",     change: "+8.2M views",   direction: "up", platform: "Instagram" },
          { handle: "Viltrox EU",            change: "+3.8M views",   direction: "up", platform: "Instagram" },
        ],
        moversTitle: "Matrix Top Contributors",
      },
    },
  },
  
  // ─── 4. Engagement Rate ───
  "engagement": {
    title: "Engagement Rate",
    subtitle: "(Likes + Comments) / Views — weighted average",
    icon: Sparkles,
    color: "#f59e0b",
    metaInfo: [
      { label: "Formula",     value: "(Likes + Comments) ÷ Views" },
      { label: "Weighted by", value: "Impression volume" },
      { label: "Refresh",     value: "Every 6h" },
    ],
    scopes: {
      all: {
        scopeLabel: "All Accounts",
        primary: { value: "3.94%", label: "Weighted Avg ER" },
        secondary: [
          { label: "KOL Avg",    value: "4.21%", color: "#ec4899" },
          { label: "Matrix Avg", value: "2.87%", color: "#06b6d4" },
        ],
        trend30d: [3.6,3.65,3.7,3.72,3.75,3.78,3.81,3.84,3.86,3.87,3.88,3.89,3.90,3.91,3.92,3.93,3.93,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94,3.94],
        breakdown: [
          { label: "Reels / Shorts",       value: "5.21%", pct: 80,  color: "#ec4899" },
          { label: "Standard Posts",       value: "3.84%", pct: 60,  color: "#3b82f6" },
          { label: "Long-form YouTube",    value: "2.92%", pct: 45,  color: "#ef4444" },
          { label: "Stories",              value: "1.87%", pct: 30,  color: "#64748b" },
        ],
        movers: [
          { handle: "@MattiHaapoja",       change: "8.7% ER (top)",   direction: "up", platform: "YouTube" },
          { handle: "@PeterLindgren",      change: "7.4% ER",         direction: "up", platform: "YouTube" },
          { handle: "@TokyoLens",          change: "6.9% ER",         direction: "up", platform: "Instagram" },
          { handle: "@VideoActive",        change: "5.8% ER",         direction: "up", platform: "TikTok" },
          { handle: "@CineEule",           change: "1.2% ER (low)",   direction: "down", platform: "YouTube" },
        ],
        moversTitle: "Highest / Lowest ER",
      },
      kol: {
        scopeLabel: "KOL Only · External Creators",
        primary: { value: "4.21%", label: "KOL Weighted ER" },
        secondary: [
          { label: "Top 10 KOL Avg", value: "6.84%", color: "#10b981" },
          { label: "Mid Tier Avg",   value: "3.92%", color: "#3b82f6" },
          { label: "Long Tail Avg",  value: "2.14%", color: "#a855f7" },
        ],
        trend30d: [3.8,3.85,3.9,3.95,4.0,4.04,4.08,4.10,4.13,4.15,4.16,4.17,4.18,4.18,4.19,4.20,4.20,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21,4.21],
        breakdown: [
          { label: "Reels / Shorts",       value: "5.84%", pct: 85,  color: "#ec4899" },
          { label: "Standard Posts",       value: "4.12%", pct: 65,  color: "#3b82f6" },
          { label: "Long-form YouTube",    value: "3.21%", pct: 50,  color: "#ef4444" },
          { label: "Stories",              value: "1.94%", pct: 30,  color: "#64748b" },
        ],
        movers: [
          { handle: "@MattiHaapoja",       change: "8.7% ER (top)",   direction: "up", platform: "YouTube" },
          { handle: "@PeterLindgren",      change: "7.4% ER",         direction: "up", platform: "YouTube" },
          { handle: "@TokyoLens",          change: "6.9% ER",         direction: "up", platform: "Instagram" },
          { handle: "@VideoActive",        change: "5.8% ER",         direction: "up", platform: "TikTok" },
          { handle: "@CineEule",           change: "1.2% ER (low)",   direction: "down", platform: "YouTube" },
        ],
        moversTitle: "KOL Highest / Lowest ER",
      },
      company: {
        scopeLabel: "Company Matrix · 18 Accounts",
        primary: { value: "2.87%", label: "Matrix Weighted ER" },
        secondary: [
          { label: "Main Brand ER",   value: "3.42%", color: "#ec4899" },
          { label: "Product Line ER", value: "2.65%", color: "#a855f7" },
          { label: "Regional ER",     value: "2.18%", color: "#06b6d4" },
        ],
        trend30d: [2.5,2.55,2.58,2.62,2.65,2.68,2.71,2.74,2.76,2.78,2.80,2.81,2.82,2.83,2.83,2.84,2.85,2.85,2.86,2.86,2.87,2.87,2.87,2.87,2.87,2.87,2.87,2.87,2.87,2.87],
        breakdown: [
          { label: "Product Demo Reels",   value: "4.18%", pct: 65,  color: "#ec4899" },
          { label: "Brand Posts",          value: "2.92%", pct: 45,  color: "#3b82f6" },
          { label: "Tutorial Videos",      value: "2.21%", pct: 35,  color: "#ef4444" },
          { label: "Announcements",        value: "1.45%", pct: 22,  color: "#64748b" },
        ],
        movers: [
          { handle: "Viltrox Cinema",        change: "4.84% ER (top)",  direction: "up", platform: "YouTube" },
          { handle: "Viltrox Official",      change: "3.42% ER",        direction: "up", platform: "Instagram" },
          { handle: "Viltrox 135mm LAB",     change: "2.91% ER",        direction: "up", platform: "Instagram" },
          { handle: "Viltrox China",         change: "2.18% ER",        direction: "up", platform: "Weibo" },
          { handle: "Viltrox EU",            change: "1.04% ER (low)",  direction: "down", platform: "Instagram" },
        ],
        moversTitle: "Matrix Highest / Lowest ER",
      },
    },
  },
  
  // ─── 5. Attributed GMV (待接入) ───
  "gmv": {
    title: "Attributed GMV",
    subtitle: "Revenue traced back to KOL content via Shopify",
    icon: ShoppingCart,
    color: "#fbbf24",
    isPending: true,
    pipelineSteps: [
      { step: "Shopify Webhook",   status: "done",         desc: "API key + endpoint registered",        owner: "Engineering", date: "May 12" },
      { step: "Order Sync",        status: "in-progress",  desc: "Backfilling last 90 days of orders",   owner: "Tom Liu",     date: "May 22" },
      { step: "UTM Tagging",       status: "in-progress",  desc: "VIA-{handle}-10 discount codes live",  owner: "Maya Wong",   date: "May 24" },
      { step: "Attribution Logic", status: "pending",      desc: "Match orders to KOL via UTM + cookie", owner: "Engineering", date: "Jun 1 (ETA)" },
      { step: "Dashboard Display", status: "pending",      desc: "Show in KPI strip + drill-in",         owner: "Engineering", date: "Jun 5 (ETA)" },
    ],
    metaInfo: [
      { label: "Status",  value: "Integration in progress · 40%" },
      { label: "Owner",   value: "Engineering (Maya Wong lead)" },
      { label: "ETA",     value: "Jun 5, 2026" },
      { label: "Blocker", value: "Awaiting Shopify webhook secret rotation" },
    ],
  },
  
  // ─── 6. Avg ROI (待接入) ───
  "roi": {
    title: "Average ROI",
    subtitle: "GMV ÷ Total Spend — measures campaign efficiency",
    icon: Target,
    color: "#fbbf24",
    isPending: true,
    pipelineSteps: [
      { step: "GMV Integration",        status: "in-progress", desc: "Depends on Shopify pipeline",         owner: "Engineering",  date: "Jun 5 (ETA)" },
      { step: "Cost Tracking",          status: "in-progress", desc: "KOL payment records · upload to V-KPI", owner: "Finance",     date: "May 28" },
      { step: "Campaign Tagging",       status: "pending",     desc: "Each spend tagged by campaign ID",     owner: "Maya Wong",    date: "Jun 8 (ETA)" },
      { step: "Aggregation",            status: "pending",     desc: "Sum spend per period, divide by GMV",  owner: "Engineering",  date: "Jun 10 (ETA)" },
      { step: "Per-KOL ROI Drilldown",  status: "pending",     desc: "Show breakdown per KOL contract",      owner: "Engineering",  date: "Jun 15 (ETA)" },
    ],
    metaInfo: [
      { label: "Status",  value: "Awaiting upstream (Shopify + Cost)" },
      { label: "Owner",   value: "Engineering + Finance" },
      { label: "ETA",     value: "Jun 15, 2026" },
      { label: "Blocker", value: "Cost data still in Google Sheets, manual export" },
    ],
  },
};
