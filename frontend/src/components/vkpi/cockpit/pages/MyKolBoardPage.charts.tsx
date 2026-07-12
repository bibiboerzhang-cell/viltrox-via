import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine } from "./MarketVoicePage.modules";
import type {
  KolLibraryRow,
  VkpiContactCoverageGroup,
  VkpiCountPoint,
  VkpiFitDistGroup,
  VkpiFunnelGroup,
  VkpiFunnelSegment,
  VkpiKpiSeriesGroup,
  VkpiPlatformDistGroup,
  VkpiSeriesPoint,
  VkpiViewsTopGroup,
} from "../../../../services/vkpi/myKolBoard-api";

// MY KOL · 图表模块族(M4 真身;MyKolBoardPage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage.charts.tsx 图形语言逐件同构(不跨页引它的私有件,防纠缠):
//   BarRow→mplatrow 条形行(funnel/fitdist/platdist/viewsTop/contacts 共用);
//   GapSparkline→demo .kpi sp 同构 + null 断点(缺日如实断线,KpiCard 滤 null 会把缺口连起来故自绘);KpiSeriesCard→demo .kpi 卡
//   (ds-kpi 全套类)+ 断点 sparkline + delta 药丸(口径挂 title:趋势线是关联指标
//   时序,不冒充卡面大数环比);FollowerTrendBody→senti tchart 双线同构;
//   Claims/Shares/CoverBody→coverrow 行语言同构。数据:board-ext 七组聚合
//   (page 层注入,本文件零直连网络)+ aggregate 现成行。
// 红线:纯展示零网络;绝不写 viltrox_fit_score / 不碰 rule_v0(fit 直方只读分桶);
//   颜色全 token var(--ds-*) 零写死色;发光只走 --ds-glow-radius(浅色 0px 自动无光);
//   动效只用 ds-viz.css 既有类(自带 reduced-motion 降级);诚实空态(空段如实空,
//   null 断点如实断,截断/降级如实标注,绝不编数)。

/* ============ 小工具(金样板同构) ============ */

// 0.344 → "34.4"(整数去尾零)
export function pctText(share: number): string {
  const p = Math.round(share * 1000) / 10;
  return p % 1 === 0 ? String(Math.round(p)) : p.toFixed(1);
}

// 中文紧凑数(20.5亿 / 25.1万;M1 起从 modules 搬家到图表族,modules 转口引用)
export function fmtZhCompact(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

// demo nglow():暗色 --ds-glow-radius=5px 发光,浅色/仪器 0px 自动无光
const glow = (color: string): React.CSSProperties => ({
  filter: `drop-shadow(0 0 var(--ds-glow-radius) ${color})`,
});

// 键盘可达:Enter / 空格触发(可点行统一)
const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); }
};

function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

/* ============ mplatrow 条形行(金样板 BarRow 同构:名 + 条 + mono 右注) ============ */
export function BarRow({
  name,
  widthPct,
  color,
  value,
  highlight = false,
  dashed = false,
  title,
  onClick,
}: {
  name: string;
  /** 条宽 0-100;0 也画空槽(demo .pbar 底槽常驻) */
  widthPct: number;
  /** 条色(CSS var 串;缺省 accent→accent-2 渐变) */
  color?: string;
  value: React.ReactNode;
  highlight?: boolean;
  /** 未评分/未知桶:muted 弱化条 */
  dashed?: boolean;
  title?: string;
  /** 行联动回调(缺省 = 纯展示行,零假按钮) */
  onClick?: () => void;
}) {
  const barBg = color || "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))";
  return (
    <div
      className={`grid grid-cols-[minmax(64px,84px)_1fr_minmax(66px,auto)] items-center gap-2.5 py-[4.5px] text-[11.5px]${
        onClick ? " -mx-1.5 cursor-pointer rounded-[7px] border border-transparent px-1.5 transition-colors hover:border-accent hover:bg-accent-soft" : ""
      }`}
      title={title}
      role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined} onClick={onClick} onKeyDown={onClick ? keyActivate(onClick) : undefined}
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

/* ============ 断点序列几何:null → 断段(不插值);孤点保留为小圆点 ============ */

type Pt = [number, number];

function buildSegments(vals: Array<number | null>, X: (i: number) => number, Y: (v: number) => number): Pt[][] {
  const segs: Pt[][] = [];
  let cur: Pt[] = [];
  vals.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      if (cur.length > 0) segs.push(cur);
      cur = [];
    } else {
      cur.push([X(i), Y(v)]);
    }
  });
  if (cur.length > 0) segs.push(cur);
  return segs;
}

