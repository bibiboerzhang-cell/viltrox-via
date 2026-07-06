// 件② 商务信号面板:制作周期 + 竞业监控 两块小卡合一(KOL 抽屉增益块,自取数)。
// 数据:GET /kol-pool/{id}/production-leadtime(签收→发布历史间隔,观察窗口链+阶段事件)
//      GET /kol-pool/{id}/competing-activity(近 90 天竞品露出,final_v1 深析产物+brand_signal)
// 均为纯读已有履约/深析数据(零新采集/零 LLM)。诚实态:后端 status='empty' 时明说
// 「无签收→发布记录 / 近 90 天无竞品露出」,绝不编数;接口失败/未授权安静缺席(增益块非阻塞)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { Timer, Radar, ExternalLink } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type LeadtimeSample = {
  source?: string;
  project_id?: number;
  delivered_at?: string | null;
  published_at?: string | null;
  days?: number;
  post_status?: string | null;
  content_url?: string | null;
};
type LeadtimeResp = {
  status?: string;
  median_days?: number | null;
  sample_count?: number;
  samples?: LeadtimeSample[];
  diagnostics?: {
    windows_seen?: number;
    posts_seen?: number;
    pairs_out_of_range?: number;
    stage_projects_seen?: number;
  };
};
type CompetingItem = {
  brand?: string;
  when?: string | null;
  scene?: string;
  risk?: string;
  source?: string;
  evidence_ref?: { evidence_id?: number | null; content_url?: string | null; title?: string; platform?: string | null };
};
type CompetingResp = {
  status?: string;
  window_days?: number;
  items?: CompetingItem[];
  undated_items?: CompetingItem[];
  brands?: Array<{ brand?: string; count?: number }>;
  scanned_videos?: number;
};

function dayStr(iso?: string | null): string {
  return iso ? String(iso).slice(0, 10) : "—";
}

