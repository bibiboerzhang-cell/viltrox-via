// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { Calendar, Check, Package, Sparkles, Users } from "lucide-react";

export const TIMELINE_CATEGORIES = {
  material: { label: "Material",  color: "#a855f7", icon: Package },
  people:   { label: "People",    color: "#3b82f6", icon: Users },
  promo:    { label: "Promo",     color: "#f59e0b", icon: Sparkles },
  event:    { label: "Event Day", color: "#10b981", icon: Calendar },
  wrap:     { label: "Wrap-up",   color: "#06b6d4", icon: Check },
};
