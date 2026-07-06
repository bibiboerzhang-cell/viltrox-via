import React from "react";
import { apiFetch } from "../../../../services/http";

// 件 C · 市场之声 → 产品线月报页(反哺产品部,账二第 6 层)。
//   月份选择(缺省近 30 天)+ 三块:抱怨聚类 / 愿望清单 / 需求空白焦段 + 给产品部的建议清单。
//   后端:GET /api/admin/vkpi/market/voice-report?month=YYYY-MM —— 纯词表/正则聚合已有
//   评论库 + 意向队列 + B&H 口碑 + 品牌需求信号,零 LLM 零新采集(method=lexicon_v0)。
//   诚实空态:每块/每数据源 status==="empty" 时如实展示后端 reason,绝不本地编数据。
//   引文可展开:<details> 折叠原声引文(作者/平台/时间/来源表)。
//   红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;只看不动手,无任何执行按钮。

type Row = Record<string, any>;

const KIND_META: Record<string, { label: string; tone: string }> = {
  new_product: { label: "新品评估", tone: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100" },
  iteration: { label: "迭代评估", tone: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100" },
  mount_expansion: { label: "卡口扩展", tone: "border-purple-300/30 bg-purple-500/[0.12] text-purple-100" },
  improvement: { label: "改进关注", tone: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100" },
  watch: { label: "声量关注", tone: "border-white/[0.1] bg-white/[0.04] text-slate-300" },
};

const CONFIDENCE_LABEL: Record<string, string> = { high: "置信高", medium: "置信中", low: "置信低" };

const SOURCE_LABEL: Record<string, string> = {
  comments: "评论库",
  intent_queue: "意向队列",
  bh_reviews: "B&H 口碑",
  brand_signal: "需求信号",
  sentiment: "情感结果",
};

function SectionCard({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[13px] font-semibold text-white">{title}</div>
        {extra ?? null}
      </div>
      {children}
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-white/[0.08] px-3 py-4 text-center text-[12px] text-slate-500">{text}</div>;
}

// 原声引文列表(可展开):summary 显示条数,展开逐条带作者/平台/时间/来源表。
function QuoteFold({ quotes, summary }: { quotes: Row[]; summary?: string }) {
  if (!Array.isArray(quotes) || quotes.length === 0) return null;
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer select-none text-[10px] text-sky-300/80 hover:text-sky-200">
        {summary || `原声引文 ×${quotes.length}(点开)`}
      </summary>
      <div className="mt-1.5 space-y-1.5">
        {quotes.map((q, i) => (
          <div key={i} className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
            <div className="text-[11.5px] leading-relaxed text-slate-300">“{q.text}”</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
              <span>{q.author || "匿名"}</span>
              {q.platform ? <span>{q.platform}</span> : null}
              {q.at ? <span>{String(q.at).slice(0, 10)}</span> : null}
              {Number(q.likes) > 0 ? <span>♥ {q.likes}</span> : null}
              {q.source ? <span className="text-slate-600">{q.source}</span> : null}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

export function MarketVoicePage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [month, setMonth] = React.useState<string>("");
  const [data, setData] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    setData(null);
    const url = `/api/admin/vkpi/market/voice-report${month ? `?month=${encodeURIComponent(month)}` : ""}`;
    apiFetch<Row>(url, { timeoutMs: 30000 }, apiToken)
      .then((res) => {
        if (alive) setData(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setError(String(err?.detail || err?.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, month]);

  const sources: Row = data?.sources || {};
  const complaints: Row = data?.complaints || {};
  const complaintCats: Row[] = Array.isArray(complaints.categories) ? complaints.categories : [];
  const maxComplaint = complaintCats.reduce((acc, c) => Math.max(acc, Number(c.count) || 0), 0);
  const wishlist: Row = data?.wishlist || {};
  const focalReqs: Row[] = Array.isArray(wishlist.focal_requests) ? wishlist.focal_requests : [];
  const zoomReqs: Row[] = Array.isArray(wishlist.zoom_requests) ? wishlist.zoom_requests : [];
  const mountReqs: Row[] = Array.isArray(wishlist.mount_requests) ? wishlist.mount_requests : [];
  const wishItems: Row[] = Array.isArray(wishlist.items) ? wishlist.items : [];
  const gaps: Row = data?.gaps || {};
  const gapItems: Row[] = Array.isArray(gaps.items) ? gaps.items : [];
  const suggestions: Row = data?.suggestions || {};
  const suggItems: Row[] = Array.isArray(suggestions.items) ? suggestions.items : [];
  const lineBuckets: Row[] = Array.isArray(data?.buckets?.product_lines) ? data!.buckets.product_lines : [];

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[18px] font-semibold text-white">市场之声 · 产品线月报</div>
          <div className="mt-0.5 text-[12px] text-slate-400">
            用户反馈聚类反哺产品部 —— 评论库 + 意向队列 + B&H 口碑纯词表聚合(lexicon_v0,零 LLM 零采集),供人工复核。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="month"
            value={month}
            onChange={(ev) => setMonth(ev.target.value)}
            className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-white outline-none focus:border-sky-300/40"
          />
          <button
            onClick={() => setMonth("")}
            className={`rounded-xl border px-3 py-2 text-[12px] ${month ? "border-white/[0.08] text-slate-400 hover:text-white" : "border-sky-300/40 text-sky-200"}`}
          >
            近30天
          </button>
        </div>
      </div>

      {!apiToken && <EmptyLine text="未登录 / 无 token,无法加载数据。" />}
      {error && <div className="rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200">{error}</div>}
      {loading && <div className="py-10 text-center text-[12px] text-slate-400">市场之声聚合中…</div>}
      {!loading && data && String(data.status) === "error" && <EmptyLine text={`聚合失败:${String(data.reason || "未知原因")}`} />}

      {!loading && data && String(data.status) !== "error" && (
        <div className="space-y-4">
          {/* 数据源与窗口(逐源诚实标状态,空源如实报) */}
          <div className="flex flex-wrap items-center gap-1.5 text-[10.5px]">
            <span className="text-slate-500">窗口 {data.window?.label || "—"} · 样本 {Number(data.sample_size) || 0} 条</span>
            {Number(data.dedup_removed) > 0 && <span className="text-slate-600">(跨源去重 -{data.dedup_removed})</span>}
            {Object.keys(sources).map((k) => {
              const s: Row = sources[k] || {};
              const ok = String(s.status) === "ready";
              return (
                <span
                  key={k}
                  title={String(s.note || s.table || "")}
                  className={`rounded-full border px-2 py-0.5 ${ok ? "border-emerald-300/25 bg-emerald-500/[0.08] text-emerald-200" : "border-white/[0.08] text-slate-500"}`}
                >
                  {SOURCE_LABEL[k] || k} {Number(s.count) || 0}
                </span>
              );
            })}
          </div>

          {String(data.status) === "empty" ? (
            <EmptyLine text={String(data.reason || "该窗口无声音数据。")} />
          ) : (
            <>
              {/* 块一:抱怨聚类 */}
              <SectionCard
                title="抱怨聚类(话题词 + 负面线索双命中)"
                extra={<span className="text-[10px] text-slate-500">命中 {Number(complaints.total_matched) || 0} 条</span>}
              >
                {String(complaints.status) === "empty" ? (
                  <EmptyLine text={String(complaints.reason || "无抱怨命中。")} />
                ) : (
                  <div className="space-y-2">
                    {complaintCats.map((c) => {
                      const count = Number(c.count) || 0;
                      const widthPct = maxComplaint > 0 ? Math.max(6, Math.round((count / maxComplaint) * 100)) : 0;
                      return (
                        <div key={String(c.key)} className={count === 0 ? "opacity-40" : ""}>
                          <div className="flex items-center gap-2 text-[11.5px]">
                            <span className="w-[92px] shrink-0 text-slate-300">{c.label}</span>
                            <div className="relative h-[8px] flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                              <div
                                className="absolute inset-y-0 left-0 rounded-full"
                                style={{ width: `${count > 0 ? widthPct : 0}%`, background: "linear-gradient(90deg, rgba(251,113,133,0.35), rgba(251,113,133,0.75))" }}
                              />
                            </div>
                            <span className="w-[40px] shrink-0 text-right tabular-nums text-slate-400">×{count}</span>
                          </div>
                          {count > 0 && <QuoteFold quotes={Array.isArray(c.quotes) ? c.quotes : []} summary={`原声 top${Math.min(3, count)}(点开)`} />}
                        </div>
                      );
                    })}
                  </div>
                )}
              </SectionCard>

              {/* 块二:愿望清单 */}
              <SectionCard
                title="愿望清单(wish / hope / please make / 需要 词表)"
                extra={<span className="text-[10px] text-slate-500">命中 {Number(wishlist.total) || 0} 条</span>}
              >
                {String(wishlist.status) === "empty" ? (
                  <EmptyLine text={String(wishlist.reason || "无愿望命中。")} />
                ) : (
                  <div className="space-y-3">
                    {focalReqs.length > 0 && (
                      <div>
                        <div className="mb-1.5 text-[11px] text-slate-500">想要的焦段</div>
                        <div className="flex flex-wrap gap-1.5">
                          {focalReqs.map((f) => (
                            <span key={String(f.focal)} title={(Array.isArray(f.quotes) && f.quotes[0]?.text) || ""} className="rounded-full border border-sky-300/25 bg-sky-500/[0.08] px-2.5 py-1 text-[11px] text-sky-100">
                              {f.focal} <span className="text-sky-300/70">×{Number(f.count) || 0}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {zoomReqs.length > 0 && (
                      <div>
                        <div className="mb-1.5 text-[11px] text-slate-500">想要的变焦段</div>
                        <div className="flex flex-wrap gap-1.5">
                          {zoomReqs.map((z) => (
                            <span key={String(z.range_mm)} className="rounded-full border border-cyan-300/25 bg-cyan-500/[0.08] px-2.5 py-1 text-[11px] text-cyan-100">
                              {z.range_mm} <span className="text-cyan-300/70">×{Number(z.count) || 0}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {mountReqs.length > 0 && (
                      <div>
                        <div className="mb-1.5 text-[11px] text-slate-500">想要的卡口</div>
                        <div className="flex flex-wrap gap-1.5">
                          {mountReqs.map((m) => (
                            <span key={String(m.mount)} className="rounded-full border border-purple-300/25 bg-purple-500/[0.08] px-2.5 py-1 text-[11px] text-purple-100">
                              {m.mount} <span className="text-purple-300/70">×{Number(m.count) || 0}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <QuoteFold quotes={wishItems} summary={`愿望原声 top${wishItems.length}(点开)`} />
                  </div>
                )}
              </SectionCard>

              {/* 块三:需求空白焦段 */}
              <SectionCard
                title="需求空白(有声量但我方目录零 SKU 的焦段)"
                extra={
                  <span className="text-[10px] text-slate-500">
                    目录焦段 {Number(gaps.catalog_focal_count) || 0} 个{gaps.catalog_basis ? ` · ${gaps.catalog_basis}` : ""}
                  </span>
                }
              >
                {String(gaps.status) === "empty" ? (
                  <EmptyLine text={String(gaps.reason || "本窗口无目录空白焦段声量。")} />
                ) : (
                  <div className="space-y-2">
                    {gapItems.map((g) => (
                      <div key={String(g.focal)} className="rounded-lg border border-white/[0.06] px-3 py-2">
                        <div className="flex flex-wrap items-center gap-2 text-[12px]">
                          <span className="rounded-md border border-rose-300/30 bg-rose-500/[0.1] px-2 py-0.5 font-medium text-rose-100">{g.focal}</span>
                          <span className="text-slate-400">声量 ×{Number(g.voice_count) || 0}</span>
                          {Number(g.wish_count) > 0 && <span className="text-emerald-200">其中愿望 ×{g.wish_count}</span>}
                          <span className="ml-auto text-[10px] text-slate-600">目录零 SKU</span>
                        </div>
                        <QuoteFold quotes={Array.isArray(g.quotes) ? g.quotes : []} />
                      </div>
                    ))}
                  </div>
                )}
              </SectionCard>

              {/* 建议清单(纯规则生成,供产品部人工复核;本页只看不动手) */}
              <SectionCard
                title="给产品部的建议(规则生成 · lexicon_v0 · 人工复核)"
                extra={<span className="text-[10px] text-slate-500">{suggItems.length} 条</span>}
              >
                {String(suggestions.status) === "empty" ? (
                  <EmptyLine text={String(suggestions.reason || "无过阈值建议。")} />
                ) : (
                  <div className="space-y-2">
                    {suggItems.map((s, i) => {
                      const meta = KIND_META[String(s.kind)] || { label: String(s.kind || "建议"), tone: "border-white/[0.1] text-slate-300" };
                      return (
                        <div key={i} className="rounded-lg border border-white/[0.06] px-3 py-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`rounded-md border px-2 py-0.5 text-[10px] ${meta.tone}`}>{meta.label}</span>
                            <span className="text-[12px] text-slate-200">{s.title}</span>
                            <span className="ml-auto shrink-0 text-[10px] tabular-nums text-slate-500">
                              证据 ×{Number(s.evidence_count) || 0} · {CONFIDENCE_LABEL[String(s.confidence)] || String(s.confidence || "—")}
                            </span>
                          </div>
                          {s.detail ? <div className="mt-1 text-[11px] leading-relaxed text-slate-400">{s.detail}</div> : null}
                          <QuoteFold quotes={Array.isArray(s.quotes) ? s.quotes : []} />
                        </div>
                      );
                    })}
                  </div>
                )}
              </SectionCard>

              {/* 产品线声量分桶(focal_matrix 词表口径) */}
              {lineBuckets.length > 0 && (
                <SectionCard
                  title="产品线声量分桶"
                  extra={<span className="text-[10px] text-slate-500">{String(data.buckets?.product_line_basis || "")}</span>}
                >
                  <div className="flex flex-wrap gap-1.5">
                    {lineBuckets.map((b) => (
                      <span
                        key={String(b.key)}
                        title={(Array.isArray(b.example) && b.example[0]?.text) || ""}
                        className={`rounded-full border px-2.5 py-1 text-[11px] ${Number(b.count) > 0 ? "border-white/[0.1] bg-white/[0.03] text-slate-200" : "border-white/[0.05] text-slate-600"}`}
                      >
                        {b.label} <span className="text-slate-500">×{Number(b.count) || 0}</span>
                      </span>
                    ))}
                  </div>
                </SectionCard>
              )}

              <div className="text-right text-[10px] text-slate-600">
                生成于 {data.generated_at}(UTC)· {String(data.note || "纯词表聚合已有数据,零 LLM;独立展示信号,不参与 V6 Fit 评分。")}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
