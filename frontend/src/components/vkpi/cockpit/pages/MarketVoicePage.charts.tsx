import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine, type Row } from "./MarketVoicePage.modules";

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
// 红线:纯展示零网络;不触 viltrox_fit_score / rule_v0;诚实空态(空段/null share
//        断点如实断,不插值不编数);显示层宪法(不渲染 author_* 等个人字段)。

/* ============ 小工具:占比格式化 / pos_share 语义染色 / 发光滤镜 ============ */

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
}) {
  const barBg = color || "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))";
  return (
    <div className="grid grid-cols-[minmax(64px,84px)_1fr_minmax(66px,auto)] items-center gap-2.5 py-[4.5px] text-[11.5px]" title={title}>
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
export function CatDonutBody({ categories, totalMatched }: { categories: Row[]; totalMatched: number }) {
  const PALETTE = ["var(--ds-accent)", "var(--ds-accent-2)", "var(--ds-good)", "var(--ds-warn)", "var(--ds-info)", "var(--ds-crit)"];
  const segs = categories
    .map((c) => ({ label: String(c.label || c.key), count: Number(c.count) || 0 }))
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
            strokeWidth={13}
            strokeLinecap="butt"
            strokeDasharray={a.dasharray}
            strokeDashoffset={a.dashoffset}
            transform="rotate(-90 60 60)"
            style={glow(a.color)}
          />
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
          <div key={i} className="flex items-center gap-2 text-[11.5px] text-ink-2">
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

export function SentiTrendBody({ senti }: { senti: Row }) {
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

function AlertRowView({ hot, dot, text, ax, axTone }: { hot?: boolean; dot: "hot" | "ok"; text: React.ReactNode; ax: React.ReactNode; axTone?: string }) {
  return (
    <div
      className={`mb-2 flex items-center gap-2.5 rounded-[10px] border px-[11px] py-[9px] last:mb-0 ${
        hot ? "border-crit bg-crit-soft" : "border-line bg-[var(--ds-card-2,var(--ds-card))]"
      }`}
    >
      <span className={dot === "hot" ? "ds-adot" : "ds-adot ds-adot--ok"} />
      <div className="min-w-0 flex-1 text-[11.5px] leading-[1.5] text-ink-2 [&_b]:text-ink">{text}</div>
      <span className={`flex-none text-[10px] font-bold ${axTone || (hot ? "text-crit" : "font-medium text-muted")}`}>{ax}</span>
    </div>
  );
}

export function AlertsBody({ alerts }: { alerts: Row }) {
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
export function LineVoiceBody({ items }: { items: Row[] }) {
  const rows = items.filter((it) => (Number(it.count) || 0) > 0);
  if (rows.length === 0) return <EmptyLine text="窗口内产品线词表零命中。" />;
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
            title={`已批注 ${Number(it.annotated) || 0}/${count} 条`}
          />
        );
      })}
      <ProvNote>产品线级词表分桶 · 无逐 SKU 真实关联(如实不下钻)· pos_share 分母=已批注条数</ProvNote>
    </div>
  );
}

/* ============ plat · 平台分布(demo plat 形态:accent 渐变条 + count · pct%) ============ */
export function PlatformBody({ items }: { items: Row[] }) {
  const total = items.reduce((a, it) => a + (Number(it.count) || 0), 0);
  if (total === 0) return <EmptyLine text="窗口内无平台计数。" />;
  return (
    <div>
      {items.map((it) => {
        const count = Number(it.count) || 0;
        const pct = (count / total) * 100;
        return <BarRow key={String(it.platform)} name={String(it.platform || "unknown")} widthPct={pct} value={`${count} · ${pctText(count / total)}%`} />;
      })}
    </div>
  );
}

/* ============ topics · 热点话题 chips(demo .topic:词 + mono 计数 + ▲▼) ============ */
export function TopicsBody({ topics }: { topics: Row }) {
  const items: Row[] = Array.isArray(topics.items) ? topics.items : [];
  if (items.length === 0) return <EmptyLine text={String(topics.reason || "窗口内话题词族零命中。")} />;
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {items.map((t) => {
          const delta = typeof t.delta_pct === "number" ? t.delta_pct : null;
          return (
            <span
              key={String(t.key)}
              className="inline-flex items-center gap-[7px] rounded-[18px] border border-line bg-[var(--ds-card-2,var(--ds-card))] px-3 py-[5px] text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-ink"
              title={delta != null ? `vs 上一等长窗 ${delta > 0 ? "+" : ""}${delta}%` : "上一窗口 0 命中 · 环比诚实省略"}
            >
              {String(t.label || t.key)} <b className="font-mono font-bold text-accent">{Number(t.count) || 0}</b>
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
      <ProvNote>热度=话题词命中评论数(lexicon_v0 词族)· ▲▼=vs 上一等长窗 · 扫描 {Number(topics.scanned) || 0} 条</ProvNote>
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

export function LanguageBody({ dist }: { dist: Row }) {
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
            title={und ? "language_detected 空 · 未检出存量" : `language_detected=${code}`}
          />
        );
      })}
      <ProvNote>语言分布 · 非地理归属(评论无真实 geo 关联,诚实口径)· 待检=未检出存量</ProvNote>
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
