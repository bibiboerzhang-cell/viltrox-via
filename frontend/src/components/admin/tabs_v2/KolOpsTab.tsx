import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  addKolOutreach,
  createKol,
  createKolCampaign,
  createKolContent,
  fetchKolCandidates,
  fetchKolDetail,
  fetchKolOpsSnapshot,
  fetchKolStaffPerformance,
  fetchKolSuggestions,
  importKolCsv,
  promoteKolCandidate,
  scoreKolContent,
  searchKolPlatform,
  updateKolCandidate,
  type KolDetailSnapshot,
} from "../../../services/admin.service";
import { Icons } from "../Icons";
import { DataTable, ErrorCard, KPIGrid, LoadingCard, PageHeader, SectionLabel, StatusPill, type DataColumn } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;
type View = "directory" | "search" | "review" | "staff";

function str(value: unknown, fallback = "—") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function num(value: unknown) {
  const next = Number(value ?? 0);
  return Number.isFinite(next) ? next : 0;
}

function usd(cents: unknown) {
  return `$${(num(cents) / 100).toFixed(2)}`;
}

function pct(value: unknown) {
  return `${(num(value) * 100).toFixed(1)}%`;
}

function statusTone(value: unknown): "pass" | "review" | "queue" | "new" | "active" | "idle" | "churn" | "block" | "flag" {
  const raw = String(value || "").toLowerCase();
  if (["active", "agreed", "approved", "imported"].includes(raw)) return "active";
  if (["replied", "contacted", "reviewing"].includes(raw)) return "review";
  if (["refused", "churned", "rejected"].includes(raw)) return "churn";
  if (["cold", "new"].includes(raw)) return "new";
  return "idle";
}

function buildQuery(filters: Record<string, string>) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value.trim()) params.set(key, value.trim());
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

function pageText(page: Row) {
  const total = num(page.total);
  const offset = num(page.offset);
  const limit = Math.max(1, num(page.limit) || 25);
  if (!total) return "0 / 0";
  return `${offset + 1}-${Math.min(offset + limit, total)} / ${total}`;
}

