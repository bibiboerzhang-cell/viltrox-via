import React from "react";
import { EmptyLine } from "./MarketVoicePage.modules";
import type { PoolDiscoveryFunnel, Row } from "./KolPoolBoardPage.actions";

// KOL 池 · 图表模块族(KolPoolBoardPage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage.charts / MyKolBoardPage.charts 图形语言逐件同构
//   (BarRow 条形行按金样板纪律持本地同构副本,不跨页引私有件防纠缠):
//   fitDist  → MyKol FitDistBody 同构:全池 V6 Fit 十分位 10 桶 + 未评分诚实桶,
//              前端只读分桶自 poolItems(既有列表 payload,零新端点);
//   platDist → 平台条形:poolItems 按 platform 前端计数(与 summary.by_platform 同径);
//   funnel   → 发现转化四段条形(近 30 天:发现→自动入库→已深析→已收藏),
//              数据 = kol-pool/summary.discovery_funnel_30d(四段同窗各自计数,
//              段算不出=键缺席 → 灰行「计数暂不可用」诚实展示,绝不编 0)。
// 红线:纯展示零网络(数据全由 page 层注入);fit 分只读分桶绝不写回 / 不触 rule_v0;
//   颜色全 token var(--ds-*) 零写死色;零 opacity 修饰类(dashed 弱化走金样板
//   BarRow 同构的内联 style);诚实空态(空池不画、段缺席如实灰行);
//   卡面零技术术语(表名/端点口径只进 SrcChip rows / tooltip)。

/* ============ 小工具(金样板同构) ============ */

// 0.344 → "34.4"(整数去尾零)
export function pctText(share: number): string {
  const p = Math.round(share * 1000) / 10;
  return p % 1 === 0 ? String(Math.round(p)) : p.toFixed(1);
}

function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

/* ============ mplatrow 条形行(金样板 BarRow 同构:名 + 条 + mono 右注;
   本页三模块全为纯展示行 → 无 onClick,零假按钮) ============ */
export function BarRow({
  name,
  widthPct,
  color,
  value,
  dashed = false,
  title,
}: {
  name: string;
  /** 条宽 0-100;0 也画空槽(demo .pbar 底槽常驻);非 0 保底 2.5 宽必可见(小占比不隐身) */
  widthPct: number;
  /** 条色(CSS var 串;缺省 accent→accent-2 渐变) */
  color?: string;
  value: React.ReactNode;
  /** 未评分/缺席桶:muted 弱化条 */
  dashed?: boolean;
  title?: string;
}) {
  const barBg = color || "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))";
  return (
    <div className="grid grid-cols-[minmax(64px,84px)_1fr_minmax(66px,auto)] items-center gap-2.5 py-[4.5px] text-[11.5px]" title={title}>
      <span className="truncate text-ink-2">{name}</span>
      <span className="h-[6px] overflow-hidden rounded-[3px] bg-line">
        <i
          className="block h-full rounded-[3px]"
          style={{
            width: widthPct > 0 ? `${Math.min(100, Math.max(widthPct, 2.5))}%` : "0%",
            background: barBg,
            opacity: dashed ? 0.45 : 1,
          }}
        />
      </span>
      <span className="text-right font-mono text-[10.5px] text-muted">{value}</span>
    </div>
  );
}

/* ============ fitDist · Fit 分布直方(MyKol FitDistBody 同构;前端只读分桶) ============ */
export function PoolFitDistBody({ items }: { items: Row[] }) {
  const buckets = Array.from({ length: 10 }, (_, i) => ({
    label: i === 9 ? "90-100 分" : `${i * 10}-${i * 10 + 9} 分`,
    min: i * 10,
    max: i === 9 ? 100 : i * 10 + 9,
    count: 0,
  }));
  let unscored = 0;
  for (const it of items || []) {
    const fit = Number((it as any)?.v6_fit);
    if (Number.isFinite(fit)) {
      buckets[Math.min(9, Math.max(0, Math.floor(fit / 10)))].count += 1;
    } else {
      unscored += 1;
    }
  }
  const total = (items || []).length;
  if (total === 0) return <EmptyLine text="全池零行——Fit 直方诚实不画。" />;
  return (
    <div>
      {buckets.map((b) => (
        <BarRow
          key={b.label}
          name={b.label}
          widthPct={(b.count / total) * 100}
          value={`${b.count.toLocaleString()} · ${pctText(b.count / total)}%`}
          title={`匹配分 ${b.min}-${b.max} 区间 KOL 数(既有分数只读分桶,本页零打分)`}
        />
      ))}
      <BarRow
        name="未评分"
        widthPct={(unscored / total) * 100}
        color="var(--ds-muted)"
        dashed
        value={`${unscored.toLocaleString()} · ${pctText(unscored / total)}%`}
        title="匹配分为空的诚实桶 · 绝不当 0 分"
      />
      <ProvNote>全池只读分布(前端分桶)· 规则打分口径(非 AI 评分)· 不含任何单个 KOL 分数</ProvNote>
    </div>
  );
}

