import React from "react";
import { ChevronDown, ChevronRight, TrendingDown, TrendingUp } from "lucide-react";
import { EmptyLine, QuoteFold } from "./MarketVoicePage.modules";
import { BarRow, pctText } from "./MarketVoicePage.charts";
import { ConfChip, fmtNum, fmtPct } from "./StrategyDeskPage.modules";
import type { BenchResp, BrandItem, GridCell, H2HItem, NoGoItem, OppItem, TrackItem, TracksResp } from "./StrategyDeskPage.modules";

// 战略台 · 图表模块 body 族(对照三块 + 赛道四块)。
//   金样板 = MarketVoicePage.charts 同构:BarRow/pctText 复用零重造;
//   数据 page 层注入,本文件零直连网络;口径句全部住 MODULE_SOURCES(卡面零术语)。
// 红线:纯读展示;不触 viltrox_fit_score / rule_v0;机会分公式/权重原文不渲染
//   (显示层宪法);颜色全 token / CSS var,零写死色;空态诚实短句。

/* ============ rank · 声量份额排名条(Viltrox 高亮;联动品牌标记) ============ */

export function RankBody({
  viltrox,
  competitors,
  focusBrand,
}: {
  viltrox: BrandItem;
  competitors: BrandItem[];
  /** 市场之声联动位:该品牌行加「联动」徽(不改排序,只标记) */
  focusBrand?: string;
}) {
  const ranked = [viltrox, ...competitors]
    .filter((b) => (Number(b.videos) || 0) > 0)
    .sort((a, b) => (Number(a.rank) || 999) - (Number(b.rank) || 999));
  if (ranked.length === 0) return <EmptyLine text="窗口内无品牌声量。" />;
  const maxVideos = ranked.reduce((acc, b) => Math.max(acc, Number(b.videos) || 0), 0);
  const focus = (focusBrand || "").trim().toLowerCase();
  return (
    <div>
      {ranked.slice(0, 8).map((b) => {
        const self = b.key === "viltrox";
        const linked = !self && focus.length > 0 && (String(b.brand || "").toLowerCase() === focus || String(b.key || "").toLowerCase() === focus);
        const videos = Number(b.videos) || 0;
        const widthPct = maxVideos > 0 ? Math.max(videos > 0 ? 4 : 0, (videos / maxVideos) * 100) : 0;
        const share = typeof b.share_of_voice === "number" ? b.share_of_voice : null;
        return (
          <BarRow
            key={String(b.key || b.brand)}
            name={`#${b.rank ?? "—"} ${b.brand || b.key || "—"}${linked ? " ◈" : ""}`}
            highlight={self}
            widthPct={widthPct}
            color={self ? "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))" : linked ? "var(--ds-accent-2)" : "var(--ds-muted)"}
            value={`${share != null ? pctText(share) + "%" : "—"} · ×${videos} · ${Number(b.kol_count) || 0}人`}
            title={linked ? `${b.brand}:来自市场之声的联动品牌` : `${b.brand || b.key}:提及视频 ${videos} 条 · 独立 KOL ${Number(b.kol_count) || 0} 人`}
          />
        );
      })}
    </div>
  );
}

/* ============ h2h · Viltrox vs 竞品(行可展开:三行对比 + 质量侧写 + 结论 + 例证) ============ */

