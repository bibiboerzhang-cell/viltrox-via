import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine, KpiCard, type Row } from "./MarketVoicePage.modules";

// 市场之声 · 图表模块族(V0h-ab 波2 终棒;MarketVoicePage 专用,页内拆件不入公共桶)。
//   demo 对照(V-KPI-板块页范式-Demo.html):
//     cat   → donutwrap(132px SVG 环图 stroke-dasharray 分段 + 中心大数 + dlegend 行,demo 342-345/606-607/778-781)
//     senti → tstats 三大数 + tchart(y 轴 + trendsvg 双线:good 渐变面积 + crit 线,demo 200-203/608-610/782-791)
//     alerts→ alertrow(.hot=crit 边+crit-soft 底+脉冲红点 .adot;「已推送 →」徽,demo 409-415/616-619)
//     bar   → mplatrow(名 + 彩色条 + mono 右注,demo 313-314;line_voice/plat/geo/comp 四模块共用)
//     topics→ .topic chips(词 + mono 计数 + ▲▼ 热度箭头,demo 353-354/612)
//   图表纪律:SVG 色值全部 var(--ds-*)(主题切换自然生效,零 JS 取色);发光只走
//   --ds-glow-radius 链路(浅色=0px 自动无光);脉冲复用 cockpit-reference.css 的
//   vkpi-lane-pulse(reduced-motion 由 ds-viz.css .ds-adot 媒体查询降级)。
//   数据点即溯源:环图分段/图例行、告警行、话题 chip、趋势数据点、榜单行全部可点
//   (onSelect/onPointClick/onRowClick 回调由 page 层注入 → 底层原声下钻弹窗);
//   可点态统一 hover 亮边 + cursor-pointer + title 提示;缺回调 = 纯展示,零假按钮。
// 红线:纯展示零网络;不触 viltrox_fit_score / rule_v0;诚实空态(空段/null share
//        断点如实断,不插值不编数);显示层宪法(不渲染 author_* 等个人字段)。

/* ============ 小工具:占比格式化 / pos_share 语义染色 / 发光滤镜 ============ */

// 数据点下钻提示语(hover title 统一口径)
const DRILL_HINT = "点击查看底层样本";
// 榜单行 v1(该维度过滤待接):点行开模块溯源弹窗,底部再跳全量样本 —— 提示语如实
const DIM_HINT = "点击查看溯源与底层样本(该维度过滤待接)";

// 键盘可达:Enter / 空格触发(可点行统一)
const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); }
};

// 0.821 → "82.1"(整数去尾零:0.8 → "80")
export function pctText(share: number): string {
  const p = Math.round(share * 1000) / 10;
  return p % 1 === 0 ? String(Math.round(p)) : p.toFixed(1);
}

// 任务规格:pos_share ≥.7 good / .4-.7 warn / <.4 crit / null=muted(待批注)
export function shareTone(share: number | null | undefined): string {
  if (share == null || !Number.isFinite(share)) return "var(--ds-muted)";
  if (share >= 0.7) return "var(--ds-good)";
  if (share >= 0.4) return "var(--ds-warn)";
  return "var(--ds-crit)";
}

// demo nglow():暗色 --ds-glow-radius=5px 发光,浅色/仪器 0px 自动无光
const glow = (color: string): React.CSSProperties => ({
  filter: `drop-shadow(0 0 var(--ds-glow-radius) ${color})`,
});

function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

/* ============ mplatrow 条形行(demo 313-314:名 74px + 条 + mono 右注 66px) ============ */
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
  /** 无批注/待检桶:muted 虚线弱化条 */
  dashed?: boolean;
  title?: string;
  /** 行下钻回调(缺省 = 纯展示行,零假按钮) */
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

