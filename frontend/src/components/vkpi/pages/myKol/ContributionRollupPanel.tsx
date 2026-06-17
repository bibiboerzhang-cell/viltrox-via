/**
 * P-KOL-5 管理层 KOL 贡献度聚合面板(独立组件,集成者负责挂载)。
 *
 * 只读:调用 GET /api/admin/vkpi/my-kol/contribution-rollup(后端用 scope.can_view_all
 * 二次 gate,仅管理层可读)。每个负责人一行,各指标分列:
 *   负责人 / 在管 KOL 数 / 已发布数 / 归因销售。
 * 诚实降级:某指标无数据源接入时显示「— / 待接入」(归因销售看 has_attribution)。
 *
 * 设计约束:此组件不改 MyKolPage.tsx(集成者挂),不引入新 CSS 文件(用内联样式),
 * 仅依赖 services/vkpi/kol-api 的 fetchContributionRollup。
 */
import { useEffect, useMemo, useState } from 'react';
import { BarChart3, RefreshCw } from 'lucide-react';
import {
  fetchContributionRollup,
  type VkpiContributionRollupItem,
} from '../../../../services/vkpi/kol-api';

interface ContributionRollupPanelProps {
  apiToken: string;
  /** 仅在 manager 视角下渲染;集成者从 MyKolPage 的 viewMode 传入。 */
  viewMode?: 'manager' | 'employee';
  windowDays?: number;
}

const WINDOW_OPTIONS = [30, 90, 180, 365];

function usd(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(Number.isFinite(value) ? value : 0);
}

const PLACEHOLDER = '— / 待接入';

export function ContributionRollupPanel({ apiToken, viewMode, windowDays }: ContributionRollupPanelProps) {
  const [items, setItems] = useState<VkpiContributionRollupItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [windowSel, setWindowSel] = useState<number>(windowDays ?? 90);
  const [reloadTick, setReloadTick] = useState(0);
  const [denied, setDenied] = useState(false);

  // 仅管理层视角展示;集成者用 viewMode gate,这里再兜底一层。
  const isManager = viewMode !== 'employee';

  useEffect(() => {
    if (!apiToken || !isManager) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchContributionRollup(apiToken, { windowDays: windowSel })
      .then((resp) => {
        if (cancelled) return;
        setItems(resp.items || []);
        setDenied(false);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = String((err as Error)?.message || '');
        // 后端非管理层返回 403:不当作错误,静默不渲染(由 viewMode gate 已避免触发)。
        if (/403|forbidden|manager scope/i.test(msg)) {
          setDenied(true);
          setItems([]);
        } else {
          setError(msg.slice(0, 120) || '贡献度读取失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, isManager, windowSel, reloadTick]);

  const totals = useMemo(() => {
    return items.reduce(
      (acc, it) => {
        acc.kols += it.active_kol_count || 0;
        acc.published += it.published_count || 0;
        acc.sales += it.attributed_sales_usd || 0;
        acc.hasAnyAttribution = acc.hasAnyAttribution || !!it.has_attribution;
        return acc;
      },
      { kols: 0, published: 0, sales: 0, hasAnyAttribution: false },
    );
  }, [items]);

  if (!isManager || denied) return null;

  return (
    <section
      style={{
        marginTop: 24,
        padding: 20,
        borderRadius: 14,
        border: '1px solid rgba(148,163,184,0.18)',
        background: 'rgba(15,23,42,0.35)',
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            <BarChart3 size={16} /> KOL 贡献度聚合
          </h2>
          <p style={{ margin: '4px 0 0', fontSize: 12, opacity: 0.7 }}>
            管理层视角 · 每位负责人一行 · 各指标分列
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 12, opacity: 0.7 }}>窗口</label>
          <select
            value={windowSel}
            onChange={(e) => setWindowSel(Number(e.target.value))}
            style={{
              fontSize: 12,
              padding: '4px 8px',
              borderRadius: 8,
              background: 'rgba(30,41,59,0.6)',
              color: 'inherit',
              border: '1px solid rgba(148,163,184,0.25)',
            }}
          >
            {WINDOW_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {d} 天
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setReloadTick((t) => t + 1)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 12,
              padding: '4px 10px',
              borderRadius: 8,
              cursor: 'pointer',
              background: 'rgba(30,41,59,0.6)',
              color: 'inherit',
              border: '1px solid rgba(148,163,184,0.25)',
            }}
          >
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
      </header>

      {error ? (
        <p style={{ fontSize: 13, color: '#f87171', margin: '8px 0' }}>读取失败:{error}</p>
      ) : null}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', opacity: 0.7, borderBottom: '1px solid rgba(148,163,184,0.2)' }}>
              <th style={{ padding: '8px 10px' }}>负责人</th>
              <th style={{ padding: '8px 10px', textAlign: 'right' }}>在管 KOL 数</th>
              <th style={{ padding: '8px 10px', textAlign: 'right' }}>已发布数</th>
              <th style={{ padding: '8px 10px', textAlign: 'right' }}>归因销售</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: '16px 10px', opacity: 0.6 }}>
                  加载中…
                </td>
              </tr>
            ) : null}
            {!loading && items.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: '16px 10px', opacity: 0.6 }}>
                  暂无在管归属(无 active claim)
                </td>
              </tr>
            ) : null}
            {items.map((it) => (
              <tr
                key={it.staff_id ?? `unknown-${it.staff_name ?? Math.random()}`}
                style={{ borderBottom: '1px solid rgba(148,163,184,0.1)' }}
              >
                <td style={{ padding: '8px 10px' }}>
                  <div style={{ fontWeight: 600 }}>{it.staff_name || `#${it.staff_id ?? '?'}`}</div>
                  {it.staff_email ? (
                    <div style={{ fontSize: 11, opacity: 0.55 }}>{it.staff_email}</div>
                  ) : null}
                </td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{it.active_kol_count ?? 0}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                  {it.published_count > 0 ? (
                    it.published_count
                  ) : (
                    <span style={{ opacity: 0.45 }}>0</span>
                  )}
                </td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                  {it.has_attribution ? (
                    usd(it.attributed_sales_usd)
                  ) : (
                    <span style={{ opacity: 0.45 }} title="归因销售数据源尚未接入该负责人">
                      {PLACEHOLDER}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
          {items.length > 0 ? (
            <tfoot>
              <tr style={{ borderTop: '1px solid rgba(148,163,184,0.25)', fontWeight: 600 }}>
                <td style={{ padding: '8px 10px' }}>合计</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{totals.kols}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{totals.published}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                  {totals.hasAnyAttribution ? usd(totals.sales) : <span style={{ opacity: 0.45 }}>{PLACEHOLDER}</span>}
                </td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>

      <p style={{ fontSize: 11, opacity: 0.5, marginTop: 12 }}>
        已发布数来自内容库(window 内 published_at);归因销售来自销售归因表(按 KOL 关联),无数据源时显示「待接入」。
      </p>
    </section>
  );
}

export default ContributionRollupPanel;
