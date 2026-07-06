import React from "react";
import { apiFetch } from "../../../../services/http";

// 件C · 发射台(LaunchPad)页。
//   选一个新品 SKU → 一键全案六段输出:① KOL 名单(招牌拍法+焦段覆盖) ② 每人预算
//   ③ 排期(中位周期倒排+同周去重) ④ 每人打法 ⑤ 官号协同 v0 ⑥ 覆盖最大化 + 每人预测战绩。
//   后端:/api/admin/vkpi/sku/list(选择器复用)| /api/admin/vkpi/launch/assemble?sku=&max_roster=。
//   全只读、纯聚合已有数据、零 LLM。兄弟件未收口的段 status=module_pending → 诚实占位提示;
//   缺数据段 status=empty → 明说原因,绝不杜撰数字;每个数字后端都带 basis 可追溯。

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

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

const CONFIDENCE_TONE: Record<string, string> = {
  high: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  medium: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  low: "border-rose-300/30 bg-rose-500/[0.1] text-rose-100",
};

function ConfidenceChip({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-[10px] text-slate-500">—</span>;
  const tone = CONFIDENCE_TONE[String(value)] || "border-white/[0.1] text-slate-300";
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>{String(value)}</span>;
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

function PendingNote({ block }: { block: Row }) {
  return (
    <div className="rounded-lg border border-amber-300/25 bg-amber-500/[0.06] px-3 py-3 text-[12px] text-amber-200/90">
      模块施工中(module_pending):{block.module || "兄弟模块"} 尚未收口,该段收口后自动变活 — 不杜撰占位数字。
    </div>
  );
}

function ErrorNote({ block }: { block: Row }) {
  return (
    <div className="rounded-lg border border-rose-300/25 bg-rose-500/[0.06] px-3 py-3 text-[12px] text-rose-200/90">
      该段聚合失败(诚实报错,不影响其余段):{String(block.reason || "unknown")}
    </div>
  );
}

/** 段状态统一分流:ready/ok 走内容渲染,module_pending/empty/error 各给诚实态。 */
function BlockBody({ block, children }: { block: Row | null | undefined; children: React.ReactNode }) {
  const status = String(block?.status || "");
  if (!block || status === "empty") return <EmptyLine text={String(block?.reason || "暂无数据(诚实空态)。")} />;
  if (status === "module_pending") return <PendingNote block={block} />;
  if (status === "error" || status === "unavailable") return <ErrorNote block={block} />;
  return <>{children}</>;
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="mt-0.5 text-[16px] font-semibold text-white">{value}</div>
    </div>
  );
}

export function LaunchPadPage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState<string>("");
  const [maxRoster, setMaxRoster] = React.useState(8);
  const [plan, setPlan] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  // SKU 选择器搜索(300ms 防抖;复用 /sku/list 端点,与 SKU 360° 同源)。
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

  const loadPlan = React.useCallback(
    (sku: string, roster: number) => {
      if (!apiToken || !sku) return;
      setSelectedSku(sku);
      setDropdownOpen(false);
      setLoading(true);
      setError("");
      setPlan(null);
      apiFetch<Row>(
        `/api/admin/vkpi/launch/assemble?sku=${encodeURIComponent(sku)}&max_roster=${roster}`,
        { timeoutMs: 90000 },
        apiToken,
      )
        .then((res) => setPlan(res))
        .catch((err: any) => setError(String(err?.detail || err?.message || "加载失败")))
        .finally(() => setLoading(false));
    },
    [apiToken],
  );

  const product: Row = plan?.product || {};
  const roster: Row = plan?.roster || {};
  const members: Row[] = Array.isArray(roster.members) ? roster.members : [];
  const budgets: Row = plan?.budgets || {};
  const budgetItems: Row[] = Array.isArray(budgets.items) ? budgets.items : [];
  const budgetTotal: Row = budgets.total || {};
  const schedule: Row = plan?.schedule || {};
  const scheduleItems: Row[] = Array.isArray(schedule.items) ? schedule.items : [];
  const playbooks: Row = plan?.playbooks || {};
  const playbookItems: Row[] = Array.isArray(playbooks.items) ? playbooks.items : [];
  const synergy: Row = plan?.official_synergy || {};
  const synergyItems: Row[] = Array.isArray(synergy.suggestions) ? synergy.suggestions : [];
  const coverage: Row = plan?.coverage || {};
  const forecast: Row = plan?.forecast || {};
  const forecastItems: Row[] = Array.isArray(forecast.items) ? forecast.items : [];
  const coverageReady = coverage.status === "ready" || coverage.status === "ok";

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div>
        <div className="text-[18px] font-semibold text-white">发射台 · 新品一键全案</div>
        <div className="mt-0.5 text-[12px] text-slate-400">
          选一个新品 SKU → 六段输出:KOL 名单 / 每人预算 / 排期 / 每人打法 / 官号协同 / 覆盖最大化 + 预测战绩。
          全部复用已有数据件纯聚合(零 LLM 零采集),每个数字带 basis 可追溯;缺数据诚实空态。
        </div>
      </div>

      {/* SKU 选择器 + roster 上限 */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            value={query}
            onChange={(ev) => {
              setQuery(ev.target.value);
              setDropdownOpen(true);
            }}
            onFocus={() => setDropdownOpen(true)}
            placeholder="搜索新品 SKU / 型号,如 85mm、AF-85MM-F14-PRO-FE…"
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
                    onClick={() => loadPlan(opt.sku, maxRoster)}
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
        <select
          value={maxRoster}
          onChange={(ev) => {
            const next = Number(ev.target.value) || 8;
            setMaxRoster(next);
            if (selectedSku) loadPlan(selectedSku, next);
          }}
          className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-2 py-2.5 text-[12px] text-slate-200 outline-none focus:border-sky-300/40"
          title="KOL 名单人数上限"
        >
          {[4, 6, 8, 10, 12].map((n) => (
            <option key={n} value={n} className="bg-[#10151f]">
              {n} 人
            </option>
          ))}
        </select>
      </div>

      {!apiToken && <EmptyLine text="未登录 / 无 token,无法加载数据。" />}
      {error && <div className="rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200">{error}</div>}
      {loading && <div className="py-10 text-center text-[12px] text-slate-400">全案组装中(候选打分 + 逐人聚合招牌/焦段/周期)…</div>}
      {!loading && !plan && !error && apiToken && <EmptyLine text="在上方搜索并选择一个 SKU,即可生成一键全案。" />}

      {!loading && plan && (
        <div className="space-y-4">
          {/* 产品概要 */}
          <SectionCard
            title={product.model_name || product.sku || selectedSku}
            extra={<span className="rounded-md border border-white/[0.08] px-2 py-0.5 text-[10px] text-slate-400">{product.sku}</span>}
          >
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <StatCell label="类目" value={[product.category_main, product.category_detail].filter(Boolean).join(" / ") || "—"} />
              <StatCell label="系列 / 卡口" value={[product.series, product.mount].filter(Boolean).join(" / ") || "—"} />
              <StatCell label="售价" value={product.price_usd != null ? `$${product.price_usd}` : "—"} />
              <StatCell label="SKU 焦段" value={plan.sku_focal || "—"} />
            </div>
          </SectionCard>

          {/* ① KOL 名单 */}
          <SectionCard
            title={`① KOL 名单(${members.length} 人)`}
            extra={
              roster.basis?.resolved_query ? (
                <span className="text-[10px] text-slate-500" title={`memory product_family:${roster.basis?.family || "—"}`}>
                  匹配口径:{roster.basis.resolved_query} → {roster.basis.family || "—"}
                </span>
              ) : undefined
            }
          >
            <BlockBody block={members.length ? { status: "ready" } : { status: "empty", reason: roster.reason || roster.candidate_pool?.reason || "候选池为空(memory 证据不足)。" }}>
              <div className="mb-2 text-[11px] text-slate-500">选人依据:{roster.selection_basis || "—"}</div>
              <div className="overflow-auto rounded-lg border border-white/[0.08]">
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03] text-left text-sky-300/80">
                      <th className="px-2 py-1.5 font-medium">KOL</th>
                      <th className="px-2 py-1.5 font-medium">平台 / 国家</th>
                      <th className="px-2 py-1.5 text-right font-medium">匹配分</th>
                      <th className="px-2 py-1.5 font-medium">招牌拍法</th>
                      <th className="px-2 py-1.5 font-medium">焦段覆盖</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => {
                      const sig: Row = m.signature || {};
                      const top: Row = sig.top_mode || {};
                      const fc: Row = m.focal_coverage || {};
                      return (
                        <tr key={String(m.kol_pool_id)} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                          <td className="max-w-[180px] px-2 py-1.5">
                            <span className="block truncate text-slate-200">{m.display_name || m.handle || `KOL ${m.kol_pool_id}`}</span>
                            <span className="block truncate text-[10px] text-slate-500">@{m.handle || "—"}</span>
                          </td>
                          <td className="px-2 py-1.5 text-slate-400">
                            {m.platform || "—"}
                            {m.country ? ` · ${m.country}` : ""}
                          </td>
                          <td className="px-2 py-1.5 text-right text-slate-300">{m.score ?? "—"}</td>
                          <td className="px-2 py-1.5">
                            {sig.status === "ready" && top.label ? (
                              <span className="rounded-full border border-sky-300/25 bg-sky-500/[0.08] px-2 py-0.5 text-[10px] text-sky-100">
                                {top.label}
                                {top.avg_views != null ? ` · 均播 ${fmtNum(top.avg_views)}` : ""}
                              </span>
                            ) : (
                              <span className="text-[10px] text-slate-500" title={String(sig.reason || "")}>
                                无深析样本
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {fc.status !== "ready" ? (
                              <span className="text-[10px] text-slate-500">—</span>
                            ) : fc.sku_focal ? (
                              <span
                                className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                  fc.sku_focal_covered
                                    ? "border-emerald-300/30 bg-emerald-500/[0.1] text-emerald-100"
                                    : "border-white/[0.1] text-slate-400"
                                }`}
                                title={`共覆盖 ${fc.covered_focal_count ?? 0} 个焦段`}
                              >
                                {fc.sku_focal} {fc.sku_focal_covered ? `拍过 ${fc.sku_focal_video_count} 条` : "空白(可切入)"}
                              </span>
                            ) : (
                              <span className="text-[10px] text-slate-400">覆盖 {fc.covered_focal_count ?? 0} 个焦段</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </BlockBody>
          </SectionCard>

          {/* ② 每人预算 */}
          <SectionCard
            title="② 每人预算"
            extra={
              budgetTotal.estimated_usd_p50 != null ? (
                <span className="text-[11px] text-amber-200">
                  总额 p50 {fmtUsd(budgetTotal.estimated_usd_p50)}(区间 {fmtUsd(budgetTotal.estimated_usd_low)}–{fmtUsd(budgetTotal.estimated_usd_high)})
                </span>
              ) : undefined
            }
          >
            <BlockBody block={budgets}>
              <div className="overflow-auto rounded-lg border border-white/[0.08]">
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03] text-left text-sky-300/80">
                      <th className="px-2 py-1.5 font-medium">KOL</th>
                      <th className="px-2 py-1.5 text-right font-medium">报价 p50</th>
                      <th className="px-2 py-1.5 text-right font-medium">区间</th>
                      <th className="px-2 py-1.5 font-medium">方法</th>
                      <th className="px-2 py-1.5 text-right font-medium">真实报价源</th>
                      <th className="px-2 py-1.5 font-medium">置信</th>
                    </tr>
                  </thead>
                  <tbody>
                    {budgetItems.map((b) => {
                      const est: Row = b.estimate || {};
                      const ok = est.status === "ok" || est.status === "ready";
                      return (
                        <tr key={String(b.kol_pool_id)} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                          <td className="max-w-[180px] truncate px-2 py-1.5 text-slate-200">{b.display_name || b.handle || `KOL ${b.kol_pool_id}`}</td>
                          <td className="px-2 py-1.5 text-right text-slate-200">{ok ? fmtUsd(est.estimated_usd_p50) : "—"}</td>
                          <td className="px-2 py-1.5 text-right text-slate-400">
                            {ok && est.estimated_usd_low != null ? `${fmtUsd(est.estimated_usd_low)}–${fmtUsd(est.estimated_usd_high)}` : "—"}
                          </td>
                          <td className="px-2 py-1.5 text-slate-400" title={ok ? "" : String(est.reason || "")}>
                            {ok ? est.method || "—" : `不可估:${String(est.reason || est.status || "—").slice(0, 40)}`}
                          </td>
                          <td className="px-2 py-1.5 text-right text-slate-400">{est.source_count ?? "—"}</td>
                          <td className="px-2 py-1.5">
                            <ConfidenceChip value={est.confidence} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {budgetTotal.unpriced_members > 0 && (
                <div className="mt-2 text-[11px] text-amber-200/80">{budgetTotal.unpriced_members} 人暂无法估价,总额只含可估的 {budgetTotal.priced_members} 人(诚实缺口,不用均值硬补)。</div>
              )}
            </BlockBody>
          </SectionCard>

          {/* ③ 排期 */}
          <SectionCard title="③ 排期(签收→发布中位周期倒排)" extra={schedule.kickoff_date ? <span className="text-[10px] text-slate-500">kickoff:{schedule.kickoff_date}</span> : undefined}>
            <BlockBody block={schedule}>
              <div className="overflow-auto rounded-lg border border-white/[0.08]">
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03] text-left text-sky-300/80">
                      <th className="px-2 py-1.5 font-medium">KOL</th>
                      <th className="px-2 py-1.5 font-medium">发布周</th>
                      <th className="px-2 py-1.5 font-medium">目标日期</th>
                      <th className="px-2 py-1.5 text-right font-medium">制作周期(天)</th>
                      <th className="px-2 py-1.5 font-medium">周期依据</th>
                      <th className="px-2 py-1.5 font-medium">置信</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scheduleItems.map((s) => {
                      const basis: Row = s.leadtime_basis || {};
                      const personal = String(basis.method || "").startsWith("personal");
                      return (
                        <tr key={String(s.kol_pool_id)} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                          <td className="max-w-[180px] truncate px-2 py-1.5 text-slate-200">{s.display_name || s.handle || `KOL ${s.kol_pool_id}`}</td>
                          <td className="px-2 py-1.5 text-slate-200">
                            {s.publish_week}
                            {s.shifted_days > 0 && (
                              <span className="ml-1 rounded border border-amber-300/25 px-1 py-0.5 text-[9px] text-amber-200" title="同周超过 2 人,顺延避免互相稀释">
                                顺延 {s.shifted_days} 天
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-1.5 text-slate-400">{s.publish_date}</td>
                          <td className="px-2 py-1.5 text-right text-slate-300">{s.leadtime_days}</td>
                          <td className="px-2 py-1.5 text-slate-400" title={String(basis.detail || "")}>
                            {personal ? `个人历史中位(${basis.sample_count} 样本)` : "默认周期(无履约历史)"}
                          </td>
                          <td className="px-2 py-1.5">
                            <ConfidenceChip value={basis.confidence} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </BlockBody>
          </SectionCard>

          {/* ④ 每人打法 */}
          <SectionCard title="④ 每人打法(招牌拍法 top1)">
            <BlockBody block={playbooks}>
              <div className="space-y-1.5">
                {playbookItems.map((pItem) => (
                  <div key={String(pItem.kol_pool_id)} className="rounded-lg border border-white/[0.06] px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] font-medium text-slate-200">{pItem.display_name || pItem.handle || `KOL ${pItem.kol_pool_id}`}</span>
                      {pItem.status !== "ready" && <span className="rounded border border-white/[0.1] px-1.5 py-0.5 text-[9px] text-slate-500">缺深析</span>}
                    </div>
                    <div className={`mt-1 text-[12px] leading-relaxed ${pItem.status === "ready" ? "text-sky-100" : "text-slate-500"}`}>{pItem.line}</div>
                    {pItem.status === "ready" && pItem.basis && (
                      <div className="mt-1 text-[10px] text-slate-500">
                        依据:{pItem.basis.video_count} 条深析命中该拍法{pItem.basis.avg_views != null ? ` · 均播放 ${fmtNum(pItem.basis.avg_views)}(${pItem.basis.views_sample} 条有播放数)` : ""}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </BlockBody>
          </SectionCard>

          {/* ⑤ 官号协同 v0 */}
          <SectionCard
            title="⑤ 官号协同 v0"
            extra={
              synergy.status === "ready" ? (
                <span className="text-[10px] text-slate-500">
                  官号 {synergy.scanned_posts} 贴中命中 {synergy.matched_posts} 条({synergy.scope === "sku_focal" ? "SKU 焦段" : "同品类"}口径)
                </span>
              ) : undefined
            }
          >
            {synergy.status === "generic" ? (
              <div className="space-y-1.5">
                <div className="rounded-lg border border-amber-300/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-200/90">
                  数据不足降级(generic=true):{String(synergy.reason || "")}
                </div>
                {(synergyItems || []).map((s, i) => (
                  <div key={i} className="rounded-lg border border-white/[0.06] px-3 py-2 text-[12px] text-slate-400">
                    {s.line}
                  </div>
                ))}
              </div>
            ) : (
              <BlockBody block={synergy}>
                <div className="space-y-1.5">
                  {synergyItems.map((s, i) => (
                    <div key={i} className="rounded-lg border border-white/[0.06] px-3 py-2">
                      <div className="text-[12px] leading-relaxed text-slate-200">{s.line}</div>
                      {s.basis && (
                        <div className="mt-1 text-[10px] text-slate-500">
                          依据:{s.basis.source}
                          {s.basis.sample != null ? ` · ${s.basis.sample} 条样本` : ""}
                          {s.basis.metric ? ` · 口径 ${s.basis.metric}` : ""}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </BlockBody>
            )}
          </SectionCard>

          {/* ⑥ 覆盖最大化 */}
          <SectionCard title="⑥ 覆盖最大化(组合去重)">
            <BlockBody block={coverageReady ? { status: "ready" } : coverage}>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                <StatCell label="选中人数" value={String((coverage.selected || []).length || 0)} />
                <StatCell label="因受众重叠淘汰" value={String((coverage.dropped_overlap || []).length || 0)} />
                <StatCell label="方法" value={String(coverage.method || "roster_optimizer")} />
              </div>
              {Array.isArray(coverage.dropped_overlap) && coverage.dropped_overlap.length > 0 && (
                <div className="mt-2 text-[11px] text-slate-500">
                  被淘汰候选:
                  {coverage.dropped_overlap
                    .slice(0, 8)
                    .map((d: any) => (typeof d === "object" && d ? d.handle || d.kol_pool_id : d))
                    .join("、")}
                  {coverage.dropped_overlap.length > 8 ? " …" : ""}
                </div>
              )}
              {coverage.note ? <div className="mt-2 text-[10px] text-slate-500">{String(coverage.note)}</div> : null}
            </BlockBody>
          </SectionCard>

          {/* ＋ 每人预测战绩 */}
          <SectionCard title="预测战绩(每人)">
            <BlockBody block={forecast}>
              <div className="overflow-auto rounded-lg border border-white/[0.08]">
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03] text-left text-sky-300/80">
                      <th className="px-2 py-1.5 font-medium">KOL</th>
                      <th className="px-2 py-1.5 text-right font-medium">预计播放 p50</th>
                      <th className="px-2 py-1.5 text-right font-medium">p10–p90</th>
                      <th className="px-2 py-1.5 text-right font-medium">互动率</th>
                      <th className="px-2 py-1.5 font-medium">置信</th>
                    </tr>
                  </thead>
                  <tbody>
                    {forecastItems.map((f) => {
                      const fc: Row = f.forecast || {};
                      const ok = fc.status === "ok" || fc.status === "ready";
                      return (
                        <tr key={String(f.kol_pool_id)} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]">
                          <td className="max-w-[180px] truncate px-2 py-1.5 text-slate-200">{f.display_name || f.handle || `KOL ${f.kol_pool_id}`}</td>
                          <td className="px-2 py-1.5 text-right text-slate-200">{ok ? fmtNum(fc.expected_views_p50) : "—"}</td>
                          <td className="px-2 py-1.5 text-right text-slate-400">
                            {ok && fc.expected_views_p10 != null ? `${fmtNum(fc.expected_views_p10)}–${fmtNum(fc.expected_views_p90)}` : "—"}
                          </td>
                          <td className="px-2 py-1.5 text-right text-slate-400">
                            {ok && fc.engagement_rate != null ? `${(Number(fc.engagement_rate) * 100).toFixed(2)}%` : "—"}
                          </td>
                          <td className="px-2 py-1.5">
                            {ok ? <ConfidenceChip value={fc.confidence} /> : <span className="text-[10px] text-slate-500" title={String(fc.reason || "")}>不可测</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </BlockBody>
          </SectionCard>

          <div className="text-right text-[10px] text-slate-600">
            生成于 {plan.generated_at}(UTC)· 纯聚合已有数据 · 零 LLM({plan.llm_calls === false ? "已验证" : "—"})· 每段 basis 可追溯
          </div>
        </div>
      )}
    </div>
  );
}