const lineD = (pts: Pt[]) => `M${pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" L")}`;

/* ============ GapSparkline:demo .kpi sp 同构 + null 断点(Sparkline 现成件会滤 null
   把缺口连起来 —— 存量快照缺日必须如实断线,故自绘;类名走 ds-sparkline 全套,
   flow 动画由 ds-viz.css 媒体查询自带 reduced-motion 降级) ============ */
export function GapSparkline({ values, color }: { values: Array<number | null>; color: string }) {
  const gid = `mk-${React.useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const W = 240;
  const H = 30;
  const finite = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (finite.length < 2) return <div className="ds-kpi__series-empty" aria-hidden="true" />;
  const max = Math.max(...finite);
  const min = Math.min(...finite);
  const range = max - min || 1;
  const n = values.length;
  const X = (i: number) => (n > 1 ? (i / (n - 1)) * (W - 4) + 2 : W / 2);
  const Y = (v: number) => H - 4 - ((v - min) / range) * (H - 8);
  const segs = buildSegments(values, X, Y);
  const lastSeg = segs[segs.length - 1];
  const last = lastSeg ? lastSeg[lastSeg.length - 1] : null;
  return (
    <svg className="ds-kpi__spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: H, display: "block" }} aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.42" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {segs.map((pts, i) => {
        if (pts.length === 1) {
          return <circle key={i} cx={pts[0][0]} cy={pts[0][1]} r="2.2" fill={color} vectorEffect="non-scaling-stroke" />;
        }
        const d = lineD(pts);
        return (
          <React.Fragment key={i}>
            <path className="ds-sparkline__area" d={`${d} L${pts[pts.length - 1][0].toFixed(1)},${H} L${pts[0][0].toFixed(1)},${H} Z`} fill={`url(#${gid})`} />
            <path className="ds-sparkline__line" d={d} fill="none" stroke={color} strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            <path className="ds-sparkline__flow" d={d} fill="none" stroke={color} strokeWidth="2.8" strokeLinecap="round" pathLength="1" vectorEffect="non-scaling-stroke" />
          </React.Fragment>
        );
      })}
      {last ? <circle className="ds-sparkline__endpoint" cx={last[0]} cy={last[1]} r="2.8" fill={color} vectorEffect="non-scaling-stroke" /> : null}
    </svg>
  );
}

/* ============ KpiSeriesCard:demo .kpi 卡(ds-kpi 全套类)+ 断点 sparkline + delta 药丸。
   与金样板 KpiCard 结构 1:1,差异仅两处诚实件:① series 支持 null 断点;② 趋势线/
   药丸挂 title 注明口径(趋势线是关联指标时序,不冒充卡面大数的环比)。 ============ */
