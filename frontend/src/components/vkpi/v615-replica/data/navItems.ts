// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Database, Heart, LayoutDashboard, LineChart, PackageCheck, RadioTower, Sparkles, Target, Users } from "lucide-react";

// B1 减法仪式:GEN2/Beta 徽章全挂在『此页面尚未接入』占位页上=假徽章,删。
// 真数据徽章(Discover/Signals/Agents 计数)是想要项,等数据侧支撑后挂回(总册 D6)。
export const NAV_ITEMS = [
  { key: "dashboard",    icon: LayoutDashboard, label: "Dashboard",       badge: null },
  { key: "intelligence", icon: Sparkles,        label: "Intelligence",    badge: null },
  { key: "my-kol",       icon: Heart,           label: "MY KOL",          badge: null },
  { key: "kol-pool",     icon: Users,           label: "KOL Pool",        badge: null },
  { key: "projects",     icon: Briefcase,       label: "Projects",        badge: null },
  { key: "campaigns",    icon: Target,          label: "Campaigns",       badge: null },
  { key: "events",       icon: Calendar,        label: "Events",          badge: "New" },
  { key: "attribution",  icon: LineChart,       label: "Attribution",     badge: null },
  { key: "analytics",    icon: BarChart3,       label: "Analytics",       badge: null },
  { key: "reports",      icon: Database,        label: "Reports",         badge: null },
  { key: "signals",      icon: RadioTower,      label: "Signals",         badge: null },
  { key: "agents",       icon: Bot,             label: "Agents",          badge: null },
  { key: "p15",          icon: Boxes,           label: "P15 Warehouse",   badge: null },
  { key: "shopify",      icon: PackageCheck,    label: "Shopify",         badge: null },
];
