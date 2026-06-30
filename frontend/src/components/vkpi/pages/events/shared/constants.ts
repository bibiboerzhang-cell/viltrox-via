import {
  Building2, PartyPopper, Plane, Plus, ShieldCheck, Star, Ticket,
  Users, Utensils, Video,
  Briefcase, Camera, Gift, ImageIcon, Megaphone, Package,
  Printer, Wrench
} from "lucide-react";
import type { IconConfig, LabelConfig } from "./types";

export const TASK_KINDS: Record<string, IconConfig> = {
  equipment: { label: "设备", icon: Briefcase, color: "#94a3b8" },
  materials: { label: "物料", icon: Package, color: "#06b6d4" },
};

// 设备 source 标签
export const EQUIP_SOURCE: Record<string, LabelConfig> = {
  in_stock_sample: { label: "现有样品", color: "#10b981" },
  in_stock:        { label: "库存",     color: "#10b981" },
  new_purchase:    { label: "新采购",   color: "#fbbf24" },
  rental:          { label: "租赁",     color: "#06b6d4" },
};

// 物料 source 标签
export const MAT_SOURCE: Record<string, LabelConfig> = {
  ship:         { label: "📦 快递寄",  color: "#06b6d4" },
  carry_on:     { label: "🧳 自带",    color: "#fbbf24" },
  in_stock:     { label: "✓ 现有库存", color: "#10b981" },
  new_purchase: { label: "🆕 新采购",   color: "#a855f7" },
  on_site_print:{ label: "🖨 现场印", color: "#ec4899" },
};

// item status 标签
export const ITEM_STATUS: Record<string, LabelConfig> = {
  ready:         { label: "就绪",     color: "#10b981" },
  purchased:     { label: "已采购",   color: "#10b981" },
  in_production: { label: "生产中",   color: "#fbbf24" },
  printing:      { label: "印制中",   color: "#fbbf24" },
  shipped:       { label: "已寄出",   color: "#06b6d4" },
  pending:       { label: "待处理",   color: "#94a3b8" },
  rented:        { label: "已租",     color: "#10b981" },
};

export const PHASE_LABELS: Record<string, { label: string; dateRange: string }> = {
  "4w":    { label: "4 周前", dateRange: "(5/13 之前)" },
  "2w":    { label: "2 周前", dateRange: "(5/27 之前)" },
  "1w":    { label: "1 周前", dateRange: "(6/03 之前)" },
  "live":  { label: "现场",    dateRange: "(6/10 - 6/13)" },
  "after": { label: "复盘",    dateRange: "(6/14 之后)" },
};

export const MATERIAL_CATEGORIES: Record<string, IconConfig> = {
  display: { label: "展示",     icon: ImageIcon, color: "#a855f7" },
  print:   { label: "印刷",     icon: Printer,   color: "#06b6d4" },
  gift:    { label: "礼品",     icon: Gift,      color: "#ec4899" },
  press:   { label: "媒体 kit", icon: Megaphone, color: "#fbbf24" },
  demo:    { label: "现场设备", icon: Briefcase, color: "#10b981" },
};

// ─────────────────────────────────────────────────────────────────────
// 任务 source/status 标签 (用于物料 tab)
// ─────────────────────────────────────────────────────────────────────
export const MATERIAL_SOURCE: Record<string, LabelConfig> = {
  ship:         { label: "📦 快递",   color: "#06b6d4" },
  carry_on:     { label: "🧳 自带",   color: "#fbbf24" },
  in_stock:     { label: "✓ 库存",   color: "#10b981" },
  new_purchase: { label: "🆕 新采购", color: "#a855f7" },
  on_site_print:{ label: "🖨 现场印", color: "#ec4899" },
  digital:      { label: "📁 数字",   color: "#94a3b8" },
};

export const PRODUCT_CATEGORIES: Record<string, IconConfig> = {
  lens:      { label: "镜头",     icon: Camera,    color: "#a855f7" },
  equipment: { label: "设备",     icon: Briefcase, color: "#06b6d4" },
  accessory: { label: "配件",     icon: Wrench,    color: "#fbbf24" },
};

export const PRODUCT_SOURCES: Record<string, LabelConfig> = {
  in_stock_sample: { label: "现有样品",   color: "#10b981", side: "have" },
  in_stock:        { label: "公司库存",   color: "#10b981", side: "have" },
  new_purchase:    { label: "新采购",     color: "#a855f7", side: "need" },
  rental:          { label: "租赁",       color: "#06b6d4", side: "need" },
  borrow:          { label: "借用",       color: "#fbbf24", side: "need" },
};

export const EVENT_TYPES: Record<string, IconConfig> = {
  tradeshow:  { label: "线下展会",     icon: Ticket,       color: "#a855f7" },
  media:      { label: "媒体活动",     icon: Camera,       color: "#06b6d4" },
  webinar:    { label: "线上 Webinar", icon: Video,        color: "#10b981" },
  kol_meetup: { label: "KOL 聚会",     icon: PartyPopper,  color: "#fbbf24" },
  internal:   { label: "内部团建",     icon: Users,        color: "#94a3b8" },
  other:      { label: "其他活动",     icon: Briefcase,    color: "#94a3b8" },
};

export const EVENT_STATUS: Record<string, LabelConfig> = {
  planning:   { label: "筹备中",   color: "#06b6d4" },
  prep_ready: { label: "物料就绪", color: "#a855f7" },
  live:       { label: "进行中",   color: "#fbbf24" },
  wrap_up:    { label: "复盘中",   color: "#94a3b8" },
  done:       { label: "已完成",   color: "#10b981" },
  cancelled:  { label: "已取消",   color: "#64748b" },
};

export const EXPENSE_CATEGORIES: Record<string, IconConfig> = {
  booth:       { label: "展位费",     icon: Building2,    color: "#a855f7" },
  travel:      { label: "差旅",       icon: Plane,        color: "#06b6d4" },
  food:        { label: "餐饮",       icon: Utensils,     color: "#fbbf24" },
  gifts:       { label: "礼品/样机",  icon: Gift,         color: "#ec4899" },
  shipping:    { label: "运输",       icon: Package,      color: "#10b981" },
  printing:    { label: "物料印刷",   icon: Printer,      color: "#06b6d4" },
  advertising: { label: "媒体广告",   icon: Megaphone,    color: "#ef4444" },
  equipment:   { label: "设备",       icon: Briefcase,    color: "#94a3b8" },
  kol_fee:     { label: "KOL 费用",   icon: Star,         color: "#a855f7" },
  insurance:   { label: "保险/许可",  icon: ShieldCheck,  color: "#64748b" },
  other:       { label: "其他",       icon: Wrench,       color: "#94a3b8" },
  custom:      { label: "自定义",     icon: Plus,         color: "#06b6d4" },  // 新建时输入 label
};