export function KpiSeriesCard({
  label,
  value,
  unit,
  tone = "good",
  pending,
  pendingNote,
  series,
  seriesColor,
  seriesTitle,
  delta,
  deltaTitle,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  tone?: "good" | "warn";
  pending?: boolean;
  pendingNote?: string;
  /** 关联指标日序列(null=缺快照日断点);null/缺席或 <2 有效点 → demo .spempty 纯虚线 */
  series?: Array<number | null> | null;
  seriesColor?: string;
  /** 趋势线口径(hover title;真表名住 SrcChip,这里只写门面语言) */
  seriesTitle?: string;
  /** 环比百分比;null/undefined = 上窗无数据,诚实不渲染药丸 */
  delta?: number | null;
  /** 药丸口径(hover title,同上) */
  deltaTitle?: string;
}) {
  const dotCls = pending ? "ds-kpi__dot--pend" : tone === "warn" ? "ds-kpi__dot--warn" : "ds-kpi__dot--good";
  const valCls = pending ? " ds-kpi__val--pend" : tone === "warn" ? " ds-kpi__val--warn" : "";
  const vals = Array.isArray(series) ? series : [];
  const finiteCount = vals.filter((v) => typeof v === "number" && Number.isFinite(v)).length;
  const deltaCls =
    delta != null && delta > 0 ? "ds-kpi__delta--up" : delta != null && delta < 0 ? "ds-kpi__delta--down" : "ds-kpi__delta--flat";
  return (
    <div className="ds-kpi ds-rise" style={seriesColor ? ({ "--vkpi-kpi-accent": seriesColor } as React.CSSProperties) : undefined}>
      <div className="ds-kpi__k">
        <span className={`ds-kpi__dot ${dotCls}`} />
        <span className="ds-kpi__label">{label}</span>
      </div>
      <div className={`ds-kpi__val${valCls}`}>
        {pending ? "—" : value}
        {!pending && unit ? <span className="ds-kpi__u">{unit}</span> : null}
        {!pending && delta != null && (
          <span className={`ds-kpi__delta ${deltaCls}`} title={deltaTitle}>
            {delta > 0 ? "▲" : delta < 0 ? "▼" : "—"}
            {Math.abs(delta).toFixed(1)}%
          </span>
        )}
      </div>
      {pending ? (
        pendingNote ? (
          <div>
            <span className="ds-kpi__pt">{pendingNote}</span>
          </div>
        ) : null
      ) : finiteCount >= 2 ? (
        <div title={seriesTitle}>
          <GapSparkline values={vals} color={seriesColor || "var(--ds-accent)"} />
        </div>
      ) : (
        <div className="ds-kpi__series-empty" aria-hidden="true" title={seriesTitle} />
      )}
    </div>
  );
}

/* ============ KPI 带四卡(M4 series 接线;设计单 K1-K4 语义不变):
   K1 在库 KOL / K2 合作推进中 = aggregate.kpi_summary 现值,趋势线/药丸 = board-ext
   kpi_series 关联时序(K1←收藏集粉丝/日,存量快照缺日 null 断点如实;K2←新视频/日,
   计数型 0 填齐)—— 口径挂 title + SrcChip,不冒充大数环比;
   K3 内容播放实测 = board-ext kol_views 点时读数(主控 evidence_metrics 注入兜底),
   无历史序列 → 诚实 spempty 虚线,永不给 delta;
   K4 官号粉丝 = official-matrix Σ followers 现值 + official_followers/日 真序列+药丸。 ============ */
