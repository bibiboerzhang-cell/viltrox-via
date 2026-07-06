import React from "react";
import { apiFetch } from "../../../../services/http";

// 件③ · SKU 360° 页。
//   顶部 SKU 选择器(搜 vkpi_products)+ 档案块:基础信息 / 内容表现聚合 /
//   内容清单(按互动排序)/ 内容契合判断 / 相关评论抽样 / B&H reviews(表存在才显)。
//   后端:/api/admin/vkpi/sku/list | /sku/{sku}/profile。全只读、纯聚合已有数据。
//   缺数据诚实空态:matched=0 就明说「暂无匹配」,绝不杜撰。

type Row = Record<string, any>;

interface SkuListItem {
  sku: string;
  model_name: string;
  marketing_name: string;
  category_main: string;
  category_detail: string;
  series: string;
  mount: string;
  price_usd: number | null;
  status: string;
}

const FIELD_LABEL: Record<string, string> = {
  final_v1_products: "深析·产品识别",
  final_v1_presence: "深析·产品在场",
  evidence_title: "标题匹配",
  final_v1_brand_exposure: "深析·品牌露出",
  final_v1_summary: "深析·内容摘要",
};

const VERDICT_TONE: Record<string, string> = {
  fit: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  partial_fit: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  not_fit: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
};

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

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

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="mt-0.5 text-[16px] font-semibold text-white">{value}</div>
    </div>
  );
}

