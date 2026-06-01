// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { BarChart3, Bot, Boxes, Briefcase, Calendar, Database, Heart, LayoutDashboard, LineChart, PackageCheck, RadioTower, Sparkles, Target, Users } from "lucide-react";

export const NAV_ITEMS = [
  { key: "dashboard",    icon: LayoutDashboard, label: "Dashboard",       badge: null },
  { key: "intelligence", icon: Sparkles,        label: "Intelligence",    badge: "GEN2" },
  { key: "my-kol",       icon: Heart,           label: "MY KOL",          badge: null },
  { key: "kol-pool",     icon: Users,           label: "KOL Pool",        badge: null },
  { key: "projects",     icon: Briefcase,       label: "Projects",        badge: null },
  { key: "campaigns",    icon: Target,          label: "Campaigns",       badge: "GEN2" },
  { key: "events",       icon: Calendar,        label: "Events",          badge: "New" },
  { key: "attribution",  icon: LineChart,       label: "Attribution",     badge: "GEN2" },
  { key: "analytics",    icon: BarChart3,       label: "Analytics",       badge: "GEN2" },
  { key: "reports",      icon: Database,        label: "Reports",         badge: "GEN2" },
  { key: "signals",      icon: RadioTower,      label: "Signals",         badge: "GEN2"  },
  { key: "agents",       icon: Bot,             label: "Agents",          badge: "GEN2"   },
  { key: "p15",          icon: Boxes,           label: "P15 Warehouse",   badge: "GEN2" },
  { key: "shopify",      icon: PackageCheck,    label: "Shopify",         badge: "Beta" },
];