export function MyKolKpiBand({
  kpi,
  kpiSeries,
  exposureValue,
  exposureNote,
  officialFollowers,
  officialNote,
}: {
  /** aggregate.kpi_summary(favorites_count / in_project_count …),null=上游 gate 已拦 */
  kpi: Record<string, number> | null;
  /** board-ext kpi_series 组(仅 status=ready 时传;null=端点失败/未就绪 → 四卡 spempty) */
  kpiSeries: VkpiKpiSeriesGroup | null;
  /** K3 兜底:主控 evidence_metrics.total_exposure;board-ext kol_views 优先 */
  exposureValue: number | null;
  exposureNote: string;
  /** official-matrix Σ totalFollowers;null=矩阵未就绪 → 诚实 pending */
  officialFollowers: number | null;
  officialNote: string;
}) {
  const favorites = Number(kpi?.favorites_count);
  const inProject = Number(kpi?.in_project_count);
  const stockVals = (pts?: VkpiSeriesPoint[]): Array<number | null> | null =>
    Array.isArray(pts) ? pts.map((p) => (typeof p.value === "number" && Number.isFinite(p.value) ? p.value : null)) : null;
  const countVals = (pts?: VkpiCountPoint[]): Array<number | null> | null =>
    Array.isArray(pts) ? pts.map((p) => Number(p.count) || 0) : null;
  const metrics = kpiSeries?.metrics;
  const kolViews = kpiSeries?.kol_views;
  const k3Value =
    typeof kolViews?.views_total === "number" && Number.isFinite(kolViews.views_total) ? kolViews.views_total : exposureValue;
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiSeriesCard
        label="在库 KOL"
        value={Number.isFinite(favorites) ? favorites.toLocaleString() : "—"}
        unit="个"
        series={stockVals(kpiSeries?.series?.pool_followers)}
        seriesColor="var(--ds-accent)"
        seriesTitle="趋势线=收藏集粉丝合计/日(存量快照 · 缺日如实断点,不插值)"
        delta={metrics?.pool_followers?.delta_pct ?? null}
        deltaTitle="环比=收藏集粉丝合计,本窗 vs 上一等长窗最近快照日(非在库数环比)"
      />
      <KpiSeriesCard
        label="合作推进中"
        value={Number.isFinite(inProject) ? inProject.toLocaleString() : "—"}
        unit="个"
        series={countVals(kpiSeries?.series?.new_videos)}
        seriesColor="var(--ds-accent-2)"
        seriesTitle="趋势线=收藏集新增视频/日(计数型 0 填齐)"
        delta={metrics?.new_videos?.delta_pct ?? null}
        deltaTitle="环比=新增视频条数,已流逝等长时段对比(非推进中数环比)"
      />
      <KpiSeriesCard
        label="内容播放实测"
        value={fmtZhCompact(k3Value)}
        pending={k3Value == null}
        pendingNote={exposureNote}
        seriesColor="var(--ds-good)"
        seriesTitle="点时实测(抓取时刻读数)无历史序列 · 诚实虚线,永不编 series"
      />
      <KpiSeriesCard
        label="官号粉丝"
        value={fmtZhCompact(officialFollowers)}
        pending={officialFollowers == null}
        pendingNote={officialNote}
        series={stockVals(kpiSeries?.series?.official_followers)}
        seriesColor="var(--ds-info)"
        seriesTitle="趋势线=官号粉丝合计/日(日快照)"
        delta={metrics?.official_followers?.delta_pct ?? null}
        deltaTitle="环比=官号粉丝合计,最近快照日 vs 上一等长窗"
      />
    </div>
  );
}

/* ============ funnel · 合作漏斗(8 段横向条形;点段 = 按该段 raw 阶段过滤 KOL 库) ============ */
export function FunnelBody({
  funnel,
  selectedStage,
  onSelectStage,
}: {
  funnel: VkpiFunnelGroup;
  /** 当前联动中的段(page 层 LibraryFilter.stage);空串 = 未过滤 */
  selectedStage: string;
  onSelectStage?: (seg: { stage: string; label: string; rawStages: string[] }) => void;
}) {
  const items = Array.isArray(funnel.items) ? funnel.items : [];
  const other = Array.isArray(funnel.other) ? funnel.other : [];
  const total = Number(funnel.total) || items.reduce((a, s) => a + (Number(s.count) || 0), 0);
  if (items.length === 0 || total === 0) return <EmptyLine text={String(funnel.reason || "收藏集内无项目指派行——漏斗诚实空。")} />;
  const pick = (seg: VkpiFunnelSegment) =>
    onSelectStage
      ? () =>
          onSelectStage({
            stage: String(seg.stage || ""),
            label: String(seg.label || seg.stage || ""),
            rawStages: Array.isArray(seg.raw_stages) ? seg.raw_stages.map(String) : [],
          })
      : undefined;
  return (
    <div>
      {items.map((seg) => {
        const count = Number(seg.count) || 0;
        const active = selectedStage !== "" && selectedStage === String(seg.stage || "");
        return (
          <BarRow
            key={String(seg.stage)}
            name={String(seg.label || seg.stage || "—")}
            widthPct={(count / total) * 100}
            value={`${count.toLocaleString()} · ${pctText(count / total)}%`}
            highlight={active}
            dashed={count === 0}
            title={active ? "已按此阶段过滤 KOL 库 · 再点取消" : "点击按此阶段过滤 KOL 库"}
            onClick={pick(seg)}
          />
        );
      })}
      {other.map((o) => (
        <BarRow
          key={`other-${String(o.stage)}`}
          name={`未识别 · ${String(o.stage || "?")}`}
          widthPct={((Number(o.count) || 0) / total) * 100}
          color="var(--ds-muted)"
          dashed
          value={`${(Number(o.count) || 0).toLocaleString()}`}
          title="未识别的新阶段值 · 诚实桶不吞行(不参与联动)"
        />
      ))}
      <ProvNote>13 个真实阶段归并 8 段展示 · 计数=项目内 KOL 指派行(非去重 KOL)· 点段过滤 KOL 库</ProvNote>
    </div>
  );
}

