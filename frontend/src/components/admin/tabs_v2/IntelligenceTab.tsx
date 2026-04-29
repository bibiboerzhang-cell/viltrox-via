import { useMemo, useState, type FormEvent } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  compareLensMarket,
  learnIntelligenceUrl,
  monitorLensMarket,
  scanIntelligenceAccount,
  scanIntelligenceMatrix,
} from "../../../services/admin.service";
import { Icons } from "../Icons";
import { DataTable, ErrorCard, KPIGrid, PageHeader, SectionLabel, StatusPill, type DataColumn } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;
type Mode = "account" | "matrix" | "monitor" | "compare" | "learn";

function str(value: unknown, fallback = "—") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function num(value: unknown) {
  const next = Number(value ?? 0);
  return Number.isFinite(next) ? next : 0;
}

function pct(value: unknown) {
  return `${num(value).toFixed(2)}%`;
}

function list(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item && typeof item === "object")) : [];
}

function nested(source: Row | null, key: string): Row {
  const value = source?.[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

export function IntelligenceTab({ token }: Props) {
  const [mode, setMode] = useState<Mode>("monitor");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [accountResult, setAccountResult] = useState<Row | null>(null);
  const [matrixResult, setMatrixResult] = useState<Row | null>(null);
  const [monitorResult, setMonitorResult] = useState<Row | null>(null);
  const [compareResult, setCompareResult] = useState<Row | null>(null);
  const [learnResult, setLearnResult] = useState<Row | null>(null);
  const [matrixText, setMatrixText] = useState("");

  const activeResult = { account: accountResult, matrix: matrixResult, monitor: monitorResult, compare: compareResult, learn: learnResult }[mode];
  const monitorOverview = nested(monitorResult, "overview");
  const accountStats = nested(accountResult, "stats");
  const matrixAggregate = nested(matrixResult, "aggregate");
  const compareA = nested(nested(compareResult, "lens_a"), "stats");
  const compareB = nested(nested(compareResult, "lens_b"), "stats");

  const kpis = useMemo(() => {
    if (mode === "monitor") {
      return [
        { label: "Videos", value: num(monitorOverview.total_videos) },
        { label: "Views", value: num(monitorOverview.total_views).toLocaleString() },
        { label: "Creators", value: num(monitorOverview.unique_creators) },
        { label: "Engagement", value: pct(monitorOverview.avg_engagement_pct) },
      ];
    }
    if (mode === "compare") {
      return [
        { label: "A Views", value: num(compareA.total_views).toLocaleString() },
        { label: "B Views", value: num(compareB.total_views).toLocaleString() },
        { label: "Winner", value: str(nested(compareResult, "comparison").winner_views) },
        { label: "Multiplier", value: `${num(nested(compareResult, "comparison").attention_multiplier).toFixed(2)}x` },
      ];
    }
    if (mode === "matrix") {
      return [
        { label: "Accounts", value: num(matrixResult?.scanned) || num(matrixResult?.total) },
        { label: "Posts", value: num(matrixAggregate.total_posts) },
        { label: "Views", value: num(matrixAggregate.total_views).toLocaleString() },
        { label: "Likes", value: num(matrixAggregate.total_likes).toLocaleString() },
      ];
    }
    return [
      { label: "Posts", value: num(accountStats.total_posts) },
      { label: "Views", value: num(accountStats.total_views).toLocaleString() },
      { label: "Likes", value: num(accountStats.total_likes).toLocaleString() },
      { label: "Comments", value: num(accountStats.total_comments).toLocaleString() },
    ];
  }, [accountStats, compareA, compareB, compareResult, matrixAggregate, matrixResult, mode, monitorOverview]);

  const runAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("account");
    setError("");
    try {
      setAccountResult(await scanIntelligenceAccount(token, {
        platform: String(form.get("platform") || "youtube"),
        handle: String(form.get("handle") || ""),
        max_posts: Number(form.get("max_posts") || 50),
        sync: true,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const runMatrix = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy("matrix");
    setError("");
    try {
      const accounts = JSON.parse(matrixText) as Array<{ platform: string; handle: string; name?: string }>;
      setMatrixResult(await scanIntelligenceMatrix(token, {
        accounts,
        max_posts_per_account: Number(new FormData(event.currentTarget).get("max_posts_per_account") || 30),
        sync: true,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const runMonitor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("monitor");
    setError("");
    try {
      setMonitorResult(await monitorLensMarket(token, {
        query: String(form.get("query") || ""),
        max_videos: Number(form.get("max_videos") || 20),
        platform: String(form.get("platform") || "youtube"),
        market: String(form.get("market") || ""),
        date_from: String(form.get("date_from") || ""),
        date_to: String(form.get("date_to") || ""),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const runCompare = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("compare");
    setError("");
    try {
      setCompareResult(await compareLensMarket(token, {
        lens_a: String(form.get("lens_a") || ""),
        lens_b: String(form.get("lens_b") || ""),
        max_videos: Number(form.get("max_videos") || 12),
        platform: String(form.get("platform") || "youtube"),
        market: String(form.get("market") || ""),
        date_from: String(form.get("date_from") || ""),
        date_to: String(form.get("date_to") || ""),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const runLearn = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("learn");
    setError("");
    try {
      setLearnResult(await learnIntelligenceUrl(token, {
        url: String(form.get("url") || ""),
        source_platform: String(form.get("source_platform") || ""),
        region_code: String(form.get("region_code") || ""),
        note: String(form.get("note") || ""),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <PageHeader
        title="Intelligence"
        subtitle="Account matrix · lens monitor · comparisons · learning"
        actions={<button type="button" className="ax-btn" onClick={() => setError("")}><Icons.trending /> Live tools</button>}
      />

      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["monitor", "compare", "account", "matrix", "learn"] as Mode[]).map((item) => (
            <button key={item} type="button" className={`ax-btn ax-btn--sm${mode === item ? " is-active" : ""}`} onClick={() => setMode(item)}>
              {item}
            </button>
          ))}
        </div>

        {error ? <ErrorCard label="Intelligence 调用失败" detail={error} onRetry={() => setError("")} /> : null}
        <KPIGrid items={kpis} />

        {mode === "monitor" ? <MonitorForm busy={busy === "monitor"} onSubmit={runMonitor} /> : null}
        {mode === "compare" ? <CompareForm busy={busy === "compare"} onSubmit={runCompare} /> : null}
        {mode === "account" ? <AccountForm busy={busy === "account"} onSubmit={runAccount} /> : null}
        {mode === "matrix" ? (
          <MatrixForm busy={busy === "matrix"} value={matrixText} onChange={setMatrixText} onSubmit={runMatrix} />
        ) : null}
        {mode === "learn" ? <LearnForm busy={busy === "learn"} onSubmit={runLearn} /> : null}

        {mode === "monitor" && monitorResult ? <MonitorResult result={monitorResult} /> : null}
        {mode === "compare" && compareResult ? <CompareResult result={compareResult} /> : null}
        {mode === "account" && accountResult ? <AccountResult result={accountResult} /> : null}
        {mode === "matrix" && matrixResult ? <MatrixResult result={matrixResult} /> : null}
        {mode === "learn" && learnResult ? <LearnResult result={learnResult} /> : null}

        {activeResult ? (
          <details className="ax-card">
            <summary style={{ cursor: "pointer", color: "var(--ax-text-2)" }}>Raw JSON</summary>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, overflow: "auto", maxHeight: 360 }}>{JSON.stringify(activeResult, null, 2)}</pre>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function MonitorForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "1.4fr 130px 100px 130px 130px 100px auto", gap: 8 }}>
      <input className="input" name="query" placeholder="产品或关键词，例如 16mm f1.8 / anamorphic" required />
      <PlatformSelect />
      <input className="input" name="market" placeholder="US / JP / EU" />
      <input className="input" name="date_from" type="date" aria-label="date from" />
      <input className="input" name="date_to" type="date" aria-label="date to" />
      <input className="input" name="max_videos" type="number" min={1} max={50} defaultValue={20} />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Scanning…" : "Run monitor"}</button>
    </form>
  );
}

function CompareForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 130px 90px 120px 120px 90px auto", gap: 8 }}>
      <input className="input" name="lens_a" placeholder="产品 A，例如 Viltrox 16mm F1.8" required />
      <input className="input" name="lens_b" placeholder="产品 B，例如 Sigma 16mm F1.4" required />
      <PlatformSelect />
      <input className="input" name="market" placeholder="US" />
      <input className="input" name="date_from" type="date" aria-label="date from" />
      <input className="input" name="date_to" type="date" aria-label="date to" />
      <input className="input" name="max_videos" type="number" min={1} max={30} defaultValue={12} />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Comparing…" : "Compare"}</button>
    </form>
  );
}

function AccountForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "140px 1fr 120px auto", gap: 8 }}>
      <select className="input" name="platform" defaultValue="youtube">
        {["youtube", "tiktok", "instagram", "facebook"].map((platform) => <option key={platform} value={platform}>{platform}</option>)}
      </select>
      <input className="input" name="handle" placeholder="@handle 或频道名" required />
      <input className="input" name="max_posts" type="number" min={1} max={1000} defaultValue={50} />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Scanning…" : "Scan account"}</button>
    </form>
  );
}

function MatrixForm({ busy, value, onChange, onSubmit }: { busy: boolean; value: string; onChange: (next: string) => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gap: 8 }}>
      <textarea
        className="input"
        rows={7}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={'粘贴真实账号 JSON，例如 [{"platform":"youtube","handle":"@real_channel","name":"US YouTube"}]'}
        required
      />
      <div style={{ display: "grid", gridTemplateColumns: "160px auto", gap: 8 }}>
        <input className="input" name="max_posts_per_account" type="number" min={1} max={1000} defaultValue={30} />
        <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Scanning matrix…" : "Scan matrix"}</button>
      </div>
    </form>
  );
}

function LearnForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "1.7fr 120px 100px 1fr auto", gap: 8 }}>
      <input className="input" name="url" placeholder="https://..." required />
      <input className="input" name="source_platform" placeholder="youtube" />
      <input className="input" name="region_code" placeholder="US" />
      <input className="input" name="note" placeholder="note" />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Queueing…" : "Learn URL"}</button>
    </form>
  );
}

function PlatformSelect() {
  return (
    <select className="input" name="platform" defaultValue="youtube">
      <option value="youtube">YouTube</option>
      <option value="tiktok">TikTok</option>
      <option value="instagram">Instagram</option>
    </select>
  );
}

function MonitorResult({ result }: { result: Row }) {
  const categories = nested(result, "categories");
  const insights = nested(result, "claude_insights");
  const hourly = nested(nested(result, "hourly_distribution"), "by_hour_utc");
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="ax-card">
        <SectionLabel>Lens Monitor Insights</SectionLabel>
        <MetaLine result={result} />
        <p style={{ marginTop: 0 }}>{str(insights.summary, str(result.error, "No written insight yet"))}</p>
        <BulletList label="Topics" items={insights.trending_topics} />
        <BulletList label="Opportunities" items={insights.opportunities} />
        <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>{str(nested(result, "hourly_distribution").recommendation)}</div>
      </div>
      <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8 }}>
        {Object.entries(categories).map(([key, value]) => {
          const row = value as Row;
          return (
            <div key={key} className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
              <SectionLabel>{key}</SectionLabel>
              <strong>{num(row.count)}</strong>
              <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>{num(row.total_views).toLocaleString()} views</div>
            </div>
          );
        })}
      </div>
      <HourBars hourly={hourly} />
      <VideoTable rows={Object.values(categories).flatMap((value) => list((value as Row).top_videos)).slice(0, 12)} />
    </div>
  );
}

function CompareResult({ result }: { result: Row }) {
  const lensA = nested(result, "lens_a");
  const lensB = nested(result, "lens_b");
  const statsA = nested(lensA, "stats");
  const statsB = nested(lensB, "stats");
  const comparison = nested(result, "comparison");
  const analysis = nested(result, "claude_analysis");
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <LensCard title={str(lensA.name)} stats={statsA} winner={comparison.winner_views === "a"} />
        <LensCard title={str(lensB.name)} stats={statsB} winner={comparison.winner_views === "b"} />
        <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
          <SectionLabel>Verdict</SectionLabel>
          <StatusPill tone={comparison.winner_views === "tie" ? "review" : "active"}>{str(comparison.winner_views)}</StatusPill>
          <div style={{ marginTop: 10, fontSize: 12 }}>Attention multiplier: {num(comparison.attention_multiplier).toFixed(2)}x</div>
          <div style={{ fontSize: 12 }}>Engagement delta: {num(comparison.engagement_delta).toFixed(2)} pts</div>
        </div>
      </div>
      <div className="ax-card">
        <SectionLabel>Competitive Analysis</SectionLabel>
        <MetaLine result={nested(result, "metadata")} />
        <p style={{ marginTop: 0 }}>{str(analysis.summary || analysis.overall_summary || analysis.verdict, "No analysis text returned")}</p>
        <BulletList label="Viltrox angles" items={analysis.viltrox_angles || analysis.opportunities || analysis.recommendations} />
      </div>
      <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <SectionLabel>{str(lensA.name)} top videos</SectionLabel>
          <VideoTable rows={list(statsA.top_videos)} />
        </div>
        <div>
          <SectionLabel>{str(lensB.name)} top videos</SectionLabel>
          <VideoTable rows={list(statsB.top_videos)} />
        </div>
      </div>
    </div>
  );
}

function MetaLine({ result }: { result: Row }) {
  const platform = str(result.platform || nested(result, "metadata").platform, "");
  const market = str(result.market || nested(result, "metadata").market, "");
  const status = str(result.provider_status || result.provider_status_a || nested(result, "metadata").provider_status, "");
  if (!platform && !market && !status) return null;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, fontSize: 11, color: "var(--ax-text-2)" }}>
      {platform ? <span>Platform: {platform}</span> : null}
      {market ? <span>Market: {market}</span> : null}
      {status ? <span>Provider: {status}</span> : null}
    </div>
  );
}

