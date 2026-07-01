// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Database, Heart, LayoutDashboard, LineChart, MapPin, MessageSquare, PackageCheck, RadioTower, ShieldCheck, Sparkles, Target, TrendingUp, Users } from "lucide-react";

// B1 减法仪式:GEN2/Beta 徽章全挂在『此页面尚未接入』占位页上=假徽章,删。
// 真数据徽章(Discover/Signals/Agents 计数)是想要项,等数据侧支撑后挂回(总册 D6)。
// 2026-06-16:v2:true 的项归到侧边栏底部「V2」折叠组(默认收起,等 V2 再做)。
// 2026-06-30(L1):ops:true 的项归到侧边栏「智能运维」折叠组,接通 Wave1-4 已建页
//   (triage / 问数 / 市场趋势 / Skill Studio),默认展开,与「V2 待开发」占位组区分。
export const NAV_ITEMS = [
  // ── 主导航(常用,常驻顶部)──
  { key: "dashboard",    icon: LayoutDashboard, label: "Dashboard",       badge: null },
  { key: "my-kol",       icon: Heart,           label: "MY KOL",          badge: null },
  { key: "kol-pool",     icon: Users,           label: "KOL Pool",        badge: null },
  { key: "projects",     icon: Briefcase,       label: "Projects",        badge: null },
  { key: "events",       icon: Calendar,        label: "Events",          badge: "New" },
  { key: "shopify",      icon: PackageCheck,    label: "Shopify",         badge: null },
  { key: "dealers",      icon: MapPin,          label: "Dealers",         badge: null },
  // ── 智能运维(Wave1-4 已建页,接通可达)──
  { key: "triage",       icon: ShieldCheck,     label: "运维 Triage",     badge: null, ops: true },
  { key: "dataQuery",    icon: MessageSquare,   label: "问数",            badge: null, ops: true },
  { key: "marketTrends", icon: TrendingUp,      label: "市场趋势",        badge: null, ops: true },
  { key: "skillStudio",  icon: Sparkles,        label: "Skill Studio",    badge: null, ops: true },
  // ── V2(暂折叠在底部,等 V2 再开发)──
  { key: "intelligence", icon: Sparkles,        label: "Intelligence",    badge: null, v2: true },
  { key: "campaigns",    icon: Target,          label: "Campaigns",       badge: null, v2: true },
  { key: "attribution",  icon: LineChart,       label: "Attribution",     badge: null, v2: true },
  { key: "analytics",    icon: BarChart3,       label: "Analytics",       badge: null, v2: true },
  { key: "reports",      icon: Database,        label: "Reports",         badge: null, v2: true },
  { key: "signals",      icon: RadioTower,      label: "Signals",         badge: null, v2: true },
  { key: "agents",       icon: Bot,             label: "Agents",          badge: null, v2: true },
  { key: "p15",          icon: Boxes,           label: "P15 Warehouse",   badge: null, v2: true },
];