/* ============ fitdist · Fit 分布直方(十分位 10 桶 + 未评分诚实桶;只读展示) ============ */
export function FitDistBody({ fitDist }: { fitDist: VkpiFitDistGroup }) {
  const buckets = Array.isArray(fitDist.buckets) ? fitDist.buckets : [];
  const unscored = Number(fitDist.unscored) || 0;
  const total = Number(fitDist.total) || buckets.reduce((a, b) => a + (Number(b.count) || 0), 0) + unscored;
  if (total === 0) return <EmptyLine text="全池零行——fit 直方诚实不画。" />;
  return (
    <div>
      {buckets.map((b) => {
        const count = Number(b.count) || 0;
        return (
          <BarRow
            key={String(b.bucket)}
            name={`${String(b.bucket)} 分`}
            widthPct={(count / total) * 100}
            value={`${count.toLocaleString()} · ${pctText(count / total)}%`}
            title={`fit ${b.min ?? "?"}-${b.max ?? "?"} 区间 KOL 数(只读分桶)`}
          />
        );
      })}
      <BarRow
        name="未评分"
        widthPct={(unscored / total) * 100}
        color="var(--ds-muted)"
        dashed
        value={`${unscored.toLocaleString()} · ${pctText(unscored / total)}%`}
        title="fit 分为空的诚实桶 · 绝不当 0 分"
      />
      <ProvNote>全池只读分布 · 规则打分口径(非 AI 评分)· 不含任何单个 KOL 分数</ProvNote>
    </div>
  );
}

/* ============ platdist · 平台分布(条形行;点行 = KOL 库按平台过滤,同一份筛选状态) ============ */
export function PlatDistBody({
  dist,
  selectedPlatform,
  onSelectPlatform,
}: {
  dist: VkpiPlatformDistGroup;
  selectedPlatform: string;
  onSelectPlatform?: (platform: string) => void;
}) {
  const items = Array.isArray(dist.items) ? dist.items : [];
  const total = Number(dist.total) || items.reduce((a, it) => a + (Number(it.count) || 0), 0);
  if (items.length === 0 || total === 0) return <EmptyLine text="收藏集为空——平台分布诚实不画。" />;
  return (
    <div>
      {items.map((it) => {
        const platform = String(it.platform || "unknown");
        const count = Number(it.count) || 0;
        const active = selectedPlatform !== "" && selectedPlatform === platform;
        return (
          <BarRow
            key={platform}
            name={platform}
            widthPct={(count / total) * 100}
            value={`${count.toLocaleString()} · ${pctText(count / total)}%`}
            highlight={active}
            title={active ? "已按此平台过滤 KOL 库 · 再点取消" : "点击按此平台过滤 KOL 库"}
            onClick={onSelectPlatform ? () => onSelectPlatform(platform) : undefined}
          />
        );
      })}
      <ProvNote>收藏集(收藏 ∪ 共享,去重)按平台计数 · 点行过滤 KOL 库</ProvNote>
    </div>
  );
}

