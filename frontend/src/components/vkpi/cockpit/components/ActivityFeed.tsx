// ActivityFeed 思考流(件2)。
// ----------------------------------------------------------------------------
// 打字机式滚动列表:把线上已有的真实事件(Apify 任务完成 / 信号预警 / 智能搜索会话 /
// 触达简报)聚合成一条条「系统正在做什么」的思考流。10s 轮询只读端点
// GET /api/admin/vkpi/activity/recent?limit=30;每条 = 图标 + 一句话 + 可点链接。
//
// 时间:后端透传 UTC("...Z" 字符串),这里用 formatLocal 按观看者浏览器时区显示,
// 绝不硬编码「刚刚 / 实时 / live」这类假新鲜度。无数据显「暂无思考流」,不装 live。
// 数据全部真实只读(某源缺表后端已跳过),前端零编造。
//
// U4 会呼吸的指挥室:新条目从顶部滑入(AnimatePresence,300ms 一次,无循环);
// 新数据到达时自动滚回顶部保持最新可见;鼠标悬停 = 阅读中 → 暂停应用新数据
// (静默缓冲,移开后一次性追平)。prefers-reduced-motion 全部降级为静态直显。

import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { usePrefersReducedMotion } from "./AnimatedNumber";
import {
  Activity,
  AlertTriangle,
  Bell,
  ClipboardCheck,
  Cpu,
  FileText,
  Mail,
  MessageCircle,
  RefreshCw,
  Search,
  Video,
} from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { formatLocal } from "../../lib/timeLocal";

const e = React.createElement;

const REFRESH_MS = 10000;

// 后端 icon 名 → lucide 组件。未命中回退 Activity(通用脉冲)。
const ICON_MAP: Record<string, any> = {
  video: Video,
  "message-circle": MessageCircle,
  search: Search,
  "refresh-cw": RefreshCw,
  "file-text": FileText,
  "clipboard-check": ClipboardCheck,
  "alert-triangle": AlertTriangle,
  bell: Bell,
  mail: Mail,
  cpu: Cpu,
};

// kind → 左边框强调色(纯视觉分类,不改数据)。
const KIND_ACCENT: Record<string, string> = {
  job: "#8b5cf6", // 紫:后台任务
  alert: "#ef4444", // 红:信号预警
  search: "#06b6d4", // 青:智能搜索
  outreach: "#10b981", // 绿:触达
};

type FeedItem = {
  ts?: string;
  kind?: string;
  icon?: string;
  text?: string;
  link?: string | null;
};

function FeedRow({ item, reduced }: { item: FeedItem; reduced: boolean }) {
  const Icon = ICON_MAP[String(item.icon || "")] || Activity;
  const accent = KIND_ACCENT[String(item.kind || "")] || "#64748b";
  const clickable = typeof item.link === "string" && item.link.length > 0;

  const onClick = () => {
    if (!clickable || typeof window === "undefined") return;
    // 站内相对链接:直接改 hash/path(cockpit 是单页,交由路由层消费)。
    window.location.assign(item.link as string);
  };

  // 入场:从顶部滑入 + 淡入,一次 300ms;退场(被挤出列表)淡出。
  // reduced-motion:initial=false + duration 0 → 静态直显,无位移。
  return e(
    motion.div,
    {
      layout: !reduced,
      initial: reduced ? false : { opacity: 0, y: -10 },
      animate: { opacity: 1, y: 0 },
      exit: { opacity: 0 },
      transition: reduced
        ? { duration: 0 }
        : { duration: 0.3, ease: [0.16, 1, 0.3, 1] },
      onClick: clickable ? onClick : undefined,
      className:
        "group flex items-start gap-2 rounded-md px-2.5 py-2 " +
        (clickable ? "cursor-pointer hover:bg-white/[0.04] " : ""),
      style: { borderLeft: `2px solid ${accent}` },
    },
    e(Icon, {
      size: 12,
      className: "mt-0.5 shrink-0",
      style: { color: accent },
      strokeWidth: 2,
    }),
    e(
      "div",
      { className: "min-w-0 flex-1" },
      e(
        "div",
        {
          className:
            "text-[11px] leading-snug text-slate-200 " +
            (clickable ? "group-hover:text-white" : ""),
        },
        item.text || "—",
      ),
      e(
        "div",
        { className: "mt-0.5 text-[9px] text-slate-500", title: item.ts || "" },
        item.ts ? formatLocal(item.ts) : "—",
      ),
    ),
  );
}