/* ============ platDist · 平台分布条形(poolItems 前端计数;与 summary.by_platform 同径) ============ */

const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  bilibili: "Bilibili",
  twitter: "X / Twitter",
  x: "X / Twitter",
  facebook: "Facebook",
  unknown: "未标平台",
};

export function PoolPlatformBody({ items }: { items: Row[] }) {
  const counts = new Map<string, number>();
  for (const it of items || []) {
    const key = String((it as any)?.platform || "").trim().toLowerCase() || "unknown";
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const total = (items || []).length;
  if (total === 0) return <EmptyLine text="全池零行——平台分布诚实不画。" />;
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return (
    <div>
      {rows.map(([platform, count]) => (
        <BarRow
          key={platform}
          name={PLATFORM_LABEL[platform] || platform}
          widthPct={(count / total) * 100}
          color={platform === "unknown" ? "var(--ds-muted)" : undefined}
          dashed={platform === "unknown"}
          value={`${count.toLocaleString()} · ${pctText(count / total)}%`}
          title={platform === "unknown" ? "平台字段为空的行 · 如实入总数" : "全池按平台计数(非重复行)"}
        />
      ))}
      <ProvNote>全池按平台计数(前端聚合)· 平台维度筛选待接 → 纯展示行零假按钮</ProvNote>
    </div>
  );
}

/* ============ funnel · 发现转化漏斗(近 30 天四段;summary 键缺席=灰行诚实缺席) ============ */

const FUNNEL_SEGMENTS: Array<{ key: keyof PoolDiscoveryFunnel; label: string; title: string }> = [
  { key: "discovered", label: "发现", title: "近 30 天找达人产出条目(含已在库命中,未去重到人)" },
  { key: "enrolled", label: "自动入库", title: "近 30 天新入池 KOL(搜到自动落池 · 非重复行)" },
  { key: "deep_analyzed", label: "已深析", title: "近 30 天出深析结果的 KOL 数(完成态结果覆盖)" },
  { key: "favorited", label: "已收藏", title: "近 30 天被收藏的 KOL 数(收藏=归我,进 MY KOL)" },
];

export function DiscoveryFunnelBody({ funnel, loading }: { funnel: PoolDiscoveryFunnel | null; loading?: boolean }) {
  if (loading && !funnel) {
    return <div className="py-6 text-center text-[12px] text-muted">漏斗计数读取中…</div>;
  }
  const present = funnel
    ? FUNNEL_SEGMENTS.filter((seg) => Number.isFinite(Number((funnel as any)[seg.key])) && (funnel as any)[seg.key] !== null)
    : [];
  if (present.length === 0) {
    return <EmptyLine text="漏斗计数暂不可用(后端未返回)——诚实缺席,不编数。" />;
  }
  const max = present.reduce((a, seg) => Math.max(a, Number((funnel as any)[seg.key]) || 0), 0) || 1;
  const missing = FUNNEL_SEGMENTS.filter((seg) => !present.includes(seg));
  return (
    <div>
      {FUNNEL_SEGMENTS.map((seg) => {
        if (missing.includes(seg)) {
          return (
            <BarRow
              key={String(seg.key)}
              name={seg.label}
              widthPct={0}
              color="var(--ds-muted)"
              dashed
              value="计数暂不可用"
              title={`${seg.title} · 本段计数暂不可用(诚实缺席,绝不编 0)`}
            />
          );
        }
        const count = Number((funnel as any)[seg.key]) || 0;
        return (
          <BarRow
            key={String(seg.key)}
            name={seg.label}
            widthPct={(count / max) * 100}
            value={count.toLocaleString()}
            title={seg.title}
          />
        );
      })}
      <ProvNote>近 {Number(funnel?.window_days) || 30} 天 · 四段同窗各自计数(非同批追踪)· 段缺席=计数暂不可用</ProvNote>
    </div>
  );
}
