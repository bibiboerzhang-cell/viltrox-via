// @ts-nocheck
// Agent-OS data-freshness 卡片(2026-06-14):KOL 池 / 成员渠道(/ 产品)的逐实体新鲜度。
// 数据全部来自真实只读端点 GET /api/admin/vkpi/dashboard/data-freshness;
// 新鲜度由后端 COALESCE(last_checked, updated_at/last_seen_at/last_sync_at) 派生(127 列起始 NULL,
// 故当前实际落在既有时间戳)。桶:fresh<3天 / aging 3-7天 / stale>7天。
// 任何实体 available=false 显「待接入」,计数为 0 就显 0,绝不编造。

import React, { useEffect, useRef, useState } from "react";
import { Clock, Database } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

const REFRESH_MS = 60000;

function relativeAge(iso) {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  const days = Math.floor(secs / 86400);
  if (days >= 1) return `${days}天`;
  const hours = Math.floor(secs / 3600);
  if (hours >= 1) return `${hours}小时`;
  const mins = Math.floor(secs / 60);
  return `${mins}分钟`;
}

const PENDING = e("span", { className: "text-amber-400/80 text-[11px]" }, "待接入");

function BucketChip({ count, accent, label }) {
  return e("div", { className: "flex items-baseline gap-1" },
    e("span", { className: `text-[13px] font-semibold tabular-nums ${accent}` }, `${count ?? 0}`),
    e("span", { className: "text-[9px] text-slate-500" }, label)
  );
}

function EntityRow({ title, entity }) {
  const available = entity && entity.available;
  let body;
  if (!available) {
    body = PENDING;
  } else {
    const oldest = relativeAge(entity.oldest_at);
    body = e("div", { className: "flex items-center gap-3 flex-wrap" },
      e(BucketChip, { count: entity.fresh, accent: "text-emerald-400/90", label: "新" }),
      e(BucketChip, { count: entity.aging, accent: "text-amber-400/90", label: "陈化" }),
      e(BucketChip, { count: entity.stale, accent: "text-rose-400/90", label: "陈旧" }),
      oldest && e("span", {
        className: "ml-1 text-[9px] text-slate-500",
        title: entity.oldest_at || "",
      }, `最老 ${oldest}`)
    );
  }
  return e("div", { className: "flex items-center justify-between gap-3 px-3 py-1.5" },
    e("div", { className: "min-w-0" },
      e("div", { className: "text-[11px] text-slate-300 leading-none mb-0.5" }, title),
      e("div", { className: "text-[8px] uppercase tracking-wide text-slate-600 leading-none" },
        available ? `源 ${entity.source_table}` : "无源"
      )
    ),
    body
  );
}

export function DataFreshnessCard({ apiToken }) {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState("");
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    if (!apiToken) {
      setError("未登录 / 无 token");
      return () => { aliveRef.current = false; };
    }
    const load = async () => {
      try {
        const data = await apiFetch(
          "/api/admin/vkpi/dashboard/data-freshness",
          { timeoutMs: 4000 },
          apiToken,
        );
        if (!aliveRef.current) return;
        setSnap(data || null);
        setError("");
      } catch {
        if (!aliveRef.current) return;
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

  const staleCount = snap && typeof snap.stale_count === "number" ? snap.stale_count : null;
  const headerAccent = staleCount && staleCount > 0 ? "text-rose-400" : "text-emerald-400";

  return e("div", {
    className: "mb-4 rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden",
  },
    e("div", { className: "flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/[0.05]" },
      e("div", { className: "flex items-center gap-2" },
        e(Database, { size: 13, className: "text-violet-400", strokeWidth: 2 }),
        e("span", { className: "text-[11px] font-medium text-slate-300" }, "数据新鲜度")
      ),
      !snap
        ? (error
            ? e("span", { className: "text-[10px] text-amber-400/80" }, "待接入")
            : e("span", { className: "text-[10px] text-slate-500" }, "读取中…"))
        : e("div", { className: "flex items-center gap-1" },
            e(Clock, { size: 11, className: headerAccent, strokeWidth: 2 }),
            e("span", { className: `text-[10px] tabular-nums ${headerAccent}` },
              staleCount === null ? "—" : `${staleCount} 陈旧`
            )
          )
    ),
    e("div", { className: "divide-y divide-white/[0.04]" },
      e(EntityRow, { title: "KOL 池", entity: snap?.kol_pool }),
      e(EntityRow, { title: "成员渠道", entity: snap?.channels }),
      snap?.products && e(EntityRow, { title: "产品目录", entity: snap.products })
    )
  );
}
