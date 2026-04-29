import { useEffect, useMemo, useState, type FormEvent } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  clearDeepSightCache,
  fetchDeepSightCacheStats,
  fetchDeepSightHealth,
  runDeepSightDiagnose,
  runDeepSightEvidencePack,
  scanDeepSightOfficialMatrix,
} from "../../../services/admin.service";
import { Icons } from "../Icons";
import { DataTable, ErrorCard, KPIGrid, PageHeader, SectionLabel, StatusPill, type DataColumn } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;
type Mode = "diagnose" | "evidence" | "matrix" | "cache";

function str(value: unknown, fallback = "—") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function num(value: unknown) {
  const next = Number(value ?? 0);
  return Number.isFinite(next) ? next : 0;
}

function list(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item && typeof item === "object")) : [];
}

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function bulletItems(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 6).map((item) => typeof item === "object" ? JSON.stringify(item) : String(item));
}

export function DeepSightTab({ token }: Props) {
  const [mode, setMode] = useState<Mode>("diagnose");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [health, setHealth] = useState<Row | null>(null);
  const [cache, setCache] = useState<Row | null>(null);
  const [result, setResult] = useState<Row | null>(null);
  const [matrixResult, setMatrixResult] = useState<Row | null>(null);

  const loadMeta = async () => {
    try {
      const [healthSnapshot, cacheSnapshot] = await Promise.all([
        fetchDeepSightHealth(token),
        fetchDeepSightCacheStats(token),
      ]);
      setHealth(healthSnapshot);
      setCache(cacheSnapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void loadMeta();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const buildPayload = (form: FormData, modelMode: "fast" | "triad") => ({
    brand: String(form.get("brand") || "Viltrox"),
    scope: String(form.get("scope") || "official_matrix"),
    days: Number(form.get("days") || 7),
    previous_days: Number(form.get("previous_days") || form.get("days") || 7),
    platforms: String(form.get("platforms") || "").split(",").map((x) => x.trim()).filter(Boolean),
    include_competitors: form.get("include_competitors") === "on",
    include_comments: form.get("include_comments") === "on",
    include_visual_life: form.get("include_visual_life") === "on",
    refresh: form.get("refresh") === "on",
    model_mode: modelMode,
    max_posts_per_account: Number(form.get("max_posts_per_account") || 60),
    concurrency: Number(form.get("concurrency") || 4),
  });

  const run = async (event: FormEvent<HTMLFormElement>, kind: "diagnose" | "evidence") => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(kind);
    setError("");
    try {
      const payload = buildPayload(form, kind === "diagnose" ? String(form.get("model_mode") || "triad") as "fast" | "triad" : "fast");
      setResult(kind === "diagnose" ? await runDeepSightDiagnose(token, payload) : await runDeepSightEvidencePack(token, payload));
      await loadMeta();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const runMatrix = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("matrix");
    setError("");
    try {
      setMatrixResult(await scanDeepSightOfficialMatrix(token, {
        max_posts_per_account: Number(form.get("max_posts_per_account") || 60),
        concurrency: Number(form.get("concurrency") || 4),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const clearCache = async () => {
    setBusy("cache");
    setError("");
    try {
      setCache(await clearDeepSightCache(token));
      await loadMeta();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const diagnosis = obj(result?.diagnosis);
  const pack = obj(result?.evidence_pack);
  const council = obj(diagnosis.council_views);
  const evidenceConfidence = obj(pack.evidence_confidence);
  const consensus = useMemo(() => {
    if (!result) return 0;
    if (diagnosis.split_vote) return 45;
    const score = num(evidenceConfidence.confidence_score);
    return Math.round((score ? score : 0.72) * 100);
  }, [diagnosis.split_vote, evidenceConfidence.confidence_score, result]);
  const kpis = [
    { label: "Consensus", value: result ? `${consensus}%` : "—", hint: diagnosis.split_vote ? "split vote" : "triad / rules" },
    { label: "Tier", value: str(result?.tier), hint: result?.cache_hit ? "cache hit" : "fresh" },
    { label: "Cache", value: str(obj(cache?.cache).size ?? obj(cache?.cache).entries ?? "—"), hint: "DeepSight cache" },
    { label: "Modules", value: String(Array.isArray(health?.modules) ? health?.modules.length : "—"), hint: "loaded" },
  ];

  return (
    <div>
      <PageHeader
        title="DeepSight"
        subtitle="Triad council · evidence pack · matrix scan · cache"
        actions={<button type="button" className="ax-btn" onClick={loadMeta}><Icons.via /> Refresh status</button>}
      />
      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {error ? <ErrorCard label="DeepSight 调用失败" detail={error} onRetry={() => setError("")} /> : null}
        <KPIGrid items={kpis} />
        <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["diagnose", "evidence", "matrix", "cache"] as Mode[]).map((item) => (
            <button key={item} type="button" className={`ax-btn ax-btn--sm${mode === item ? " is-active" : ""}`} onClick={() => setMode(item)}>
              {item}
            </button>
          ))}
        </div>

        {mode === "diagnose" || mode === "evidence" ? (
          <DeepSightForm mode={mode} busy={busy === mode} onSubmit={(event) => run(event, mode)} />
        ) : null}
        {mode === "matrix" ? <MatrixForm busy={busy === "matrix"} onSubmit={runMatrix} /> : null}
        {mode === "cache" ? <CachePanel cache={cache} busy={busy === "cache"} onClear={clearCache} /> : null}

        {result ? <DiagnosisResult result={result} consensus={consensus} /> : null}
        {matrixResult ? <MatrixResult result={matrixResult} /> : null}
      </div>
    </div>
  );
}

function DeepSightForm({ mode, busy, onSubmit }: { mode: "diagnose" | "evidence"; busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 170px 90px 110px 1fr 110px 110px", gap: 8 }}>
        <input className="input" name="brand" placeholder="Brand" defaultValue="Viltrox" required />
        <select className="input" name="scope" defaultValue="official_matrix">
          <option value="official_matrix">official matrix</option>
          <option value="ugc_market">UGC market</option>
          <option value="all_visual_life">all visual life</option>
        </select>
        <input className="input" name="days" type="number" min={1} max={180} defaultValue={7} />
        <input className="input" name="previous_days" type="number" min={1} max={180} defaultValue={7} />
        <input className="input" name="platforms" placeholder="youtube,tiktok,instagram" />
        <input className="input" name="max_posts_per_account" type="number" min={5} max={1000} defaultValue={60} />
        <input className="input" name="concurrency" type="number" min={1} max={12} defaultValue={4} />
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {mode === "diagnose" ? (
          <select className="input" name="model_mode" defaultValue="triad" style={{ width: 150 }}>
            <option value="fast">fast rules</option>
            <option value="triad">triad council</option>
          </select>
        ) : null}
        <label><input type="checkbox" name="include_competitors" defaultChecked /> competitors</label>
        <label><input type="checkbox" name="include_comments" defaultChecked /> comments</label>
        <label><input type="checkbox" name="include_visual_life" defaultChecked /> visual life</label>
        <label><input type="checkbox" name="refresh" /> refresh cache</label>
        <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Running…" : mode === "diagnose" ? "Run diagnosis" : "Build evidence pack"}</button>
      </div>
    </form>
  );
}

function MatrixForm({ busy, onSubmit }: { busy: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="ax-card" onSubmit={onSubmit} style={{ display: "grid", gridTemplateColumns: "160px 160px auto", gap: 8 }}>
      <input className="input" name="max_posts_per_account" type="number" min={5} max={1000} defaultValue={60} />
      <input className="input" name="concurrency" type="number" min={1} max={12} defaultValue={4} />
      <button className="ax-btn" type="submit" disabled={busy}>{busy ? "Scanning…" : "Scan official matrix"}</button>
    </form>
  );
}

function CachePanel({ cache, busy, onClear }: { cache: Row | null; busy: boolean; onClear: () => void }) {
  return (
    <div className="ax-card" style={{ display: "grid", gap: 8 }}>
      <SectionLabel>Cache stats</SectionLabel>
      <pre style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }}>{JSON.stringify(cache, null, 2)}</pre>
      <button className="ax-btn" type="button" disabled={busy} onClick={onClear}>{busy ? "Clearing…" : "Clear DeepSight cache"}</button>
    </div>
  );
}

function DiagnosisResult({ result, consensus }: { result: Row; consensus: number }) {
  const diagnosis = obj(result.diagnosis);
  const pack = obj(result.evidence_pack);
  const council = obj(diagnosis.council_views);
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="ax-card">
        <SectionLabel>Diagnosis</SectionLabel>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <StatusPill tone={str(diagnosis.overall_health) === "critical" ? "block" : str(diagnosis.overall_health) === "warning" ? "review" : "active"}>{str(diagnosis.overall_health)}</StatusPill>
          <span>Consensus {consensus}%</span>
          <span>{diagnosis.split_vote ? "分歧存在" : "无明显分歧"}</span>
          <span>Tier {str(result.tier)}</span>
        </div>
        <p>{str(diagnosis.one_liner, "No diagnosis returned")}</p>
      </div>
      <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
        <CouncilCard title="Claude 结构/风险" data={obj(council.claude)} fallback={bulletItems(pack.risk_flags)} />
        <CouncilCard title="GPT 用户/情绪" data={obj(council.gpt)} fallback={bulletItems(obj(pack.comment_analysis).negative_keywords)} />
        <CouncilCard title="Gemini 增长/机会" data={obj(council.gemini)} fallback={bulletItems(pack.opportunities)} />
      </div>
      <BreakdownTables diagnosis={diagnosis} />
    </div>
  );
}

function CouncilCard({ title, data, fallback }: { title: string; data: Row; fallback: string[] }) {
  const items = bulletItems(data.risks || data.platform_notes || data.positive_keywords || data.opportunities);
  return (
    <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
      <SectionLabel>{title}</SectionLabel>
      <p style={{ minHeight: 48 }}>{str(data.summary, fallback[0] || "No council output for this run")}</p>
      <div style={{ display: "grid", gap: 4, fontSize: 12 }}>
        {(items.length ? items : fallback).slice(0, 4).map((item, index) => <span key={index}>· {item}</span>)}
      </div>
    </div>
  );
}

function BreakdownTables({ diagnosis }: { diagnosis: Row }) {
  const columns: DataColumn<Row>[] = [
    { key: "name", label: "Item", width: "1fr", render: (r) => <strong>{str(r.platform || r.account || r.product || r.target || r.name)}</strong> },
    { key: "signal", label: "Signal", width: "1fr", render: (r) => str(r.diagnostic_flag || r.summary || r.status || r.evidence) },
    { key: "delta", label: "Delta", width: "0.5fr", render: (r) => str(r.wow_views_change ?? r.mentions ?? r.count ?? "") },
  ];
  const rows = [...list(diagnosis.platform_diagnosis), ...list(diagnosis.account_diagnosis), ...list(diagnosis.product_insight)].slice(0, 12);
  return (
    <div className="ax-card">
      <SectionLabel>Evidence breakdown</SectionLabel>
      <DataTable columns={columns} rows={rows} rowKey={(row, index) => `${str(row.platform || row.account || row.product)}-${index}`} showCheckbox={false} emptyLabel="No evidence rows returned" />
    </div>
  );
}

function MatrixResult({ result }: { result: Row }) {
  return (
    <details className="ax-card" open>
      <summary style={{ cursor: "pointer" }}>Official matrix scan result</summary>
      <pre style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto", fontSize: 11 }}>{JSON.stringify(result, null, 2)}</pre>
    </details>
  );
}

export default DeepSightTab;
