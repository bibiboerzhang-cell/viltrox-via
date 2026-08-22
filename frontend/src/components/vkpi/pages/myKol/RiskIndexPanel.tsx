/**
 * C6 KOL 风险指数面板(候选 A·实时聚合,零新 LLM)。独立组件,集成者负责挂载。
 *
 * 只读:GET /api/admin/vkpi/my-kol/risk-index(后端 require_tab('vkpi','read');owner/
 * manager 可传 staffId 跨看,普通成员强制只看自己)。后端用 Gemini final_v1 深析的
 * **结构化信号**(非关键词 SQL)按 KOL 聚合出风险分 + 三维拆解:
 *   内容无深度(w1) · 素材复用(w2,方向未确认) · 竞品露出(w3,否定感知)。
 *
 * 诚实口径(核心):**只覆盖已深析 KOL**。未深析 KOL(analyzed=false / risk_index=null)
 * 显示「未分析」灰态,**绝不当 0 风险**——别把未知当无风险。
 *
 * 设计约束:不改 MyKolPage.tsx(集成者挂);不新建 CSS / 不改 services(用内联样式 +
 * 直接 apiFetch);仅依赖 services/http 的 apiFetch 与 lucide-react 图标。
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { AlertTriangle, RefreshCw, ShieldAlert } from 'lucide-react';
import { apiFetch } from '../../../../services/http';

// --- 后端响应类型(本地声明,避免改 services/vkpi/kol-api)。形状对齐 risk_index.compute_risk_index。
interface RiskDimension {
  label: string;
  score: number | null;
  weight: number;
  available: boolean;
  source?: string;
  matched_brands?: string[];
  direction_confirmed?: boolean;
}

interface RiskIndexItem {
  kol_pool_id: number;
  handle?: string | null;
  display_name?: string | null;
  platform?: string | null;
  analyzed: boolean;
  analyzed_video_count: number;
  risk_index: number | null;
  dimensions: {
    no_depth: RiskDimension;
    asset_reuse: RiskDimension;
    competitor_exposure: RiskDimension;
  } | null;
}

interface RiskIndexResponse {
  items?: RiskIndexItem[];
  summary?: {
    kol_count?: number;
    analyzed_kol_count?: number;
    unanalyzed_kol_count?: number;
    coverage_ratio?: number;
    high_risk_count?: number;
    weights?: Record<string, number>;
    deep_table_available?: boolean;
    notes?: Record<string, string>;
  };
  staff_id?: number;
}

interface RiskIndexPanelProps {
  apiToken: string;
  /** owner/manager 跨看某成员时传入;普通成员省略(后端强制只看自己)。 */
  staffId?: number;
}

// 风险分 -> 色档(诚实分级,非评分指纹;纯展示)。色值走 --ds-* token,浅深两主题自适配。
function riskColor(value: number): string {
  if (value >= 60) return 'var(--ds-crit)'; // 高风险:红
  if (value >= 35) return 'var(--ds-warn)'; // 中风险:黄
  return 'var(--ds-good)'; // 低风险:绿
}

function riskLabel(value: number): string {
  if (value >= 60) return '高风险';
  if (value >= 35) return '中风险';
  return '低风险';
}