export function KolOpsTab({ token }: Props) {
  const [view, setView] = useState<View>("directory");
  const [items, setItems] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [page, setPage] = useState<Row>({});
  const [candidates, setCandidates] = useState<Row[]>([]);
  const [candidatePage, setCandidatePage] = useState<Row>({});
  const [performance, setPerformance] = useState<Row[]>([]);
  const [detail, setDetail] = useState<KolDetailSnapshot | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filters, setFilters] = useState({ q: "", staff_id: "", country: "", platform: "", status: "", date_from: "", date_to: "", limit: "25", offset: "0" });
  const [candidateFilters, setCandidateFilters] = useState({ q: "", platform: "", market: "", status: "new", date_from: "", date_to: "", limit: "25", offset: "0" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState("");
  const [suggestions, setSuggestions] = useState<Row | null>(null);
  const [searchResult, setSearchResult] = useState<Row | null>(null);

  const loadCandidates = async (nextFilters = candidateFilters) => {
    const snapshot = await fetchKolCandidates(token, buildQuery(nextFilters));
    setCandidates(snapshot.items || []);
    setCandidatePage(snapshot.page || {});
  };

  const load = async (nextFilters = filters, nextCandidateFilters = candidateFilters) => {
    setLoading(true);
    setError("");
    try {
      const [snapshot, perf, candidateSnapshot] = await Promise.all([
        fetchKolOpsSnapshot(token, buildQuery(nextFilters)),
        fetchKolStaffPerformance(token),
        fetchKolCandidates(token, buildQuery(nextCandidateFilters)),
      ]);
      setItems(snapshot.items || []);
      setSummary(snapshot.summary || {});
      setPage(snapshot.page || {});
      setPerformance(perf.items || []);
      setCandidates(candidateSnapshot.items || []);
      setCandidatePage(candidateSnapshot.page || {});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const openDetail = async (row: Row) => {
    const id = Number(row.id);
    if (!id) return;
    setSelectedId(id);
    setSuggestions(null);
    try {
      setDetail(await fetchKolDetail(token, id));
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const kpis = useMemo(() => [
    { label: "KOL 总数", value: String(summary.kol_count ?? items.length), hint: "当前筛选" },
    { label: "总投入", value: usd(summary.total_cost_cents), hint: "campaign cost" },
    { label: "转化收入", value: usd(summary.total_revenue_cents), hint: "attribution" },
    { label: "平均 ROI", value: pct(summary.roi), hint: "扣除成本" },
  ], [items.length, summary]);

  const columns: DataColumn<Row>[] = [
    { key: "channel", label: "频道名", width: "1.35fr", render: (r) => <strong>{str(r.channel_name)}</strong> },
    { key: "platform", label: "平台", width: "0.65fr", render: (r) => str(r.platform) },
    { key: "country", label: "市场", width: "0.55fr", render: (r) => str(r.country) },
    { key: "staff", label: "对接人", width: "0.75fr", render: (r) => str(r.assigned_staff_name) },
    { key: "status", label: "状态", width: "0.75fr", render: (r) => <StatusPill tone={statusTone(r.contact_status)}>{str(r.contact_status, "cold")}</StatusPill> },
    { key: "views", label: "播放", width: "0.65fr", render: (r) => num(r.views).toLocaleString() },
    { key: "engagement", label: "互动", width: "0.65fr", render: (r) => pct(r.engagement_rate) },
    { key: "cost", label: "成本", width: "0.65fr", render: (r) => usd(r.cost_cents) },
    { key: "cpv", label: "CPV", width: "0.6fr", render: (r) => `$${num(r.cpv).toFixed(4)}` },
    { key: "revenue", label: "收入", width: "0.65fr", render: (r) => usd(r.revenue_cents) },
    { key: "roi", label: "ROI", width: "0.6fr", render: (r) => pct(r.roi) },
    { key: "ai", label: "AI", width: "0.5fr", render: (r) => Math.round(num(r.avg_ai_quality_score)) || "—" },
  ];

  const candidateColumns: DataColumn<Row>[] = [
    { key: "channel", label: "候选频道", width: "1.2fr", render: (r) => <strong>{str(r.channel_name)}</strong> },
    { key: "platform", label: "平台", width: "0.55fr", render: (r) => str(r.platform) },
    { key: "market", label: "市场", width: "0.5fr", render: (r) => str(r.market || r.country) },
    { key: "title", label: "样本内容", width: "1.6fr", render: (r) => <a href={str(r.source_url, "#")} target="_blank" rel="noreferrer">{str(r.sample_title || r.source_url)}</a> },
    { key: "views", label: "播放", width: "0.55fr", render: (r) => num(r.avg_views).toLocaleString() },
    { key: "status", label: "审核", width: "0.65fr", render: (r) => <StatusPill tone={statusTone(r.status)}>{str(r.status, "new")}</StatusPill> },
    {
      key: "actions",
      label: "操作",
      width: "1.2fr",
      render: (r) => (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }} onClick={(event) => event.stopPropagation()}>
          <button className="ax-btn ax-btn--sm" type="button" disabled={busy.startsWith(`candidate:${r.id}:`)} onClick={() => handleCandidateAction(r, "reviewing")}>审核</button>
          <button className="ax-btn ax-btn--sm" type="button" disabled={busy.startsWith(`candidate:${r.id}:`)} onClick={() => handleCandidateAction(r, "rejected")}>拒绝</button>
          <button className="ax-btn ax-btn--sm" type="button" disabled={busy.startsWith(`candidate:${r.id}:`)} onClick={() => handleCandidateAction(r, "promote")}>导入</button>
        </div>
      ),
    },
  ];

  const handleCsv = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setBusy("csv");
    try {
      const result = await importKolCsv(token, file);
      setToast(`已导入 ${result.imported ?? 0} 个 KOL`);
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
      event.currentTarget.value = "";
    }
  };

  const handleCreateKol = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("create-kol");
    try {
      await createKol(token, {
        channel_name: String(form.get("channel_name") || ""),
        platform: String(form.get("platform") || "youtube"),
        country: String(form.get("country") || ""),
        niche: String(form.get("niche") || ""),
        contact_email: String(form.get("contact_email") || ""),
        contact_status: "cold",
      });
      event.currentTarget.reset();
      setToast("KOL 已创建");
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const handlePlatformSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const nextCandidateFilters = {
      ...candidateFilters,
      q: String(form.get("query") || ""),
      platform: String(form.get("platform") || ""),
      market: String(form.get("market") || ""),
      status: "new",
      offset: "0",
    };
    setBusy("platform-search");
    setSearchResult(null);
    try {
      const result = await searchKolPlatform(token, {
        query: nextCandidateFilters.q,
        platform: nextCandidateFilters.platform,
        market: nextCandidateFilters.market,
        niche: String(form.get("niche") || ""),
        max_results: Number(form.get("max_results") || 25),
      });
      setSearchResult(result);
      setCandidateFilters(nextCandidateFilters);
      await loadCandidates(nextCandidateFilters);
      setToast(`平台搜索完成，保存候选 ${result.saved_candidates ?? 0} 个`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const handleCandidateAction = async (row: Row, action: "reviewing" | "rejected" | "promote") => {
    const id = Number(row.id);
    if (!id) return;
    setBusy(`candidate:${id}:${action}`);
    try {
      if (action === "promote") {
        await promoteKolCandidate(token, id, { niche: row.niche, country: row.country });
        setToast("候选已导入 KOL 名单");
        await load();
      } else {
        await updateKolCandidate(token, id, { status: action });
        setToast(`候选已标记为 ${action}`);
        await loadCandidates();
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const handleQuickAction = async (kind: "outreach" | "campaign" | "content" | "suggest" | "score") => {
    if (!detail?.kol?.id) return;
    const kolId = Number(detail.kol.id);
    setBusy(kind);
    try {
      if (kind === "outreach") {
        const notes = window.prompt("对接备注", "");
        await addKolOutreach(token, kolId, { action_type: "email", notes: notes || "" });
      } else if (kind === "campaign") {
        const productSku = window.prompt("推广产品 SKU", "");
        if (!productSku) throw new Error("请输入真实 product SKU");
        const costUsd = Number(window.prompt("投入成本 USD", "0") || 0);
        await createKolCampaign(token, kolId, { product_sku: productSku, status: "planning", cost_cents: Math.round(costUsd * 100) });
      } else if (kind === "content") {
        const campaignId = Number(detail.campaigns?.[0]?.id || 0);
        if (!campaignId) throw new Error("先创建一个 campaign");
        const contentUrl = window.prompt("内容 URL", "");
        if (!contentUrl) throw new Error("请输入真实 content URL");
        await createKolContent(token, {
          campaign_id: campaignId,
          content_url: contentUrl,
          platform: String(detail.kol.platform || "youtube"),
          views: Number(window.prompt("播放量", "0") || 0),
          likes: Number(window.prompt("点赞", "0") || 0),
          comments: Number(window.prompt("评论", "0") || 0),
          shares: Number(window.prompt("转发", "0") || 0),
        });
      } else if (kind === "score") {
        const contentId = Number(detail.content?.[0]?.id || 0);
        if (!contentId) throw new Error("先添加一条 content");
        await scoreKolContent(token, contentId);
      } else {
        setSuggestions(await fetchKolSuggestions(token, kolId));
      }
      setDetail(await fetchKolDetail(token, kolId));
      setToast("操作完成");
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const applyFilters = async () => {
    const next = { ...filters, offset: "0" };
    setFilters(next);
    await load(next);
  };

  const changePage = async (nextOffset: unknown) => {
    const next = { ...filters, offset: String(nextOffset ?? 0) };
    setFilters(next);
    await load(next);
  };

  const applyCandidateFilters = async () => {
    const next = { ...candidateFilters, offset: "0" };
    setCandidateFilters(next);
    await loadCandidates(next);
  };

  const changeCandidatePage = async (nextOffset: unknown) => {
    const next = { ...candidateFilters, offset: String(nextOffset ?? 0) };
    setCandidateFilters(next);
    await loadCandidates(next);
  };

  if (loading) return <LoadingCard label="Loading KOL Operations…" />;
  if (error) return <ErrorCard label="KOL Ops 加载失败" detail={error} onRetry={() => load()} />;

  return (
    <div>
      <PageHeader
        title="KOL Ops"
        subtitle="Platform search · candidate review · KOL directory · campaigns · scoring"
        actions={(
          <label className="ax-btn">
            <Icons.download />
            {busy === "csv" ? "Importing…" : "Import CSV"}
            <input type="file" accept=".csv,text/csv" onChange={handleCsv} style={{ display: "none" }} />
          </label>
        )}
      />

      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {toast ? <div className="ax-card" style={{ color: "var(--ax-text-5)" }}>{toast}</div> : null}
        <KPIGrid items={kpis} />

        <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {([
            ["directory", "KOL 名单"],
            ["search", "平台搜索"],
            ["review", "候选审核"],
            ["staff", "对接人"],
          ] as Array<[View, string]>).map(([key, label]) => (
            <button key={key} type="button" className={`ax-btn ax-btn--sm${view === key ? " is-active" : ""}`} onClick={() => setView(key)}>
              {label}
            </button>
          ))}
        </div>

        {view === "directory" ? (
          <>
            <KolFilterBar filters={filters} setFilters={setFilters} onApply={applyFilters} />
            <div className="ax-card">
              <SectionLabel>新增 KOL</SectionLabel>
              <form onSubmit={handleCreateKol} style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr 0.6fr 0.8fr 1fr auto", gap: 8 }}>
                <input className="input" name="channel_name" placeholder="频道名" required />
                <select className="input" name="platform" defaultValue="youtube">
                  {["youtube", "tiktok", "instagram", "twitter", "reddit"].map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
                <input className="input" name="country" placeholder="US" />
                <input className="input" name="niche" placeholder="photography" />
                <input className="input" name="contact_email" placeholder="email" />
                <button type="submit" className="ax-btn" disabled={busy === "create-kol"}><Icons.plus /> Add</button>
              </form>
            </div>
            <div className="ax-card" style={{ overflowX: "auto" }}>
              <DataTable columns={columns} rows={items} rowKey={(row, index) => String(row.id || index)} selectedId={selectedId ? String(selectedId) : null} onRowClick={openDetail} showCheckbox={false} emptyLabel="还没有 KOL 数据，先导入 CSV、平台搜索候选，或手动新增。" />
              <Pager page={page} onPage={changePage} />
            </div>
            {detail ? <KolDetail detail={detail} suggestions={suggestions} busy={busy} onQuickAction={handleQuickAction} /> : null}
          </>
        ) : null}

        {view === "search" ? (
          <>
            <PlatformSearchForm busy={busy === "platform-search"} onSubmit={handlePlatformSearch} />
            {searchResult ? <ProviderResult result={searchResult} /> : null}
            <CandidateTable rows={candidates} columns={candidateColumns} page={candidatePage} onPage={changeCandidatePage} />
          </>
        ) : null}

        {view === "review" ? (
          <>
            <CandidateFilterBar filters={candidateFilters} setFilters={setCandidateFilters} onApply={applyCandidateFilters} />
            <CandidateTable rows={candidates} columns={candidateColumns} page={candidatePage} onPage={changeCandidatePage} />
          </>
        ) : null}

        {view === "staff" ? <StaffPerformance rows={performance} /> : null}
      </div>
    </div>
  );
}

function KolFilterBar({ filters, setFilters, onApply }: { filters: Record<string, string>; setFilters: (fn: (prev: any) => any) => void; onApply: () => void }) {
  return (
    <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "1.2fr repeat(6, minmax(0, 1fr)) auto", gap: 8 }}>
      <input className="input" placeholder="搜索频道/URL/niche/email" value={filters.q} onChange={(event) => setFilters((prev) => ({ ...prev, q: event.target.value }))} />
      <input className="input" placeholder="staff_id" value={filters.staff_id} onChange={(event) => setFilters((prev) => ({ ...prev, staff_id: event.target.value }))} />
      <input className="input" placeholder="US" value={filters.country} onChange={(event) => setFilters((prev) => ({ ...prev, country: event.target.value }))} />
      <select className="input" value={filters.platform} onChange={(event) => setFilters((prev) => ({ ...prev, platform: event.target.value }))}>
        <option value="">all platform</option>
        {["youtube", "tiktok", "instagram", "twitter", "reddit"].map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <select className="input" value={filters.status} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}>
        <option value="">all status</option>
        {["cold", "contacted", "replied", "agreed", "active", "refused", "churned"].map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <input className="input" type="date" value={filters.date_from} onChange={(event) => setFilters((prev) => ({ ...prev, date_from: event.target.value }))} />
      <input className="input" type="date" value={filters.date_to} onChange={(event) => setFilters((prev) => ({ ...prev, date_to: event.target.value }))} />
      <button type="button" className="ax-btn" onClick={onApply}><Icons.filter /> Apply</button>
    </div>
  );
}

function CandidateFilterBar({ filters, setFilters, onApply }: { filters: Record<string, string>; setFilters: (fn: (prev: any) => any) => void; onApply: () => void }) {
  return (
    <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "1.2fr repeat(6, minmax(0, 1fr)) auto", gap: 8 }}>
      <input className="input" placeholder="搜索候选/内容/来源 URL" value={filters.q} onChange={(event) => setFilters((prev) => ({ ...prev, q: event.target.value }))} />
      <select className="input" value={filters.platform} onChange={(event) => setFilters((prev) => ({ ...prev, platform: event.target.value }))}>
        <option value="">all platform</option>
        {["youtube", "tiktok", "instagram"].map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <input className="input" placeholder="market" value={filters.market} onChange={(event) => setFilters((prev) => ({ ...prev, market: event.target.value.toUpperCase() }))} />
      <select className="input" value={filters.status} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}>
        {["new", "reviewing", "rejected", "imported", ""].map((s) => <option key={s || "all"} value={s}>{s || "all status"}</option>)}
      </select>
      <input className="input" type="date" value={filters.date_from} onChange={(event) => setFilters((prev) => ({ ...prev, date_from: event.target.value }))} />
      <input className="input" type="date" value={filters.date_to} onChange={(event) => setFilters((prev) => ({ ...prev, date_to: event.target.value }))} />
      <input className="input" type="number" min={10} max={100} value={filters.limit} onChange={(event) => setFilters((prev) => ({ ...prev, limit: event.target.value }))} />
      <button type="button" className="ax-btn" onClick={onApply}><Icons.filter /> Apply</button>
    </div>
  );
}

function PlatformSearchForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "1.4fr 130px 90px 1fr 100px auto", gap: 8 }}>
      <input className="input" name="query" placeholder="搜索真实 KOL/产品关键词，例如 anamorphic lens review" required />
      <select className="input" name="platform" defaultValue="youtube">
        <option value="youtube">YouTube</option>
        <option value="tiktok">TikTok</option>
        <option value="instagram">Instagram hashtag</option>
      </select>
      <input className="input" name="market" placeholder="US" />
      <input className="input" name="niche" placeholder="photography / cine / vlog" />
      <input className="input" name="max_results" type="number" min={1} max={100} defaultValue={25} />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Searching…" : "Search platform"}</button>
    </form>
  );
}

function ProviderResult({ result }: { result: Row }) {
  return (
    <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, fontSize: 12 }}>
      <span>Status: {str(result.status)}</span>
      <span>Platform: {str(result.platform)}</span>
      <span>Market: {str(result.market)}</span>
      <span>Returned: {num((result.metadata as Row | undefined)?.returned).toLocaleString()}</span>
      <span>Saved: {num(result.saved_candidates).toLocaleString()}</span>
      {result.message ? <span style={{ gridColumn: "1 / -1", color: "var(--ax-status-block)" }}>{str(result.message)}</span> : null}
    </div>
  );
}

function CandidateTable({ rows, columns, page, onPage }: { rows: Row[]; columns: DataColumn<Row>[]; page: Row; onPage: (offset: unknown) => void }) {
  return (
    <div className="ax-card" style={{ overflowX: "auto" }}>
      <DataTable columns={columns} rows={rows} rowKey={(row, index) => String(row.id || index)} showCheckbox={false} emptyLabel="暂无候选。先在平台搜索页抓取真实结果。" />
      <Pager page={page} onPage={onPage} />
    </div>
  );
}

function Pager({ page, onPage }: { page: Row; onPage: (offset: unknown) => void }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, paddingTop: 10, fontSize: 12 }}>
      <button className="ax-btn ax-btn--sm" type="button" disabled={page.prev_offset === null || page.prev_offset === undefined} onClick={() => onPage(page.prev_offset)}>上一页</button>
      <span style={{ color: "var(--ax-text-2)" }}>{pageText(page)}</span>
      <button className="ax-btn ax-btn--sm" type="button" disabled={page.next_offset === null || page.next_offset === undefined} onClick={() => onPage(page.next_offset)}>下一页</button>
    </div>
  );
}

