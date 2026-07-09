// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Clapperboard, Compass, Contact, Database, Heart, LayoutDashboard, LineChart, MapPin, MessageSquare, PackageCheck, RadioTower, Rocket, ShieldCheck, Sparkles, Target, TrendingUp, Users } from "lucide-react";

// B1 减法仪式:GEN2/Beta 徽章全挂在『此页面尚未接入』占位页上=假徽章,删。
// 真数据徽章(Discover/Signals/Agents 计数)是想要项,等数据侧支撑后挂回(总册 D6)。
// 2026-06-16:v2:true 的项归到侧边栏底部「V2」折叠组(默认收起,等 V2 再做)。
// 2026-06-30(L1):ops:true 的项归到侧边栏「智能运维」折叠组,接通 Wave1-4 已建页
//   (triage / 问数 / 市场趋势 / Skill Studio),默认展开,与「V2 待开发」占位组区分。
export const NAV_ITEMS = [
  // ── 主导航(常用,常驻顶部)──
  // 分组(group)= 侧边栏分区标题(2026-07 门面对齐 mockup)。group 为附加字段,
  // 顶栏标题 / 移动端导航只读 key/label/icon,不受影响。项按 group 连续排列。
  // 总览
  { key: "dashboard",    icon: LayoutDashboard, label: "Dashboard",       badge: null, group: "总览" },
  // 达人运营
  { key: "my-kol",       icon: Heart,           label: "MY KOL",          badge: null, group: "达人运营" },
  { key: "kol-pool",     icon: Users,           label: "KOL Pool",        badge: null, group: "达人运营" },
  { key: "kolProfile",   icon: Contact,         label: "KOL 档案",         badge: null, group: "达人运营" },
  // 增长渠道
  { key: "projects",     icon: Briefcase,       label: "Projects",        badge: null, group: "增长渠道" },
  { key: "events",       icon: Calendar,        label: "Events",          badge: null, group: "增长渠道" },
  { key: "shopify",      icon: PackageCheck,    label: "Shopify",         badge: null, group: "增长渠道" },
  { key: "dealers",      icon: MapPin,          label: "Dealers",         badge: null, group: "增长渠道" },
  // 智能中枢
  { key: "intelligent",  icon: Sparkles,        label: "Intelligent 问答", badge: null, group: "智能中枢" },
  { key: "marketVoice",  icon: RadioTower,      label: "市场之声",         badge: null, group: "智能中枢" },
  { key: "sku360",       icon: Boxes,           label: "SKU 360°",        badge: null, group: "智能中枢" },
  { key: "creativeLibrary", icon: Clapperboard, label: "创意资产库",       badge: null, group: "智能中枢" },
  // 自动化
  { key: "replyQueue",   icon: MessageSquare,   label: "回复队列",         badge: null, group: "自动化" },
  { key: "launchpad",    icon: Rocket,          label: "发射台",           badge: null, group: "自动化" },
  { key: "autonomy",     icon: ShieldCheck,     label: "自治驾照",         badge: null, group: "自动化" },
  { key: "strategyBoard", icon: Target,          label: "战略台",           badge: null, group: "自动化" },
  { key: "gtmCommand",   icon: Compass,          label: "GTM Command",      badge: null, group: "自动化" },
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
