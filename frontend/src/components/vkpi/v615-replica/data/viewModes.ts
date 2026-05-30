// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

import { Calendar, MapPin, Store, Users } from "lucide-react";

export const VIEW_MODES = {
  kols: {
    label: "KOLs",
    icon: Users,
    color: "#a855f7",
    desc: "KOL 分布读取真实 API",
    pinColor: "#a855f7",
    hierarchy: {},
    apiEndpoint: "/api/admin/vkpi/dashboard/kol-distribution",
    available: false,
  },
  dealers: {
    label: "Dealers",
    icon: Store,
    color: "#10b981",
    desc: "Dealer locations endpoint 待接入",
    pinColor: "#10b981",
    hierarchy: {},
    apiEndpoint: "/api/admin/vkpi/dealers/locations",
    available: false,
  },
  customer: {
    label: "Customer Heatmap",
    icon: MapPin,
    color: "#f59e0b",
    desc: "Real-time order density from Shopify & Amazon",
    pinColor: "#f59e0b",
    hierarchy: {},
    apiEndpoint: "/api/admin/vkpi/shopify/customer-heatmap",
    available: false,
    waitingMessage: "Awaiting Shopify integration",
  },
  events: {
    label: "Events",
    icon: Calendar,
    color: "#fbbf24",
    desc: "events/upcoming endpoint 待接入",
    pinColor: "#fbbf24",
    hierarchy: {},
    apiEndpoint: "/api/admin/vkpi/events/upcoming",
    available: false,
    isEvents: true,
  },
};
