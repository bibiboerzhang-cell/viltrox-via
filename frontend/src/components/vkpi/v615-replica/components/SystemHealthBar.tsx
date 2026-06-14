// @ts-nocheck
// P5 系统健康条(诚实化 2026-06-14):队列 / 今日 LLM 成本 / 数据新鲜度 / Worker 状态。
// 数据全部来自真实只读端点 GET /api/admin/vkpi/dashboard/system-health;
// 任何字段 available=false 时显示「待接入」,绝不编造数字。

import React, { useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, Database, DollarSign, Server } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

const REFRESH_MS = 30000;

function relativeTime(iso) {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}秒前`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${Math.round(hours / 24)}天前`;
}

function Cell({ icon, label, children, accent }) {
  return e("div", { className: "flex items-center gap-2 px-3 py-1.5 min-w-0" },
    e(icon, { size: 13, className: accent || "text-slate-400", strokeWidth: 2 }),
    e("div", { className: "min-w-0" },
      e("div", { className: "text-[9px] uppercase tracking-wide text-slate-500 leading-none mb-0.5" }, label),
      e("div", { className: "text-[11px] text-slate-200 leading-none truncate" }, children)
    )
  );
}

const PENDING = e("span", { className: "text-amber-400/80" }, "待接入");

export function SystemHealthBar({ apiToken }) {
  const [health, setHealth] = useState(null);
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
          "/api/admin/vkpi/dashboard/system-health",
          { timeoutMs: 4000 },
          apiToken,
        );
        if (!aliveRef.current) return;
        setHealth(data || null);
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

  const queue = health?.queue || {};
  const llm = health?.llm_cost_today || {};
  const fresh = health?.data_freshness || {};
  const worker = health?.worker_online || {};
  const blocked = health?.blocked_reasons || {};

  // 队列 done/failed/blocked
  let queueCell;
  if (!health) {
    queueCell = error ? PENDING : e("span", { className: "text-slate-500" }, "读取中…");
  } else if (!queue.available) {
    queueCell = PENDING;
  } else {
    const topBlocked = Array.isArray(blocked.items) && blocked.items.length > 0
      ? `${blocked.items[0].reason}×${blocked.items[0].count}`
      : null;
    queueCell = e(React.Fragment, null,
      e("span", { className: "text-emerald-400/90 tabular-nums" }, `${queue.done ?? 0}`),
      e("span", { className: "text-slate-600" }, " / "),
      e("span", { className: "text-rose-400/90 tabular-nums" }, `${queue.failed ?? 0}`),
      e("span", { className: "text-slate-600" }, " / "),
      e("span", { className: (queue.blocked ?? 0) > 0 ? "text-amber-400/90 tabular-nums" : "text-slate-400 tabular-nums" }, `${queue.blocked ?? 0}`),
      topBlocked && e("span", { className: "ml-1 text-[9px] text-slate-500", title: "首位阻塞原因" }, `· ${topBlocked}`)
    );
  }

  // 今日 LLM 成本
  let llmCell;
  if (!health) {
    llmCell = error ? PENDING : e("span", { className: "text-slate-500" }, "—");
  } else if (!llm.available) {
    llmCell = PENDING;
  } else {
    llmCell = e("span", { className: "text-slate-200 tabular-nums" },
      `$${(llm.cost_usd ?? 0).toFixed(2)}`,
      e("span", { className: "ml-1 text-[9px] text-slate-500" }, `· ${llm.call_count ?? 0} 次`)
    );
  }

  // 数据新鲜度
  let freshCell;
  if (!health) {
    freshCell = error ? PENDING : e("span", { className: "text-slate-500" }, "—");
  } else if (!fresh.available) {
    freshCell = PENDING;
  } else {
    const rel = relativeTime(fresh.max_updated_at);
    freshCell = rel
      ? e("span", { className: "text-slate-200", title: fresh.max_updated_at }, rel)
      : e("span", { className: "text-slate-500" }, "无记录");
  }

  // Worker 状态
  let workerCell;
  let workerAccent = "text-slate-400";
  if (!health) {
    workerCell = error ? PENDING : e("span", { className: "text-slate-500" }, "—");
  } else if (!worker.available) {
    workerCell = PENDING;
  } else if (worker.online) {
    workerAccent = "text-emerald-400";
    workerCell = e("span", { className: "flex items-center gap-1.5 text-emerald-400/90" },
      e("span", { className: "inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" }),
      "在线"
    );
  } else {
    workerAccent = "text-slate-500";
    const rel = relativeTime(worker.last_heartbeat_at);
    workerCell = e("span", { className: "flex items-center gap-1.5 text-slate-400", title: worker.last_heartbeat_at },
      e("span", { className: "inline-block h-1.5 w-1.5 rounded-full bg-slate-600" }),
      rel ? `离线 · ${rel}` : "离线"
    );
  }

  return e("div", {
    className: "mb-4 flex items-stretch flex-wrap divide-x divide-white/[0.05] rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden",
  },
    e("div", { className: "flex items-center gap-2 px-3 py-1.5" },
      e(Activity, { size: 13, className: "text-violet-400", strokeWidth: 2 }),
      e("span", { className: "text-[11px] font-medium text-slate-300" }, "系统健康")
    ),
    e("div", { className: "flex items-stretch flex-wrap divide-x divide-white/[0.05] border-l border-white/[0.05]" },
      e(Cell, { icon: Server, label: "队列 完成/失败/阻塞" }, queueCell),
      e(Cell, { icon: DollarSign, label: "今日 LLM 成本" }, llmCell),
      e(Cell, { icon: Database, label: "数据新鲜度" }, freshCell),
      e(Cell, { icon: worker.online ? Activity : AlertTriangle, label: "Worker 状态", accent: workerAccent }, workerCell)
    )
  );
}
