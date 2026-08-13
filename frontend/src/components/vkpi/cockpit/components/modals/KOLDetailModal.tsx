// Base verbatim from vkpi_v6.15.7_integrated.html
// #22 真指标:open 时 resolve(mover.handle)→主池真 followers/均播/发布明细;未匹配诚实标注、不编造。
// #5 合作按钮:匹配主池后「加大投入/续约/评估/退出合作」接真 /cooperation 登记;未匹配则提示先入库。

import React from "react";
import { m } from "framer-motion";
import { ExternalLink, ImageIcon, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { formatNumber } from "../../lib/format";
import { kolHumanDisplayName } from "../../lib/kolIdentity";
import { resolveKolPool, recordKolCooperation } from "../../../../../services/vkpi/kolPool-api";

const e = React.createElement;

export function KOLDetailModal({ mover, onClose, onOpenKolPool, apiToken = "" }: { mover?: any; onClose?: () => void; onOpenKolPool?: (mover: any) => void; apiToken?: string }) {
  const { t } = useT();
  const [resolved, setResolved] = React.useState<any>(null);
  const [resolving, setResolving] = React.useState(false);
  const [coopBusy, setCoopBusy] = React.useState("");
  const [coopMsg, setCoopMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const handle = mover && mover.handle ? String(mover.handle) : "";
  const displayName = kolHumanDisplayName({ ...mover, display_name: mover?.display_name || mover?.name || mover?.handle });
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
  const pendingBtn = "rounded-md border border-line px-3 py-1.5 text-[11px] text-muted opacity-60 cursor-not-allowed";
  const liveBtn = "rounded-md border border-line px-3 py-1.5 text-[11px] text-ink-2 hover:bg-accent-soft";
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

  const realPosts: any[] = matched && Array.isArray(resolved.recent_posts) ? resolved.recent_posts : [];
  const erValue = matched && Number(resolved.engagement_rate) ? `${Number(resolved.engagement_rate).toFixed(2)}%` : (matched ? "—" : (mover.er || "—"));
  const fitNumber = Number(mover?.raw?.fit_now ?? mover?.raw?.v6_fit ?? mover?.raw?.fit_score);
  const fitValue = Number.isFinite(fitNumber) ? `Fit ${fitNumber.toFixed(1)}` : (String(mover.deltaFollower || "").startsWith("Fit") ? mover.deltaFollower : "—");
  const followersValue = matched && resolved.followers != null
    ? formatNumber(Number(resolved.followers))
    : mover?.raw?.followers != null
      ? formatNumber(Number(mover.raw.followers))
      : "—";
  const avgViewsValue = matched && resolved.avg_views != null ? formatNumber(Number(resolved.avg_views)) : "—";
  const subtitle = resolving
    ? "解析主池数据…"
    : matched
      ? `${resolved.platform || mover.platform || ""}${resolved.followers ? " · " + formatNumber(resolved.followers) + " followers" : ""}${resolved.avg_views ? " · 均播 " + formatNumber(resolved.avg_views) : ""}`.replace(/^ · /, "")
      : `${mover.type === "matrix" ? "公司矩阵" : "榜单动量"} · 未匹配主池(指标以榜单为准)`;

  return e(m.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "cockpit-modal fixed inset-0 flex items-center justify-center bg-black/65 backdrop-blur-lg p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(m.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-2xl rounded-xl border border-line bg-card shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "px-5 py-3.5 border-b border-line flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-[16px] font-bold text-white",
          style: { background: `linear-gradient(135deg, ${mover.badgeColor}cc, ${mover.badgeColor}88)` }
        }, displayName.charAt(0).toUpperCase()),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-2 mb-0.5" },
            e("h2", { className: "text-sm font-semibold text-ink" }, displayName),
            mover.type === "matrix" && e("span", { className: "text-[9px] uppercase tracking-wider bg-accent-soft text-accent px-1.5 py-0.5 rounded" }, "矩阵"),
            matched && e("span", { className: "text-[9px] uppercase tracking-wider bg-good-soft text-good px-1.5 py-0.5 rounded" }, `主池 #${poolId}`)
          ),
          e("div", { className: "text-[10px] text-muted" }, subtitle)
        ),
        e("button", { onClick: onClose, className: "shrink-0 rounded-md border border-line bg-panel p-1.5 text-muted hover:text-ink hover:bg-accent-soft", title: "关闭" }, e(X, { size: 14 }))
      ),
      // Body
      e("div", { className: "p-5 space-y-3" },
        // 真指标：无时序就不画趋势线。
        e("div", { className: "grid grid-cols-3 gap-2" },
          [
            { label: "V6 Fit", value: fitValue, note: "KOL Pool" },
            { label: "粉丝 / 均播", value: followersValue, note: avgViewsValue !== "—" ? `均播 ${avgViewsValue}` : "均播未记录" },
            { label: "证据互动率", value: erValue, note: matched ? "video evidence" : "未匹配主池" },
          ].map((metricRow, i) => e("div", { key: i, className: "rounded-md border border-line bg-panel p-2.5" },
            e("div", { className: "text-[9px] text-muted" }, metricRow.label),
            e("div", { className: "mt-1 text-[14px] font-semibold text-ink" }, metricRow.value || "—"),
            e("div", { className: "mt-1 text-[8.5px] text-muted" }, metricRow.note)
          ))
        ),
        // Recent posts — 真发布明细(resolved.recent_posts);未匹配/为空诚实标注,不编造
        e("div", null,
          e("div", { className: "text-[10px] text-muted mb-2" }, t("最近发布")),
          realPosts.length > 0
            ? e("div", { className: "grid grid-cols-3 gap-2" },
                realPosts.slice(0, 3).map((p: any, i: number) => e("a", { key: p.id || i, href: p.post_url || p.url || undefined, target: "_blank", rel: "noreferrer", className: "group rounded-md border border-line bg-panel overflow-hidden hover:border-line-strong" },
                  e("div", { className: "relative aspect-video bg-[var(--ds-bg-2)] flex items-center justify-center overflow-hidden" },
                    p.thumbnail_url
                      ? e("img", { src: p.thumbnail_url, alt: p.title || "发布证据", className: "h-full w-full object-cover transition duration-300 group-hover:scale-[1.02] motion-reduce:transition-none" })
                      : e(ImageIcon, { size: 20, className: "text-muted" }),
                    (p.post_url || p.url) && e("span", { className: "absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-md border border-white/20 bg-black/50 text-white" }, e(ExternalLink, { size: 10 }))
                  ),
                  e("div", { className: "p-2" },
                    e("div", { className: "text-[10px] font-medium text-ink truncate" }, String(p.title || p.caption || p.text || p.url || `#${i + 1}`)),
                    e("div", { className: "text-[9px] text-muted mt-0.5" }, [p.reach || p.views ? formatNumber(Number(p.reach || p.views)) + " 曝光" : "", p.likes != null ? formatNumber(Number(p.likes)) + " 赞" : ""].filter(Boolean).join(" · ") || "—")
                  )
                ))
              )
            : e("div", { className: "text-[10px] text-muted rounded-md border border-line bg-panel px-3 py-3 text-center" },
                resolving ? "解析中…" : matched ? "主池暂无发布明细记录" : "未匹配主池,无发布明细(可在 KOL Pool 入库后补全)"),
        ),
        // Actions — #5:匹配主池后合作按钮接真 /cooperation;查看完整档案 → 跳 KOL Pool
        e("div", { className: "pt-2 flex flex-wrap items-center gap-2 border-t border-line" },
          [coopBtn(t("评估")), coopBtn("续约")],
          e("button", {
            key: "profile",
            onClick: openProfile,
            disabled: !openProfile,
            title: openProfile ? "在 KOL Pool 查看完整档案" : "待接入",
            className: openProfile ? liveBtn : pendingBtn
          }, "查看完整档案"),
          coopMsg && e("span", { key: "msg", className: `text-[10px] ${coopMsg.ok ? "text-good" : "text-bad"}` }, coopMsg.text)
        )
      )
    )
  );
}
