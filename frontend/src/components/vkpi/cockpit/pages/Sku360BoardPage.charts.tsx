import React from "react";
import { EmptyLine, KpiCard } from "./MarketVoicePage.modules";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

// SKU 360° · 图表模块族(Sku360BoardPage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage.charts / KolProfileBoardPage.charts 图形语言逐件同构
//   (不跨页引它们的私有件,防纠缠):BarRow 条形行(关联创作者/推广候选/市场风险共用)、
//   KPI 带四卡(KpiCard 现成件:提及内容卡趋势线 = board-series?board=sku360&sku=
//   该 SKU 标题提及/日真序列(关联指标,卡面大数是全量档案计数 → 不挂环比药丸);
//   其余三指标为请求时实算聚合无历史时序 → 诚实 spempty 虚线,永不编 series/环比)。
// 红线:纯展示零网络;fit/评分只读展示绝不写回;颜色全 token var(--ds-*) 零写死色;
//   零 opacity 修饰类(条形弱化走内联 style,金样板同款);诚实空态,绝不编数。

export type Row = Record<string, unknown>;

/* ============ 小工具(金样板同构;宽进严出,远端契约不齐一律防御) ============ */

export function asRow(v: unknown): Row | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : null;
}

export function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

export function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

export function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// 0-1 或 0-100 的比例统一到 0-100(存量列口径不一,读侧容错)
export function pct(v: number | null): number | null {
  if (v === null) return null;
  const p = v <= 1 ? v * 100 : v;
  return p >= 0 && p <= 100 ? Math.round(p * 100) / 100 : null;
}

