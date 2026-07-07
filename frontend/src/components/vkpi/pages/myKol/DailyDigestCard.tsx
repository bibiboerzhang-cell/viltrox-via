// A3 每日学习摘要卡:收藏 KOL + 公司官号「这段时间发生了什么」(MyKolPage 顶部增益块)。
// 数据:GET /api/admin/vkpi/my-kol/daily-digest?days= —— 纯聚合已有数据
// (evidence 新增 / 发布窗口播放环比 + followers 环比 / Viltrox 提及词表 / 新联系方式计数
//  / 官号日快照 + 最好一条),零新采集/零 LLM。员工只看自己集合,管理层默认全员并集。
// 诚实态:每块 status==="empty" 时如实展示 reason(本地快照滞后也直说);接口失败整卡安静缺席。
// 联系方式只出类型/来源计数,明文走既有 contact_reveal 门控,绝不在摘要里裸露。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { BookOpenCheck, Building2, Contact, Megaphone, TrendingUp, Video } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type KolBrief = { kol_pool_id?: number; handle?: string; display_name?: string; platform?: string };
type NewVideoItem = KolBrief & {
  evidence_id?: number; title?: string; content_url?: string; media_kind?: string | null;
  view_count?: number | null; posted_day?: string | null; added_day?: string | null;
};
type ShiftItem = KolBrief & {
  cur_views?: number; prev_views?: number; cur_count?: number; prev_count?: number;
  delta_views?: number; delta_pct?: number | null;
};
type FollowerItem = KolBrief & { latest_followers?: number; base_followers?: number; delta_followers?: number; delta_pct?: number | null };
type MentionItem = KolBrief & { evidence_id?: number; title?: string; content_url?: string; view_count?: number | null; matched_terms?: string[] };
type ContactItem = KolBrief & { contact_type?: string; contact_source?: string | null; added_day?: string | null };
type BestPostItem = {
  title?: string; post_url?: string; platform?: string; handle?: string; display_name?: string;
  views?: number | null; views_delta?: number | null; likes?: number | null; comments?: number | null;
};
type Block<T> = { status?: string; reason?: string; note?: string } & T;
type DigestResp = {
  status?: string;
  reason?: string;
  window?: { days?: number; since?: string; until?: string };
  scope?: { staff_id?: number | null; mode?: string; kol_count?: number };
  kol?: Block<{
    new_videos?: Block<{ count?: number; kol_count?: number; items?: NewVideoItem[] }>;
    view_shift?: Block<{
      kol_count?: number; items?: ShiftItem[];
      followers?: Block<{ as_of?: string; base_date?: string; items?: FollowerItem[] }>;
    }>;
    viltrox_mentions?: Block<{ count?: number; items?: MentionItem[] }>;
    new_contacts?: Block<{ count?: number; by_type?: Record<string, number>; items?: ContactItem[] }>;
  }>;
  official?: Block<{
    snapshot_date?: string;
    latest_available?: string;
    totals?: { channel_count?: number; followers_delta?: number; views_delta?: number; posts_delta?: number };
    top_channels?: { platform?: string; handle?: string; display_name?: string; views_delta?: number; followers_delta?: number }[];
    best_post?: Block<{ method?: string; items?: BestPostItem[] }>;
  }>;
};

const DAY_CHOICES: { days: number; label: string }[] = [
  { days: 1, label: "昨天" },
  { days: 7, label: "近7天" },
  { days: 30, label: "近30天" },
];

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1_000_000) return sign + (Math.round(a / 100_000) / 10).toFixed(1) + "M";
  if (a >= 1_000) return sign + (Math.round(a / 100) / 10).toFixed(1) + "K";
  return sign + String(a);
}

function kolName(k: KolBrief): string {
  return k.display_name || k.handle || (k.kol_pool_id != null ? `KOL#${k.kol_pool_id}` : "—");
}