/* ============ viewsTop · 播放 Top 视频 KOL 榜(实测 view_count 降序;纯展示) ============ */
export function ViewsTopBody({ viewsTop }: { viewsTop: VkpiViewsTopGroup }) {
  const items = Array.isArray(viewsTop.items) ? viewsTop.items : [];
  if (items.length === 0) return <EmptyLine text={String(viewsTop.reason || "无任何带实测播放的视频——播放榜诚实空。")} />;
  const max = items.reduce((a, it) => Math.max(a, Number(it.total_views) || 0), 0) || 1;
  return (
    <div>
      {items.map((it, i) => {
        const views = Number(it.total_views) || 0;
        return (
          <BarRow
            key={`${it.kol_pool_id}-${i}`}
            name={String(it.display_name || it.handle || "—")}
            widthPct={(views / max) * 100}
            value={`${fmtZhCompact(views)} · ${Number(it.video_count) || 0}条`}
            title={`${String(it.platform || "?")} ${String(it.handle || "")} · 实测播放合计(点时读数)`}
          />
        );
      })}
      <ProvNote>点时实测(抓取时刻读数,非时序)· 未实测(NULL)剔除 ≠ 0 播放 · 全库口径</ProvNote>
    </div>
  );
}

/* ============ contacts · 联系方式覆盖(覆盖率大数 + 类型条形;永远零明文) ============ */
export function ContactsBody({ cc }: { cc: VkpiContactCoverageGroup }) {
  const types = Array.isArray(cc.types) ? cc.types : [];
  const coverage = typeof cc.coverage === "number" && Number.isFinite(cc.coverage) ? cc.coverage : null;
  const covered = Number(cc.covered) || 0;
  const total = Number(cc.total) || 0;
  if (types.length === 0 && total === 0) return <EmptyLine text="联系方式表零行且收藏集为空——诚实空。" />;
  const max = types.reduce((a, t) => Math.max(a, Number(t.count) || 0), 0) || 1;
  return (
    <div>
      <div className="mb-3 mt-0.5 flex flex-wrap items-end gap-6">
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-ink">
            {coverage != null ? pctText(coverage) : "—"}
            <span className="text-[13px]">%</span>
          </span>
          <span className="mt-[5px] text-[10px] text-muted">收藏集覆盖率</span>
        </div>
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-ink-2">
            {covered.toLocaleString()}<span className="text-[13px] text-muted">/{total.toLocaleString()}</span>
          </span>
          <span className="mt-[5px] text-[10px] text-muted">有任一联系方式的 KOL</span>
        </div>
      </div>
      {types.map((t) => {
        const count = Number(t.count) || 0;
        return (
          <BarRow
            key={String(t.contact_type)}
            name={String(t.contact_type || "unknown")}
            widthPct={(count / max) * 100}
            value={count.toLocaleString()}
            title="类型计数=全池(明文值一列不进接口)"
          />
        );
      })}
      <ProvNote>类型计数=全池 · 覆盖率=收藏集 · 明文联系方式只走披露门控,本卡永远零明文</ProvNote>
    </div>
  );
}

/* ============ followerTrend · 粉丝趋势双线(收藏集 vs 官号;缺快照日断线) ============ */

function TrendSeries({ segs, color, fill, H, gid }: { segs: Pt[][]; color: string; fill: boolean; H: number; gid: string }) {
  return (
    <>
      {fill && (
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.22" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {segs.map((pts, i) => {
        if (pts.length === 1) {
          return <circle key={i} cx={pts[0][0]} cy={pts[0][1]} r="2.5" fill={color} vectorEffect="non-scaling-stroke" />;
        }
        const d = lineD(pts);
        const last = pts[pts.length - 1];
        return (
          <React.Fragment key={i}>
            {fill && <path d={`${d} L${last[0].toFixed(1)},${H} L${pts[0][0].toFixed(1)},${H} Z`} fill={`url(#${gid})`} />}
            <path d={d} fill="none" stroke={color} strokeWidth="2.2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" style={glow(color)} />
            <circle cx={last[0]} cy={last[1]} r="3" fill={color} vectorEffect="non-scaling-stroke" />
          </React.Fragment>
        );
      })}
    </>
  );
}

