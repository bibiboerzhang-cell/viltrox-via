// G4 件②③:品牌安全 × 受众真实性 合一面板(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/brand-safety + /authenticity —— 全库内信号 v0
// (FTC 披露/评论负面聚类/争议词表/竞品绑定 + 评论重复/模板化/互动离群/既有假粉列),
// 零外网、零 LLM、零写库;12 类风险框架诚实标「库内信号 v0,外网扫描待接」。
// 诚实态:每信号 status==="empty" 如实展示 reason;两接口都失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,risk_level 仅 none/info/warn 提示绝不下结论;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { ShieldAlert, UserCheck } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type Level = "none" | "info" | "warn" | string;
type FrameworkEntry = {
  key?: string; label?: string; coverage?: string; risk_level?: Level; signal_count?: number; note?: string;
};
type SafetyResp = {
  status?: string; reason?: string; risk_level?: Level; framework?: FrameworkEntry[];
  signals?: {
    disclosure?: any; comment_negativity?: any; content_controversy?: any; competitor_binding?: any;
  };
  coverage?: { evidence_count?: number; deep_analyzed_count?: number; comments_scanned?: number };
};
type AuthResp = {
  status?: string; reason?: string; authenticity_score?: number | null;
  confidence?: { label?: string; comment_sample?: number };
  deductions?: { signal?: string; points?: number; level?: Level }[];
  signals?: Record<string, any>;
};

// 等级 chip:none=静默灰,info=天蓝提示,warn=琥珀预警(绝不用红色下定论)。
function levelChip(level: Level, textWhenNone = "无信号") {
  const lv = String(level || "none");
  const cls = lv === "warn"
    ? "bg-amber-500/15 text-amber-200"
    : lv === "info"
      ? "bg-sky-500/15 text-sky-200"
      : "bg-slate-500/10 text-slate-500";
  return e("span", { className: "rounded px-1.5 py-0.5 text-[9px] " + cls }, lv === "none" ? textWhenNone : lv);
}

function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, reason || "暂无数据");
}

function SignalLine(label: string, ready: boolean, body: React.ReactNode, reason?: string) {
  return e("div", { className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "text-[9.5px] font-medium text-slate-300" }, label),
    ready
      ? e("div", { className: "mt-0.5 text-[9.5px] leading-relaxed text-slate-400" }, body)
      : e("div", { className: "mt-0.5 text-[9px] text-slate-600" }, reason || "暂无数据"),
  );
}

const AUTH_SIGNAL_LABELS: Record<string, string> = {
  commenter_repeat: "评论者重复",
  template_comments: "评论模板化",
  engagement_outlier: "互动播放比离群",
  inflation_flag: "假粉离群列(P0-3)",
};