export function Sku360Page({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState<string>("");
  const [profile, setProfile] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  // 选择器搜索(300ms 防抖)。query 为空时也拉默认列表,方便直接浏览。
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      apiFetch<{ items?: SkuListItem[] }>(
        `/api/admin/vkpi/sku/list?query=${encodeURIComponent(query.trim())}&limit=30`,
        { timeoutMs: 6000 },
        apiToken,
      )
        .then((res) => {
          if (!alive) return;
          setOptions(Array.isArray(res.items) ? res.items : []);
        })
        .catch(() => {
          if (alive) setOptions([]);
        })
        .finally(() => {
          if (alive) setSearching(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, query]);

  const loadProfile = React.useCallback(
    (sku: string) => {
      if (!apiToken || !sku) return;
      setSelectedSku(sku);
      setDropdownOpen(false);
      setLoading(true);
      setError("");
      setProfile(null);
      apiFetch<Row>(`/api/admin/vkpi/sku/${encodeURIComponent(sku)}/profile`, { timeoutMs: 30000 }, apiToken)
        .then((res) => setProfile(res))
        .catch((err: any) => setError(String(err?.detail || err?.message || "加载失败")))
        .finally(() => setLoading(false));
    },
    [apiToken],
  );

  const product: Row = profile?.product || {};
  const content: Row = profile?.content || {};
  const agg: Row = content.aggregate || {};
  const items: Row[] = Array.isArray(content.items) ? content.items : [];
  const fitMatches: Row[] = Array.isArray(content.content_fit_matches) ? content.content_fit_matches : [];
  const topCreators: Row[] = Array.isArray(agg.top_creators) ? agg.top_creators : [];
  const comments: Row = profile?.comments || {};
  const commentSample: Row[] = Array.isArray(comments.sample) ? comments.sample : [];
  const bh: Row = profile?.bh_reviews || {};
  const bhSample: Row[] = Array.isArray(bh.sample) ? bh.sample : [];

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div>
        <div className="text-[18px] font-semibold text-white">SKU 360°</div>
        <div className="mt-0.5 text-[12px] text-slate-400">
          选一个 SKU,看谁的内容提到/评测了它 —— 全部来自已有视频深析 / 证据 / 评论数据,纯聚合零采集。
        </div>
      </div>

      {/* SKU 选择器 */}
      <div className="relative">
        <input
          value={query}
          onChange={(ev) => {
            setQuery(ev.target.value);
            setDropdownOpen(true);
          }}
          onFocus={() => setDropdownOpen(true)}
          placeholder="搜索 SKU / 型号,如 85mm、AF-85MM-F14-PRO-FE…"
          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[13px] text-white placeholder:text-slate-500 outline-none focus:border-sky-300/40"
        />
        {dropdownOpen && (
          <div className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded-xl border border-white/[0.1] bg-[#10151f] shadow-xl">
            {searching && <div className="px-3 py-2 text-[12px] text-slate-500">搜索中…</div>}
            {!searching && options.length === 0 && <div className="px-3 py-2 text-[12px] text-slate-500">无匹配 SKU</div>}
            {!searching &&
              options.map((opt) => (
                <button
                  key={opt.sku}
                  onClick={() => loadProfile(opt.sku)}
                  className="flex w-full items-center justify-between gap-2 border-b border-white/[0.04] px-3 py-2 text-left last:border-0 hover:bg-white/[0.05]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] text-slate-200">{opt.model_name || opt.marketing_name || opt.sku}</span>
                    <span className="block truncate text-[10px] text-slate-500">
                      {opt.sku}
                      {opt.mount ? ` · ${opt.mount}` : ""}
                      {opt.category_main ? ` · ${opt.category_main}` : ""}
                    </span>
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-400">{opt.price_usd != null ? `$${opt.price_usd}` : ""}</span>
                </button>
              ))}
          </div>
        )}
      </div>

      {!apiToken && <EmptyLine text="未登录 / 无 token,无法加载数据。" />}
      {error && <div className="rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200">{error}</div>}
      {loading && <div className="py-10 text-center text-[12px] text-slate-400">SKU 档案聚合中…</div>}
      {!loading && !profile && !error && apiToken && <EmptyLine text="在上方搜索并选择一个 SKU,即可查看 360° 档案。" />}

      {!loading && profile && (
        <div className="space-y-4">
          {/* 基础信息 */}
          <SectionCard
            title={product.model_name || product.sku || selectedSku}
            extra={<span className="rounded-md border border-white/[0.08] px-2 py-0.5 text-[10px] text-slate-400">{product.sku}</span>}
          >
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <StatCell label="类目" value={[product.category_main, product.category_detail].filter(Boolean).join(" / ") || "—"} />
              <StatCell label="系列 / 卡口" value={[product.series, product.mount].filter(Boolean).join(" / ") || "—"} />
              <StatCell label="售价" value={product.price_usd != null ? `$${product.price_usd}` : "—"} />
              <StatCell label="目录状态" value={product.status || "—"} />
            </div>
            {product.description ? <div className="mt-3 text-[12px] leading-relaxed text-slate-400">{String(product.description).slice(0, 400)}</div> : null}
            {product.product_url ? (
              <a href={product.product_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-[11px] text-sky-300 hover:underline">
                官网产品页 ↗
              </a>
            ) : null}
          </SectionCard>

          {/* 内容表现聚合 */}
          <SectionCard title="内容表现">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
              <StatCell label="提及内容数" value={fmtNum(agg.content_count)} />
              <StatCell label="创作者数" value={fmtNum(agg.creator_count)} />
              <StatCell label="总曝光(views)" value={fmtNum(agg.total_views)} />
              <StatCell label="总互动(赞+评)" value={fmtNum((agg.total_likes || 0) + (agg.total_comments || 0))} />
              <StatCell label="平均互动率" value={agg.avg_engagement_rate != null ? `${(agg.avg_engagement_rate * 100).toFixed(2)}%` : "—"} />
            </div>
            {topCreators.length > 0 && (
              <div className="mt-3">
                <div className="mb-1.5 text-[11px] text-slate-500">TOP 创作者(按曝光)</div>
                <div className="flex flex-wrap gap-1.5">
                  {topCreators.map((c) => (
                    <span key={String(c.kol_pool_id)} className="rounded-full border border-sky-300/25 bg-sky-500/[0.08] px-2.5 py-1 text-[11px] text-sky-100">
                      {c.display_name || c.handle || `KOL ${c.kol_pool_id}`}
                      <span className="ml-1.5 text-sky-300/70">
                        {fmtNum(c.total_views)} views · {c.content_count} 条
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {content.note ? <div className="mt-3 text-[11px] text-amber-200/80">{content.note}</div> : null}
          </SectionCard>

          {/* 内容清单 */}
          <SectionCard title={`内容清单(按互动排序,共 ${items.length} 条)`}>
            {items.length === 0 ? (
              <EmptyLine text="该 SKU 暂无匹配内容 —— 视频深析与标题均未命中别名(诚实空态)。" />
            ) : (
              <div className="overflow-auto rounded-lg border border-white/[0.08]">
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03] text-left text-sky-300/80">
                      <th className="px-2 py-1.5 font-medium">内容</th>
                      <th className="px-2 py-1.5 font-medium">创作者</th>
                      <th className="px-2 py-1.5 font-medium">平台</th>
                      <th className="px-2 py-1.5 font-medium">发布</th>
                      <th className="px-2 py-1.5 text-right font-medium">曝光</th>
                      <th className="px-2 py-1.5 text-right font-medium">互动</th>
                      <th className="px-2 py-1.5 font-medium">匹配依据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it) => (
                      <tr key={String(it.evidence_id)} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                        <td className="max-w-[280px] px-2 py-1.5">
                          {it.content_url ? (
                            <a href={it.content_url} target="_blank" rel="noreferrer" className="block truncate text-sky-200 hover:underline">
                              {it.title || it.content_url}
                            </a>
                          ) : (
                            <span className="block truncate text-slate-300">{it.title || "—"}</span>
                          )}
                        </td>
                        <td className="max-w-[140px] truncate px-2 py-1.5 text-slate-300">{it.kol?.display_name || it.kol?.handle || "—"}</td>
                        <td className="px-2 py-1.5 text-slate-400">{it.platform || "—"}</td>
                        <td className="px-2 py-1.5 text-slate-400">{it.posted_at || "—"}</td>
                        <td className="px-2 py-1.5 text-right text-slate-300">{fmtNum(it.view_count)}</td>
                        <td className="px-2 py-1.5 text-right text-slate-300">{fmtNum(it.engagement)}</td>
                        <td className="px-2 py-1.5">
                          <span
                            className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10px] text-slate-400"
                            title={`命中别名:${it.match?.alias || "—"}`}
                          >
                            {FIELD_LABEL[String(it.match?.field)] || it.match?.field || "—"}
                            {it.has_deep_analysis ? "" : " (仅标题)"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>

          {/* 内容契合判断(content_fit_v1 缓存,只读) */}
          <SectionCard title="内容契合判断(content_fit 缓存)">
            {fitMatches.length === 0 ? (
              <EmptyLine text="暂无针对该 SKU 的 KOL 内容契合判断缓存。" />
            ) : (
              <div className="space-y-1.5">
                {fitMatches.map((f) => (
                  <div key={String(f.kol_pool_id)} className="rounded-lg border border-white/[0.06] px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className={`rounded-md border px-2 py-0.5 text-[10px] ${VERDICT_TONE[String(f.fit_verdict)] || "border-white/[0.1] text-slate-300"}`}>
                        {f.fit_verdict || "—"}
                      </span>
                      <span className="text-[12px] text-slate-200">{f.display_name || f.handle || `KOL ${f.kol_pool_id}`}</span>
                      <span className="text-[10px] text-slate-500">{f.platform}</span>
                      {f.confidence != null && <span className="ml-auto text-[10px] text-slate-500">置信 {(Number(f.confidence) * 100).toFixed(0)}%</span>}
                    </div>
                    {Array.isArray(f.fit_reasons) && f.fit_reasons.length > 0 && (
                      <ul className="mt-1 list-disc pl-5 text-[11px] text-slate-400">
                        {f.fit_reasons.slice(0, 2).map((r: string, i: number) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* 相关评论抽样 */}
          <SectionCard
            title="相关评论抽样"
            extra={<span className="text-[10px] text-slate-500">命中 {comments.matched_total ?? 0} 条 / 扫描 {comments.scanned ?? 0} 条</span>}
          >
            {commentSample.length === 0 ? (
              <EmptyLine text="评论库暂无提及该型号的评论(诚实空态)。" />
            ) : (
              <div className="space-y-1.5">
                {commentSample.map((c) => (
                  <div key={String(c.comment_id)} className="rounded-lg border border-white/[0.06] px-3 py-2">
                    <div className="text-[12px] leading-relaxed text-slate-300">{c.comment_text}</div>
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-500">
                      <span>{c.author_handle || "匿名"}</span>
                      <span>{c.platform}</span>
                      {c.language_detected ? <span>{c.language_detected}</span> : null}
                      {c.created_at ? <span>{c.created_at}</span> : null}
                      {c.likes_count ? <span>♥ {fmtNum(c.likes_count)}</span> : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* B&H reviews(表存在才读) */}
          <SectionCard
            title="B&H 用户评论"
            extra={bh.table_present && bh.avg_rating != null ? <span className="text-[11px] text-amber-200">均分 {bh.avg_rating} / 5</span> : undefined}
          >
            {!bh.table_present ? (
              <EmptyLine text="B&H reviews 表未建,跳过该数据源。" />
            ) : bhSample.length === 0 ? (
              <EmptyLine text="B&H 评论库暂无该 SKU 的评论(数据源已接,等 reviews actor 喂数)。" />
            ) : (
              <div className="space-y-1.5">
                {bhSample.map((r) => (
                  <div key={String(r.review_id)} className="rounded-lg border border-white/[0.06] px-3 py-2">
                    <div className="flex items-center gap-2 text-[12px] text-slate-200">
                      {r.rating != null && <span className="text-amber-200">{"★".repeat(Math.round(Number(r.rating)))}</span>}
                      <span className="font-medium">{r.title || "—"}</span>
                    </div>
                    {r.body ? <div className="mt-1 text-[11px] leading-relaxed text-slate-400">{r.body}</div> : null}
                    <div className="mt-1 text-[10px] text-slate-500">
                      {r.author || "匿名"}
                      {r.review_date ? ` · ${r.review_date}` : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <div className="text-right text-[10px] text-slate-600">生成于 {profile.generated_at}(UTC)· 纯聚合已有数据</div>
        </div>
      )}
    </div>
  );
}
