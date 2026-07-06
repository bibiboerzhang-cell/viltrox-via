import React from "react";
import { apiFetch } from "../../../../services/http";

// D 段级 · 创意资产库 v0(只读检索页)。
//   「哪个开头/哪种画面最灵」:已深析(final_v1)视频拆成段级条目(开头/分镜/产品露出),
//   三路词表过滤(自由词 query / 拍法 style / 焦段 focal),按所属视频播放数降序。
//   后端:/api/admin/vkpi/creative-segments/search。纯已有分析文本的索引,零切片零 LLM。
//   缺数据诚实空态:深析库空/过滤零命中都明说,绝不杜撰。
//   白名单使用权请求进外联模板=后续刀,本页只读检索。

type Row = Record<string, any>;

const SEGMENT_TYPE_LABEL: Record<string, string> = {
  opening: "开头段",
  scene: "分镜段",
  product_exposure: "产品露出段",
};

const SEGMENT_TYPE_TONE: Record<string, string> = {
  opening: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100",
  scene: "border-white/[0.1] bg-white/[0.04] text-slate-200",
  product_exposure: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
};

// 静态兜底(与后端 SHOOTING_MODES 词表同口径);facets 回来后用真实计数覆盖
const STYLE_FALLBACK: { key: string; label: string }[] = [
  { key: "cinematic_broll", label: "电影感B-roll" },
  { key: "talking_review", label: "口播评测" },
  { key: "pov", label: "POV第一视角" },
  { key: "tutorial", label: "教程演示" },
  { key: "street", label: "街拍实测" },
  { key: "unboxing", label: "开箱上手" },
  { key: "sample_showcase", label: "样片展示" },
];

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-white/[0.08] px-3 py-4 text-center text-[12px] text-slate-500">{text}</div>;
}