export function CommerceSignalsPanel({ apiToken, kolPoolId }: any) {
  const [leadtime, setLeadtime] = React.useState<LeadtimeResp | null>(null);
  const [competing, setCompeting] = React.useState<CompetingResp | null>(null);

  // 开抽屉/换 KOL 各拉一次(后端纯聚合读,轻);单边失败置 null,另一边照常渲染。
  React.useEffect(() => {
    setLeadtime(null);
    setCompeting(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    const idPath = encodeURIComponent(String(kolPoolId));
    void apiFetch<LeadtimeResp>(`/api/admin/vkpi/kol-pool/${idPath}/production-leadtime`, {}, apiToken)
      .then((p) => { if (!cancelled) setLeadtime(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setLeadtime(null); });
    void apiFetch<CompetingResp>(`/api/admin/vkpi/kol-pool/${idPath}/competing-activity`, {}, apiToken)
      .then((p) => { if (!cancelled) setCompeting(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setCompeting(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId) return null;
  const ltOk = leadtime && leadtime.status !== "error" ? leadtime : null;
  const cpOk = competing && competing.status !== "error" ? competing : null;
  // 两边都拿不到(未授权/后端未接线/都报错):整块安静缺席,不给用户甩报错。
  if (!ltOk && !cpOk) return null;

  const medianDays = ltOk && typeof ltOk.median_days === "number" ? ltOk.median_days : null;
  const sampleCount = Number(ltOk?.sample_count) || 0;
  const samples = Array.isArray(ltOk?.samples) ? ltOk!.samples!.slice(0, 3) : [];
  const outOfRange = Number(ltOk?.diagnostics?.pairs_out_of_range) || 0;
  const windowsSeen = Number(ltOk?.diagnostics?.windows_seen) || 0;

  const items = Array.isArray(cpOk?.items) ? cpOk!.items! : [];
  const undated = Array.isArray(cpOk?.undated_items) ? cpOk!.undated_items! : [];
  const brands = Array.isArray(cpOk?.brands) ? cpOk!.brands! : [];
  const windowDays = Number(cpOk?.window_days) || 90;
  const competingCount = items.length + undated.length;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "commercesignals",
      header: e(React.Fragment, null,
        e(Timer, { size: 11, className: "text-sky-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "商务信号 · 制作周期 & 竞业"),
        medianDays != null && e("span", {
          className: "rounded bg-sky-500/12 px-1.5 py-0.5 text-[9px] font-medium text-sky-200",
        }, `中位 ${medianDays} 天`),
        competingCount > 0 && e("span", {
          className: "rounded bg-rose-500/12 px-1.5 py-0.5 text-[9px] font-medium text-rose-200",
        }, `竞品 ×${competingCount}`),
      ),
    },
    // ── 卡 1:制作周期(签收→发布历史间隔)──────────────────────────
    ltOk && e("div", { className: "mt-1 rounded border border-white/[0.05] bg-black/15 px-2.5 py-2" },
      e("div", { className: "flex items-center gap-1.5 mb-1" },
        e(Timer, { size: 10, className: "text-sky-400" }),
        e("span", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "制作周期 · 签收→发布"),
      ),
      ltOk.status === "ok" && medianDays != null
        ? e(React.Fragment, null,
            e("div", { className: "flex items-baseline gap-2" },
              e("span", { className: "text-[18px] font-bold tabular-nums text-white" }, String(medianDays)),
              e("span", { className: "text-[10px] text-slate-400" }, "天(中位)"),
              e("span", { className: "text-[9px] text-slate-500" }, `样本 ${sampleCount} 段履约`),
            ),
            samples.length > 0 && e("div", { className: "mt-1.5 space-y-1" },
              samples.map((s: LeadtimeSample, i: number) => e("div", {
                key: i,
                className: "flex items-center justify-between gap-2 rounded border border-white/[0.04] bg-black/20 px-2 py-1",
              },
                e("span", { className: "text-[9.5px] text-slate-300 tabular-nums" },
                  `${dayStr(s.delivered_at)} 签收 → ${dayStr(s.published_at)} 发布`,
                ),
                e("span", { className: "shrink-0 text-[9.5px] font-semibold tabular-nums text-sky-200" },
                  `${typeof s.days === "number" ? s.days : "—"} 天`,
                ),
              )),
            ),
          )
        // 诚实空态:履约链里还没有「签收后发布」的完整记录;有坏配对(发布早于签收的历史回填)如实说。
        : e("div", { className: "text-[10px] leading-relaxed text-slate-500" },
            windowsSeen > 0 && outOfRange > 0
              ? `有 ${windowsSeen} 个签收窗口,但 ${outOfRange} 对签收/发布时间不构成有效间隔(发布早于签收的历史回填)— 暂无法计算制作周期。`
              : "该 KOL 还没有「签收→发布」的完整履约记录 — 履约闭环跑起来后这里会自动点亮。",
          ),
    ),
    // ── 卡 2:竞业监控(近 90 天竞品露出)──────────────────────────
    cpOk && e("div", { className: "mt-1.5 rounded border border-white/[0.05] bg-black/15 px-2.5 py-2" },
      e("div", { className: "flex items-center gap-1.5 mb-1" },
        e(Radar, { size: 10, className: "text-rose-400" }),
        e("span", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, `竞业监控 · 近 ${windowDays} 天`),
        brands.length > 0 && e("span", { className: "text-[9px] text-slate-600" }, `扫描 ${Number(cpOk.scanned_videos) || 0} 条深析视频`),
      ),
      competingCount > 0
        ? e(React.Fragment, null,
            brands.length > 0 && e("div", { className: "flex flex-wrap gap-1 mb-1.5" },
              brands.slice(0, 8).map((b, i: number) => e("span", {
                key: i,
                className: "px-1.5 py-0.5 rounded text-[9.5px] border",
                style: { background: "rgba(244,63,94,0.12)", borderColor: "rgba(244,63,94,0.25)", color: "#fda4af" },
              }, `${b.brand || "?"} ×${Number(b.count) || 0}`)),
            ),
            e("div", { className: "space-y-1" },
              [...items, ...undated].slice(0, 5).map((it: CompetingItem, i: number) => {
                const url = it.evidence_ref?.content_url || "";
                return e("div", { key: i, className: "rounded border border-white/[0.04] bg-black/20 px-2 py-1" },
                  e("div", { className: "flex items-center justify-between gap-2" },
                    e("span", { className: "text-[10px] font-medium text-rose-200" }, String(it.brand || "?")),
                    e("span", { className: "shrink-0 text-[9px] text-slate-500 tabular-nums" },
                      it.when ? dayStr(it.when) : "发布日期未知",
                    ),
                  ),
                  (it.scene || url) && e("div", { className: "mt-0.5 flex items-center justify-between gap-2" },
                    e("span", { className: "truncate text-[9px] leading-relaxed text-slate-400" }, String(it.scene || "")),
                    url && e("a", {
                      href: url, target: "_blank", rel: "noopener noreferrer",
                      className: "shrink-0 text-slate-500 hover:text-slate-300",
                      title: "打开原内容",
                      onClick: (ev: any) => ev.stopPropagation(),
                    }, e(ExternalLink, { size: 9 })),
                  ),
                );
              }),
            ),
            undated.length > 0 && e("div", { className: "mt-1 text-[8.5px] text-slate-600" },
              `含 ${undated.length} 条发布日期未知的露出(视频抓取时无日期,不冒充近 ${windowDays} 天)`,
            ),
          )
        // 诚实空态:已深析内容里近窗没有竞品露出(或该 KOL 尚无深析样本)。
        : e("div", { className: "text-[10px] leading-relaxed text-slate-500" },
            Number(cpOk.scanned_videos) > 0
              ? `近 ${windowDays} 天已深析的 ${Number(cpOk.scanned_videos)} 条内容里未发现竞品露出。`
              : `近 ${windowDays} 天没有已深析的内容可查 — 跑过深度分析后这里会自动点亮。`,
          ),
    ),
    ),
  );
}