/* ============ cat · 类别构成环图(demo donut():R44 / 描边13 / 分段留缝2 / 中心大数) ============ */
// 数据点即溯源:分段与图例行可点(onSelect(key,label,count))→ 类别原声下钻弹窗;
// hover 分段加粗描边 + 图例行亮边(纯两态,尊重 reduced-motion:无动画只有态)。
export function CatDonutBody({
  categories,
  totalMatched,
  onSelect,
}: {
  categories: Row[];
  totalMatched: number;
  onSelect?: (key: string, label: string, count: number) => void;
}) {
  const PALETTE = ["var(--ds-accent)", "var(--ds-accent-2)", "var(--ds-good)", "var(--ds-warn)", "var(--ds-info)", "var(--ds-crit)"];
  const [hot, setHot] = React.useState<number | null>(null);
  const segs = categories
    .map((c) => ({ key: String(c.key || c.label || ""), label: String(c.label || c.key), count: Number(c.count) || 0 }))
    .filter((c) => c.count > 0)
    .map((c, i) => ({ ...c, color: PALETTE[i % PALETTE.length] }));
  const sum = segs.reduce((a, s) => a + s.count, 0);
  if (sum === 0) return <EmptyLine text="窗口内类别零命中,环图诚实不画。" />;
  const R = 44;
  const C = 2 * Math.PI * R;
  let off = 0;
  const arcs = segs.map((s) => {
    const len = (s.count / sum) * C;
    const arc = { ...s, dasharray: `${Math.max(0, len - 2)} ${C - len + 2}`, dashoffset: -off };
    off += len;
    return arc;
  });
  const center = totalMatched > 0 ? totalMatched : sum;
  return (
    <div className="flex items-center gap-[18px]">
      <svg viewBox="0 0 120 120" style={{ width: 132, height: 132, flex: "none", overflow: "visible" }} aria-hidden="true">
        <circle cx={60} cy={60} r={R} fill="none" stroke="var(--ds-line)" strokeWidth={13} />
        {arcs.map((a, i) => (
          <circle
            key={i}
            cx={60}
            cy={60}
            r={R}
            fill="none"
            stroke={a.color}
            strokeWidth={hot === i ? 15 : 13}
            strokeLinecap="butt"
            strokeDasharray={a.dasharray}
            strokeDashoffset={a.dashoffset}
            transform="rotate(-90 60 60)"
            style={{ ...glow(a.color), cursor: onSelect ? "pointer" : undefined }}
            onClick={onSelect ? () => onSelect(a.key, a.label, a.count) : undefined}
            onMouseEnter={() => setHot(i)} onMouseLeave={() => setHot(null)}
          >
            {onSelect ? <title>{`${a.label} ×${a.count} · ${DRILL_HINT}`}</title> : null}
          </circle>
        ))}
        <text x={60} y={58} textAnchor="middle" style={{ fill: "var(--ds-text)", font: "700 21px var(--ds-font-mono)", letterSpacing: "-0.03em" }}>
          {center}
        </text>
        <text x={60} y={74} textAnchor="middle" style={{ fill: "var(--ds-muted)", font: "600 7.5px var(--ds-font-sans)", letterSpacing: "0.12em" }}>
          HITS
        </text>
      </svg>
      <div className="flex min-w-0 flex-1 flex-col gap-[7px]">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`flex items-center gap-2 text-[11.5px] text-ink-2${
              onSelect ? " -mx-1 cursor-pointer rounded-[7px] border border-transparent px-1 py-px transition-colors hover:border-accent hover:bg-accent-soft" : ""
            }`}
            role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined} title={onSelect ? DRILL_HINT : undefined}
            onClick={onSelect ? () => onSelect(s.key, s.label, s.count) : undefined}
            onKeyDown={onSelect ? keyActivate(() => onSelect(s.key, s.label, s.count)) : undefined}
            onMouseEnter={() => setHot(i)} onMouseLeave={() => setHot(null)}
          >
            <span className="h-[9px] w-[9px] flex-none rounded-[3px]" style={{ background: s.color }} />
            <span className="min-w-0 flex-1 truncate">{s.label}</span>
            <b className="font-mono font-semibold text-ink">{pctText(s.count / sum)}%</b>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============ senti · 情绪趋势(demo tstats 三大数 + trendsvg 双线;null 期诚实断线) ============ */

type Pt = [number, number];

// null → 断段(不插值);孤点(前后都空)以单点段保留,画成小圆点
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