function Tag({ text, tone }: { text: string; tone?: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${tone || "border-white/[0.1] bg-white/[0.03] text-slate-300"}`}>
      {text}
    </span>
  );
}

export function CreativeLibraryPage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [query, setQuery] = React.useState("");
  const [style, setStyle] = React.useState("");
  const [focal, setFocal] = React.useState("");
  const [result, setResult] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  // 三筛选任一变化 → 300ms 防抖重查;首载空筛选拉全库 TOP
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    const timer = window.setTimeout(() => {
      const qs = new URLSearchParams({
        query: query.trim(),
        style: style.trim(),
        focal: focal.trim(),
        limit: "30",
      });
      apiFetch<Row>(`/api/admin/vkpi/creative-segments/search?${qs.toString()}`, { timeoutMs: 45000 }, apiToken)
        .then((res) => {
          if (alive) setResult(res);
        })
        .catch((err: any) => {
          if (alive) setError(String(err?.detail || err?.message || "加载失败"));
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, query, style, focal]);

  const items: Row[] = Array.isArray(result?.items) ? (result?.items as Row[]) : [];
  const facets: Row = result?.facets || {};
  const facetStyles: Row[] = Array.isArray(facets.styles) ? facets.styles : [];
  const facetFocals: Row[] = Array.isArray(facets.focals) ? facets.focals : [];
  const styleOptions: { key: string; label: string; count?: number }[] =
    facetStyles.length > 0
      ? facetStyles.map((f) => ({ key: String(f.key), label: String(f.label || f.key), count: Number(f.segment_count) || 0 }))
      : STYLE_FALLBACK;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div>
        <div className="text-[18px] font-semibold text-white">创意资产库</div>
        <div className="mt-0.5 text-[12px] text-slate-400">
          「哪个开头/哪种画面最灵」—— 已深析视频拆成段级条目(开头/分镜/产品露出),词表过滤检索,按所属视频播放数排序。
        </div>
        <div className="mt-1 text-[11px] text-amber-200/80">
          白名单使用权请求进外联模板=后续刀,本页只读检索;条目均为已有 final_v1 分析文本的索引(零切片文件、零 LLM)。
        </div>
      </div>

      {/* 三筛选 */}
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <input
          value={query}
          onChange={(ev) => setQuery(ev.target.value)}
          placeholder="自由词,如 悬念 / 对比 / bokeh / 夜景…"
          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[13px] text-white placeholder:text-slate-500 outline-none focus:border-sky-300/40"
        />
        <select
          value={style}
          onChange={(ev) => setStyle(ev.target.value)}
          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[13px] text-white outline-none focus:border-sky-300/40 [&>option]:bg-[#10151f]"
        >
          <option value="">全部拍法</option>
          {styleOptions.map((opt) => (
            <option key={opt.key} value={opt.label}>
              {opt.label}
              {opt.count != null && opt.count > 0 ? `(${opt.count} 段)` : ""}
            </option>
          ))}
        </select>
        <input
          value={focal}
          onChange={(ev) => setFocal(ev.target.value)}
          placeholder={`焦段,如 85mm${facetFocals.length > 0 ? ` · 库里最多:${facetFocals.slice(0, 3).map((f) => f.focal).join(" / ")}` : ""}`}
          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-[13px] text-white placeholder:text-slate-500 outline-none focus:border-sky-300/40"
        />
      </div>

      {!apiToken && <EmptyLine text="未登录 / 无 token,无法加载数据。" />}
      {error && <div className="rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200">{error}</div>}
      {loading && <div className="py-10 text-center text-[12px] text-slate-400">段级索引检索中…</div>}

      {!loading && result && result.status === "error" && (
        <EmptyLine text={`检索暂不可用:${String(result.reason || "未知原因")}`} />
      )}
      {!loading && result && result.status === "empty" && (
        <EmptyLine text={String(result.reason || "深析库为空,段级资产无从索引。")} />
      )}

      {!loading && result && result.status === "ready" && (
        <div className="space-y-3">
          <div className="text-[11px] text-slate-500">
            扫描 {fmtNum(result.scanned_videos)} 条深析视频 · 索引 {fmtNum(result.segment_count)} 个段级条目 · 命中{" "}
            {fmtNum(result.matched)} 段(展示前 {items.length} 段,按视频播放数降序)
          </div>

          {items.length === 0 ? (
            <EmptyLine text="三路过滤零命中 —— 词表规则不硬凑(诚实空态);换个词,或用上方下拉里真实存在的拍法/焦段。" />
          ) : (
            <div className="space-y-2">
              {items.map((it) => {
                const video: Row = it.video || {};
                const kol: Row = it.kol || {};
                const source: Row = it.source || {};
                const styles: Row[] = Array.isArray(it.styles) ? it.styles : [];
                const videoStyles: Row[] = Array.isArray(it.video_styles) ? it.video_styles : [];
                const openings: Row[] = Array.isArray(it.opening_types) ? it.opening_types : [];
                const focals: string[] = Array.isArray(it.focals) ? it.focals : [];
                return (
                  <div key={String(it.segment_id)} className="rounded-2xl border border-white/[0.06] bg-white/[0.015] px-4 py-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={`rounded-md border px-2 py-0.5 text-[10px] ${SEGMENT_TYPE_TONE[String(it.segment_type)] || "border-white/[0.1] text-slate-300"}`}>
                        {SEGMENT_TYPE_LABEL[String(it.segment_type)] || it.segment_type}
                      </span>
                      {it.timestamp ? <span className="text-[10px] text-slate-500">{it.timestamp}</span> : null}
                      {openings.map((t) => (
                        <Tag key={`o-${t.key}`} text={String(t.label)} tone="border-violet-300/30 bg-violet-500/[0.12] text-violet-100" />
                      ))}
                      {styles.map((t) => (
                        <Tag key={`s-${t.key}`} text={String(t.label)} tone="border-emerald-300/30 bg-emerald-500/[0.1] text-emerald-100" />
                      ))}
                      {videoStyles
                        .filter((t) => !styles.some((s) => s.key === t.key))
                        .map((t) => (
                          <Tag key={`vs-${t.key}`} text={`全片:${String(t.label)}`} />
                        ))}
                      {focals.map((f) => (
                        <Tag key={`f-${f}`} text={f} tone="border-sky-300/30 bg-sky-500/[0.1] text-sky-100" />
                      ))}
                      <span className="ml-auto text-[11px] text-slate-400">{fmtNum(video.view_count)} views</span>
                    </div>

                    <div className="mt-2 text-[12px] leading-relaxed text-slate-200">{String(it.description || "")}</div>

                    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                      {video.content_url ? (
                        <a href={String(video.content_url)} target="_blank" rel="noreferrer" className="max-w-[360px] truncate text-sky-300 hover:underline">
                          {String(video.title || video.content_url)} ↗
                        </a>
                      ) : (
                        <span className="max-w-[360px] truncate text-slate-300">{String(video.title || "—")}</span>
                      )}
                      <span className="text-slate-500">{String(video.platform || "—")}</span>
                      <span className="text-slate-400">{String(kol.display_name || kol.handle || `KOL ${kol.kol_pool_id ?? "—"}`)}</span>
                      {video.posted_at ? <span className="text-slate-600">{String(video.posted_at).slice(0, 10)}</span> : null}
                      <span
                        className="ml-auto rounded border border-white/[0.06] px-1.5 py-0.5 text-[9px] text-slate-500"
                        title={`可追溯:evidence ${source.evidence_id} · ${source.derive_method}`}
                      >
                        evidence {String(source.evidence_id ?? "—")} · {String(source.layer || "—")}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {result.note ? <div className="text-[10px] text-slate-600">{String(result.note)}</div> : null}
          <div className="text-right text-[10px] text-slate-600">生成于 {String(result.generated_at || "—")}(UTC)· 纯聚合已有数据</div>
        </div>
      )}
    </div>
  );
}