// 带符号的 delta 徽标:正绿负红零灰(纯展示)。
function DeltaTag(delta: number | null | undefined, pct?: number | null) {
  if (delta == null || !Number.isFinite(Number(delta))) return null;
  const v = Number(delta);
  const cls = v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-500";
  const text = (v > 0 ? "+" : "") + fmtNum(v) + (typeof pct === "number" ? ` (${pct > 0 ? "+" : ""}${pct}%)` : "");
  return e("span", { className: "tabular-nums font-medium " + cls }, text);
}

// 块级诚实空态:reason 来自后端,不本地编造。
function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/80" }, reason || "暂无数据");
}

function TitleLink(title?: string, url?: string) {
  const text = title || url || "(无标题)";
  return url
    ? e("a", {
        href: url, target: "_blank", rel: "noreferrer",
        className: "truncate text-[10.5px] text-slate-200 hover:text-cyan-200 hover:underline",
        title: text,
      }, text)
    : e("span", { className: "truncate text-[10.5px] text-slate-200", title: text }, text);
}

// 迷你块外壳:图标 + 标题 + 右侧计数徽标。
function MiniBlock(icon: React.ReactNode, title: string, badge: React.ReactNode, body: React.ReactNode) {
  return e("div", { className: "rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2.5 min-w-0" },
    e("div", { className: "flex items-center gap-1.5 mb-1.5" },
      icon,
      e("span", { className: "text-[10.5px] font-medium text-slate-300" }, title),
      e("span", { className: "ml-auto" }, badge || null),
    ),
    body,
  );
}

function CountBadge(count: number | undefined, unit: string) {
  if (!count) return null;
  return e("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] tabular-nums text-cyan-200" }, `${fmtNum(count)} ${unit}`);
}

