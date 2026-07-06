import React from "react";
import {
  getKolPoolDetailBundle,
  getKolCooperation,
  getKolPoolLlmDeepAnalysis,
  getKolPoolIntelligenceCard,
  getKolPoolCompetitors,
  type VkpiKolPoolDetailBundleResponse,
  type VkpiKolLlmDeepAnalysisResponse,
  type VkpiKolPoolIntelligenceCard,
} from "../../../../services/vkpi/kolPool-api";
import {
  getKolSignaturePanel,
  getKolProductionLeadtime,
  getKolCompetingActivity,
  type Row,
} from "../../../../services/vkpi/kolProfile-api";

// 件④ · KOL 档案页完整版组装(纯前端聚合,零新后端,零 LLM,零新采集)。
//   目标:3 秒读懂「他是谁 / 什么最灵 / 值多少钱」。
//   八层块:①身份 ②内容招牌(件①端点) ③受众 ④商业(合作+制作周期·件②端点)
//          ⑤信用(竞业+假粉) ⑥深析摘要 ⑦评论声音 ⑧动作条(直链既有抽屉功能,不重造)。
//   兄弟件端点可能未挂载:所有远端块独立 try/catch 诚实空态,页面本体不炸。
//   红线:viltrox_fit_score 只读展示,绝不写;缺数据显式空态,绝不杜撰。
//
//   id 传递约定(collect_anchors 同步主线):
//     sessionStorage["vkpi:kol-profile-id"] = String(kol_pool_id)
//     window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"))
//     CockpitApp 监听该事件 → setActiveNav("kolProfile");本页挂载/事件时自读 id。
//     也支持 URL 参数 ?kolProfileId=123 直链。

const PROFILE_ID_KEY = "vkpi:kol-profile-id";
const OPEN_PROFILE_EVENT = "vkpi:open-kol-profile";
const NOT_MOUNTED_HINT = "端点未上线或暂不可用 · 兄弟件挂载后自动点亮";

// ── 小工具:防御式取值(远端契约不齐,一律宽进严出)──

function asRow(v: unknown): Row | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : null;
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// 0-1 或 0-100 的比例统一到 0-100(读回口径不一的老列都有)。
function pct(v: number | null): number | null {
  if (v === null) return null;
  const p = v <= 1 ? v * 100 : v;
  return p >= 0 && p <= 100 ? Math.round(p * 10) / 10 : null;
}

function fmtCompact(v: number | null): string {
  if (v === null) return "—";
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v * 10) / 10);
}

function fmtDate(v: unknown): string {
  const s = str(v);
  return s ? s.slice(0, 10) : "—";
}

function parseMaybeJson(v: unknown): Row | null {
  const direct = asRow(v);
  if (direct) return direct;
  const s = str(v);
  if (!s || (!s.startsWith("{") && !s.startsWith("["))) return null;
  try {
    return asRow(JSON.parse(s));
  } catch {
    return null;
  }
}

// 从未知形状的对象里捞第一句"有意义的人话"(招牌面板/深析摘要契约未定时的降级读法)。
const MEANING_KEYS = ["summary", "headline", "conclusion", "signature", "insight", "reason", "note", "description", "text", "label", "title"];
function firstMeaningfulString(row: Row | null, maxLen = 90): string {
  if (!row) return "";
  for (const key of MEANING_KEYS) {
    const s = str(row[key]).trim();
    if (s.length >= 4) return s.length > maxLen ? `${s.slice(0, maxLen)}…` : s;
  }
  for (const value of Object.values(row)) {
    const s = str(value).trim();
    if (s.length >= 8 && !/^(ready|ok|missing|empty|unknown|none|null)$/i.test(s)) {
      return s.length > maxLen ? `${s.slice(0, maxLen)}…` : s;
    }
  }
  return "";
}

// ── 通用远端状态 ──

type RemoteState<T> = { status: "idle" | "loading" | "ready" | "error"; data: T | null; error: string };

