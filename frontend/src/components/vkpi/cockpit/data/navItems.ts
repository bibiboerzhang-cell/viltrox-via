// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Contact, Database, Heart, LayoutDashboard, LineChart, MapPin, MessageSquare, PackageCheck, RadioTower, Rocket, ShieldCheck, Sparkles, Target, TrendingUp, Users } from "lucide-react";

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
  // P1 智能可见周(拍定默认:入口全员可见)——Intelligent 问答 + 评论区销售员回复队列。
  { key: "intelligent",  icon: Sparkles,        label: "Intelligent 问答", badge: "New" },
  { key: "replyQueue",   icon: MessageSquare,   label: "回复队列",         badge: "New" },
  // 第2轮 档案工程:SKU 360°(产品视角反查内容/人)+ KOL 完整档案(八层组装页)。
  { key: "sku360",       icon: Boxes,           label: "SKU 360°",        badge: "New" },
  { key: "kolProfile",   icon: Contact,         label: "KOL 档案",         badge: "New" },
  // 第4轮 发射台:新品 SKU 一键出六输出全案(名单/预算/排期/打法/官号协同/覆盖组合)。
  { key: "launchpad",    icon: Rocket,          label: "发射台",           badge: "New" },
  // 第5轮 自治层:驾照板(挣来的自治 L0-L4)+ 市场之声(用户反馈反哺产品部)。
  { key: "autonomy",     icon: ShieldCheck,     label: "自治驾照",         badge: "New" },
  { key: "marketVoice",  icon: RadioTower,      label: "市场之声",         badge: "New" },
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