export function ActivityFeed({ apiToken }: { apiToken?: string }) {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const reduced = usePrefersReducedMotion();
  const aliveRef = useRef(true);
  // hover = 用户阅读中:暂停应用新数据(静默缓冲到 pendingRef),移开后追平。
  const hoverRef = useRef(false);
  const pendingRef = useRef<FeedItem[] | null>(null);
  const loadedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    aliveRef.current = true;
    if (!apiToken) {
      setError("未登录 / 无 token");
      return () => {
        aliveRef.current = false;
      };
    }
    const load = async () => {
      try {
        const data: any = await apiFetch(
          "/api/admin/vkpi/activity/recent?limit=30",
          { timeoutMs: 4000 },
          apiToken,
        );
        if (!aliveRef.current) return;
        const next: FeedItem[] = Array.isArray(data?.items) ? data.items : [];
        if (hoverRef.current && loadedRef.current) {
          // 阅读中:先缓冲,不打断视线;mouseleave 时一次性应用。
          pendingRef.current = next;
        } else {
          pendingRef.current = null;
          setItems(next);
        }
        loadedRef.current = true;
        setLoaded(true);
        setError("");
      } catch {
        if (!aliveRef.current) return;
        // 轮询失败保留上一轮内容,只在从未加载成功时提示「待接入」。
        setError("真实 API 无信号");
      }
    };
    load();
    const timer = globalThis.setInterval(load, REFRESH_MS);
    return () => {
      aliveRef.current = false;
      globalThis.clearInterval(timer);
    };
  }, [apiToken]);

  // 稳定 key(AnimatePresence 需要):ts+kind+text 指纹,重复条目加序号后缀。
  const keyed = useMemo(() => {
    const seen = new Map<string, number>();
    return items.map((item) => {
      const base = `${item.ts || ""}|${item.kind || ""}|${item.text || ""}`;
      const n = (seen.get(base) || 0) + 1;
      seen.set(base, n);
      return { item, key: n > 1 ? `${base}#${n}` : base };
    });
  }, [items]);

  // 新条目滚入时自动回到顶部,保持最新可见(hover 阅读中不打扰)。
  const topKey = keyed.length ? keyed[0].key : "";
  useEffect(() => {
    if (!topKey || hoverRef.current) return;
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    }
  }, [topKey, reduced]);

  const onMouseEnter = () => {
    hoverRef.current = true;
  };
  const onMouseLeave = () => {
    hoverRef.current = false;
    if (pendingRef.current) {
      setItems(pendingRef.current);
      pendingRef.current = null;
    }
  };

  let body: any;
  if (!loaded && !error) {
    body = e(
      "div",
      { className: "px-3 py-8 text-center text-[11px] text-slate-500" },
      "读取中…",
    );
  } else if (items.length === 0) {
    body = e(
      "div",
      {
        className:
          "rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[11px] text-slate-500",
      },
      error ? "待接入" : "暂无思考流",
    );
  } else {
    body = e(
      "div",
      {
        ref: scrollRef,
        onMouseEnter,
        onMouseLeave,
        "data-testid": "activity-scroll",
        className: "space-y-1 overflow-y-auto pr-1",
        style: { maxHeight: 340 },
      },
      // initial=false:首屏整批不做滑入(避免 30 条齐舞);只有后续新条目滑入。
      e(
        AnimatePresence,
        { initial: false },
        keyed.map(({ item, key }) => e(FeedRow, { key, item, reduced })),
      ),
    );
  }

  return e(
    "div",
    {
      className:
        "h-full rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl flex flex-col",
    },
    e(
      "div",
      { className: "mb-3 flex items-center justify-between" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Activity, { size: 14, className: "text-violet-300", strokeWidth: 2 }),
        e("h3", { className: "text-sm font-semibold text-white" }, "思考流"),
      ),
      e(
        "span",
        { className: "text-[9px] text-slate-500" },
        loaded && items.length ? `${items.length} 条` : "",
      ),
    ),
    e("div", { className: "flex-1" }, body),
  );
}

export default ActivityFeed;