function LensCard({ title, stats, winner }: { title: string; stats: Row; winner: boolean }) {
  return (
    <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)", borderColor: winner ? "rgba(99,165,30,0.45)" : undefined }}>
      <SectionLabel>{winner ? "Winner" : "Lens"}</SectionLabel>
      <h3 style={{ margin: "4px 0 10px" }}>{title}</h3>
      <div style={{ display: "grid", gap: 4, fontSize: 12 }}>
        <span>Videos: {num(stats.video_count || stats.total_videos)}</span>
        <span>Views: {num(stats.total_views).toLocaleString()}</span>
        <span>Likes: {num(stats.total_likes).toLocaleString()}</span>
        <span>Engagement: {pct(stats.avg_engagement_pct)}</span>
      </div>
    </div>
  );
}

function AccountResult({ result }: { result: Row }) {
  return (
    <div className="ax-card">
      <SectionLabel>Account Posts</SectionLabel>
      <VideoTable rows={list(result.posts).slice(0, 20)} />
    </div>
  );
}

function MatrixResult({ result }: { result: Row }) {
  const rows = list(result.results);
  const columns: DataColumn<Row>[] = [
    { key: "name", label: "Account", width: "1fr", render: (r) => <strong>{str(r.account_name || r.handle)}</strong> },
    { key: "platform", label: "Platform", width: "0.7fr", render: (r) => str(r.platform) },
    { key: "posts", label: "Posts", width: "0.6fr", render: (r) => num(nested(r, "stats").total_posts) },
    { key: "views", label: "Views", width: "0.8fr", render: (r) => num(nested(r, "stats").total_views).toLocaleString() },
    { key: "status", label: "Status", width: "0.8fr", render: (r) => <StatusPill tone={r.error ? "block" : "active"}>{r.error ? "error" : str(r.status, "done")}</StatusPill> },
  ];
  return (
    <div className="ax-card">
      <SectionLabel>Matrix Results</SectionLabel>
      <DataTable columns={columns} rows={rows} rowKey={(row, index) => `${row.platform}-${row.handle}-${index}`} showCheckbox={false} />
    </div>
  );
}