export function DailyDigestCard({ apiToken }: { apiToken?: string }) {
  const [data, setData] = React.useState<DigestResp | null>(null);
  const [days, setDays] = React.useState<number>(1);
  const [loading, setLoading] = React.useState(false);

  // 只读拉取(后端纯聚合已有数据,读得起);失败静默,整卡安静缺席。
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    setLoading(true);
    void apiFetch<DigestResp>(`/api/admin/vkpi/my-kol/daily-digest?days=${encodeURIComponent(String(days))}`, {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, days]);

  if (!apiToken) return null;
  if (!data && !loading) return null; // 接口失败/未拉到:安静缺席,不甩报错
  if (!data) return null; // 首拉中也先不占位,拉到再一次性渲染
  if (String(data.status || "") === "error") return null;

  const kol = data.kol || {};
  const nv = kol.new_videos || {};
  const shift = kol.view_shift || {};
  const followers = shift.followers || {};
  const mentions = kol.viltrox_mentions || {};
  const contacts = kol.new_contacts || {};
  const official = data.official || {};
  const bestPost = official.best_post || {};
  const bestItems = Array.isArray(bestPost.items) ? bestPost.items : [];
  const totals = official.totals || {};
  const kolSetEmpty = String(kol.status || "") === "empty";

  return e("section", { className: "mt-4 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-3" },
    // ── 卡头:标题 + 范围说明 + 窗口切换 ──
    e("div", { className: "flex flex-wrap items-center gap-2" },
      e(BookOpenCheck, { size: 14, className: "text-cyan-300" }),
      e("span", { className: "text-[12px] font-semibold text-slate-100" }, "每日学习"),
      e("span", { className: "text-[10px] text-slate-500" },
        `MY KOL ${Number(data.scope?.kol_count) || 0} 个 + 官号 · ${data.window?.since || ""} 以来的变化`),
      e("div", { className: "ml-auto flex items-center gap-1" },
        DAY_CHOICES.map((choice) => e("button", {
          key: choice.days,
          type: "button",
          onClick: () => setDays(choice.days),
          className: "rounded-full px-2 py-0.5 text-[9.5px] transition-colors " + (days === choice.days
            ? "bg-cyan-500/20 text-cyan-200 border border-cyan-300/30"
            : "bg-black/20 text-slate-400 border border-white/[0.06] hover:text-slate-200"),
        }, choice.label)),
      ),
    ),

    kolSetEmpty
      ? e("div", { className: "mt-2" }, EmptyLine(String(kol.reason || "")))
      : e("div", { className: "mt-2.5 grid gap-2 md:grid-cols-2 xl:grid-cols-3" },

          // ── 🆕 新视频(evidence 新增)──
          MiniBlock(
            e(Video, { size: 11, className: "text-emerald-300" }),
            "新视频",
            CountBadge(Number(nv.count) || 0, "条"),
            nv.status === "ready"
              ? e("div", { className: "space-y-1" },
                  e("div", { className: "text-[9px] text-slate-500" }, `${Number(nv.kol_count) || 0} 个 KOL 有新内容入库`),
                  (nv.items || []).slice(0, 3).map((it, i) => e("div", { key: it.evidence_id || i, className: "flex items-center gap-1.5 min-w-0" },
                    TitleLink(it.title, it.content_url),
                    e("span", { className: "ml-auto shrink-0 text-[9px] tabular-nums text-slate-500" }, "播放 " + fmtNum(it.view_count)),
                  )),
                )
              : EmptyLine(String(nv.reason || "")),
          ),

          // ── 📈 播放异动(环比)+ followers 环比 ──
          MiniBlock(
            e(TrendingUp, { size: 11, className: "text-amber-300" }),
            "播放异动(环比)",
            CountBadge(Number(shift.kol_count) || 0, "KOL"),
            shift.status === "ready"
              ? e("div", { className: "space-y-1" },
                  (shift.items || []).slice(0, 3).map((it, i) => e("div", { key: it.kol_pool_id || i, className: "flex items-center gap-1.5 text-[10px] min-w-0" },
                    e("span", { className: "truncate text-slate-300", title: kolName(it) }, kolName(it)),
                    e("span", { className: "ml-auto shrink-0 text-[9px] text-slate-500 tabular-nums" }, `${fmtNum(it.cur_views)} vs ${fmtNum(it.prev_views)}`),
                    e("span", { className: "shrink-0 text-[9px]" }, DeltaTag(it.delta_views, it.delta_pct)),
                  )),
                  shift.note && e("div", { className: "text-[8.5px] leading-relaxed text-slate-600" }, String(shift.note)),
                  followers.status === "ready" && e("div", { className: "pt-1 border-t border-white/[0.05]" },
                    e("div", { className: "text-[9px] text-slate-500 mb-0.5" }, `粉丝环比(快照 ${followers.base_date || "—"} → ${followers.as_of || "—"})`),
                    (followers.items || []).slice(0, 2).map((it, i) => e("div", { key: it.kol_pool_id || i, className: "flex items-center gap-1.5 text-[10px] min-w-0" },
                      e("span", { className: "truncate text-slate-300", title: kolName(it) }, kolName(it)),
                      e("span", { className: "ml-auto shrink-0 text-[9px]" }, DeltaTag(it.delta_followers, it.delta_pct)),
                    )),
                  ),
                )
              : EmptyLine(String(shift.reason || "")),
          ),

          // ── 💬 提及 Viltrox 的新内容(词表)──
          MiniBlock(
            e(Megaphone, { size: 11, className: "text-purple-300" }),
            "提及 Viltrox",
            CountBadge(Number(mentions.count) || 0, "条"),
            mentions.status === "ready"
              ? e("div", { className: "space-y-1" },
                  (mentions.items || []).slice(0, 3).map((it, i) => e("div", { key: it.evidence_id != null ? String(it.evidence_id) : String(i), className: "flex items-center gap-1.5 min-w-0" },
                    TitleLink(it.title, it.content_url),
                    e("span", { className: "ml-auto shrink-0 text-[9px] text-slate-500" }, kolName(it)),
                  )),
                  e("div", { className: "text-[8.5px] text-slate-600" }, "词表识别(标题/频道名),宁缺毋滥"),
                )
              : EmptyLine(String(mentions.reason || "")),
          ),

          // ── 📇 联系方式新获得(只出类型/来源,明文走门控)──
          MiniBlock(
            e(Contact, { size: 11, className: "text-sky-300" }),
            "新联系方式",
            CountBadge(Number(contacts.count) || 0, "条"),
            contacts.status === "ready"
              ? e("div", { className: "space-y-1" },
                  e("div", { className: "flex flex-wrap gap-1" },
                    Object.entries(contacts.by_type || {}).map(([type, count]) => e("span", {
                      key: type,
                      className: "rounded bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-200 tabular-nums",
                    }, `${type} ×${count}`)),
                  ),
                  (contacts.items || []).slice(0, 3).map((it, i) => e("div", { key: i, className: "flex items-center gap-1.5 text-[10px] min-w-0" },
                    e("span", { className: "truncate text-slate-300", title: kolName(it) }, kolName(it)),
                    e("span", { className: "ml-auto shrink-0 text-[9px] text-slate-500" }, (it.contact_type || "") + (it.contact_source ? ` · ${it.contact_source}` : "")),
                  )),
                  e("div", { className: "text-[8.5px] text-slate-600" }, "摘要不出明文,查看走原有联系方式入口"),
                )
              : EmptyLine(String(contacts.reason || "")),
          ),

          // ── 🏢 官号昨日表现 + 最好一条(占两列宽,信息密度高)──
          e("div", { className: "md:col-span-2 xl:col-span-2" },
            MiniBlock(
              e(Building2, { size: 11, className: "text-cyan-300" }),
              "公司官号",
              official.status === "ready"
                ? e("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200" }, `快照 ${official.snapshot_date || "—"}`)
                : null,
              official.status === "ready"
                ? e("div", { className: "space-y-1.5" },
                    e("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400 tabular-nums" },
                      e("span", null, `${Number(totals.channel_count) || 0} 个官号`),
                      e("span", null, "粉丝 ", DeltaTag(totals.followers_delta)),
                      e("span", null, "播放 ", DeltaTag(totals.views_delta)),
                      e("span", null, "发帖 ", DeltaTag(totals.posts_delta)),
                    ),
                    (official.top_channels || []).length > 0 && e("div", { className: "flex flex-wrap gap-1" },
                      (official.top_channels || []).map((c, i) => e("span", {
                        key: i,
                        className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-[9px] text-slate-300 tabular-nums",
                        title: `${c.platform || ""} ${c.handle || ""}`,
                      }, `${c.display_name || c.handle || "—"} 播放+${fmtNum(c.views_delta)}`)),
                    ),
                    bestPost.status === "ready" && bestItems[0] && e("div", { className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
                      e("div", { className: "flex items-center gap-1.5 min-w-0" },
                        e("span", { className: "shrink-0 text-[9px] font-bold text-amber-300" }, "最好一条"),
                        TitleLink(bestItems[0].title, bestItems[0].post_url),
                      ),
                      e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2.5 text-[9px] tabular-nums text-slate-500" },
                        e("span", null, `${bestItems[0].display_name || bestItems[0].handle || ""} · ${bestItems[0].platform || ""}`),
                        e("span", null, "播放 " + fmtNum(bestItems[0].views)),
                        e("span", null, "当日 ", DeltaTag(bestItems[0].views_delta)),
                        bestPost.note && e("span", { className: "text-slate-600" }, String(bestPost.note)),
                      ),
                    ),
                    bestPost.status !== "ready" && EmptyLine(String(bestPost.reason || "")),
                  )
                : EmptyLine(String(official.reason || "")),
            ),
          ),
        ),

    e("div", { className: "mt-2 text-[8.5px] text-slate-600" },
      "口径:纯聚合已有数据(视频证据 / KOL 日快照 followers / 官号日快照),零 LLM;数据不足的块如实留空,不外推。"),
  );
}
