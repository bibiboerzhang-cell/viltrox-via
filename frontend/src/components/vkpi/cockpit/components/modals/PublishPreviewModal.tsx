// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Bell, Check, Edit3, ImageIcon, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { PLATFORM_ICONS_MAP } from "../../data/platformIconsMap";
import { approvePublish, reschedulePublish, remindPublishKol } from "../../../../../services/vkpi/publish-api";

const e = React.createElement;

export function PublishPreviewModal({ item, onClose, apiToken = "" }: { item?: any; onClose?: () => void; apiToken?: string }) {
  const { t } = useT();
  const [busy, setBusy] = React.useState("");
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  // #18 真来源标识(recent-content 落库的 source_table+source_id;normalizeCalendar 经 raw 透传)。
  const raw = (item && item.raw) ? item.raw : (item || {});
  const srcTable = String(raw.source_table || "");
  const srcId = String(raw.source_id || "");
  const canAct = !!(apiToken && srcTable && srcId);
  const meta = { platform: (item && item.platform) || "", account_handle: (item && (item.kolName || item.label)) || "", title: (item && (item.title || item.label)) || "" };
  // 报错人话化:线上发布审批接口(迁移 173)可能未部署 → 404/Not Found 时不甩原始报错,
  // 直接告诉用户「发布审批后端未上线」。其他错误保留原文便于排查。
  const friendlyErr = (err: any): string => {
    const status = err && typeof err.status === "number" ? err.status : 0;
    const raw = String(err && err.message ? err.message : err);
    if (status === 404 || /404|not found/i.test(raw)) return "发布审批后端未上线(接口 404),操作未生效";
    return raw;
  };
  const doApprove = async () => {
    if (!canAct || busy) return;
    setBusy("approve"); setMsg(null);
    try { await approvePublish(apiToken, srcTable, srcId, meta); setMsg({ ok: true, text: "已通过审批" }); }
    catch (err: any) { setMsg({ ok: false, text: friendlyErr(err) }); }
    finally { setBusy(""); }
  };
  const doReschedule = async () => {
    if (!canAct || busy) return;
    const input = (window.prompt("新发布时间(如 2026-07-01 14:00):") || "").trim();
    if (!input) return;
    const d = new Date(input);
    if (isNaN(d.getTime())) { setMsg({ ok: false, text: "时间格式无法解析" }); return; }
    setBusy("reschedule"); setMsg(null);
    try { await reschedulePublish(apiToken, srcTable, srcId, d.toISOString(), meta); setMsg({ ok: true, text: `发布时间已更新:${d.toLocaleString()}` }); }
    catch (err: any) { setMsg({ ok: false, text: friendlyErr(err) }); }
    finally { setBusy(""); }
  };
  const doRemind = async () => {
    if (!canAct || busy) return;
    setBusy("remind"); setMsg(null);
    try { await remindPublishKol(apiToken, srcTable, srcId, meta); setMsg({ ok: true, text: "已记录提醒 KOL" }); }
    catch (err: any) { setMsg({ ok: false, text: friendlyErr(err) }); }
    finally { setBusy(""); }
  };
  if (!item) return null;
  const platCfg = (PLATFORM_ICONS_MAP as any)[item.platform] || PLATFORM_ICONS_MAP.default;
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "cockpit-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-lg rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[13px] font-bold text-white",
          style: { background: item.color || "#a855f7" }
        }, item.kolAvatar || ((item.label || "").split(" · ").pop() || "").charAt(1) || "K"),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "text-[12px] font-medium text-white truncate" }, item.kolName || item.label),
          e("div", { className: "flex items-center gap-2 text-[9px] text-slate-500 mt-0.5" },
            e("span", { className: "flex items-center gap-1" }, e(platCfg.Icon, { size: 10, style: { color: platCfg.color } }), item.platform),
            e("span", null, "·"),
            e("span", null, item.dateStr || (item.dayStr + " " + item.time)),
            e("span", null, "·"),
            e("span", { style: { color: "#fbbf24" } }, t("待审批"))
          )
        ),
        e("button", { onClick: onClose, className: "shrink-0 rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
      ),
      // Body
      e("div", { className: "p-5 space-y-3" },
        // 关联 Project chip
        item.projectName && e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "text-[9px] text-slate-500" }, t("关联项目")),
          e("span", { className: "text-[10px] font-medium text-purple-200 px-2 py-0.5 bg-purple-500/[0.15] border border-purple-500/[0.25] rounded" }, item.projectName)
        ),
        // Content preview
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", { className: "rounded-md border border-white/[0.08] bg-gradient-to-br from-purple-900/30 to-slate-900 aspect-video flex items-center justify-center" },
            e(ImageIcon, { size: 32, className: "text-slate-600" })
          ),
          e("div", null,
            e("div", { className: "text-[9px] text-slate-500 mb-1" }, "内容预览"),
            e("div", { className: "text-[11px] font-medium text-white leading-relaxed mb-1" }, item.title || item.label),
            // 诚实化:无真实简介时不再兜底编造「12 分钟评测…」
            e("div", { className: "text-[10px] text-slate-400 leading-relaxed" }, item.description || "暂无内容简介(待接入)")
          )
        ),
        // 预测指标(诚实化:原 ~12M / ~6.2% / ~340 是硬编码假数,预测后端未接入 → 统一「待接入」)
        e("div", { className: "grid grid-cols-3 gap-2" },
          [
            { label: "预期曝光" },
            { label: "预期 ER" },
            { label: "短链点击" },
          ].map((s, i) => e("div", {
            key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-center",
            title: "预测指标后端未接入,接入后显示真实预估"
          },
            e("div", { className: "text-[9px] text-slate-500 mb-0.5" }, s.label),
            e("div", { className: "text-[11px] font-medium text-slate-500" }, "待接入")
          ))
        ),
        // Actions(2026-06-12 死按钮诚实化:审批/编辑时间/提醒 KOL 无写接口 → disabled+待接入;取消 → 关闭弹窗)
        e("div", { className: "flex flex-wrap items-center gap-2 pt-2" },
          e("button", { onClick: doApprove, disabled: !canAct || !!busy, title: canAct ? "标记审批通过" : "缺少来源标识,无法审批", className: "flex items-center gap-1 rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed" },
            e(Check, { size: 11 }), busy === "approve" ? "提交中…" : t("审批通过")),
          e("button", { onClick: doReschedule, disabled: !canAct || !!busy, title: "编辑计划发布时间", className: "flex items-center gap-1 rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04] disabled:opacity-50 disabled:cursor-not-allowed" },
            e(Edit3, { size: 11 }), busy === "reschedule" ? "更新中…" : "编辑时间"),
          e("button", { onClick: doRemind, disabled: !canAct || !!busy, title: "记录提醒 KOL", className: "flex items-center gap-1 rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04] disabled:opacity-50 disabled:cursor-not-allowed" },
            e(Bell, { size: 11 }), busy === "remind" ? "记录中…" : "提醒 KOL"),
          msg && e("span", { className: `text-[10px] ${msg.ok ? "text-emerald-300" : "text-rose-300"}` }, msg.text),
          e("button", { onClick: onClose, className: "flex items-center gap-1 ml-auto rounded-md border border-red-500/30 px-3 py-1.5 text-[11px] text-red-300 hover:bg-red-500/[0.06]" },
            e(X, { size: 11 }), "关闭")
        )
      )
    )
  );
}