export function FollowerTrendBody({ kpiSeries }: { kpiSeries: VkpiKpiSeriesGroup }) {
  const poolPts = Array.isArray(kpiSeries.series?.pool_followers) ? kpiSeries.series!.pool_followers! : [];
  const offPts = Array.isArray(kpiSeries.series?.official_followers) ? kpiSeries.series!.official_followers! : [];
  const toVals = (pts: VkpiSeriesPoint[]) => pts.map((p) => (typeof p.value === "number" && Number.isFinite(p.value) ? p.value : null));
  const poolVals = toVals(poolPts);
  const offVals = toVals(offPts);
  const n = Math.max(poolVals.length, offVals.length);
  const finite = [...poolVals, ...offVals].filter((v): v is number => v != null);
  if (n === 0 || finite.length === 0) return <EmptyLine text="窗口内两侧均无粉丝快照——诚实不画线。" />;
  const yMax = Math.max(...finite);
  const yMin = Math.min(...finite);
  const pad = (yMax - yMin || Math.max(yMax, 1)) * 0.08;
  const top = yMax + pad;
  const bottom = Math.max(0, yMin - pad);
  const span = top - bottom || 1;
  const W = 640;
  const H = 176;
  const pl = 8;
  const pr = 8;
  const pt = 12;
  const pb = 16;
  const iW = W - pl - pr;
  const iH = H - pt - pb;
  const X = (i: number) => pl + (n > 1 ? (i / (n - 1)) * iW : iW / 2);
  const Y = (v: number) => pt + (1 - (v - bottom) / span) * iH;
  const poolSegs = buildSegments(poolVals, X, Y);
  const offSegs = buildSegments(offVals, X, Y);
  const axisDates = (poolPts.length >= offPts.length ? poolPts : offPts).map((p) => String(p.date || ""));
  const ticks = [top, bottom + span / 2, bottom];
  return (
    <div>
      <div className="relative pl-[38px]">
        <div className="absolute bottom-0 left-0 top-0 z-[2] flex flex-col justify-between font-mono text-[8.5px] text-muted">
          {ticks.map((t, i) => (
            <span key={i}>{fmtZhCompact(Math.round(t))}</span>
          ))}
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-[176px] w-full" aria-hidden="true">
          {[1, 2].map((i) => {
            const y = pt + (i / 3) * iH;
            return <line key={i} x1={pl} y1={y} x2={W - pr} y2={y} stroke="var(--ds-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />;
          })}
          <TrendSeries segs={poolSegs} color="var(--ds-accent)" fill H={H} gid="mk-ft-pool" />
          <TrendSeries segs={offSegs} color="var(--ds-info)" fill={false} H={H} gid="mk-ft-off" />
        </svg>
      </div>
      <div className="mt-2 flex items-center gap-3.5 text-[10.5px] text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-[3px] w-[13px] rounded-[2px] bg-accent" />
          收藏集 KOL 粉丝
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-[3px] w-[13px] rounded-[2px] bg-info" />
          官号粉丝
        </span>
        <span className="ml-auto font-mono text-[10px] text-muted">
          {axisDates.length > 0 ? `${axisDates[0]} → ${axisDates[axisDates.length - 1]} · 按日` : ""}
        </span>
      </div>
      <ProvNote>两线同轴同刻度 · 存量快照缺日如实断线不插值 · KOL 侧快照 06-16 起有缺日</ProvNote>
    </div>
  );
}

/* ============ claims · 我的认领(aggregate.claims 小列表;coverrow 行语言同构) ============ */
export function ClaimsBody({ claims }: { claims: Array<Record<string, unknown>> }) {
  if (claims.length === 0) return <EmptyLine text="无认领记录(本人名下 0 行)——诚实空。" />;
  return (
    <div>
      {claims.map((c) => {
        const active = String(c.status || "").toLowerCase() === "active";
        return (
          <div key={String(c.id)} className="flex items-center gap-2 border-b border-line py-1.5 text-[11.5px] last:border-0">
            <span
              className={`h-[7px] w-[7px] flex-none rounded-full ${active ? "bg-good" : "bg-muted"}`}
              style={active ? { boxShadow: "0 0 5px var(--ds-good)" } : undefined}
            />
            <span className={`min-w-0 flex-1 truncate ${active ? "text-ink-2" : "text-muted"}`} title={`kol_id #${String(c.kol_id ?? "—")}`}>
              {String(c.kol_name || `#${String(c.kol_id ?? "—")}`)}
            </span>
            <span className="flex-none font-mono text-[9px] text-muted">{String(c.kol_platform || "")}</span>
            <span className={`flex-none font-mono text-[9.5px] ${active ? "text-good" : "text-muted"}`}>
              {active ? "active" : String(c.status || "—")}
              {c.expires_at ? ` · 至 ${formatLocal(String(c.expires_at))}` : ""}
            </span>
          </div>
        );
      })}
      <ProvNote>本人认领原样列出 · 名称/平台经 kols 表回填,空=如实空</ProvNote>
    </div>
  );
}

