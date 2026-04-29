import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  addKolOutreach,
  createKol,
  createKolCampaign,
  createKolContent,
  fetchKolDetail,
  fetchKolOpsSnapshot,
  fetchKolStaffPerformance,
  fetchKolSuggestions,
  importKolCsv,
  scoreKolContent,
  type KolDetailSnapshot,
} from "../../../services/admin.service";
import { Icons } from "../Icons";
import {
  DataTable,
  ErrorCard,
  KPIGrid,
  LoadingCard,
  PageHeader,
  SectionLabel,
  StatusPill,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;

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
  if (["active", "agreed"].includes(raw)) return "active";
  if (["replied", "contacted"].includes(raw)) return "review";
  if (["refused", "churned"].includes(raw)) return "churn";
  if (["cold"].includes(raw)) return "new";
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

export function KolOpsTab({ token }: Props) {
  const [items, setItems] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [performance, setPerformance] = useState<Row[]>([]);
  const [detail, setDetail] = useState<KolDetailSnapshot | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filters, setFilters] = useState({ staff_id: "", country: "", platform: "", status: "" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState("");
  const [suggestions, setSuggestions] = useState<Row | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [snapshot, perf] = await Promise.all([
        fetchKolOpsSnapshot(token, buildQuery(filters)),
        fetchKolStaffPerformance(token),
      ]);
      setItems(snapshot.items || []);
      setSummary(snapshot.summary || {});
      setPerformance(perf.items || []);
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
    { key: "channel", label: "频道名", width: "1.4fr", render: (r) => <strong>{str(r.channel_name)}</strong> },
    { key: "platform", label: "平台", width: "0.7fr", render: (r) => str(r.platform) },
    { key: "country", label: "国家", width: "0.6fr", render: (r) => str(r.country) },
    { key: "staff", label: "对接人", width: "0.8fr", render: (r) => str(r.assigned_staff_name) },
    { key: "status", label: "状态", width: "0.8fr", render: (r) => <StatusPill tone={statusTone(r.contact_status)}>{str(r.contact_status, "cold")}</StatusPill> },
    { key: "views", label: "播放量", width: "0.75fr", render: (r) => num(r.views).toLocaleString() },
    { key: "engagement", label: "互动率", width: "0.75fr", render: (r) => pct(r.engagement_rate) },
    { key: "cost", label: "成本", width: "0.7fr", render: (r) => usd(r.cost_cents) },
    { key: "cpv", label: "CPV", width: "0.65fr", render: (r) => `$${num(r.cpv).toFixed(4)}` },
    { key: "revenue", label: "收入", width: "0.7fr", render: (r) => usd(r.revenue_cents) },
    { key: "roi", label: "ROI", width: "0.65fr", render: (r) => pct(r.roi) },
    { key: "ai", label: "AI 评分", width: "0.65fr", render: (r) => Math.round(num(r.avg_ai_quality_score)) || "—" },
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
        const views = Number(window.prompt("播放量", "0") || 0);
        const likes = Number(window.prompt("点赞", "0") || 0);
        const comments = Number(window.prompt("评论", "0") || 0);
        const shares = Number(window.prompt("转发", "0") || 0);
        await createKolContent(token, {
          campaign_id: campaignId,
          content_url: contentUrl,
          platform: String(detail.kol.platform || "youtube"),
          views,
          likes,
          comments,
          shares,
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

  if (loading) return <LoadingCard label="Loading KOL Operations…" />;
  if (error) return <ErrorCard label="KOL Ops 加载失败" detail={error} onRetry={load} />;

  return (
    <div>
      <PageHeader
        title="KOL Ops"
        subtitle="KOL list · CSV import · outreach · campaigns · content scoring"
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

        <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 8 }}>
          {(["staff_id", "country", "platform", "status"] as const).map((key) => (
            <input
              key={key}
              className="input"
              placeholder={key}
              value={filters[key]}
              onChange={(event) => setFilters((prev) => ({ ...prev, [key]: event.target.value }))}
            />
          ))}
          <button type="button" className="ax-btn" onClick={load}><Icons.filter /> Apply</button>
        </div>

        <div className="ax-card">
          <SectionLabel>新增 KOL</SectionLabel>
          <form onSubmit={handleCreateKol} style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr 0.6fr 0.8fr 1fr auto", gap: 8 }}>
            <input className="input" name="channel_name" placeholder="频道名" required />
            <input className="input" name="platform" placeholder="youtube" defaultValue="youtube" required />
            <input className="input" name="country" placeholder="US" />
            <input className="input" name="niche" placeholder="photography" />
            <input className="input" name="contact_email" placeholder="email" />
            <button type="submit" className="ax-btn" disabled={busy === "create-kol"}><Icons.plus /> Add</button>
          </form>
        </div>

        <div className="ax-card" style={{ overflowX: "auto" }}>
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(row, index) => String(row.id || index)}
            selectedId={selectedId ? String(selectedId) : null}
            onRowClick={openDetail}
            showCheckbox={false}
            emptyLabel="还没有 KOL 数据，先导入 CSV 或手动新增。"
          />
        </div>

        <div className="ax-card">
          <SectionLabel>对接人聚合</SectionLabel>
          <div style={{ display: "grid", gap: 6 }}>
            {performance.slice(0, 8).map((row) => (
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

        {detail ? (
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
                <button className="ax-btn ax-btn--sm" type="button" onClick={() => handleQuickAction("outreach")}>Add outreach</button>
                <button className="ax-btn ax-btn--sm" type="button" onClick={() => handleQuickAction("campaign")}>Add campaign</button>
                <button className="ax-btn ax-btn--sm" type="button" onClick={() => handleQuickAction("content")}>Add content</button>
                <button className="ax-btn ax-btn--sm" type="button" onClick={() => handleQuickAction("score")}>Score</button>
                <button className="ax-btn ax-btn--sm" type="button" onClick={() => handleQuickAction("suggest")}>AI 建议</button>
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
        ) : null}
      </div>
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