function LearnResult({ result }: { result: Row }) {
  return (
    <div className="ax-card">
      <SectionLabel>Learning Job</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, fontSize: 12 }}>
        <span>Status: {str(result.status)}</span>
        <span>Job: {str(result.job_id)}</span>
        <span>Source: {str(result.source_platform)}</span>
        <span>URL: {str(result.url)}</span>
      </div>
    </div>
  );
}

function VideoTable({ rows }: { rows: Row[] }) {
  const columns: DataColumn<Row>[] = [
    { key: "title", label: "Title", width: "1.8fr", render: (r) => <a href={str(r.url, "#")} target="_blank" rel="noreferrer">{str(r.title)}</a> },
    { key: "channel", label: "Channel", width: "0.9fr", render: (r) => str(r.channel) },
    { key: "views", label: "Views", width: "0.7fr", render: (r) => num(r.views).toLocaleString() },
    { key: "likes", label: "Likes", width: "0.7fr", render: (r) => num(r.likes).toLocaleString() },
    { key: "published", label: "Published", width: "0.8fr", render: (r) => str(r.published) },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(row, index) => `${row.url || row.title}-${index}`} showCheckbox={false} emptyLabel="No videos returned" />;
}

function HourBars({ hourly }: { hourly: Row }) {
  const maxViews = Math.max(1, ...Object.values(hourly).map((value) => num((value as Row).views)));
  return (
    <div className="ax-card">
      <SectionLabel>Hour-of-day Distribution UTC</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(24, 1fr)", gap: 4, alignItems: "end", minHeight: 110 }}>
        {Array.from({ length: 24 }).map((_, hour) => {
          const key = String(hour).padStart(2, "0");
          const row = (hourly[key] || {}) as Row;
          const height = Math.max(4, Math.round((num(row.views) / maxViews) * 90));
          return (
            <div key={key} title={`${key}:00 · ${num(row.views).toLocaleString()} views`} style={{ display: "grid", gap: 4, alignItems: "end" }}>
              <div style={{ height, background: "var(--ax-text-5)", opacity: 0.75, borderRadius: 2 }} />
              <span style={{ fontSize: 8, color: "var(--ax-text-1)", textAlign: "center" }}>{hour % 3 === 0 ? key : ""}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BulletList({ label, items }: { label: string; items: unknown }) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <SectionLabel>{label}</SectionLabel>
      <div style={{ display: "grid", gap: 4, fontSize: 12 }}>
        {rows.slice(0, 6).map((item, index) => <span key={index}>· {typeof item === "object" ? JSON.stringify(item) : String(item)}</span>)}
      </div>
    </div>
  );
}

export default IntelligenceTab;
