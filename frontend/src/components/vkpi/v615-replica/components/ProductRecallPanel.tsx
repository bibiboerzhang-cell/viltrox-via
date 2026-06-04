import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, Search, Target, UsersRound, Video } from "lucide-react";

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

function compactNumber(value: unknown): string {
  const next = n(value);
  if (!next) return "";
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 10_000) return `${Math.round(next / 1_000)}K`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

function compactFollowers(value: unknown): string {
  const next = n(value);
  if (!next) return "";
  if (next >= 100_000_000) return `${(next / 100_000_000).toFixed(1)}亿 粉丝`;
  if (next >= 10_000) return `${(next / 10_000).toFixed(1)}万 粉丝`;
  return `${Math.round(next).toLocaleString()} 粉丝`;
}

function cleanText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function arrayOfRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object" && !Array.isArray(entry))
    : [];
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(cleanText).filter(Boolean) : [];
}

function recallItems(value: unknown): VkpiKolRecallItem[] {
  return arrayOfRecords(value) as unknown as VkpiKolRecallItem[];
}

function initialFor(item: VkpiKolRecallItem): string {
  const text = cleanText(item.display_name || item.handle || item.platform || "K");
  return text.slice(0, 1).toUpperCase() || "K";
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

function RecallAvatar({ item }: { item: VkpiKolRecallItem }) {
  const [imageFailed, setImageFailed] = useState(false);
  const avatarUrl = cleanText(item.avatar_url);
  const fallback = (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/[0.08] bg-white/[0.04] text-[12px] font-semibold text-slate-300">
      {initialFor(item)}
    </span>
  );
  if (avatarUrl && !imageFailed) {
    return (
      <img
        src={avatarUrl}
        alt=""
        className="h-8 w-8 shrink-0 rounded-md object-cover"
        referrerPolicy="no-referrer"
        onError={(event) => {
          event.currentTarget.onerror = null;
          setImageFailed(true);
        }}
      />
    );
  }
  return fallback;
}

function EvidenceThumbnail({ src }: { src: string }) {
  const [imageFailed, setImageFailed] = useState(false);
  const thumb = cleanText(src);
  if (!thumb || imageFailed) return null;
  return (
    <div className="relative h-12 w-16 shrink-0 overflow-hidden rounded-md bg-black/30">
      <img
        src={thumb}
        alt=""
        className="h-full w-full object-cover"
        referrerPolicy="no-referrer"
        onError={(event) => {
          event.currentTarget.onerror = null;
          setImageFailed(true);
        }}
      />
      <span className="absolute bottom-0 left-0 bg-black/65 px-1 py-0.5 text-[8px] text-slate-200">代表作封面</span>
    </div>
  );
}

function EvidencePreview({ item }: { item: VkpiKolRecallItem }) {
  const evidence = arrayOfRecords(item.representative_evidence).filter((entry) => cleanText(entry.title || entry.content_url));
  if (!evidence.length) return null;
  const primary = evidence[0];
  const title = cleanText(primary.title || primary.content_url);
  const href = cleanText(primary.content_url);
  const thumb = cleanText(primary.thumbnail_url);
  const views = compactNumber(primary.view_count);
  const likes = compactNumber(primary.like_count);
  const Wrapper = href ? "a" : "div";
  return (
    <div className="mt-2">
      <Wrapper
        {...(href ? { href, target: "_blank", rel: "noreferrer" } : {})}
        className="group flex min-w-0 gap-2"
      >
        <EvidenceThumbnail src={thumb} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1 text-[9px] uppercase tracking-wide text-slate-600">
            代表作
            {href ? <ExternalLink size={10} className="text-slate-600 transition-colors group-hover:text-violet-200" /> : null}
          </div>
          <div className="mt-0.5 truncate text-[10.5px] text-slate-300 group-hover:text-violet-100">{title}</div>
          {views || likes ? (
            <div className="mt-0.5 text-[9.5px] text-slate-600">
              {views ? `${views} views` : ""}
              {views && likes ? " · " : ""}
              {likes ? `${likes} likes` : ""}
            </div>
          ) : null}
        </div>
      </Wrapper>
      {evidence.length > 1 ? (
        <div className="mt-1.5 grid gap-1">
          {evidence.slice(1, 3).map((entry, index) => {
            const nextTitle = cleanText(entry.title || entry.content_url);
            const nextHref = cleanText(entry.content_url);
            return nextHref ? (
              <a
                key={`${nextHref}-${index}`}
                href={nextHref}
                target="_blank"
                rel="noreferrer"
                className="truncate text-[10px] text-slate-500 hover:text-violet-200"
              >
                {nextTitle}
              </a>
            ) : (
              <div key={`${nextTitle}-${index}`} className="truncate text-[10px] text-slate-500">
                {nextTitle}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function RecallCard({ item }: { item: VkpiKolRecallItem }) {
  const profileHref = cleanText(item.profile_url);
  const handle = cleanText(item.handle || item.display_name || `KOL #${item.kol_pool_id}`);
  const reason = cleanText(item.recall_reason || item.type_reason || "画像匹配:索引画像与产品 query 相近");
  const lenses = stringList(item.used_lenses).slice(0, 3);
  const followers = compactFollowers(item.followers);
  const bio = cleanText(item.bio);
  return (
    <article className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <RecallAvatar item={item} />
          <div className="min-w-0">
            {profileHref ? (
              <a href={profileHref} target="_blank" rel="noreferrer" className="block truncate text-[12px] font-medium text-white hover:text-violet-100">
                {handle}
              </a>
            ) : (
              <div className="truncate text-[12px] font-medium text-white">{handle}</div>
            )}
            <div className="mt-0.5 truncate text-[10.5px] text-slate-500">
              {item.display_name || "未命名"} · {item.platform || "unknown"}
              {followers ? ` · ${followers}` : ""}
            </div>
            {bio ? <div className="mt-1 max-w-[280px] truncate text-[10px] text-slate-500">{bio}</div> : null}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="tabular-nums text-[12px] font-semibold text-violet-100">{fmt(item.recall_rank_score ?? item.vector_score)}</div>
          <div className="text-[9px] uppercase tracking-wide text-slate-600">召回排序分</div>
          <div className="mt-0.5 tabular-nums text-[9.5px] text-slate-500">vector {fmt(item.vector_score)}</div>
        </div>
      </div>
      <p className="mt-2 text-[10.5px] leading-4 text-slate-300">{reason}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <TypeBadge item={item} />
        <span className="rounded-md border border-white/[0.07] px-1.5 py-0.5 text-[10px] text-slate-400">
          C {fmt(item.creator_type_score, 1)}
        </span>
        <span className="rounded-md border border-white/[0.07] px-1.5 py-0.5 text-[10px] text-slate-400">
          R {fmt(item.reviewer_type_score, 1)}
        </span>
        {lenses.map((lens) => (
          <span key={lens} title={item.used_lenses_note || "从作品提取的镜头提及"} className="rounded-md border border-violet-300/15 bg-violet-400/[0.08] px-1.5 py-0.5 text-[10px] text-violet-100">
            {lens}
          </span>
        ))}
      </div>
      <EvidencePreview item={item} />
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
  const creatorItems = useMemo(() => recallItems(result?.buckets?.creator), [result]);
  const reviewerItems = useMemo(() => recallItems(result?.buckets?.reviewer), [result]);

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
