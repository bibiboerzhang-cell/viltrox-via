// #21 trend-pulse:近期行业帖 hashtag 实时聚合(自取数;数据稀疏如实显示,不编造)。
import React, { useEffect, useState } from "react";
import { AlertTriangle, Database, Flame, Loader2 } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type TrendState = "idle" | "loading" | "empty" | "error" | "ready";

type TrendItem = {
  hashtag?: string;
  platform?: string;
  post_count?: number;
  engagement?: number;
  evidence_count?: number;
  source_label?: string;
};

type TrendResponse = {
  status?: string;
  available?: boolean;
  reason?: string;
  generated_at?: string;
  source?: string;
  source_label?: string;
  window_days?: number;
  evidence_count?: number;
  posts_scanned?: number;
  trends?: TrendItem[];
  summary?: {
    total_hashtags?: number;
    window_days?: number;
    source?: string;
    source_label?: string;
    evidence_count?: number;
    evidence_rows?: number;
    posts_scanned?: number;
    post_count?: number;
  };
};

function finiteNonNegative(...values: unknown[]) {
  for (const value of values) {
    if (value == null || value === "" || typeof value === "boolean") continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric >= 0) return Math.floor(numeric);
  }
  return null;
}

function errorMessage(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error || "");
  return raw.trim().slice(0, 120) || "读取失败";
}

export function TrendPulseBar({ apiToken = "" }: { apiToken?: string }) {
  const [data, setData] = useState<TrendResponse | null>(null);
  const [state, setState] = useState<TrendState>(apiToken ? "loading" : "idle");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!apiToken) {
      setData(null);
      setError("");
      setState("idle");
      return;
    }
    let alive = true;
    setData(null);
    setError("");
    setState("loading");
    apiFetch<TrendResponse>("/api/admin/vkpi/industry-data/hashtag-trends/v0?limit=12&days=14", {}, apiToken)
      .then((response) => {
        if (!alive) return;
        if (!response || response.available === false || String(response.status || "").toLowerCase() === "error") {
          throw new Error(response?.reason || "热词源暂不可用");
        }
        const trends = Array.isArray(response.trends) ? response.trends : [];
        setData(response);
        setState(trends.length ? "ready" : "empty");
      })
      .catch((reason) => {
        if (!alive) return;
        setData(null);
        setError(errorMessage(reason));
        setState("error");
      });
    return () => { alive = false; };
  }, [apiToken]);

  if (!apiToken) return null;

  const trends = data && Array.isArray(data.trends) ? data.trends : [];
  const summary = data?.summary || {};
  const total = finiteNonNegative(summary.total_hashtags, trends.length) ?? trends.length;
  const windowDays = finiteNonNegative(summary.window_days, data?.window_days, 14) ?? 14;
  const sourceLabel = String(
    summary.source_label
      || summary.source
      || data?.source_label
      || data?.source
      || "行业帖",
  );
  const platformCount = new Set(
    trends.map((trend) => String(trend.platform || "").trim().toLowerCase()).filter(Boolean),
  ).size;
  const exactEvidenceCount = finiteNonNegative(
    summary.evidence_count,
    summary.evidence_rows,
    summary.posts_scanned,
    summary.post_count,
    data?.evidence_count,
    data?.posts_scanned,
  );
  const tagHitCount = trends.reduce(
    (sum, trend) => sum + (finiteNonNegative(trend.evidence_count, trend.post_count) ?? 0),
    0,
  );
  const evidenceLabel = exactEvidenceCount != null
    ? `证据 ${exactEvidenceCount} 条`
    : state === "ready"
      ? `标签命中 ${tagHitCount} 次`
      : state === "empty"
        ? "证据 0 条"
        : "证据 --";
  const sourceMeta = `${sourceLabel}${platformCount ? ` · ${platformCount} 平台` : ""} · 近 ${windowDays} 天 · ${evidenceLabel}`;
  const headline = state === "loading"
    ? "聚合中"
    : state === "error"
      ? "读取失败"
      : state === "empty"
        ? "暂无热词"
        : `${total} 个热词`;

  let content: React.ReactNode;
  if (state === "loading") {
    content = e("div", { className: "flex min-w-0 items-center gap-2 text-[10px] text-muted" },
      e(Loader2, { size: 13, className: "shrink-0 animate-spin text-rose-300" }),
      e("span", null, "正在聚合真实行业帖…"),
    );
  } else if (state === "error") {
    content = e("div", { className: "flex min-w-0 items-center gap-2 rounded-md border border-red-400/20 bg-red-500/[0.06] px-2 py-1 text-[10px] text-crit" },
      e(AlertTriangle, { size: 12, className: "shrink-0" }),
      e("span", { className: "shrink-0 font-medium" }, "热词源暂不可用"),
      e("span", { className: "min-w-0 truncate text-muted", title: error }, error),
    );
  } else if (state === "empty") {
    content = e("div", { className: "flex min-w-0 items-center gap-2 rounded-md border border-line bg-white/[0.02] px-2 py-1 text-[10px] text-muted" },
      e(Database, { size: 12, className: "shrink-0" }),
      e("span", { className: "font-medium text-ink-2" }, `近 ${windowDays} 天暂无行业帖热词`),
      e("span", { className: "truncate" }, "真实行业帖入库后自动出现"),
    );
  } else {
    content = e("div", { className: "flex min-w-0 flex-wrap gap-1.5", "aria-label": "热词证据" },
      trends.slice(0, 12).map((trend, index) => {
        const hashtag = String(trend.hashtag || "").trim() || "--";
        const postCount = finiteNonNegative(trend.post_count, trend.evidence_count) ?? 0;
        const platform = String(trend.platform || "").trim();
        return e("span", {
          key: `${platform || "all"}:${hashtag}:${index}`,
          className: "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-medium",
          style: { background: "rgba(239,68,68,0.06)", borderColor: "rgba(239,68,68,0.2)", color: "#fda4af" },
          title: `${postCount} 帖${platform ? " · " + platform : ""}${trend.engagement != null ? ` · 互动 ${trend.engagement}` : ""}`,
        },
        e("span", null, `#${hashtag}`),
        postCount ? e("span", { className: "text-[9px] text-rose-200/70" }, `·${postCount}`) : null,
        platform ? e("span", { className: "rounded bg-white/[0.05] px-1 text-[8px] uppercase text-muted" }, platform) : null,
        );
      }),
    );
  }

  return e("section", {
    className: "grid gap-2 rounded-xl border border-line bg-panel p-3 backdrop-blur-xl md:grid-cols-[auto_minmax(0,1fr)] md:items-center",
    "data-state": state,
    "aria-label": "近期市场热词",
    title: data?.generated_at ? `聚合于 ${data.generated_at}` : undefined,
  },
    e("div", { className: "flex min-w-[190px] shrink-0 items-center gap-2" },
      e("div", { className: "rounded-md p-1.5", style: { background: "rgba(239,68,68,0.18)" } }, e(Flame, { size: 12, className: "text-rose-400" })),
      e("div", { className: "min-w-0" },
        e("div", { className: "text-[11px] font-semibold text-ink" }, "近期市场热词"),
        e("div", { className: "flex flex-wrap items-center gap-x-1 text-[9px] text-muted" },
          e("span", { className: state === "error" ? "text-crit" : state === "ready" ? "text-rose-300" : "" }, headline),
          e("span", { "aria-label": "热词取数口径" }, `· ${sourceMeta}`),
        ),
      )
    ),
    content,
  );
}