function useRemote<T>(enabled: boolean, loader: () => Promise<T>, deps: React.DependencyList): RemoteState<T> {
  const [state, setState] = React.useState<RemoteState<T>>({ status: "idle", data: null, error: "" });
  React.useEffect(() => {
    if (!enabled) {
      setState({ status: "idle", data: null, error: "" });
      return;
    }
    let alive = true;
    setState({ status: "loading", data: null, error: "" });
    loader()
      .then((data) => {
        if (alive) setState({ status: "ready", data, error: "" });
      })
      .catch((err: unknown) => {
        if (alive) setState({ status: "error", data: null, error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// ── 展示原子 ──

function Hint({ text, tone = "muted" }: { text: string; tone?: "muted" | "warn" }) {
  return (
    <div className={`rounded-md border border-dashed px-3 py-4 text-center text-[11px] ${tone === "warn" ? "border-amber-300/20 text-amber-200/70" : "border-white/[0.08] text-slate-500"}`}>
      {text}
    </div>
  );
}

function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-[12px]">
      <span className="shrink-0 text-slate-500">{label}</span>
      <span className="min-w-0 break-words text-right text-slate-200">{value}</span>
    </div>
  );
}

function Chip({ text, tone = "slate" }: { text: string; tone?: string }) {
  const tones: Record<string, string> = {
    slate: "border-white/10 bg-white/[0.04] text-slate-300",
    emerald: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
    sky: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100",
    amber: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
    red: "border-red-300/30 bg-red-500/[0.12] text-red-200",
    violet: "border-violet-300/30 bg-violet-500/[0.12] text-violet-100",
  };
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] ${tones[tone] ?? tones.slate}`}>{text}</span>;
}

// 未知契约对象的防御式通用渲染:标量→KV,字符串数组→chips,对象数组→摘要行,嵌套对象→展开一层。
const META_KEYS = new Set([
  "status", "method", "mode", "provider_calls", "llm_calls", "write_db", "diagnostics",
  "generated_at", "kol_pool_id", "source", "source_table", "cache", "note", "contract",
  "evidence", "runs", "persisted", "committed_relations",
]);

const SUMMARY_STR_KEYS = ["label", "name", "title", "brand", "competitor_brand", "handle", "text", "format", "type", "action_type", "status_after", "risk_tier", "sample"];
const SUMMARY_NUM_KEYS = ["count", "pct", "score", "risk_score", "days", "value", "views", "like_count"];

function objectSummary(row: Row): string {
  const parts: string[] = [];
  for (const key of SUMMARY_STR_KEYS) {
    const s = str(row[key]).trim();
    if (s) parts.push(s);
    if (parts.length >= 2) break;
  }
  for (const key of SUMMARY_NUM_KEYS) {
    const n = num(row[key]);
    if (n !== null) parts.push(`${key}=${Math.round(n * 100) / 100}`);
    if (parts.length >= 4) break;
  }
  if (parts.length === 0) {
    for (const [k, v] of Object.entries(row)) {
      const s = str(v).trim();
      if (s) parts.push(`${k}: ${s.length > 40 ? `${s.slice(0, 40)}…` : s}`);
      if (parts.length >= 2) break;
    }
  }
  return parts.join(" · ") || "(无可读字段)";
}

function GenericValue({ label, value, depth }: { label: string; value: unknown; depth: number }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return null;
    const scalars = value.filter((x) => typeof x === "string" || typeof x === "number");
    if (scalars.length === value.length) {
      return (
        <div className="space-y-1 text-[12px]">
          <div className="text-slate-500">{label}</div>
          <div className="flex flex-wrap gap-1">
            {scalars.slice(0, 8).map((s, i) => <Chip key={i} text={String(s)} />)}
            {scalars.length > 8 ? <Chip text={`+${scalars.length - 8}`} /> : null}
          </div>
        </div>
      );
    }
    return (
      <div className="space-y-1 text-[12px]">
        <div className="text-slate-500">{label}</div>
        <ul className="space-y-1">
          {value.slice(0, 5).map((v, i) => {
            const row = asRow(v);
            return (
              <li key={i} className="rounded-md border border-white/[0.06] px-2 py-1 text-slate-300/90">
                {row ? objectSummary(row) : str(v) || "—"}
              </li>
            );
          })}
          {value.length > 5 ? <li className="text-[10px] text-slate-500">…共 {value.length} 条</li> : null}
        </ul>
      </div>
    );
  }
  const row = asRow(value);
  if (row) {
    if (depth >= 1) return <KV label={label} value={objectSummary(row)} />;
    const entries = Object.entries(row).filter(([k, v]) => !META_KEYS.has(k) && v !== null && v !== undefined && v !== "").slice(0, 6);
    if (entries.length === 0) return null;
    return (
      <div className="space-y-1.5 rounded-md border border-white/[0.05] bg-white/[0.015] p-2">
        <div className="text-[11px] font-medium text-slate-400">{label}</div>
        {entries.map(([k, v]) => <GenericValue key={k} label={k} value={v} depth={depth + 1} />)}
      </div>
    );
  }
  const s = str(value);
  if (!s) return null;
  return <KV label={label} value={s.length > 160 ? `${s.slice(0, 160)}…` : s} />;
}

function GenericRows({ data, maxKeys = 10 }: { data: Row; maxKeys?: number }) {
  const entries = Object.entries(data)
    .filter(([k, v]) => !META_KEYS.has(k) && v !== null && v !== undefined && v !== "")
    .slice(0, maxKeys);
  if (entries.length === 0) return <Hint text="有响应但暂无实质字段(诚实空态)" />;
  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => <GenericValue key={k} label={k} value={v} depth={0} />)}
    </div>
  );
}

// 分布条(评论情绪 / 品牌态度等 {label: count} 字典)。
function DistBars({ dist, title }: { dist: Record<string, number>; title: string }) {
  const entries = Object.entries(dist)
    .map(([k, v]) => [k, num(v) ?? 0] as [string, number])
    .filter(([k, v]) => v > 0 && k !== "")
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  if (total === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-slate-500">{title}</div>
      {entries.map(([label, count]) => {
        const share = Math.round((count / total) * 100);
        return (
          <div key={label} className="flex items-center gap-2 text-[11px]">
            <span className="w-20 shrink-0 truncate text-slate-400">{label}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
              <div className="h-full rounded-full bg-sky-400/60" style={{ width: `${share}%` }} />
            </div>
            <span className="w-14 shrink-0 text-right text-slate-300">{count} · {share}%</span>
          </div>
        );
      })}
    </div>
  );
}

// 八层块外壳:编号 + 标题 + 数据源脚注。
function Block({ no, title, source, children }: { no: string; title: string; source: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-white">
          <span className="mr-1.5 text-slate-500">{no}</span>{title}
        </h3>
        <span className="truncate text-[10px] text-slate-600" title={source}>{source}</span>
      </div>
      {children}
    </section>
  );
}

function RemoteBody<T>({ state, errorHint, render }: {
  state: RemoteState<T>;
  errorHint?: string;
  render: (data: T) => React.ReactNode;
}) {
  if (state.status === "idle") return <Hint text="等待选择 KOL" />;
  if (state.status === "loading") return <Hint text="加载中…" />;
  if (state.status === "error") return <Hint tone="warn" text={errorHint || `暂不可用:${state.error}`} />;
  if (state.data === null) return <Hint text="暂无数据(诚实空态)" />;
  return <>{render(state.data)}</>;
}

function fitTone(fit: number | null): string {
  if (fit === null) return "slate";
  if (fit >= 80) return "emerald";
  if (fit >= 60) return "sky";
  if (fit >= 40) return "amber";
  return "red";
}

const RISK_TIER_TONE: Record<string, string> = { avoid: "red", caution: "amber", safe: "emerald", opportunity: "sky" };

// ═══════════════════════════ 页面本体 ═══════════════════════════

export function KolProfilePage({
  apiToken = "",
  kolId: kolIdProp = null,
  onNavigate,
}: {
  apiToken?: string;
  kolId?: number | string | null;
  onNavigate?: (navKey: string) => void;
}) {
  const readPendingId = React.useCallback((): number => {
    try {
      const url = new URLSearchParams(window.location.search).get("kolProfileId");
      const fromUrl = Number(url);
      if (url && Number.isFinite(fromUrl) && fromUrl > 0) return fromUrl;
      const stored = Number(window.sessionStorage.getItem(PROFILE_ID_KEY));
      return Number.isFinite(stored) && stored > 0 ? stored : 0;
    } catch {
      return 0;
    }
  }, []);

  const [kolId, setKolId] = React.useState<number>(() => {
    const fromProp = Number(kolIdProp);
    if (Number.isFinite(fromProp) && fromProp > 0) return fromProp;
    return typeof window !== "undefined" ? readPendingId() : 0;
  });
  const [draftId, setDraftId] = React.useState<string>("");
  const [reloadKey, setReloadKey] = React.useState(0);
  const [copied, setCopied] = React.useState(false);

  // prop 变化 / 事件到达 → 重读 id(打开档案页的两条入口)。
  React.useEffect(() => {
    const fromProp = Number(kolIdProp);
    if (Number.isFinite(fromProp) && fromProp > 0) setKolId(fromProp);
  }, [kolIdProp]);
  React.useEffect(() => {
    const consume = () => {
      const next = readPendingId();
      if (next > 0) setKolId(next);
    };
    window.addEventListener(OPEN_PROFILE_EVENT, consume);
    return () => window.removeEventListener(OPEN_PROFILE_EVENT, consume);
  }, [readPendingId]);

  const applyDraftId = () => {
    const n = Number(draftId);
    if (!Number.isFinite(n) || n <= 0) return;
    try {
      window.sessionStorage.setItem(PROFILE_ID_KEY, String(n));
    } catch { /* sessionStorage 不可用忽略 */ }
    setKolId(n);
  };

  const enabled = Boolean(apiToken) && kolId > 0;
  const deps: React.DependencyList = [apiToken, kolId, reloadKey, enabled];

  // ── 七路独立加载(互不阻塞,单路失败只暗掉自己的块)──
  const bundle = useRemote<VkpiKolPoolDetailBundleResponse>(enabled, () => getKolPoolDetailBundle(apiToken, kolId), deps);
  const signature = useRemote<Row>(enabled, () => getKolSignaturePanel(apiToken, kolId), deps);
  const cooperation = useRemote<Row>(enabled, () => getKolCooperation(apiToken, kolId), deps);
  const leadtime = useRemote<Row>(enabled, () => getKolProductionLeadtime(apiToken, kolId), deps);
  const credit = useRemote<{ source: string; data: Row }>(
    enabled,
    async () => {
      try {
        return { source: "competing-activity", data: await getKolCompetingActivity(apiToken, kolId) };
      } catch {
        // 兄弟件未挂载 → 兜底既有 /competitors(已在线),信用块不至于全黑。
        return { source: "competitors", data: (await getKolPoolCompetitors(apiToken, kolId)) as Row };
      }
    },
    deps,
  );
  const deep = useRemote<VkpiKolLlmDeepAnalysisResponse>(enabled, () => getKolPoolLlmDeepAnalysis(apiToken, kolId, 20), deps);
  const card = useRemote<VkpiKolPoolIntelligenceCard>(enabled, () => getKolPoolIntelligenceCard(apiToken, kolId), deps);

  // ── 头部派生(全部来自 detail_bundle,缺则诚实 "—")──
  const itemRow: Row | null = bundle.data?.item ? (bundle.data.item as unknown as Row) : null;
  const displayName = str(itemRow?.display_name) || str(itemRow?.handle) || (kolId > 0 ? `KOL #${kolId}` : "未选择");
  const handle = str(itemRow?.handle);
  const platform = str(itemRow?.platform);
  const country = str(itemRow?.country);
  const avatarUrl = str(itemRow?.avatar_url);
  const profileUrl = str(itemRow?.profile_url);
  const email = str(itemRow?.email);
  const bio = str(itemRow?.bio);
  const followers = num(itemRow?.followers);
  const avgViews = num(itemRow?.avg_views);
  const engagementPct = pct(num(itemRow?.engagement_rate));
  const fit = num(itemRow?.viltrox_fit_score); // 只读展示,红线:绝不写回
  const realFollowersPct = pct(num(itemRow?.real_followers_pct) ?? num(itemRow?.real_followers_percent));
  const audienceEstimated = parseMaybeJson(itemRow?.audience_estimated) ?? parseMaybeJson(itemRow?.audience_estimated_json);
  const audienceLanguages = asRow(itemRow?.audience_languages);
  const videoCount = asArray(itemRow?.video_evidence).length;

  // ── 3 秒读懂条 ──
  const whoLine = [str(itemRow?.primary_topic), str(itemRow?.content_style), str(itemRow?.production_quality)]
    .filter(Boolean).join(" · ") || (bio ? (bio.length > 70 ? `${bio.slice(0, 70)}…` : bio) : "身份画像待补全");
  const signatureLine = signature.status === "ready"
    ? (firstMeaningfulString(signature.data) || "招牌面板已上线 · 暂无结论字段")
    : (firstMeaningfulString(asRow(deep.data?.summary)) || "招牌内容面板待上线(件①)");
  const coopLabel = str(asRow(cooperation.data)?.status_label);
  const worthLine = [
    fit !== null ? `Fit ${Math.round(fit)}` : "Fit 未评",
    coopLabel ? `合作:${coopLabel}` : "合作:未记录",
    followers !== null ? `${fmtCompact(followers)} 粉` : "",
  ].filter(Boolean).join(" · ");

  // ── ⑧ 动作条:直链既有功能,不重造 ──
  const openDrawer = () => {
    if (kolId <= 0) return;
    try {
      window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(kolId));
    } catch { /* localStorage 不可用忽略 */ }
    // CockpitApp 已监听该事件 → 切到 kol-pool 板块;KOLPoolPage 消费 pending id 开抽屉,
    // 抽屉 footer 自带 加入收藏 / 发起合作邀请(联系) / 补全档案 真动作。
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item"));
    onNavigate?.("kol-pool");
  };
  const copyEmail = () => {
    if (!email) return;
    try {
      void navigator.clipboard.writeText(email).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      });
    } catch { /* clipboard 不可用忽略 */ }
  };

  // ── 未选择 KOL:诚实引导态(不杜撰任何档案)──
  if (!enabled) {
    return (
      <div className="space-y-4 p-4">
        <div>
          <h2 className="text-base font-semibold text-white">KOL 档案</h2>
          <p className="mt-1 text-[12px] text-slate-400">3 秒读懂:他是谁 · 什么最灵 · 值多少钱。纯聚合已有数据,零新采集。</p>
        </div>
        <div className="rounded-xl border border-dashed border-white/[0.08] p-8 text-center">
          <div className="text-[13px] text-slate-300">{!apiToken ? "未登录 / 无 token" : "尚未选择 KOL"}</div>
          <div className="mt-2 text-[11px] text-slate-500">
            从 KOL Pool 行点入(写 sessionStorage[&quot;{PROFILE_ID_KEY}&quot;] 后派发 {OPEN_PROFILE_EVENT}),或直接输入池内 ID:
          </div>
          <div className="mx-auto mt-3 flex max-w-xs items-center gap-2">
            <input
              value={draftId}
              onChange={(e) => setDraftId(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyDraftId(); }}
              placeholder="kol_pool_id,如 42"
              inputMode="numeric"
              className="flex-1 rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-slate-200 placeholder:text-slate-600"
            />
            <button
              onClick={applyDraftId}
              disabled={!apiToken || !Number.isFinite(Number(draftId)) || Number(draftId) <= 0}
              className="rounded-md border border-sky-300/30 bg-sky-500/[0.12] px-3 py-1.5 text-[12px] text-sky-100 hover:bg-sky-500/[0.2] disabled:opacity-50"
            >
              打开档案
            </button>
          </div>
        </div>
      </div>
    );
  }

  const coopRow = asRow(cooperation.data);
  const coopEvents = asArray(coopRow?.events);
  const commentIntel = asRow(card.data?.comment_intelligence);
  const commentCounts = asRow(commentIntel?.counts);
  const sentimentDist = asRow(commentCounts?.sentiment);
  const attitudeDist = asRow(commentCounts?.brand_attitude);
  const deepSummary = asRow(deep.data?.summary);
  const deepPrimary = deep.data?.primary_result ?? null;
  const dims11 = deepPrimary ? asRow(deepPrimary.llm_dimensions_11) : null;

  return (
    <div className="space-y-4 p-4">
      {/* ═══ 头部:detail_bundle ═══ */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
        <RemoteBody
          state={bundle}
          errorHint={`档案主体加载失败:${bundle.error}`}
          render={() => (
            <div className="flex flex-wrap items-start gap-4">
              {avatarUrl
                ? <img src={avatarUrl} alt={displayName} className="h-14 w-14 shrink-0 rounded-full border border-white/10 object-cover" />
                : <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-lg text-slate-300">{(displayName || "?").slice(0, 1).toUpperCase()}</div>}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-semibold text-white">{displayName}</span>
                  {handle ? <span className="text-[12px] text-slate-400">@{handle}</span> : null}
                  {platform ? <Chip text={platform} tone="sky" /> : null}
                  {country ? <Chip text={country} /> : null}
                  <Chip text={fit !== null ? `Viltrox Fit ${Math.round(fit)}` : "Fit 未评"} tone={fitTone(fit)} />
                </div>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-slate-300">
                  <span>粉丝 <b className="text-white">{fmtCompact(followers)}</b></span>
                  <span>均播 <b className="text-white">{fmtCompact(avgViews)}</b></span>
                  <span>互动率 <b className="text-white">{engagementPct !== null ? `${engagementPct}%` : "—"}</b></span>
                  <span>本地视频证据 <b className="text-white">{videoCount || "—"}</b></span>
                </div>
                {/* 3 秒读懂条 */}
                <div className="mt-3 grid gap-2 text-[11px] sm:grid-cols-3">
                  <div className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"><span className="text-slate-500">他是谁 </span><span className="text-slate-200">{whoLine}</span></div>
                  <div className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"><span className="text-slate-500">什么最灵 </span><span className="text-slate-200">{signatureLine}</span></div>
                  <div className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"><span className="text-slate-500">值多少钱 </span><span className="text-slate-200">{worthLine}</span></div>
                </div>
              </div>
            </div>
          )}
        />
        {/* ═══ ⑧ 动作条:复用抽屉既有动作,直链不重造 ═══ */}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-3">
          <button onClick={openDrawer} className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.25]">
            收藏 / 联系 / 补全档案(打开 KOL Pool 抽屉)→
          </button>
          {profileUrl ? (
            <a href={profileUrl} target="_blank" rel="noreferrer" className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.08]">
              打开平台主页 ↗
            </a>
          ) : null}
          {email ? (
            <button onClick={copyEmail} className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.08]">
              {copied ? "已复制 ✓" : `复制邮箱 ${email}`}
            </button>
          ) : <span className="text-[11px] text-slate-600">邮箱未收录 · 抽屉里可添加联系方式</span>}
          <button onClick={() => setReloadKey((k) => k + 1)} className="ml-auto rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-slate-400 hover:text-slate-200">
            刷新
          </button>
        </div>
      </div>

      {/* ═══ 八层块 ═══ */}
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">

        {/* ① 身份 */}
        <Block no="①" title="身份" source="detail_bundle · vkpi_kol_pool">
          <RemoteBody
            state={bundle}
            errorHint={`加载失败:${bundle.error}`}
            render={() => (
              <div className="space-y-2">
                {bio ? <p className="text-[12px] leading-relaxed text-slate-300">{bio}</p> : <Hint text="bio 为空 · 等待补全档案" />}
                {profileUrl ? <KV label="主页" value={<a className="text-sky-300 hover:underline" href={profileUrl} target="_blank" rel="noreferrer">{profileUrl.length > 42 ? `${profileUrl.slice(0, 42)}…` : profileUrl}</a>} /> : null}
                {email ? <KV label="邮箱" value={email} /> : null}
                <KV label="语言" value={str(itemRow?.language) || "—"} />
                <KV label="来源" value={str(itemRow?.source_type) || "—"} />
                <KV label="内容量" value={num(itemRow?.posts_count) !== null ? fmtCompact(num(itemRow?.posts_count)) : "—"} />
                <KV label="入池时间" value={fmtDate(itemRow?.created_at)} />
                <KV label="最近可见" value={fmtDate(itemRow?.last_seen_at)} />
              </div>
            )}
          />
        </Block>

        {/* ② 内容 · 招牌面板(件①端点) */}
        <Block no="②" title="内容 · 招牌面板" source="/kol-pool/{id}/signature(件①)">
          <RemoteBody
            state={signature}
            errorHint={NOT_MOUNTED_HINT}
            render={(data) => <GenericRows data={data} />}
          />
        </Block>

        {/* ③ 受众 */}
        <Block no="③" title="受众画像(估算)" source="detail_bundle · audience_estimated">
          <RemoteBody
            state={bundle}
            errorHint={`加载失败:${bundle.error}`}
            render={() => (
              (audienceEstimated || audienceLanguages)
                ? (
                  <div className="space-y-2">
                    {audienceEstimated ? <GenericRows data={audienceEstimated} maxKeys={8} /> : null}
                    {audienceLanguages ? <GenericValue label="audience_languages" value={audienceLanguages} depth={0} /> : null}
                  </div>
                )
                : <Hint text="受众画像未估算 · 抽屉「补全档案」可点火全链(评论抽样→三层推断)" />
            )}
          />
        </Block>

        {/* ④ 商业:合作历史 + 制作周期(件②端点) */}
        <Block no="④" title="商业 · 合作与周期" source="/cooperation + /production-leadtime(件②)">
          <div className="space-y-3">
            <RemoteBody
              state={cooperation}
              errorHint={`合作历史暂不可用:${cooperation.error}`}
              render={() => (
                <div className="space-y-2">
                  <KV label="当前状态" value={coopLabel || "未设定"} />
                  {coopEvents.length > 0 ? (
                    <ul className="space-y-1">
                      {coopEvents.slice(0, 5).map((ev, i) => {
                        const row = asRow(ev);
                        return (
                          <li key={i} className="rounded-md border border-white/[0.06] px-2 py-1 text-[11px] text-slate-300/90">
                            <span className="text-slate-500">{fmtDate(row?.created_at)}</span>{" "}
                            {str(row?.action_type) || "动作"} → {str(row?.status_after) || "—"}
                            {str(row?.note) ? <span className="text-slate-500"> · {str(row?.note).slice(0, 40)}</span> : null}
                          </li>
                        );
                      })}
                    </ul>
                  ) : <Hint text="暂无合作记录 · 抽屉/详情里记一条即点亮" />}
                </div>
              )}
            />
            <div className="border-t border-white/[0.05] pt-2">
              <div className="mb-1.5 text-[11px] text-slate-500">制作周期(接单 → 发布)</div>
              <RemoteBody
                state={leadtime}
                errorHint={NOT_MOUNTED_HINT}
                render={(data) => <GenericRows data={data} maxKeys={6} />}
              />
            </div>
          </div>
        </Block>

        {/* ⑤ 信用:竞业 + 假粉 */}
        <Block no="⑤" title="信用 · 竞业与假粉" source="/competing-activity(兜底 /competitors)">
          <div className="space-y-3">
            <RemoteBody
              state={credit}
              errorHint={`竞业数据暂不可用:${credit.error}`}
              render={({ source, data }) => {
                if (source === "competitors") {
                  const relations = asArray(data.relations)
                    .map((r) => asRow(r))
                    .filter((r): r is Row => Boolean(r))
                    .filter((r) => (num(r.risk_score) ?? 0) > 0)
                    .sort((a, b) => (num(b.risk_score) ?? 0) - (num(a.risk_score) ?? 0));
                  if (relations.length === 0) return <Hint text="未检测到竞品品牌关联(本地信号)" />;
                  return (
                    <div className="space-y-1">
                      {relations.slice(0, 5).map((r, i) => {
                        const tier = str(r.risk_tier) || "opportunity";
                        return (
                          <div key={i} className="flex items-center justify-between gap-2 rounded-md border border-white/[0.06] px-2 py-1 text-[11px]">
                            <span className="truncate text-slate-200">{str(r.competitor_brand) || "品牌?"}</span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              <Chip text={tier} tone={RISK_TIER_TONE[tier] ?? "slate"} />
                              <span className="text-slate-400">risk {Math.round((num(r.risk_score) ?? 0) * 100) / 100}</span>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  );
                }
                return <GenericRows data={data} maxKeys={8} />;
              }}
            />
            <div className="border-t border-white/[0.05] pt-2">
              <div className="mb-1.5 text-[11px] text-slate-500">假粉嫌疑(真粉率口径)</div>
              {realFollowersPct !== null ? (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="w-20 shrink-0 text-slate-400">真粉估算</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-red-400/25">
                      <div className="h-full rounded-full bg-emerald-400/70" style={{ width: `${realFollowersPct}%` }} />
                    </div>
                    <span className="w-24 shrink-0 text-right text-slate-300">{realFollowersPct}% 真 / {Math.round((100 - realFollowersPct) * 10) / 10}% 疑</span>
                  </div>
                </div>
              ) : <Hint text="真粉率未收录(real_followers_pct 空)· 诚实不猜" />}
            </div>
          </div>
        </Block>

        {/* ⑥ 深析摘要 */}
        <Block no="⑥" title="深析摘要" source="llm-deep-analysis · llm_dimensions_11">
          <RemoteBody
            state={deep}
            errorHint={`深析读取失败:${deep.error}`}
            render={(data) => {
              if (data.status !== "ready" && !deepPrimary && !deepSummary) {
                return <Hint text="该 KOL 尚无深析结果 · 抽屉可入队视频深析(预算闸内)" />;
              }
              return (
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-1.5">
                    {deepPrimary && num(deepPrimary.llm_v6_fit) !== null ? <Chip text={`LLM v6 fit ${Math.round((num(deepPrimary.llm_v6_fit) ?? 0) * 100) / 100}`} tone="violet" /> : null}
                    {deepPrimary && num(deepPrimary.confidence) !== null ? <Chip text={`置信 ${Math.round((num(deepPrimary.confidence) ?? 0) * 100) / 100}`} /> : null}
                    {num(data.count) !== null ? <Chip text={`${data.count} 条分析`} /> : null}
                  </div>
                  {deepSummary ? <GenericRows data={deepSummary} maxKeys={6} /> : null}
                  {dims11 ? (
                    <div className="space-y-1">
                      <div className="text-[11px] text-slate-500">11 维(关键结论)</div>
                      {Object.entries(dims11).slice(0, 11).map(([k, v]) => {
                        const row = asRow(v);
                        const scoreN = num(v) ?? (row ? (num(row.score) ?? num(row.value)) : null);
                        const text = row ? (str(row.reason) || str(row.summary) || str(row.label)) : (typeof v === "string" ? v : "");
                        return (
                          <div key={k} className="flex items-start justify-between gap-2 text-[11px]">
                            <span className="shrink-0 text-slate-500">{k}</span>
                            <span className="min-w-0 text-right text-slate-300">
                              {scoreN !== null ? <b className="text-white">{Math.round(scoreN * 100) / 100}</b> : null}
                              {text ? <span className="ml-1">{text.length > 60 ? `${text.slice(0, 60)}…` : text}</span> : (scoreN === null ? "—" : null)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                  {!deepSummary && !dims11 ? <Hint text="有分析记录但无摘要字段(诚实空态)" /> : null}
                </div>
              );
            }}
          />
        </Block>

        {/* ⑦ 评论声音 */}
        <Block no="⑦" title="评论声音 · 意向分布" source="intelligence-card · vkpi_comments">
          <RemoteBody
            state={card}
            errorHint={`评论情报暂不可用:${card.error}`}
            render={() => {
              if (!commentIntel || str(commentIntel.status) === "empty" || str(commentIntel.status) === "not_configured") {
                return <Hint text="暂无已抓评论 · 抽屉可入队评论采集后点亮" />;
              }
              const questions = num(commentCounts?.questions) ?? 0;
              const opportunities = num(commentCounts?.opportunities) ?? 0;
              const issues = num(commentCounts?.issues) ?? 0;
              const cached = num(commentIntel.cached_comment_count) ?? 0;
              return (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-1.5">
                    <Chip text={`样本 ${cached} 条`} />
                    {questions > 0 ? <Chip text={`提问 ${questions}`} tone="sky" /> : null}
                    {opportunities > 0 ? <Chip text={`机会 ${opportunities}`} tone="emerald" /> : null}
                    {issues > 0 ? <Chip text={`吐槽 ${issues}`} tone="amber" /> : null}
                  </div>
                  {sentimentDist ? <DistBars title="情绪分布" dist={sentimentDist as Record<string, number>} /> : null}
                  {attitudeDist ? <DistBars title="品牌态度" dist={attitudeDist as Record<string, number>} /> : null}
                  {!sentimentDist && !attitudeDist && cached === 0 ? <Hint text="评论已配置但样本为 0(诚实空态)" /> : null}
                </div>
              );
            }}
          />
        </Block>
      </div>

      <p className="text-[10px] text-slate-600">
        件④ · 档案页为已有数据的只读聚合:零新采集 / 零 LLM 调用 / fit 分只读展示。远端块(②招牌、④周期、⑤竞业)依赖兄弟件端点,未挂载时显示待上线,不影响其余板块。
      </p>
    </div>
  );
}
