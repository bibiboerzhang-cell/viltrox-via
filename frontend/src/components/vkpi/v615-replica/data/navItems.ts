// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Command, Database, Heart, LayoutDashboard, LineChart, PackageCheck, RadioTower, Sparkles, Target, Users, Zap } from "lucide-react";

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
  { key: "signals",      icon: RadioTower,      label: "Signals",         badge: "49"  },
  { key: "agents",       icon: Bot,             label: "Agents",          badge: "7"   },
  { key: "via",          icon: Zap,             label: "VIA Creator Hub", badge: null  },
  { key: "p15",          icon: Boxes,           label: "P15 Warehouse",   badge: "Beta" },
  { key: "shopify",      icon: PackageCheck,    label: "Shopify",         badge: "Beta" },
  { key: "vos",          icon: Command,         label: "V-OS",            badge: "Beta" },
];
