// P5 系统健康条(诚实化 2026-06-14):队列 / 今日 LLM 成本 / 数据新鲜度 / Worker 状态。
// 数据全部来自真实只读端点 GET /api/admin/vkpi/dashboard/system-health;
// 任何字段 available=false 时显示「待接入」,绝不编造数字。

import React, { useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, Database, DollarSign, GitCommit, Server, ShieldCheck, ShieldAlert } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { getHealthTrust, type VkpiHealthTrust } from "../../../../services/vkpi/settings-api";

const e = React.createElement;

function shortSha(value: any) {
  const s = String(value || "").trim();
  return s ? s.slice(0, 8) : "--";
}

const REFRESH_MS = 30000;

function relativeTime(iso: any) {
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

function Cell({ icon, label, children, accent }: any) {
  return e("div", { className: "flex items-center gap-2 px-3 py-1.5 min-w-0" },
    e(icon, { size: 13, className: accent || "text-slate-400", strokeWidth: 2 }),
    e("div", { className: "min-w-0" },
      e("div", { className: "text-[9px] uppercase tracking-wide text-slate-500 leading-none mb-0.5" }, label),
      e("div", { className: "text-[11px] text-slate-200 leading-none truncate" }, children)
    )
  );
}

const PENDING = e("span", { className: "text-amber-400/80" }, "待接入");

export function SystemHealthBar({ apiToken }: any) {
  const [health, setHealth] = useState<any>(null);
  const [error, setError] = useState("");
  // F3:运行态信任块(/health trust)——三 sha · 对齐 · worker 在线 · 迁移号。
  const [trust, setTrust] = useState<VkpiHealthTrust | null>(null);
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
    const loadTrust = async () => {
      const data = await getHealthTrust();
      if (!aliveRef.current) return;
      setTrust(data);
    };
    load();
    loadTrust();
    const timer = globalThis.setInterval(() => { load(); loadTrust(); }, REFRESH_MS);
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

  // F3 运行态信任块:三 sha 一栏 + 对齐绿/红醒目 + worker 离线红字。
  // trust=null(/health 不可达)时整块隐藏,不编造对齐状态。
  let trustBar: any = null;
  if (trust) {
    const aligned = trust.sha_aligned;
    const alignedAccent = aligned === true ? "text-emerald-400" : aligned === false ? "text-rose-400" : "text-amber-400";
    const alignBox = aligned === true
      ? "border-emerald-400/40 bg-emerald-400/[0.06]"
      : aligned === false
        ? "border-rose-400/50 bg-rose-400/[0.08]"
        : "border-amber-400/30 bg-amber-400/[0.05]";
    const alignLabel = aligned === true ? "版本对齐" : aligned === false ? "版本不一致" : "对齐待确认";

    const workerOnline = trust.worker_online;
    const workerOffline = workerOnline === false;

    const shaChip = (label: string, value: any, mismatch: boolean) => e("div",
      { className: "flex items-center gap-1" },
      e("span", { className: "text-[9px] uppercase tracking-wide text-slate-500" }, label),
      e("span", {
        className: `text-[10px] font-mono tabular-nums ${mismatch ? "text-rose-300" : "text-slate-300"}`,
        title: String(value || ""),
      }, shortSha(value)),
    );

    const serverSha = trust.server_git_sha;
    const clientSha = trust.client_git_sha;
    const workerShaVal = trust.worker_sha;

    trustBar = e("div", {
      className: `mb-2 flex items-center flex-wrap gap-x-4 gap-y-1.5 rounded-xl border px-3 py-2 ${alignBox}`,
    },
      // 对齐徽标(绿/红/待确认醒目)
      e("div", { className: "flex items-center gap-1.5" },
        e(aligned === false ? ShieldAlert : ShieldCheck, { size: 14, className: alignedAccent, strokeWidth: 2.2 }),
        e("span", { className: `text-[11px] font-semibold ${alignedAccent}` }, alignLabel),
      ),
      // 三 sha 一栏可见
      e("div", { className: "flex items-center gap-3" },
        e(GitCommit, { size: 12, className: "text-slate-500", strokeWidth: 2 }),
        shaChip("server", serverSha, aligned === false),
        shaChip("client", clientSha, aligned === false),
        shaChip("worker", workerShaVal, false),
      ),
      // db 迁移号
      e("div", { className: "flex items-center gap-1" },
        e(Database, { size: 12, className: "text-slate-500", strokeWidth: 2 }),
        e("span", { className: "text-[9px] uppercase tracking-wide text-slate-500" }, "迁移"),
        e("span", { className: "text-[10px] font-mono tabular-nums text-slate-300" }, trust.db_migration_max || "--"),
      ),
      // Worker 离线醒目红字 / 在线绿点
      workerOffline
        ? e("div", { className: "flex items-center gap-1.5 ml-auto rounded-md border border-rose-400/50 bg-rose-400/[0.12] px-2 py-0.5", title: trust.worker_heartbeat || "" },
            e(AlertTriangle, { size: 13, className: "text-rose-400", strokeWidth: 2.2 }),
            e("span", { className: "text-[11px] font-semibold text-rose-300" }, "Worker 离线"),
            (() => { const rel = relativeTime(trust.worker_heartbeat); return rel ? e("span", { className: "text-[9px] text-rose-400/70" }, `· ${rel}`) : null; })(),
          )
        : workerOnline === true
          ? e("div", { className: "flex items-center gap-1.5 ml-auto" },
              e("span", { className: "inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" }),
              e("span", { className: "text-[10px] text-emerald-400/90" }, "Worker 在线"),
            )
          : e("div", { className: "flex items-center gap-1.5 ml-auto" },
              e("span", { className: "inline-block h-1.5 w-1.5 rounded-full bg-slate-600" }),
              e("span", { className: "text-[10px] text-slate-500" }, "Worker 状态未知"),
            ),
    );
  }

  const healthBar = e("div", {
    className: "flex items-stretch flex-wrap divide-x divide-white/[0.05] rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden",
  },
    e("div", { className: "flex items-center gap-2 px-3 py-1.5" },
      e(Activity, { size: 13, className: "text-violet-400", strokeWidth: 2 }),
      e("span", { className: "text-[11px] font-medium text-slate-300" }, "系统健康")
    ),
    e("div", { className: "flex items-stretch flex-wrap divide-x divide-white/[0.05] border-l border-white/[0.05]" },
      e(Cell, { icon: Server, label: "队列 完成/失败/阻塞" }, queueCell),
      e(Cell, { icon: DollarSign, label: "今日 LLM 成本" }, llmCell),
      // P3:数据新鲜度是系统诊断,移出业务主页(归 Settings/Diagnostics);freshCell 计算保留以备后台用。
      e(Cell, { icon: worker.online ? Activity : AlertTriangle, label: "Worker 状态", accent: workerAccent }, workerCell)
    )
  );

  return e("div", { className: "mb-4" }, trustBar, healthBar);
}
