// Base verbatim from vkpi_v6.15.7_integrated.html
// #22 真指标:open 时 resolve(mover.handle)→主池真 followers/均播/发布明细;未匹配诚实标注、不编造。
// #5 合作按钮:匹配主池后「加大投入/续约/评估/退出合作」接真 /cooperation 登记;未匹配则提示先入库。

import React from "react";
import { motion } from "framer-motion";
import { ImageIcon, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { formatNumber } from "../../lib/format";
import { resolveKolPool, recordKolCooperation } from "../../../../../services/vkpi/kolPool-api";

const e = React.createElement;

export function KOLDetailModal({ mover, onClose, onOpenKolPool, apiToken = "" }: { mover?: any; onClose?: () => void; onOpenKolPool?: (mover: any) => void; apiToken?: string }) {
  const { t } = useT();
  const [resolved, setResolved] = React.useState<any>(null);
  const [resolving, setResolving] = React.useState(false);
  const [coopBusy, setCoopBusy] = React.useState("");
  const [coopMsg, setCoopMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const handle = mover && mover.handle ? String(mover.handle) : "";
  React.useEffect(() => {
    let alive = true;
    setResolved(null); setCoopMsg(null);
    if (!apiToken || !handle) return;
    setResolving(true);
    resolveKolPool(apiToken, handle.replace("@", ""), (mover && mover.platform) || "")
      .then((r: any) => { if (alive) setResolved(r && r.matched ? r : { matched: false }); })
      .catch(() => { if (alive) setResolved({ matched: false }); })
      .finally(() => { if (alive) setResolving(false); });
    return () => { alive = false; };
  }, [apiToken, handle, mover && mover.platform]);

  if (!mover) return null;
  const matched = !!(resolved && resolved.matched);
  const poolId = matched ? resolved.kol_pool_id : null;
  const pendingBtn = "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-500 opacity-60 cursor-not-allowed";
  const liveBtn = "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]";
  const openProfile = onOpenKolPool ? () => { onClose && onClose(); onOpenKolPool(mover); } : undefined;

  const recordCoop = async (action: string) => {
    if (!apiToken || !poolId || coopBusy) return;
    setCoopBusy(action); setCoopMsg(null);
    try {
      await recordKolCooperation(apiToken, poolId, action);
      setCoopMsg({ ok: true, text: `已登记:${action}` });
    } catch (err: any) {
      setCoopMsg({ ok: false, text: String(err && err.message ? err.message : err) });
    } finally { setCoopBusy(""); }
  };
  const coopBtn = (label: string, danger = false) =>
    matched
      ? e("button", {
          key: label, disabled: !!coopBusy, onClick: () => recordCoop(label),
          title: `登记一条合作动作:${label}`,
          className: danger
            ? "rounded-md border border-red-500/30 px-3 py-1.5 text-[11px] text-red-300 hover:bg-red-500/[0.1]" + (coopBusy ? " opacity-60" : "")
            : liveBtn + (coopBusy ? " opacity-60" : ""),
        }, coopBusy === label ? "登记中…" : label)
      : e("button", { key: label, disabled: true, title: "先在 KOL Pool 入库后可登记合作", className: danger ? "rounded-md border border-red-500/20 px-3 py-1.5 text-[11px] text-red-300/50 opacity-60 cursor-not-allowed" : pendingBtn }, label);

  // sparkline 形状示意(榜单动量);数值用真:resolved 优先。
  const trendData = {
    follower: [0,2,3,5,8,11,12.4].map(v => ({ value: v })),
    reach:    [0,5,12,18,24,32,38.4].map(v => ({ value: v })),
    er:       [5.2,5.4,5.6,5.8,6.0,6.2,6.24].map(v => ({ value: v })),
  };
  const realPosts: any[] = matched && Array.isArray(resolved.recent_posts) ? resolved.recent_posts : [];
  const erValue = matched && Number(resolved.engagement_rate) ? `${Number(resolved.engagement_rate).toFixed(2)}%` : (matched ? "—" : (mover.er || "—"));
  const subtitle = resolving
    ? "解析主池数据…"
    : matched
      ? `${resolved.platform || mover.platform || ""}${resolved.followers ? " · " + formatNumber(resolved.followers) + " followers" : ""}${resolved.avg_views ? " · 均播 " + formatNumber(resolved.avg_views) : ""}`.replace(/^ · /, "")
      : `${mover.type === "matrix" ? "公司矩阵" : "榜单动量"} · 未匹配主池(指标以榜单为准)`;

  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "cockpit-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-2xl rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-[16px] font-bold text-white",
          style: { background: `linear-gradient(135deg, ${mover.badgeColor}cc, ${mover.badgeColor}88)` }
        }, (mover.handle || "K").replace("@", "").charAt(0).toUpperCase()),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-2 mb-0.5" },
            e("h2", { className: "text-sm font-semibold text-white" }, mover.handle),
            mover.type === "matrix" && e("span", { className: "text-[9px] uppercase tracking-wider bg-purple-500/[0.15] text-purple-300 px-1.5 py-0.5 rounded" }, "矩阵"),
            matched && e("span", { className: "text-[9px] uppercase tracking-wider bg-emerald-500/[0.15] text-emerald-300 px-1.5 py-0.5 rounded" }, `主池 #${poolId}`),
            e("span", {
              className: "text-[9px] font-semibold px-2 py-0.5 rounded ml-1",
              style: { background: `${mover.badgeColor}22`, color: mover.badgeColor }
            }, mover.badge + " " + (mover.badge === "★" ? "Top performer" : mover.badge === "↑" ? "Rising" : mover.badge === "✓" ? "Active" : "At risk"))
          ),
          e("div", { className: "text-[10px] text-slate-500" }, subtitle)
        ),
        e("button", { onClick: onClose, className: "shrink-0 rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
      ),
      // Body
      e("div", { className: "p-5 space-y-3" },
        // 3 trend sparklines
        e("div", { className: "grid grid-cols-3 gap-2" },
          [
            { label: "7 天粉丝", value: mover.deltaFollower, color: "#10b981", data: trendData.follower },
            { label: "7 天曝光", value: mover.deltaReach,    color: "#06b6d4", data: trendData.reach },
            { label: "ER",       value: erValue,             color: "#fbbf24", data: trendData.er },
          ].map((m, i) => e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
            e("div", { className: "text-[9px] text-slate-500" }, m.label),
            e("div", { className: "text-[14px] font-semibold mb-1", style: { color: String(m.value || "").startsWith("-") ? "#ef4444" : m.color } }, m.value || "—"),
            // mini sparkline
            e("svg", { viewBox: "0 0 80 24", style: { width: "100%", height: "20px" } },
              (() => {
                const vals = m.data.map(d => (typeof d === "object" ? d.value : d));
                const max = Math.max(...vals, 1);
                const min = Math.min(...vals);
                const range = max - min || 1;
                const pts = vals.map((v, idx) => {
                  const x = (idx / (vals.length - 1)) * 78 + 1;
                  const y = 22 - ((v - min) / range) * 20;
                  return x.toFixed(1) + "," + y.toFixed(1);
                }).join(" ");
                return e("polyline", { points: pts, fill: "none", stroke: m.color, strokeWidth: 1.5, strokeLinecap: "round" });
              })()
            )
          ))
        ),
        // Recent posts — 真发布明细(resolved.recent_posts);未匹配/为空诚实标注,不编造
        e("div", null,
          e("div", { className: "text-[10px] text-slate-500 mb-2" }, t("最近发布")),
          realPosts.length > 0
            ? e("div", { className: "grid grid-cols-3 gap-2" },
                realPosts.slice(0, 3).map((p: any, i: number) => e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] overflow-hidden" },
                  e("div", { className: "aspect-video bg-gradient-to-br from-purple-900/30 to-slate-900 flex items-center justify-center" },
                    e(ImageIcon, { size: 20, className: "text-slate-600" })
                  ),
                  e("div", { className: "p-2" },
                    e("div", { className: "text-[10px] font-medium text-white truncate" }, String(p.title || p.caption || p.text || p.url || `#${i + 1}`)),
                    e("div", { className: "text-[9px] text-slate-500 mt-0.5" }, [p.reach || p.views ? formatNumber(Number(p.reach || p.views)) + " 曝光" : "", p.er ? "ER " + p.er : ""].filter(Boolean).join(" · ") || "—")
                  )
                ))
              )
            : e("div", { className: "text-[10px] text-slate-600 rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-3 text-center" },
                resolving ? "解析中…" : matched ? "主池暂无发布明细记录" : "未匹配主池,无发布明细(可在 KOL Pool 入库后补全)"),
        ),
        // Actions — #5:匹配主池后合作按钮接真 /cooperation;查看完整档案 → 跳 KOL Pool
        e("div", { className: "pt-2 flex flex-wrap items-center gap-2 border-t border-white/[0.06]" },
          mover.badge !== "⚠️"
            ? [coopBtn(t("加大投入")), coopBtn("续约")]
            : [coopBtn(t("评估")), coopBtn("退出合作", true)],
          e("button", {
            key: "profile",
            onClick: openProfile,
            disabled: !openProfile,
            title: openProfile ? "在 KOL Pool 查看完整档案" : "待接入",
            className: openProfile ? liveBtn : pendingBtn
          }, "查看完整档案 →"),
          coopMsg && e("span", { key: "msg", className: `text-[10px] ${coopMsg.ok ? "text-emerald-300" : "text-rose-300"}` }, coopMsg.text)
        )
      )
    )
  );
}