function H2HDetail({ item, comp }: { item: H2HItem; comp?: BrandItem }) {
  const rows = Array.isArray(item.rows) ? item.rows : [];
  const examples = (comp && Array.isArray(comp.top_examples) ? comp.top_examples : []).slice(0, 3);
  const eng = comp?.engagement;
  return (
    <div className="border-t border-line bg-panel px-3 py-2">
      <div className="grid grid-cols-[1fr_72px_72px] gap-x-2 text-[10px]">
        <span className="text-muted">指标</span>
        <span className="text-right font-semibold text-accent">Viltrox</span>
        <span className="text-right text-ink-2">{item.brand || "竞品"}</span>
        {rows.map((r, i) => (
          <React.Fragment key={r.metric || i}>
            <span className="mt-1 text-ink-2" title={r.metric}>{r.label || r.metric || "—"}</span>
            <span className="mt-1 text-right font-mono tabular-nums text-ink">{fmtNum(r.viltrox)}</span>
            <span className="mt-1 text-right font-mono tabular-nums text-ink-2">{fmtNum(r.competitor)}</span>
          </React.Fragment>
        ))}
      </div>
      {eng ? (
        <div className="mt-1.5 text-[10px] text-muted">
          {`内容质量侧写:被提及视频均互动率 ${fmtPct(eng.avg_rate)}(样本 ${Number(eng.sample) || 0} 条` +
            (eng.confidence === "low" ? ",样本偏小低置信" : eng.confidence === "none" ? ",播放数缺失无样本" : "") + ")"}
        </div>
      ) : null}
      {item.verdict ? (
        <div className="mt-1.5 rounded-lg border border-warn bg-warn-soft px-2 py-1 text-[10.5px] leading-relaxed text-ink-2">{item.verdict}</div>
      ) : null}
      {examples.length > 0 ? (
        <div className="mt-1.5 space-y-0.5">
          <div className="text-[9.5px] text-muted">例证视频(按播放排序):</div>
          {examples.map((ex, i) => (
            <div key={ex.evidence_id || i} className="flex items-center gap-1.5 text-[10px]">
              {ex.content_url ? (
                <a
                  href={ex.content_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="truncate text-ink-2 transition-colors hover:text-accent hover:underline"
                  title={ex.title || ex.content_url}
                >
                  {ex.title || ex.content_url}
                </a>
              ) : (
                <span className="truncate text-ink-2">{ex.title || "(无标题)"}</span>
              )}
              <span className="flex-none font-mono tabular-nums text-muted">
                {fmtNum(ex.view_count)}
                {ex.kol_name ? ` · ${ex.kol_name}` : ""}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function H2HBody({
  h2h,
  compByKey,
  openKey,
  onToggle,
}: {
  h2h: H2HItem[];
  compByKey: Record<string, BrandItem>;
  /** 展开态由 page 层持有(联动品牌可从外部直接点亮某行) */
  openKey: string | null;
  onToggle: (key: string) => void;
}) {
  if (h2h.length === 0) return <EmptyLine text="窗口内无可对照竞品。" />;
  return (
    <div>
      {h2h.slice(0, 10).map((h, i) => {
        const key = String(h.key || i);
        const open = openKey === key;
        return (
          <div key={key} className="border-b border-line last:border-b-0">
            <button
              type="button"
              className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-accent-soft"
              onClick={() => onToggle(key)}
            >
              {open ? <ChevronDown size={11} className="flex-none text-muted" /> : <ChevronRight size={11} className="flex-none text-muted" />}
              <span className="w-[76px] flex-none truncate text-[11.5px] text-ink">{h.brand || h.key || "—"}</span>
              <span className="min-w-0 flex-1 truncate text-[10px] text-muted" title={h.verdict}>{h.verdict || ""}</span>
            </button>
            {open ? <H2HDetail item={h} comp={h.key ? compByKey[h.key] : undefined} /> : null}
          </div>
        );
      })}
    </div>
  );
}

/* ============ focal · 焦段格局热区(红=SKU 空档 / 黄=有货无声 / 亮=双方有声) ============ */

function FocalCell({ cell }: { cell: GridCell }) {
  const skuWeak = !!cell.sku_weak;
  const voiceWeak = !!cell.voice_weak;
  const both = (Number(cell.viltrox_videos) || 0) > 0;
  const cls = skuWeak
    ? "border-crit bg-crit-soft text-crit"
    : voiceWeak
      ? "border-warn bg-warn-soft text-warn"
      : both
        ? "border-accent bg-accent-soft text-accent"
        : "border-line bg-card text-muted";
  const tip =
    `${cell.focal}:竞品声量 ${Number(cell.competitor_videos) || 0} 条` +
    ((cell.competitor_brands || []).length ? `(${(cell.competitor_brands || []).join("/")})` : "") +
    ` · 我方声量 ${Number(cell.viltrox_videos) || 0} 条 · 官方 SKU ${Number(cell.official_sku_count) || 0} 个` +
    (cell.flagship ? ` · 旗舰 ${cell.flagship}` : "") +
    (skuWeak ? " —— 竞品有声量而我方 SKU 覆盖弱" : voiceWeak ? " —— 有货无声(有 SKU 但零内容声量)" : "");
  return (
    <span className={`rounded-md border px-1.5 py-0.5 font-mono text-[9.5px] tabular-nums ${cls}`} title={tip}>
      {`${cell.focal} ${Number(cell.competitor_videos) || 0}v${Number(cell.viltrox_videos) || 0}`}
    </span>
  );
}

export function FocalBody({ grid }: { grid: NonNullable<BenchResp["focal_grid"]> }) {
  const cells = Array.isArray(grid.cells) ? grid.cells : [];
  if (grid.status === "empty" || cells.length === 0) {
    return <EmptyLine text={String(grid.reason || "焦段格局暂无数据。")} />;
  }
  return (
    <div>
      <div className="flex flex-wrap gap-1">
        {cells.map((c, i) => (
          <FocalCell key={c.focal || i} cell={c} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9.5px]">
        <span className="text-crit">■ 竞品有声量 · 我方零官方 SKU</span>
        <span className="text-warn">■ 有货无声</span>
        <span className="text-accent">■ 双方都有声量</span>
      </div>
    </div>
  );
}

/* ============ 赛道 · 机会分档色(good/warn/muted 三档,token 化) ============ */

function scoreBg(score: number): string {
  if (score >= 40) return "color-mix(in srgb, var(--ds-good) 18%, transparent)";
  if (score >= 20) return "color-mix(in srgb, var(--ds-warn) 15%, transparent)";
  if (score > 0) return "color-mix(in srgb, var(--ds-muted) 12%, transparent)";
  return "color-mix(in srgb, var(--ds-muted) 5%, transparent)";
}

function scoreText(score: number): string {
  if (score >= 40) return "text-good";
  if (score >= 20) return "text-warn";
  if (score > 0) return "text-ink-2";
  return "text-muted";
}

function MomBadge({ trend, momPct }: { trend: string; momPct: number }) {
  if (trend === "rising") {
    return (
      <span className="inline-flex items-center gap-0.5 text-good">
        <TrendingUp size={10} strokeWidth={2} />
        <span className="font-mono text-[9px] tabular-nums">{(momPct > 0 ? "+" : "") + momPct}%</span>
      </span>
    );
  }
  if (trend === "falling") {
    return (
      <span className="inline-flex items-center gap-0.5 text-crit">
        <TrendingDown size={10} strokeWidth={2} />
        <span className="font-mono text-[9px] tabular-nums">{momPct}%</span>
      </span>
    );
  }
  return <span className="text-[9px] text-muted">环比平稳</span>;
}

/* ============ 选中赛道证据详情(四信号 + 红格 + 原声 + 竞品例证;公式不渲染) ============ */

function TrackDetail({ track }: { track: TrackItem }) {
  const d = track.demand || {};
  const c = track.coverage || {};
  const comp = track.competitors || {};
  const op = track.opportunity || {};
  const wishQuotes = Array.isArray(d.wish_quotes) ? d.wish_quotes : [];
  const voiceQuotes = Array.isArray(d.voice_quotes) ? d.voice_quotes : [];
  const quotes = wishQuotes.length > 0 ? wishQuotes : voiceQuotes;
  const redFlags: string[] = [];
  if ((Number(c.sku_count) || 0) === 0) redFlags.push("目录零 SKU(焦段红格)");
  if ((Number(c.our_voice_videos) || 0) === 0) redFlags.push("我方内容零声量");
  const topSharePct = typeof comp.top_share === "number" ? Math.round(comp.top_share * 100) : null;
  const score = Number(op.score) || 0;
  return (
    <div className="mt-2 rounded-xl border border-line bg-panel px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[12px] font-semibold text-ink">{track.label || track.key || "—"}</span>
        <span className={`rounded-md px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${scoreText(score)}`} style={{ background: scoreBg(score) }}>
          机会分 {score}
        </span>
        <ConfChip level={String(op.confidence || track.confidence || "low")} />
        {comp.monopoly ? <span className="rounded-md border border-crit bg-crit-soft px-1.5 py-0.5 text-[9.5px] text-crit">竞品垄断</span> : null}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-ink-2">
        <span>
          ① 需求 <span className="font-mono font-medium tabular-nums text-ink">{Number(d.total) || 0}</span>
        </span>
        <span className="font-mono tabular-nums text-muted">评论 {Number(d.comment_recent) || 0}/{Number(d.comment_prev) || 0}(近/前30天)</span>
        <MomBadge trend={String(d.comment_trend || "stable")} momPct={Number(d.comment_mom_pct) || 0} />
        <span className="font-mono tabular-nums text-muted">证据 {Number(d.evidence_recent) || 0}/{Number(d.evidence_prev) || 0}(近/前90天)</span>
        <MomBadge trend={String(d.evidence_trend || "stable")} momPct={Number(d.evidence_mom_pct) || 0} />
        {(Number(d.wish_count) || 0) > 0 ? (
          <span className="rounded-md border border-accent-2 px-1.5 py-0.5 text-[9.5px] text-accent-2">愿望 ×{d.wish_count}</span>
        ) : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-ink-2">
        <span>
          ② 覆盖{" "}
          <span className="font-mono font-medium tabular-nums text-ink">
            SKU {Number(c.sku_count) || 0} + 我方声量 {Number(c.our_voice_videos) || 0}
          </span>
        </span>
        <span>
          ③ 竞品{" "}
          {comp.status === "ready" ? (
            <span className="font-medium text-ink">
              {comp.top_brand || "—"} 占 {topSharePct != null ? topSharePct + "%" : "—"}(命中 {Number(comp.total_mentions) || 0} 条 /{" "}
              {Number(comp.brand_count) || 0} 品牌)
            </span>
          ) : (
            <span className="text-muted">词表零命中,按未垄断处理(低置信)</span>
          )}
        </span>
      </div>
      {redFlags.length > 0 ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {redFlags.map((f, i) => (
            <span key={i} className="rounded-md border border-crit bg-crit-soft px-1.5 py-0.5 text-[9.5px] text-crit">{f}</span>
          ))}
        </div>
      ) : null}
      {quotes.length > 0 ? (
        <QuoteFold
          quotes={quotes}
          title={`${wishQuotes.length > 0 ? "愿望原声" : "声量原声"} · ${String(track.label || track.key || "")}`}
        />
      ) : null}
      {comp.example ? (
        <div className="mt-1.5 text-[10px] text-muted">
          竞品例证:
          {comp.example.content_url ? (
            <a
              href={comp.example.content_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ink-2 transition-colors hover:text-accent hover:underline"
            >
              {comp.example.title || comp.example.content_url}
            </a>
          ) : (
            <span className="text-ink-2">{comp.example.title || "—"}</span>
          )}
          <span className="text-muted">
            {` — ${comp.example.brand || ""}${comp.example.view_count != null ? `(播放 ${fmtNum(comp.example.view_count)})` : ""}`}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/* ============ matrix · 机会矩阵(维度切换 + 3×3 格 + 赛道芯片点选) ============ */

function binOf(norm: number): number {
  if (norm >= 0.67) return 2;
  if (norm >= 0.34) return 1;
  return 0;
}

const COV_LABELS = ["覆盖高", "覆盖中", "覆盖低"];
const DEMAND_LABELS = ["需求低", "需求中", "需求高"];

export function MatrixBody({
  tracksResp,
  dim,
  onDimChange,
  selectedId,
  onSelect,
}: {
  tracksResp: TracksResp;
  dim: "category" | "focal";
  onDimChange: (dim: "category" | "focal") => void;
  selectedId: string;
  onSelect: (trackId: string) => void;
}) {
  const tracks = (dim === "category" ? tracksResp.category_tracks : tracksResp.focal_tracks) || [];
  const selected = tracks.find((t) => t.track_id === selectedId) || null;

  // 3×3 机会矩阵:x=需求 bin(低→高),y=我方覆盖 bin(高→低,顶行=覆盖高)
  const cells: TrackItem[][][] = [0, 1, 2].map(() => [0, 1, 2].map(() => [] as TrackItem[]));
  for (const t of tracks) {
    const dx = binOf(Number(t.demand?.norm) || 0);
    const cy = binOf(Number(t.coverage?.norm) || 0);
    cells[2 - cy][dx].push(t);
  }
  for (const row of cells) for (const cell of row) cell.sort((a, b) => (Number(b.opportunity?.score) || 0) - (Number(a.opportunity?.score) || 0));

  return (
    <div>
      <div className="mb-2 flex items-center gap-1">
        {(["category", "focal"] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => onDimChange(key)}
            aria-pressed={dim === key}
            className={`rounded-md border px-2 py-0.5 text-[10px] transition-colors ${
              dim === key ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-ink-2"
            }`}
          >
            {key === "category" ? "品类维" : "焦段维"}
          </button>
        ))}
        <span className="ml-auto font-mono text-[9.5px] text-muted">{tracks.length} 条赛道</span>
      </div>
      <div className="space-y-1">
        {cells.map((row, ri) => (
          <div key={ri} className="flex items-stretch gap-1">
            <div className="flex w-[42px] flex-none items-center text-[9.5px] text-muted">{COV_LABELS[ri]}</div>
            {row.map((cell, ci) => {
              const maxScore = cell.reduce((acc, t) => Math.max(acc, Number(t.opportunity?.score) || 0), 0);
              return (
                <div
                  key={ci}
                  className="min-h-[46px] flex-1 rounded-lg border border-line p-1"
                  style={{ background: scoreBg(maxScore) }}
                  title={`${COV_LABELS[ri]} × ${DEMAND_LABELS[ci]} · ${cell.length} 条赛道`}
                >
                  <div className="flex flex-wrap gap-0.5">
                    {cell.slice(0, 6).map((t) => {
                      const score = Number(t.opportunity?.score) || 0;
                      const active = t.track_id === selectedId;
                      return (
                        <button
                          key={t.track_id}
                          type="button"
                          onClick={() => onSelect(active ? "" : String(t.track_id || ""))}
                          className={`rounded-md border px-1 py-0.5 font-mono text-[9px] leading-none transition-colors ${
                            active ? "border-accent bg-accent-soft text-accent" : `border-line bg-card hover:border-accent ${scoreText(score)}`
                          }`}
                          title={`${t.label || t.key}:机会分 ${score}(需求 ${Number(t.demand?.total) || 0} / SKU ${Number(t.coverage?.sku_count) || 0})`}
                        >
                          {`${t.key || "—"} ${score}`}
                        </button>
                      );
                    })}
                    {cell.length > 6 ? <span className="px-0.5 font-mono text-[8.5px] text-muted">+{cell.length - 6}</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
        <div className="flex items-center gap-1 pl-[46px]">
          {DEMAND_LABELS.map((l) => (
            <div key={l} className="flex-1 text-center text-[9.5px] text-muted">{l}</div>
          ))}
        </div>
      </div>
      {selected ? <TrackDetail track={selected} /> : null}
    </div>
  );
}

/* ============ oppTop · Top 机会赛道(点行 → 机会矩阵切维并展开详情) ============ */

export function OppTopBody({
  opportunities,
  onPick,
}: {
  opportunities: OppItem[];
  onPick: (dim: "category" | "focal", trackId: string) => void;
}) {
  if (opportunities.length === 0) return <EmptyLine text="暂无过阈值机会赛道。" />;
  return (
    <div className="space-y-0.5">
      {opportunities.slice(0, 5).map((op, i) => {
        const score = Number(op.opportunity?.score) || 0;
        return (
          <button
            key={op.track_id || i}
            type="button"
            onClick={() => onPick(String(op.dimension || "category") === "focal" ? "focal" : "category", String(op.track_id || ""))}
            title="在机会矩阵中展开该赛道证据"
            className="flex w-full items-center gap-2 rounded-lg border border-transparent px-1.5 py-1 text-left transition-colors hover:border-accent hover:bg-accent-soft"
          >
            <span className={`w-[32px] flex-none text-right font-mono text-[11px] font-bold tabular-nums ${scoreText(score)}`}>{score}</span>
            <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2">{op.label || op.track_id || "—"}</span>
            {(Number(op.demand?.wish_count) || 0) > 0 ? (
              <span className="flex-none rounded-md border border-accent-2 px-1 py-0.5 text-[8.5px] text-accent-2">愿望×{op.demand?.wish_count}</span>
            ) : null}
            <ConfChip level={String(op.opportunity?.confidence || "low")} />
          </button>
        );
      })}
    </div>
  );
}

/* ============ noGo · 不进清单 + 卡口愿望信号(诚实给理由) ============ */

export function NoGoBody({
  noGo,
  mounts,
}: {
  noGo: NoGoItem[];
  mounts: NonNullable<TracksResp["mount_signals"]>;
}) {
  if (noGo.length === 0 && mounts.length === 0) return <EmptyLine text="暂无不进结论与卡口信号。" />;
  return (
    <div>
      {noGo.length > 0 ? (
        <div className="space-y-1">
          {noGo.slice(0, 4).map((ng, i) => (
            <div key={ng.track_id || i} className="text-[10px] leading-relaxed text-muted">
              <span className="text-ink-2">{(ng.label || ng.track_id || "—") + ":"}</span>
              {String(ng.reason || "")}
            </div>
          ))}
          {noGo.length > 4 ? <div className="text-[9.5px] text-muted">另有 {noGo.length - 4} 条不进赛道(理由同上两类)</div> : null}
        </div>
      ) : null}
      {mounts.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted">卡口愿望:</span>
          {mounts.slice(0, 5).map((m, i) => (
            <span
              key={m.mount || i}
              className="rounded-md border border-line bg-card px-1.5 py-0.5 text-[10px] text-ink-2"
              title={Array.isArray(m.quotes) && m.quotes[0] ? `原声:${m.quotes[0].text || ""}` : ""}
            >
              {m.mount} ×{Number(m.wish_count) || 0}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
