import { useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Search, Target, UsersRound, Video } from "lucide-react";

import {
  recallKolProfiles,
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
} from "../../../../domains/kol";

const DEFAULT_LAB_QUERY = `Product query profile: Viltrox AF 35mm F1.2 LAB.
Creator use cases: environmental portrait, street photography, documentary storytelling, wedding and engagement photography, low-light portrait, editorial fashion, hybrid photo and video, premium full-frame storyteller.
Desired creator profile: high quality people and scene storytelling, strong portrait or street portfolio, visible lens or camera review credibility, Viltrox or mirrorless lens experience, cinematic natural-light style, premium full-frame audience.`;

const DEFAULT_CREATOR_QUOTA = 7;
const DEFAULT_REVIEWER_QUOTA = 3;

function n(value: unknown): number {
  const next = Number(value);
  return Number.isFinite(next) ? next : 0;
}

function fmt(value: unknown, digits = 3): string {
  return n(value).toFixed(digits);
}

function typeTone(item: VkpiKolRecallItem): string {
  if (item.profile_type === "mixed") return "border-amber-300/30 bg-amber-400/[0.10] text-amber-200";
  if (item.profile_type === "creator") return "border-emerald-300/30 bg-emerald-400/[0.10] text-emerald-200";
  return "border-sky-300/30 bg-sky-400/[0.10] text-sky-200";
}

function bucketLabel(bucket: string): string {
  return bucket === "creator" ? "创作者桶" : "测评号桶";
}

function TypeBadge({ item }: { item: VkpiKolRecallItem }) {
  return (
    <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] ${typeTone(item)}`}>
      {item.type_label || (item.profile_type === "creator" ? "创作者" : "测评号")}
      {item.profile_type === "mixed" ? ` -> ${bucketLabel(item.bucket)}` : ""}
    </span>
  );
}

function RecallCard({ item }: { item: VkpiKolRecallItem }) {
  return (
    <article className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[12px] font-medium text-white">{item.handle || item.display_name || `KOL #${item.kol_pool_id}`}</div>
          <div className="mt-0.5 truncate text-[10.5px] text-slate-500">{item.display_name || "未命名"} · {item.platform || "unknown"}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="tabular-nums text-[12px] font-semibold text-violet-100">{fmt(item.vector_score)}</div>
          <div className="text-[9px] uppercase tracking-wide text-slate-600">vector</div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <TypeBadge item={item} />
        <span className="rounded-md border border-white/[0.07] px-1.5 py-0.5 text-[10px] text-slate-400">
          C {fmt(item.creator_type_score, 1)}
        </span>
        <span className="rounded-md border border-white/[0.07] px-1.5 py-0.5 text-[10px] text-slate-400">
          R {fmt(item.reviewer_type_score, 1)}
        </span>
      </div>
    </article>
  );
}

function RecallBucket({
  title,
  icon,
  tone,
  items,
}: {
  title: string;
  icon: "creator" | "reviewer";
  tone: string;
  items: VkpiKolRecallItem[];
}) {
  const Icon = icon === "creator" ? UsersRound : Video;
  return (
    <section className="min-w-0 rounded-lg border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`flex h-6 w-6 items-center justify-center rounded-md ${tone}`}>
            <Icon size={13} />
          </span>
          <h3 className="text-[12px] font-semibold text-white">{title}</h3>
        </div>
        <span className="tabular-nums text-[10px] text-slate-500">{items.length}</span>
      </div>
      <div className="grid gap-2">
        {items.map((item) => (
          <RecallCard key={`${item.bucket}-${item.kol_pool_id}`} item={item} />
        ))}
        {!items.length ? (
          <div className="rounded-lg border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">暂无结果</div>
        ) : null}
      </div>
    </section>
  );
}

export function ProductRecallPanel({ apiToken = "" }: { apiToken?: string }) {
  const [queryText, setQueryText] = useState(DEFAULT_LAB_QUERY);
  const [result, setResult] = useState<VkpiKolRecallResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const diagnostics = result?.diagnostics || {};
  const creatorItems = useMemo(() => result?.buckets?.creator || [], [result]);
  const reviewerItems = useMemo(() => result?.buckets?.reviewer || [], [result]);

  const runRecall = async (nextQuery = queryText) => {
    if (!apiToken) {
      setError("未登录 / 无 token");
      setResult(null);
      return;
    }
    const cleanQuery = String(nextQuery || "").trim();
    if (!cleanQuery) {
      setError("query 为空");
      setResult(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await recallKolProfiles(apiToken, {
        queryText: cleanQuery,
        candidateLimit: 50,
        limit: 10,
        creatorQuota: DEFAULT_CREATOR_QUOTA,
        reviewerQuota: DEFAULT_REVIEWER_QUOTA,
        ratioPolicy: "soft",
        mixedPolicy: "dominant",
        dedupe: true,
      });
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "产品召回接口失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!apiToken) return;
    void runRecall(DEFAULT_LAB_QUERY);
  }, [apiToken]);

  return (
    <section className="mb-4 rounded-lg border border-violet-300/[0.12] bg-violet-950/[0.08] p-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md border border-violet-300/20 bg-violet-400/[0.10] text-violet-200">
              <Target size={14} />
            </span>
            <div>
              <h2 className="text-[13px] font-semibold text-white">产品召回结果</h2>
              <div className="mt-0.5 text-[10.5px] text-slate-500">
                creator {DEFAULT_CREATOR_QUOTA} : reviewer {DEFAULT_REVIEWER_QUOTA} · {result?.method || "vector_recall"}
              </div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
          <span className="rounded-md border border-white/[0.07] px-2 py-1">候选 {String(diagnostics.candidate_count ?? "--")}</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">创作者 {String(diagnostics.creator_returned ?? "--")}</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">测评号 {String(diagnostics.reviewer_returned ?? "--")}</span>
        </div>
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-[180px_minmax(0,1fr)_auto]">
        <select
          value="35mm_lab"
          onChange={() => {
            setQueryText(DEFAULT_LAB_QUERY);
            void runRecall(DEFAULT_LAB_QUERY);
          }}
          className="rounded-md border border-white/[0.08] bg-white/[0.025] px-2 py-2 text-[11px] text-white outline-none"
        >
          <option value="35mm_lab">35mm F1.2 LAB</option>
        </select>
        <textarea
          value={queryText}
          onChange={(event) => setQueryText(event.target.value)}
          rows={2}
          className="min-h-[42px] rounded-md border border-white/[0.08] bg-black/20 px-3 py-2 text-[11px] leading-5 text-slate-300 outline-none placeholder-slate-600 focus:border-violet-400/40"
        />
        <button
          type="button"
          onClick={() => void runRecall()}
          disabled={loading || !apiToken}
          className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-md border border-violet-300/20 bg-violet-500/[0.16] px-3 text-[11px] font-medium text-violet-100 transition-colors hover:bg-violet-500/[0.24] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : result ? <RefreshCw size={13} /> : <Search size={13} />}
          召回
        </button>
      </div>

      {error ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-200">{error}</div>
      ) : null}

      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <RecallBucket
          title="创作者"
          icon="creator"
          tone="bg-emerald-400/[0.10] text-emerald-200"
          items={creatorItems}
        />
        <RecallBucket
          title="测评号"
          icon="reviewer"
          tone="bg-sky-400/[0.10] text-sky-200"
          items={reviewerItems}
        />
      </div>
    </section>
  );
}