/* ============ shares · 共享池(aggregate 共享行;0 行 = 已建未用诚实空) ============ */
export function SharesBody({ rows }: { rows: KolLibraryRow[] }) {
  if (rows.length === 0) return <EmptyLine text="共享池已建未用——0 条共享授予,如实空(不装 live)。" />;
  return (
    <div>
      {rows.map((row) => (
        <div key={row.poolId} className="flex items-center gap-2 border-b border-line py-1.5 text-[11.5px] last:border-0">
          <span className="h-[7px] w-[7px] flex-none rounded-full bg-accent-2" />
          <span className="min-w-0 flex-1 truncate text-ink-2">{row.name}</span>
          <span className="flex-none font-mono text-[9px] text-muted">{row.platform}</span>
          <span className="flex-none text-[9.5px] text-muted">{row.sharedByName ? `来自 ${row.sharedByName}` : "共享给我"}</span>
        </div>
      ))}
      <ProvNote>只读可见性授予(谁共享给谁)· 行动权仍在原属人</ProvNote>
    </div>
  );
}

/* ============ cover · 数据覆盖盲区(金样板 CoverRow 同构;board-ext 无此组 →
   静态盘点 2026-07-11 硬编码读数,如实标注非实时、会过期) ============ */
const COVER_STATIC_DATE = "2026-07-11";
const COVER_ROWS: Array<{ name: string; table: string; count: number; note: string }> = [
  { name: "外联记录", table: "kol_outreach", count: 0, note: "履约闭环外联主表 · 0 行 = 尚无真实外联落库" },
  { name: "合作事件", table: "vkpi_kol_cooperation_events", count: 0, note: "合作事件流水 · 0 行" },
  { name: "生命周期", table: "vkpi_kol_lifecycle_events", count: 0, note: "KOL 生命周期事件 · 0 行" },
  { name: "联盟销售", table: "vkpi_goaffpro_sales / _kol_links", count: 0, note: "GoAffPro 归因销售 · 0 行" },
  { name: "内容手录", table: "vkpi_content_posts", count: 0, note: "手录内容端点落库 · 0 行" },
  { name: "触点", table: "vkpi_kol_pool_touches", count: 2, note: "KOL 池触点流水" },
];

export function CoverBody() {
  return (
    <div>
      <div className="mb-2 rounded-[9px] border border-warn bg-warn-soft px-2.5 py-1.5 text-[10px] leading-[1.6] text-ink-2">
        <b className="font-semibold text-warn">静态盘点 {COVER_STATIC_DATE}</b> —— board-ext 无此组,以下为盘点日硬编码读数,非实时、会过期。
      </div>
      {COVER_ROWS.map((row) => (
        <div
          key={row.table}
          className="flex items-center gap-2 border-b border-line py-1.5 text-[11.5px] last:border-0"
          title={`${row.table} · ${row.note}`}
        >
          <span
            className={`h-[7px] w-[7px] flex-none rounded-full ${row.count > 0 ? "bg-good" : "bg-muted"}`}
            style={row.count > 0 ? { boxShadow: "0 0 5px var(--ds-good)" } : undefined}
          />
          <span className={`min-w-0 flex-1 truncate ${row.count > 0 ? "text-ink-2" : "text-muted"}`}>{row.name}</span>
          <span className={`flex-none font-mono text-[9.5px] ${row.count > 0 ? "text-muted" : "text-warn"}`}>
            {row.count > 0 ? `${row.count} 条` : "0 条 · 盲区"}
          </span>
        </div>
      ))}
      <ProvNote>逐源如实标注 · 空源 = 盲区不装 live · 读数以盘点日期为准</ProvNote>
    </div>
  );
}