export function SafetyAuthenticityPanel({ apiToken, kolPoolId }: any) {
  const [safety, setSafety] = React.useState<SafetyResp | null>(null);
  const [auth, setAuth] = React.useState<AuthResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端纯聚合已有数据,读得起);单接口失败各自静默。
  React.useEffect(() => {
    setSafety(null);
    setAuth(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    const id = encodeURIComponent(String(kolPoolId));
    void apiFetch<SafetyResp>(`/api/admin/vkpi/kol-pool/${id}/brand-safety`, {}, apiToken)
      .then((p) => { if (!cancelled) setSafety(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setSafety(null); });
    void apiFetch<AuthResp>(`/api/admin/vkpi/kol-pool/${id}/authenticity`, {}, apiToken)
      .then((p) => { if (!cancelled) setAuth(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setAuth(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId) return null;
  const safetyOk = !!safety && String(safety.status || "") !== "error";
  const authOk = !!auth && String(auth.status || "") !== "error";
  if (!safetyOk && !authOk) return null; // 两路都失败/未回:安静缺席,不甩后端报错

  const framework = (safetyOk && Array.isArray(safety!.framework)) ? safety!.framework! : [];
  const signals = (safetyOk && safety!.signals) || {};
  const negativity = (signals as any).comment_negativity || {};
  const disclosure = (signals as any).disclosure || {};
  const competitor = (signals as any).competitor_binding || {};
  const controversy = (signals as any).content_controversy || {};
  const score = authOk ? auth!.authenticity_score : null;
  const scoreColor = typeof score === "number"
    ? (score >= 80 ? "text-emerald-300" : score >= 60 ? "text-amber-300" : "text-rose-300")
    : "text-slate-500";
  const confidence = (authOk && auth!.confidence) || {};
  const authSignals = (authOk && auth!.signals) || {};

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "safety-authenticity",
      header: e(React.Fragment, null,
        e(ShieldAlert, { size: 11, className: "text-amber-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "品牌安全 × 受众真实性"),
        e("span", { className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-[9px] text-slate-400" }, "库内信号 v0 · 外网扫描待接"),
        safetyOk && levelChip(safety!.risk_level || "none", "安全无信号"),
      ),
    },
      // ── 🛡️ 品牌安全:12 类风险框架 ──
      e("div", { className: "flex items-center gap-1.5 mt-2 mb-1" },
        e("span", { className: "text-[10px]" }, "🛡️"),
        e("span", { className: "text-[10px] font-medium text-slate-300" }, "品牌安全 12 类框架"),
        safetyOk && e("span", { className: "text-[8.5px] text-slate-600" },
          `证据 ${Number(safety!.coverage?.evidence_count) || 0} 条 · 深析 ${Number(safety!.coverage?.deep_analyzed_count) || 0} · 评论 ${Number(safety!.coverage?.comments_scanned) || 0}`),
      ),
      !safetyOk
        ? EmptyLine("品牌安全扫描暂不可用")
        : e(React.Fragment, null,
            String(safety!.status) === "empty" && EmptyLine(String(safety!.reason || "")),
            e("div", { className: "grid grid-cols-2 gap-1" },
              framework.map((f, i) => e("div", {
                key: f.key || i,
                className: "flex items-center gap-1.5 rounded border border-white/[0.05] bg-black/20 px-1.5 py-1",
              },
                e("span", { className: "truncate text-[9.5px] text-slate-300", title: f.note || f.label }, f.label || f.key),
                e("span", { className: "ml-auto shrink-0" },
                  f.coverage === "external_pending"
                    ? e("span", { className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-[9px] text-slate-600" }, "待接")
                    : levelChip(f.risk_level || "none")),
              )),
            ),
            // 四路信号摘要(逐信号诚实空态)
            e("div", { className: "mt-1.5 space-y-1" },
              SignalLine("FTC 披露习惯", disclosure.status === "ready",
                e(React.Fragment, null,
                  `已披露 ${Number(disclosure.disclosed) || 0} · 疑似未披露 ${Number(disclosure.undisclosed_suspect) || 0}(warn ${Number(disclosure.warn_count) || 0})· 干净 ${Number(disclosure.clean) || 0} `,
                  levelChip(disclosure.level || "none"),
                ), String(disclosure.reason || "")),
              SignalLine("评论区负面聚类", negativity.status === "ready",
                e(React.Fragment, null,
                  `负面密度 ${((Number(negativity.negative_density) || 0) * 100).toFixed(1)}%(${Number(negativity.negative_count) || 0}/${Number(negativity.comments_scanned) || 0})`,
                  (Array.isArray(negativity.clusters) ? negativity.clusters : []).slice(0, 3).map((c: any, i: number) =>
                    e("span", { key: i, className: "ml-1 rounded bg-rose-500/10 px-1.5 py-0.5 text-[9px] text-rose-200" }, `${c.term} ×${c.count}`)),
                  " ", levelChip(negativity.level || "none"),
                ), String(negativity.reason || "")),
              SignalLine("内容争议词表(标题+深析)", controversy.status === "ready",
                e(React.Fragment, null,
                  (Array.isArray(controversy.categories) ? controversy.categories : [])
                    .filter((c: any) => (Number(c.video_count) || 0) > 0)
                    .map((c: any, i: number) =>
                      e("span", { key: i, className: "mr-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200" }, `${c.label} ×${c.video_count}`)),
                  (Array.isArray(controversy.categories) ? controversy.categories : []).every((c: any) => !(Number(c.video_count) || 0)) && "8 类词表零命中(词表启发式,非背书)",
                ), String(controversy.reason || "")),
              SignalLine("竞品独占迹象", competitor.status === "ready",
                e(React.Fragment, null,
                  `竞品声量占比 ${((Number(competitor.competitor_share) || 0) * 100).toFixed(0)}%(Viltrox ${Number(competitor.viltrox_videos) || 0} vs 竞品 ${Number(competitor.competitor_units) || 0})`,
                  " ", levelChip(competitor.level || "none"),
                  competitor.message && e("div", { className: "mt-0.5 text-[9px] text-slate-500" }, String(competitor.message)),
                ), String(competitor.reason || "")),
            ),
          ),

      // ── ✅ 受众真实性:综合分 + 四路信号 ──
      e("div", { className: "flex items-center gap-1.5 mt-2.5 mb-1" },
        e(UserCheck, { size: 10, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] font-medium text-slate-300" }, "受众真实性"),
        authOk && confidence.label && e("span", { className: "text-[8.5px] text-slate-600" },
          `置信 ${confidence.label} · 评论样本 ${Number(confidence.comment_sample) || 0}`),
      ),
      !authOk
        ? EmptyLine("受众真实性信号暂不可用")
        : String(auth!.status) === "empty"
          ? EmptyLine(String(auth!.reason || ""))
          : e(React.Fragment, null,
              e("div", { className: "flex items-center gap-2" },
                e("span", { className: "text-[20px] font-bold tabular-nums " + scoreColor },
                  typeof score === "number" ? String(score) : "—"),
                e("span", { className: "text-[9px] text-slate-500" }, "/100(保守启发式,仅信号不下结论)"),
              ),
              (Array.isArray(auth!.deductions) ? auth!.deductions! : []).length > 0 && e("div", { className: "mt-1 flex flex-wrap gap-1" },
                auth!.deductions!.map((d, i) => e("span", {
                  key: i,
                  className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200",
                }, `${AUTH_SIGNAL_LABELS[String(d.signal)] || d.signal} −${Number(d.points) || 0}`)),
              ),
              e("div", { className: "mt-1.5 space-y-1" },
                Object.entries(AUTH_SIGNAL_LABELS).map(([key, label]) => {
                  const sig: any = (authSignals as any)[key] || {};
                  const ready = sig.status === "ready";
                  let body: React.ReactNode = null;
                  if (key === "commenter_repeat" && ready) {
                    body = e(React.Fragment, null,
                      `重复评论占比 ${((Number(sig.repeat_comment_share) || 0) * 100).toFixed(1)}%(${Number(sig.unique_commenters) || 0} 个评论者 / ${Number(sig.comments_with_author) || 0} 条)`,
                      " ", levelChip(sig.level || "none"));
                  } else if (key === "template_comments" && ready) {
                    const b = sig.breakdown || {};
                    body = e(React.Fragment, null,
                      `模板化占比 ${((Number(sig.templated_share) || 0) * 100).toFixed(1)}%(emoji ${Number(b.emoji_only) || 0} · 超短 ${Number(b.very_short) || 0} · 同文 ${Number(b.duplicated_text) || 0})`,
                      " ", levelChip(sig.level || "none"));
                  } else if (key === "engagement_outlier" && ready) {
                    body = e(React.Fragment, null,
                      `个人中位互动率池分位 p${Math.round((Number(sig.pool_percentile) || 0) * 100)}(对照 ${Number(sig.pool_kols_compared) || 0} 个 KOL)· ${String(sig.direction || "")}`,
                      " ", levelChip(sig.level || "none"));
                  } else if (key === "inflation_flag" && ready) {
                    body = e(React.Fragment, null,
                      sig.suspect_inflation
                        ? `已命中假粉离群规则:${String(sig.reason || "")}`
                        : "已检测未命中(规则法非背书)",
                      " ", levelChip(sig.level || "none"));
                  }
                  return e(React.Fragment, { key }, SignalLine(label, ready, body, String(sig.reason || "")));
                }),
              ),
            ),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:全库内信号 v0(标题/深析文本/已入库评论/既有假粉列),零外网、零 LLM;12 类框架对齐同行但外网扫描待接;独立展示信号,不参与 V6 Fit 评分。"),
    ),
  );
}