function StaffPerformance({ rows }: { rows: Row[] }) {
  return (
    <div className="ax-card">
      <SectionLabel>对接人聚合</SectionLabel>
      <div style={{ display: "grid", gap: 6 }}>
        {rows.map((row) => (
          <div key={String(row.staff_id)} style={{ display: "grid", gridTemplateColumns: "1fr repeat(5, 0.7fr)", gap: 8, fontSize: 11 }}>
            <strong>{str(row.staff_name, `Staff #${row.staff_id}`)}</strong>
            <span>{num(row.kol_count)} KOL</span>
            <span>{num(row.campaign_count)} campaigns</span>
            <span>{usd(row.total_cost_cents)}</span>
            <span>{usd(row.total_revenue_cents)}</span>
            <span>{pct(row.roi)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function KolDetail({ detail, suggestions, busy, onQuickAction }: { detail: KolDetailSnapshot; suggestions: Row | null; busy: string; onQuickAction: (kind: "outreach" | "campaign" | "content" | "suggest" | "score") => void }) {
  return (
    <div className="ax-card" style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <SectionLabel>KOL Detail</SectionLabel>
          <h3 style={{ margin: "4px 0" }}>{str(detail.kol.channel_name)}</h3>
          <div style={{ color: "var(--ax-text-2)", fontSize: 11 }}>
            {str(detail.kol.platform)} · {str(detail.kol.country)} · {str(detail.kol.niche)}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {(["outreach", "campaign", "content", "score", "suggest"] as const).map((kind) => (
            <button key={kind} className="ax-btn ax-btn--sm" type="button" disabled={busy === kind} onClick={() => onQuickAction(kind)}>
              {kind === "suggest" ? "AI 建议" : kind}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
        <MiniList label="Outreach" rows={detail.outreach} fields={["action_type", "notes", "action_at"]} />
        <MiniList label="Campaigns" rows={detail.campaigns} fields={["product_sku", "status", "cost_cents"]} />
        <MiniList label="Content" rows={detail.content} fields={["platform", "views", "ai_quality_score"]} />
        <MiniList label="Attribution" rows={detail.attribution} fields={["shopify_order_id", "attributed_revenue_cents"]} />
      </div>

      {suggestions ? (
        <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
          <SectionLabel>AI 建议</SectionLabel>
          {(Array.isArray(suggestions.suggestions) ? suggestions.suggestions : []).map((item, index) => (
            <div key={index} style={{ fontSize: 12, marginBottom: 6 }}>{String(item)}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MiniList({ label, rows, fields }: { label: string; rows: Row[]; fields: string[] }) {
  return (
    <div style={{ minWidth: 0 }}>
      <SectionLabel>{label}</SectionLabel>
      {rows.length === 0 ? (
        <div style={{ color: "var(--ax-text-1)", fontSize: 11 }}>No records</div>
      ) : (
        rows.slice(0, 4).map((row, index) => (
          <div key={String(row.id || index)} style={{ borderTop: "1px solid var(--ax-border)", padding: "6px 0", fontSize: 11 }}>
            {fields.map((field) => (
              <div key={field} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                <span style={{ color: "var(--ax-text-1)" }}>{field}: </span>{field.includes("cents") ? usd(row[field]) : str(row[field])}
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

export default KolOpsTab;