// 中文紧凑数(20.5亿 / 25.1万;金样板 fmtZhCompact 同构)
export function fmtZhCompact(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

export function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

/* ============ 条形行(金样板 BarRow 同构:名 + 条 + mono 右注) ============ */
export function BarRow({
  name,
  widthPct,
  color,
  value,
  highlight = false,
  dashed = false,
  title,
}: {
  name: string;
  /** 条宽 0-100;0 也画空槽(demo .pbar 底槽常驻) */
  widthPct: number;
  /** 条色(CSS var 串;缺省 accent→accent-2 渐变) */
  color?: string;
  value: React.ReactNode;
  highlight?: boolean;
  /** 弱信号桶:muted 弱化条 */
  dashed?: boolean;
  title?: string;
}) {
  const barBg = color || "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))";
  return (
    <div
      className="grid grid-cols-[minmax(72px,120px)_1fr_minmax(66px,auto)] items-center gap-2.5 py-[4.5px] text-[11.5px]"
      title={title}
    >
      <span className={`truncate ${highlight ? "font-bold text-accent" : "text-ink-2"}`}>{name}</span>
      <span className="h-[6px] overflow-hidden rounded-[3px] bg-line">
        <i
          className="block h-full rounded-[3px]"
          style={{
            width: `${Math.max(0, Math.min(100, widthPct))}%`,
            background: barBg,
            opacity: dashed ? 0.45 : 1,
          }}
        />
      </span>
      <span className="text-right font-mono text-[10.5px] text-muted">{value}</span>
    </div>
  );
}

/* ============ KPI 带四卡:提及内容 / 创作者 / 总曝光 / 平均互动率。
   四指标全部 = /sku/{sku}/profile aggregate 请求时实算(别名匹配现扫),
   无逐 SKU 历史时序端点 → 四卡 series 缺席 = KpiCard 自动 spempty 诚实虚线,
   永不编趋势/环比。 ============ */
export function SkuKpiBand({
  contentCount,
  creatorCount,
  totalViews,
  avgEngagementPct,
  pending,
  pendingNote,
  boardSeries,
}: {
  /** aggregate.content_count(命中该 SKU 的内容条数) */
  contentCount: number | null;
  /** aggregate.creator_count(去重创作者数) */
  creatorCount: number | null;
  /** aggregate.total_views(命中内容曝光合计) */
  totalViews: number | null;
  /** aggregate.avg_engagement_rate × 100(赞+评 / 曝光) */
  avgEngagementPct: number | null;
  /** 档案主体未就绪(加载中/失败)→ 四卡诚实 pending */
  pending: boolean;
  pendingNote: string;
  /** board-series?board=sku360&sku= 响应(null=未就绪/失败 → 趋势位 spempty 诚实虚线) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="提及内容"
        value={contentCount !== null ? contentCount.toLocaleString() : "—"}
        unit="条"
        tone={contentCount !== null && contentCount === 0 ? "warn" : "good"}
        pending={pending}
        pendingNote={pendingNote}
        series={boardSeriesVals(boardSeries ?? null, "sku_mentions")}
        seriesColor="var(--ds-accent)"
      />
      <KpiCard
        label="覆盖创作者"
        value={creatorCount !== null ? creatorCount.toLocaleString() : "—"}
        unit="人"
        pending={pending || creatorCount === null}
        pendingNote={pendingNote}
      />
      <KpiCard
        label="内容总曝光"
        value={fmtZhCompact(totalViews)}
        pending={pending || totalViews === null}
        pendingNote={pendingNote}
      />
      <KpiCard
        label="平均互动率"
        value={avgEngagementPct !== null ? avgEngagementPct : "—"}
        unit="%"
        pending={pending || avgEngagementPct === null}
        pendingNote={pending ? pendingNote : "无曝光基数,互动率不可算"}
      />
    </div>
  );
}

/* ============ 关联创作者(aggregate.top_creators:按曝光排序条形榜) ============ */
export function CreatorsBody({ creators, onOpenKol }: { creators: Row[]; onOpenKol?: (kolPoolId: number) => void }) {
  const rows = creators
    .map((c) => asRow(c))
    .filter((c): c is Row => Boolean(c))
    .slice(0, 8);
  if (rows.length === 0) return <EmptyLine text="该 SKU 暂无关联创作者(内容零命中)。" />;
  const max = rows.reduce((a, c) => Math.max(a, num(c.total_views) ?? 0), 0) || 1;
  return (
    <div>
      {rows.map((c, i) => {
        const kid = num(c.kol_pool_id);
        const name = str(c.display_name) || str(c.handle) || (kid !== null ? `#${kid}` : "—");
        const views = num(c.total_views) ?? 0;
        return (
          <div key={i} className="flex items-center gap-1.5">
            <div className="min-w-0 flex-1">
              <BarRow
                name={name}
                widthPct={(views / max) * 100}
                value={`${fmtZhCompact(views)} · ×${num(c.content_count) ?? 0}`}
                title={`${name} · 曝光 ${views.toLocaleString()} · 内容 ${num(c.content_count) ?? 0} 条`}
              />
            </div>
            {onOpenKol && kid !== null ? (
              <button
                type="button"
                onClick={() => onOpenKol(kid)}
                title="打开 KOL 档案"
                className="flex-none rounded-md border border-line px-1.5 py-0.5 text-[9.5px] text-muted transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
              >
                档案 →
              </button>
            ) : null}
          </div>
        );
      })}
      <ProvNote>按命中内容曝光合计排序 · 榜内计数只含命中该 SKU 的内容</ProvNote>
    </div>
  );
}

/* ============ 评论声量(comments 别名命中抽样;日期为源数据日粒度,原样绝对展示) ============ */
export function VoiceBody({ comments }: { comments: Row }) {
  const sample = asArray(comments.sample)
    .map((c) => asRow(c))
    .filter((c): c is Row => Boolean(c));
  const matched = num(comments.matched_total) ?? 0;
  const scanned = num(comments.scanned) ?? 0;
  if (sample.length === 0) {
    return <EmptyLine text={`评论库暂无提及该型号的评论(已扫 ${scanned.toLocaleString()} 条,零命中)。`} />;
  }
  return (
    <div>
      {sample.slice(0, 6).map((c, i) => (
        <div key={i} className="border-b border-line py-2 last:border-0">
          <div className="text-[11.5px] leading-relaxed text-ink-2">
            {str(c.comment_text).length > 160 ? `${str(c.comment_text).slice(0, 160)}…` : str(c.comment_text) || "—"}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[9.5px] text-muted">
            <span>{str(c.author_handle) || "匿名"}</span>
            {str(c.platform) ? <span>{str(c.platform)}</span> : null}
            {str(c.language_detected) ? <span>{str(c.language_detected)}</span> : null}
            {str(c.created_at) ? (
              <span className="font-mono" title="评论日期(源数据日粒度 · UTC)">
                {str(c.created_at)}
              </span>
            ) : null}
            {(num(c.likes_count) ?? 0) > 0 ? <span className="font-mono">♥ {fmtZhCompact(num(c.likes_count))}</span> : null}
            {str(c.matched_alias) ? (
              <span className="font-mono" title="命中别名(归一化 token 边界匹配)">
                命中「{str(c.matched_alias)}」
              </span>
            ) : null}
          </div>
        </div>
      ))}
      <ProvNote>
        命中 {matched.toLocaleString()} 条 / 已扫 {scanned.toLocaleString()} 条 · 抽样按点赞降序
      </ProvNote>
    </div>
  );
}

/* ============ B&H 口碑(表存在才读;本地 0 行 → 诚实待喂数) ============ */
export function BhBody({ bh }: { bh: Row }) {
  const present = bh.table_present === true;
  if (!present) return <EmptyLine text="口碑评论表未建,该数据源未接入。" />;
  const sample = asArray(bh.sample)
    .map((r) => asRow(r))
    .filter((r): r is Row => Boolean(r));
  const avg = num(bh.avg_rating);
  if (sample.length === 0) {
    return <EmptyLine text="口碑评论库暂无该 SKU 的评论(数据源已接,等采集任务喂数)。" />;
  }
  return (
    <div>
      {avg !== null ? (
        <div className="mb-1.5 flex items-center gap-2 text-[12px]">
          <span className="font-mono text-[17px] font-bold text-warn">{avg}</span>
          <span className="text-muted">/ 5 均分 · {num(bh.matched_total) ?? sample.length} 条</span>
        </div>
      ) : null}
      {sample.slice(0, 5).map((r, i) => (
        <div key={i} className="border-b border-line py-2 last:border-0">
          <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-ink">
            {num(r.rating) !== null ? (
              <span className="font-mono text-warn">{"★".repeat(Math.max(0, Math.min(5, Math.round(num(r.rating) ?? 0))))}</span>
            ) : null}
            <span className="min-w-0 truncate font-medium">{str(r.title) || "—"}</span>
          </div>
          {str(r.body) ? (
            <div className="mt-0.5 text-[10.5px] leading-relaxed text-ink-2">
              {str(r.body).length > 140 ? `${str(r.body).slice(0, 140)}…` : str(r.body)}
            </div>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[9.5px] text-muted">
            <span>{str(r.author) || "匿名"}</span>
            {str(r.review_date) ? <span className="font-mono">{str(r.review_date)}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ============ 推广候选(campaign card:启发式圈选,只读,人工复核;palette 备选) ============ */

const RISK_TIER_TONE: Record<string, string> = {
  high: "border-crit bg-crit-soft text-crit",
  medium: "border-warn bg-warn-soft text-warn",
  low: "border-good bg-good-soft text-good",
};
const RISK_TIER_LABEL: Record<string, string> = { high: "风险高", medium: "风险中", low: "风险低", unknown: "风险未评" };

export function CandidatesBody({ card, onOpenKol }: { card: Row; onOpenKol?: (kolPoolId: number) => void }) {
  const candidates = asArray(card.kol_candidates)
    .map((c) => asRow(c))
    .filter((c): c is Row => Boolean(c));
  const market = asRow(card.market_risk);
  const tier = str(market?.risk_tier) || "unknown";
  const brands = asArray(market?.top_competitor_brands)
    .map((b) => asRow(b))
    .filter((b): b is Row => Boolean(b));
  if (candidates.length === 0 && brands.length === 0) {
    return <EmptyLine text="无证据支撑的推广候选(圈选零命中);市场信号亦无关联。" />;
  }
  const maxBrand = brands.reduce((a, b) => Math.max(a, num(b.count) ?? 0), 0) || 1;
  return (
    <div className="space-y-2.5">
      <div className="rounded-[9px] border border-warn bg-warn-soft px-2.5 py-1.5 text-[10.5px] text-warn">
        启发式圈选 · 仅供人工参考,不构成自动推荐(建联前须人工复核)
      </div>
      {candidates.length > 0 ? (
        <div>
          {candidates.slice(0, 8).map((c, i) => {
            const kid = num(c.kol_pool_id);
            const name = str(c.display_name) || str(c.handle) || (kid !== null ? `#${kid}` : "—");
            const score = num(c.score) ?? 0;
            const conf = num(c.confidence);
            const risks = asArray(c.risk_flags).length;
            return (
              <div key={i} className="flex items-center gap-1.5">
                <div className="min-w-0 flex-1">
                  <BarRow
                    name={name}
                    widthPct={score}
                    color={risks > 0 ? "var(--ds-warn)" : undefined}
                    dashed={risks > 0}
                    value={`${Math.round(score)} 分${conf !== null ? ` · 信 ${Math.round(conf * 100)}%` : ""}`}
                    title={`证据 ${asArray(c.evidence).length} 条${risks > 0 ? " · 有风险标记" : ""} · 圈选分非契合分`}
                  />
                </div>
                {onOpenKol && kid !== null ? (
                  <button
                    type="button"
                    onClick={() => onOpenKol(kid)}
                    title="打开 KOL 档案"
                    className="flex-none rounded-md border border-line px-1.5 py-0.5 text-[9.5px] text-muted transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
                  >
                    档案 →
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyLine text="无证据支撑的候选(候选必须带证据,零编造)。" />
      )}
      <div>
        <div className="mb-0.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">同期市场信号</div>
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          <span className={`rounded-md border px-2 py-0.5 text-[10px] ${RISK_TIER_TONE[tier] || "border-line text-muted"}`}>
            {RISK_TIER_LABEL[tier] || tier}
          </span>
          {(num(market?.risk_score) ?? 0) > 0 ? (
            <span className="font-mono text-[9.5px] text-muted">风险分 {num(market?.risk_score)}</span>
          ) : null}
        </div>
        {brands.length > 0 ? (
          brands
            .slice(0, 5)
            .map((b, i) => (
              <BarRow key={i} name={str(b.brand) || "—"} widthPct={((num(b.count) ?? 0) / maxBrand) * 100} color="var(--ds-warn)" value={`×${num(b.count) ?? 0}`} />
            ))
        ) : (
          <EmptyLine text="窗口内无竞品信号。" />
        )}
      </div>
    </div>
  );
}