// 数据点即溯源:onPointClick(kind)——点已有数据的趋势点 → 该情感批注的原声下钻;
// 命中热区 = 透明 circle r=8(小点难点原样解决),title 提示,缺回调零热区。
export function SentiTrendBody({ senti, onPointClick }: { senti: Row; onPointClick?: (kind: "positive" | "negative") => void }) {
  const trend: Row[] = Array.isArray(senti.trend) ? senti.trend : [];
  const posShare = typeof senti.pos_share === "number" ? senti.pos_share : null;
  const negShare = typeof senti.neg_share === "number" ? senti.neg_share : null;
  const doneTotal = Number(senti.done_total) || 0;
  const granLabel = String(senti.granularity) === "week" ? "周" : "日";

  const posVals = trend.map((t) => (typeof t.pos_share === "number" ? t.pos_share * 100 : null));
  const negVals = trend.map((t) => (typeof t.neg_share === "number" ? t.neg_share * 100 : null));
  const finite = [...posVals, ...negVals].filter((v): v is number => v != null);
  const yMax = Math.max(80, Math.ceil((finite.length ? Math.max(...finite) : 0) / 10) * 10);

  const W = 640;
  const H = 176;
  const pl = 8;
  const pr = 8;
  const pt = 12;
  const pb = 16;
  const iW = W - pl - pr;
  const iH = H - pt - pb;
  const n = trend.length;
  const X = (i: number) => pl + (n > 1 ? (i / (n - 1)) * iW : iW / 2);
  const Y = (v: number) => pt + (1 - v / yMax) * iH;
  const posSegs = buildSegments(posVals, X, Y);
  const negSegs = buildSegments(negVals, X, Y);
  const drawable = posSegs.length > 0 || negSegs.length > 0;
  const ticks = [yMax, Math.round((yMax * 2) / 3), Math.round(yMax / 3), 0];

  return (
    <div>
      {/* demo .tstats:三大数(正面 good / 负面 crit / 样本) */}
      <div className="mb-3 mt-0.5 flex flex-wrap gap-6">
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-good">
            {posShare != null ? pctText(posShare) : "—"}
            <span className="text-[13px]">%</span>
          </span>
          <span className="mt-[5px] text-[10px] text-muted">正面占比</span>
        </div>
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-crit">
            {negShare != null ? pctText(negShare) : "—"}
            <span className="text-[13px]">%</span>
          </span>
          <span className="mt-[5px] text-[10px] text-muted">负面占比</span>
        </div>
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-ink">{doneTotal.toLocaleString()}</span>
          <span className="mt-[5px] text-[10px] text-muted">已批注样本</span>
        </div>
      </div>
      {!drawable ? (
        <EmptyLine text="窗口内无逐期情绪趋势点(全期空)——诚实不画线。" />
      ) : (
        <>
          {/* demo .tchart:左侧 y 轴 + trendsvg 双线(空期断线不插值) */}
          <div className="relative pl-[30px]">
            <div className="absolute bottom-0 left-0 top-0 z-[2] flex flex-col justify-between font-mono text-[8.5px] text-muted">
              {ticks.map((t, i) => (
                <span key={i}>{i === 0 ? `${t}%` : t}</span>
              ))}
            </div>
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-[176px] w-full" aria-hidden="true">
              {[1, 2].map((i) => {
                const y = pt + (i / 3) * iH;
                return <line key={i} x1={pl} y1={y} x2={W - pr} y2={y} stroke="var(--ds-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />;
              })}
              <TrendSeries segs={posSegs} color="var(--ds-good)" fill H={H} gid="mv-senti-pos" />
              <TrendSeries segs={negSegs} color="var(--ds-crit)" fill={false} H={H} gid="mv-senti-neg" />
              {/* 下钻热区:只在真实数据点上放大点击面(透明 r=8),空期无热区不装点 */}
              {onPointClick &&
                posVals.map((v, i) => v == null ? null : (
                  <circle key={`hp-${i}`} cx={X(i)} cy={Y(v)} r={8} fill="transparent" style={{ cursor: "pointer" }} onClick={() => onPointClick("positive")}>
                    <title>{`${String(trend[i]?.period_start || "")} 正面占比点 · ${DRILL_HINT}`}</title>
                  </circle>
                ))}
              {onPointClick &&
                negVals.map((v, i) => v == null ? null : (
                  <circle key={`hn-${i}`} cx={X(i)} cy={Y(v)} r={8} fill="transparent" style={{ cursor: "pointer" }} onClick={() => onPointClick("negative")}>
                    <title>{`${String(trend[i]?.period_start || "")} 负面占比点 · ${DRILL_HINT}`}</title>
                  </circle>
                ))}
            </svg>
          </div>
          {/* demo .legend2 */}
          <div className="mt-2 flex items-center gap-3.5 text-[10.5px] text-ink-2">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-[3px] w-[13px] rounded-[2px] bg-good" />
              正面
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-[3px] w-[13px] rounded-[2px] bg-crit" />
              负面
            </span>
            <span className="ml-auto font-mono text-[10px] text-muted">
              {trend.length > 0 ? `${String(trend[0].period_start)} → ${String(trend[trend.length - 1].period_start)} · 按${granLabel}` : ""}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/* ============ alerts · 声量告警(demo alertrow;正常态全绿,绝不摆假告警) ============ */

function AlertRowView({ hot, dot, text, ax, axTone, onClick }: {
  hot?: boolean; dot: "hot" | "ok"; text: React.ReactNode; ax: React.ReactNode; axTone?: string;
  /** 类别行下钻回调(聚合「其余 N 类别」行无单一类别不传 = 纯展示) */
  onClick?: () => void;
}) {
  return (
    <div
      className={`mb-2 flex items-center gap-2.5 rounded-[10px] border px-[11px] py-[9px] last:mb-0 ${
        hot ? "border-crit bg-crit-soft" : "border-line bg-[var(--ds-card-2,var(--ds-card))]"
      }${onClick ? " cursor-pointer transition-colors hover:border-accent" : ""}`}
      role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined} title={onClick ? DRILL_HINT : undefined}
      onClick={onClick} onKeyDown={onClick ? keyActivate(onClick) : undefined}
    >
      <span className={dot === "hot" ? "ds-adot" : "ds-adot ds-adot--ok"} />
      <div className="min-w-0 flex-1 text-[11.5px] leading-[1.5] text-ink-2 [&_b]:text-ink">{text}</div>
      <span className={`flex-none text-[10px] font-bold ${axTone || (hot ? "text-crit" : "font-medium text-muted")}`}>{ax}</span>
    </div>
  );
}

// 数据点即溯源:每条类别行可点(onSelect(category,label))→ 告警原声下钻;
// 聚合「其余 N 类别」行无单一类别,如实不可点。
export function AlertsBody({ alerts, onSelect }: { alerts: Row; onSelect?: (category: string, label: string) => void }) {
  const cats: Row[] = Array.isArray(alerts.categories) ? alerts.categories : [];
  const pushed: Row[] = Array.isArray(alerts.recent_pushed) ? alerts.recent_pushed : [];
  const windowHours = Number(alerts.window_hours) || 8;
  const threshold = Number(alerts.threshold) || 3;
  if (cats.length === 0) return <EmptyLine text="告警引擎未返回类别评估。" />;
  const triggered = cats.filter((c) => c.triggered);
  const watching = cats.filter((c) => !c.triggered && (Number(c.negative_count) || 0) > 0);
  const calm = cats.filter((c) => !c.triggered && (Number(c.negative_count) || 0) === 0);
  const pushedFor = (category: string) => pushed.find((p) => String(p.dedupe_key || "").startsWith(`voice_alert:${category}:`));
  return (
    <div>
      {triggered.map((c) => {
        const hit = pushedFor(String(c.category));
        const score = Number(c.score) || 0;
        const count = Number(c.negative_count) || 0;
        return (
          <AlertRowView
            key={String(c.category)}
            hot
            dot="hot"
            onClick={onSelect ? () => onSelect(String(c.category), String(c.label || c.category)) : undefined}
            text={
              <>
                <b>{String(c.label || c.category)}</b> · {windowHours}h 内 {count} 条负面
                {score !== count ? ` · 加权 ${score} 分` : ""}(阈值 {threshold})
              </>
            }
            ax={hit ? "已推送 →" : "触发"}
          />
        );
      })}
      {watching.map((c) => (
        <AlertRowView
          key={String(c.category)}
          dot="ok"
          onClick={onSelect ? () => onSelect(String(c.category), String(c.label || c.category)) : undefined}
          text={
            <>
              {String(c.label || c.category)} · {windowHours}h 内 {Number(c.negative_count) || 0} 条负面,未达阈值({threshold}),观察中
            </>
          }
          ax="监控"
        />
      ))}
      {calm.length > 0 && (
        <AlertRowView
          dot="ok"
          text={
            <>
              {triggered.length + watching.length > 0 ? `其余 ${calm.length} 类别` : `全部 ${calm.length} 类别`} · 情绪稳定,无异动
            </>
          }
          ax="正常"
          axTone="font-medium text-good"
        />
      )}
      {pushed.length > 0 && (
        <div className="mt-2.5">
          <div className="mb-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-muted">最近推送(vkpi_action_inbox)</div>
          {pushed.slice(0, 3).map((p) => (
            <div key={String(p.id)} className="flex items-center gap-2 border-b border-line py-1.5 text-[10.5px] text-ink-2 last:border-0">
              <span className="min-w-0 flex-1 truncate">{String(p.title || p.dedupe_key)}</span>
              <span className="flex-none font-mono text-[9px] text-muted">
                {String(p.status || "")} · {formatLocal(p.created_at ? String(p.created_at) : null)}
              </span>
            </div>
          ))}
        </div>
      )}
      <ProvNote>
        规则 · 类别 × {windowHours}h 负面加权 ≥ {threshold} 触发 · 本页纯读评估不推送 · 扫描 {Number(alerts.scanned) || 0} 条
      </ProvNote>
    </div>
  );
}

/* ============ line_voice · 产品线声音榜(demo sku 形态;诚实:产品线级非 SKU) ============ */
// 行可点(onRowClick,v1):该维度过滤待接 → 开模块溯源弹窗 + 底部「底层样本」,如实不装
export function LineVoiceBody({ items, onRowClick }: { items: Row[]; onRowClick?: () => void }) {
  const rows = items.filter((it) => (Number(it.count) || 0) > 0);
  if (rows.length === 0) return <EmptyLine text="窗口内产品线声量零命中。" />;
  return (
    <div>
      {rows.map((it) => {
        const share = typeof it.pos_share === "number" ? it.pos_share : null;
        const count = Number(it.count) || 0;
        return (
          <BarRow
            key={String(it.key)}
            name={String(it.label || it.key)}
            widthPct={share != null ? share * 100 : 12}
            color={shareTone(share)}
            dashed={share == null}
            value={share != null ? `${pctText(share)}% 正 · ${count}条` : `待批注 · ${count}条`}
            title={`已批注 ${Number(it.annotated) || 0}/${count} 条${onRowClick ? ` · ${DIM_HINT}` : ""}`}
            onClick={onRowClick}
          />
        );
      })}
      <ProvNote>产品线级分桶 · 无逐 SKU 真实关联(如实不按 SKU 下钻)· 正面占比分母=已批注条数</ProvNote>
    </div>
  );
}

/* ============ plat · 平台分布(demo plat 形态:accent 渐变条 + count · pct%) ============ */
// 行可点(onRowClick,v1):平台维度过滤待接 → 开模块溯源弹窗 + 底部「底层样本」,如实不装
export function PlatformBody({ items, onRowClick }: { items: Row[]; onRowClick?: () => void }) {
  const total = items.reduce((a, it) => a + (Number(it.count) || 0), 0);
  if (total === 0) return <EmptyLine text="窗口内无平台计数。" />;
  return (
    <div>
      {items.map((it) => {
        const count = Number(it.count) || 0;
        const pct = (count / total) * 100;
        return (
          <BarRow
            key={String(it.platform)}
            name={String(it.platform || "unknown")}
            widthPct={pct}
            value={`${count} · ${pctText(count / total)}%`}
            title={onRowClick ? DIM_HINT : undefined}
            onClick={onRowClick}
          />
        );
      })}
    </div>
  );
}

/* ============ topics · 热点话题 chips(demo .topic:词 + mono 计数 + ▲▼) ============ */
// 数据点即溯源:chip 可点(onSelect(key,label,count))→ 话题原声下钻弹窗
export function TopicsBody({ topics, onSelect }: { topics: Row; onSelect?: (key: string, label: string, count: number) => void }) {
  const items: Row[] = Array.isArray(topics.items) ? topics.items : [];
  if (items.length === 0) return <EmptyLine text={String(topics.reason || "窗口内话题零命中。")} />;
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {items.map((t) => {
          const delta = typeof t.delta_pct === "number" ? t.delta_pct : null;
          const count = Number(t.count) || 0;
          const pick = onSelect ? () => onSelect(String(t.key), String(t.label || t.key), count) : undefined;
          return (
            <span
              key={String(t.key)}
              className={`inline-flex items-center gap-[7px] rounded-[18px] border border-line bg-[var(--ds-card-2,var(--ds-card))] px-3 py-[5px] text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-ink${
                pick ? " cursor-pointer" : ""
              }`}
              role={pick ? "button" : undefined} tabIndex={pick ? 0 : undefined} onClick={pick} onKeyDown={pick ? keyActivate(pick) : undefined}
              title={`${delta != null ? `对比上一等长窗 ${delta > 0 ? "+" : ""}${delta}%` : "上一窗口 0 命中 · 环比诚实省略"}${pick ? ` · ${DRILL_HINT}` : ""}`}
            >
              {String(t.label || t.key)} <b className="font-mono font-bold text-accent">{count}</b>
              {delta != null && delta !== 0 && (
                <span className={`text-[9px] font-bold ${delta > 0 ? "text-good" : "text-crit"}`}>
                  {delta > 0 ? "▲" : "▼"}
                  {pctText(Math.abs(delta) / 100)}%
                </span>
              )}
            </span>
          );
        })}
      </div>
      <ProvNote>热度=话题词命中评论数 · ▲▼=对比上一等长窗 · 扫描 {Number(topics.scanned) || 0} 条 · 点 chip 看底层样本</ProvNote>
    </div>
  );
}

/* ============ geo · 按语言 / 市场(language_dist;und=待检;诚实:语言分布非地理) ============ */
const LANG_LABEL: Record<string, string> = {
  en: "英语",
  zh: "中文",
  de: "德语",
  fr: "法语",
  es: "西语",
  pt: "葡语",
  it: "意语",
  ja: "日语",
  ko: "韩语",
  ru: "俄语",
  ro: "罗马尼亚语",
  id: "印尼语",
  ar: "阿拉伯语",
  und: "待检",
};

// 行可点(onRowClick,v1):语言维度过滤待接 → 开模块溯源弹窗 + 底部「底层样本」,如实不装
export function LanguageBody({ dist, onRowClick }: { dist: Row; onRowClick?: () => void }) {
  const items: Row[] = Array.isArray(dist.items) ? dist.items : [];
  const total = Number(dist.total) || items.reduce((a, it) => a + (Number(it.count) || 0), 0);
  if (items.length === 0 || total === 0) return <EmptyLine text={String(dist.reason || "窗口内无语言分桶。")} />;
  return (
    <div>
      {items.map((it) => {
        const code = String(it.language || "und");
        const und = code === "und";
        const count = Number(it.count) || 0;
        return (
          <BarRow
            key={code}
            name={und ? "待检" : `${LANG_LABEL[code] || code.toUpperCase()}`}
            widthPct={(count / total) * 100}
            color={und ? "var(--ds-muted)" : undefined}
            dashed={und}
            value={`${count} · ${pctText(count / total)}%`}
            title={`${und ? "language_detected 空 · 未检出存量" : `language_detected=${code}`}${onRowClick ? ` · ${DIM_HINT}` : ""}`}
            onClick={onRowClick}
          />
        );
      })}
      <ProvNote>语言分布 · 非地理归属(评论无真实地理关联,诚实口径)· 待检=未检出存量</ProvNote>
    </div>
  );
}

/* ============ comp · 同话题竞品声量(百家饭同口径;Viltrox 首行高亮) ============ */
export function CompetitorBody({ comp }: { comp: Row }) {
  const items: Row[] = Array.isArray(comp.items) ? comp.items : [];
  if (items.length === 0) return <EmptyLine text={String(comp.reason || "窗口内无品牌露出信号。")} />;
  return (
    <div>
      {items.map((it) => {
        const self = Boolean(it.is_self);
        const share = typeof it.share === "number" ? it.share : null;
        const count = Number(it.count) || 0;
        return (
          <BarRow
            key={String(it.brand)}
            name={String(it.brand)}
            highlight={self}
            widthPct={share != null ? share * 100 : 0}
            color={self ? "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))" : "var(--ds-muted)"}
            value={share != null ? `${count} 条 · ${pctText(share)}%` : `${count} 条`}
          />
        );
      })}
      <ProvNote>
        竞品份额上升 = 威胁信号 · 联动战略台 · 样本 {Number(comp.sample_videos) || 0} 条已深析视频(视频×品牌去重)
      </ProvNote>
    </div>
  );
}

/* ============ KPI 带四卡(V0h-ab 语义;V1.1 第四卡「已转产品部」接真计数) ============ */
// 本月反馈/待处理 = kpi_prev 真环比口径(缺组回落 voice-report 旧口径计数);
// 正面情绪占比 = sentiment_summary(批注 0 行 → 诚实 pending);
// 已转产品部 = prd_referrals 组真计数(vkpi_market_prd_referrals 窗口 COUNT,0 也如实 0;
// 表未建/组缺失 → 诚实 pending 带后端 reason,不编数)。sparkline 均为按日真序列。
export function KpiBandCards({
  ext,
  fallbackComments,
  fallbackQueue,
}: {
  ext: Row | null;
  fallbackComments: number;
  fallbackQueue: number;
}) {
  const prevMetrics: Row = ext?.kpi_prev?.metrics || {};
  const commentsSeries = (ext?.kpi_series?.series?.comments || []).map((p: Row) => Number(p.count) || 0);
  const queueSeries = (ext?.kpi_series?.series?.queue || []).map((p: Row) => Number(p.count) || 0);
  const senti: Row = ext?.sentiment_summary || {};
  const posShare = typeof senti.pos_share === "number" ? senti.pos_share : null;
  const posSeries = (Array.isArray(senti.trend) ? senti.trend : []).map((t: Row) =>
    typeof t.pos_share === "number" ? t.pos_share * 100 : null,
  );
  const commentsCur = Number(prevMetrics.comments?.current ?? fallbackComments) || 0;
  const queueCur = Number(prevMetrics.queue?.current ?? fallbackQueue) || 0;
  const prdGroup: Row | undefined = ext?.prd_referrals;
  const prdReady = !!prdGroup && String(prdGroup.status) === "ready" && prdGroup.count != null;
  const prdSeries = (Array.isArray(prdGroup?.series) ? prdGroup.series : []).map((p: Row) => Number(p.count) || 0);
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="本月反馈"
        value={commentsCur.toLocaleString()}
        unit="条"
        series={commentsSeries}
        seriesColor="var(--ds-accent)"
        delta={prevMetrics.comments?.delta_pct ?? null}
      />
      {/* 待处理 = 回复队列积压 → demo .dot.w + warn 染数值/线色 */}
      <KpiCard
        label="待处理"
        value={queueCur.toLocaleString()}
        unit="条"
        tone="warn"
        series={queueSeries}
        seriesColor="var(--ds-warn)"
        delta={prevMetrics.queue?.delta_pct ?? null}
      />
      {posShare != null ? (
        <KpiCard
          label="正面情绪占比"
          value={(Math.round(posShare * 1000) / 10).toFixed(1)}
          unit="%"
          series={posSeries}
          seriesColor="var(--ds-good)"
        />
      ) : (
        <KpiCard
          label="正面情绪占比"
          value="—"
          pending
          pendingNote={ext ? "窗口内情绪批注 0 行" : "扩展报表待接通"}
        />
      )}
      {/* V1.1:PRD 通路已建(vkpi_market_prd_referrals)→ 真计数,0 也如实显示 0 */}
      {prdReady ? (
        <KpiCard
          label="已转产品部"
          value={(Number(prdGroup.count) || 0).toLocaleString()}
          unit="条"
          series={prdSeries}
          seriesColor="var(--ds-accent-2)"
          delta={prdGroup.delta_pct ?? null}
        />
      ) : (
        <KpiCard
          label="已转产品部"
          value="—"
          pending
          pendingNote={String(prdGroup?.reason || (ext ? "转交计数待接通" : "扩展报表待接通"))}
        />
      )}
    </div>
  );
}