function pct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${Math.round(value)}`;
}

// 表面/边线/文字全走设计 token(此前写死暗色 rgba,浅色主题下整块灰底+灰字看不清)。
const PANEL_BG = 'var(--ds-card)'; // 面板外壳
const CARD_BG = 'var(--ds-card-2)'; // 卡中卡:KOL 风险卡表面
const BORDER = '1px solid var(--ds-line)';
const MUTED = 'var(--ds-muted)';
// 刷新钮:与换肤层给全站 mykol 按钮的同一 token 三件套(--ds-panel/--ds-text-2/--ds-line)。
const ACTION_BTN: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  fontSize: 'var(--ds-fs-12)',
  padding: '4px 10px',
  borderRadius: 8,
  cursor: 'pointer',
  background: 'var(--ds-panel)',
  color: 'var(--ds-text-2)',
  border: '1px solid var(--ds-line)',
};

function DimensionRow({ dim }: { dim: RiskDimension }) {
  const unavailable = !dim.available || dim.score == null;
  const brands = dim.matched_brands ?? [];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--ds-fs-11-5)' }}>
      <span style={{ width: 72, color: MUTED, flexShrink: 0 }}>{dim.label}</span>
      <div style={{ flex: 1, height: 6, borderRadius: 4, background: 'var(--ds-line-strong)', overflow: 'hidden' }}>
        {!unavailable ? (
          <div
            style={{
              width: `${Math.max(0, Math.min(100, dim.score as number))}%`,
              height: '100%',
              background: riskColor(dim.score as number),
            }}
          />
        ) : null}
      </div>
      <span style={{ width: 56, textAlign: 'right', flexShrink: 0 }}>
        {unavailable ? (
          <span style={{ color: MUTED }} title="该维度此 KOL 视频无对应深析分">未分析</span>
        ) : (
          <span style={{ color: 'var(--ds-text)' }}>{pct(dim.score)}</span>
        )}
      </span>
      {dim.direction_confirmed === false ? (
        <span style={{ color: MUTED, fontSize: 'var(--ds-fs-10)' }} title="该维度语义方向尚未经线上抽样确认">?</span>
      ) : null}
      {brands.length > 0 ? (
        <span style={{ fontSize: 'var(--ds-fs-10)', color: 'var(--ds-crit)' }} title={`命中竞品:${brands.join(', ')}`}>
          {brands.slice(0, 3).join('/')}
        </span>
      ) : null}
    </div>
  );
}

function KolRiskCard({ item }: { item: RiskIndexItem }) {
  const name = item.display_name || item.handle || `KOL #${item.kol_pool_id}`;
  if (!item.analyzed || item.risk_index == null || item.dimensions == null) {
    // 未深析:灰态「未分析」,绝不渲染 0 风险。灰态用 muted 文字表达,不整卡打 opacity。
    return (
      <div
        style={{
          padding: 12,
          borderRadius: 10,
          border: BORDER,
          background: 'var(--ds-card)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 'var(--ds-fs-13)', color: 'var(--ds-text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {name}
            </div>
            {item.platform ? <div style={{ fontSize: 'var(--ds-fs-10-5)', color: MUTED }}>{item.platform}</div> : null}
          </div>
          <span
            style={{
              fontSize: 'var(--ds-fs-11)',
              padding: '3px 8px',
              borderRadius: 999,
              background: 'var(--ds-line)',
              color: MUTED,
              flexShrink: 0,
            }}
            title="该 KOL 尚无 ready 视频深析,风险未知(不等于无风险)"
          >
            未分析
          </span>
        </div>
      </div>
    );
  }

  const risk = item.risk_index;
  const color = riskColor(risk);
  return (
    <div style={{ padding: 12, borderRadius: 10, border: BORDER, background: CARD_BG }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--ds-fs-13)', color: 'var(--ds-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {name}
          </div>
          <div style={{ fontSize: 'var(--ds-fs-10-5)', color: MUTED }}>
            {item.platform ? `${item.platform} · ` : ''}
            {item.analyzed_video_count} 条已深析视频
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <span style={{ fontSize: 'var(--ds-fs-11)', color }}>{riskLabel(risk)}</span>
          <span
            style={{
              fontSize: 18,
              fontWeight: 700,
              color,
              lineHeight: 1,
              minWidth: 32,
              textAlign: 'right',
            }}
          >
            {pct(risk)}
          </span>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <DimensionRow dim={item.dimensions.no_depth} />
        <DimensionRow dim={item.dimensions.asset_reuse} />
        <DimensionRow dim={item.dimensions.competitor_exposure} />
      </div>
    </div>
  );
}

export function RiskIndexPanel({ apiToken, staffId }: RiskIndexPanelProps) {
  const [items, setItems] = useState<RiskIndexItem[]>([]);
  const [summary, setSummary] = useState<RiskIndexResponse['summary']>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    const query = staffId != null ? `?staff_id=${encodeURIComponent(String(staffId))}` : '';
    apiFetch<RiskIndexResponse>(`/api/admin/vkpi/my-kol/risk-index${query}`, {}, apiToken)
      .then((resp) => {
        if (cancelled) return;
        setItems(resp.items || []);
        setSummary(resp.summary);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String((err as Error)?.message || '').slice(0, 140) || '风险指数读取失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, staffId, reloadTick]);

  // 已分析的排前(按风险降序),未分析的沉底。
  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      const ra = a.analyzed && a.risk_index != null ? a.risk_index : -1;
      const rb = b.analyzed && b.risk_index != null ? b.risk_index : -1;
      if (ra === rb) return (a.display_name || '').localeCompare(b.display_name || '');
      return rb - ra;
    });
  }, [items]);

  const analyzedCount = summary?.analyzed_kol_count ?? items.filter((i) => i.analyzed).length;
  const total = summary?.kol_count ?? items.length;
  const coverage = total > 0 ? Math.round((analyzedCount / total) * 100) : 0;

  return (
    <section style={{ marginTop: 24, padding: 20, borderRadius: 14, border: BORDER, background: PANEL_BG, color: 'var(--ds-text)' }}>
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
            <ShieldAlert size={16} /> KOL 风险指数
          </h2>
          <p style={{ margin: '4px 0 0', fontSize: 'var(--ds-fs-12)', color: MUTED }}>
            内容无深度 · 素材复用 · 竞品露出(基于视频深析,实时聚合)
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 'var(--ds-fs-11-5)', color: MUTED }}>
            已分析 {analyzedCount}/{total}（{coverage}%）
            {summary?.high_risk_count ? (
              <span style={{ color: 'var(--ds-crit)', marginLeft: 8 }}>· 高风险 {summary.high_risk_count}</span>
            ) : null}
          </span>
          <button type="button" onClick={() => setReloadTick((t) => t + 1)} style={ACTION_BTN}>
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
      </header>

      {error ? (
        <p style={{ fontSize: 'var(--ds-fs-13)', color: 'var(--ds-crit)', margin: '8px 0', display: 'flex', alignItems: 'center', gap: 6 }}>
          <AlertTriangle size={14} /> 读取失败:{error}
        </p>
      ) : null}

      {loading && items.length === 0 ? (
        <p style={{ fontSize: 'var(--ds-fs-13)', color: MUTED, margin: '8px 0' }}>加载中…</p>
      ) : null}

      {!loading && items.length === 0 && !error ? (
        <p style={{ fontSize: 'var(--ds-fs-13)', color: MUTED, margin: '8px 0' }}>该负责人名下暂无 KOL(收藏 / 共享均为空)。</p>
      ) : null}

      {items.length > 0 ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 12,
          }}
        >
          {sorted.map((item) => (
            <KolRiskCard key={item.kol_pool_id} item={item} />
          ))}
        </div>
      ) : null}

      <p style={{ fontSize: 'var(--ds-fs-11)', color: MUTED, marginTop: 14, lineHeight: 1.6 }}>
        风险分 = 加权合成(内容无深度 / 素材复用 / 竞品露出),数据源为视频深析结构化信号(Gemini final_v1）。
        竞品露出用否定感知判定(口径同 dashboard 过滤），「素材复用」语义方向标 ? 表示尚未经线上抽样确认。
        <br />
        未深析的 KOL 显示「未分析」灰态(风险未知，<strong>不等于无风险</strong>)；仅约 18.6% KOL 已深析。
      </p>
    </section>
  );
}

export default RiskIndexPanel;
