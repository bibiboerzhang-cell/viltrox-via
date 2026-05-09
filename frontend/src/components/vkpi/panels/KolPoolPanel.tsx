// frontend/src/components/vkpi/panels/KolPoolPanel.tsx
//
// R59: KOL Pool 管理面板
//
// 功能:
//   - 列表展示 (search / filter by platform)
//   - 一键链接到主 KOL
//   - 显示 dedup 状态 (linked_main_kol_id)
//   - import 入口 (跳转到 Apify settings 或显示批量导入弹窗)

import { useEffect, useState } from 'react';

import { CardHeader } from '../shared/CardHeader';
import { PlatformPill } from '../shared/PlatformPill';
import { Avatar } from '../shared/Avatar';
import type { VkpiPlatform } from '../vkpiTypes';

interface KolPoolItem {
  id: number;
  pool_uid: string;
  platform: string;
  handle: string;
  display_name?: string;
  avatar_url?: string;
  followers?: number;
  avg_views?: number;
  engagement_rate?: number;
  viltrox_fit_score?: number;
  linked_main_kol_id?: number | null;
  source_type?: string;
  source_ref?: string;
  created_at?: string;
}

interface KolPoolPanelProps {
  apiToken: string;
  onListPool: (params: { search?: string; platform?: string; limit?: number }) => Promise<{ items?: KolPoolItem[] }>;
  onLinkToMain?: (kolPoolId: number, mainKolId: number) => Promise<void>;
  onOpenImport?: () => void;
}

export function KolPoolPanel({ apiToken, onListPool, onLinkToMain, onOpenImport }: KolPoolPanelProps) {
  const [items, setItems] = useState<KolPoolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [platform, setPlatform] = useState('');
  const [error, setError] = useState('');
  const [linkingId, setLinkingId] = useState<number | null>(null);

  async function loadList() {
    if (!apiToken) {
      setError('未登录');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const result = await onListPool({ search, platform, limit: 100 });
      setItems(result.items || []);
    } catch (err) {
      setError((err as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken]);

  async function handleLinkToMain(item: KolPoolItem) {
    if (!onLinkToMain) {
      setError('链接功能未配置');
      return;
    }
    const input = window.prompt(`输入主 KOL ID(链接 ${item.handle} 到 kols 表):`);
    if (!input) return;
    const mainId = Number(input);
    if (!mainId || isNaN(mainId)) {
      setError('无效的主 KOL ID');
      return;
    }
    setLinkingId(item.id);
    try {
      await onLinkToMain(item.id, mainId);
      await loadList();
    } catch (err) {
      setError((err as Error).message || '链接失败');
    } finally {
      setLinkingId(null);
    }
  }

  const platformOptions = ['', 'instagram', 'tiktok', 'youtube', 'xiaohongshu', 'x', 'bilibili'];

  return (
    <div className="vkpi-card">
      <CardHeader title="KOL Pool 候选池" />
      <p className="vkpi-muted">从 Apify / CSV 导入的潜在合作 KOL，链接后进入主表 kols。</p>

      {/* 控件栏 */}
      <div className="vkpi-form-row" style={{ marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <input
          className="vkpi-input"
          placeholder="搜索 handle / display_name / bio"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void loadList();
          }}
          style={{ flex: '1 1 240px' }}
        />
        <select
          className="vkpi-input"
          value={platform}
          onChange={(event) => setPlatform(event.target.value)}
          style={{ flex: '0 0 160px' }}
        >
          {platformOptions.map((p) => (
            <option key={p || 'all'} value={p}>
              {p || '全部平台'}
            </option>
          ))}
        </select>
        <button className="vkpi-button" type="button" onClick={() => void loadList()} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
        {onOpenImport && (
          <button className="vkpi-button vkpi-button--primary" type="button" onClick={onOpenImport}>
            一键导入
          </button>
        )}
      </div>

      {error && (
        <div className="vkpi-alert vkpi-alert--error" style={{ marginBottom: 12 }}>
          {error}
        </div>
      )}

      {/* 列表 */}
      {!loading && items.length === 0 ? (
        <div className="vkpi-empty">
          暂无候选 KOL。点击"一键导入"从 Apify / CSV 导入。
        </div>
      ) : (
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead>
              <tr>
                <th style={{ width: 48 }}></th>
                <th>Handle / Name</th>
                <th>平台</th>
                <th>粉丝</th>
                <th>平均播放</th>
                <th>互动率</th>
                <th>适配度</th>
                <th>状态</th>
                <th>来源</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Avatar
                      src={item.avatar_url}
                      name={item.display_name || item.handle}
                      size="sm"
                    />
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{item.handle}</div>
                    {item.display_name && (
                      <div style={{ fontSize: 12, color: 'var(--vkpi-color-text-muted)' }}>
                        {item.display_name}
                      </div>
                    )}
                  </td>
                  <td>
                    <PlatformPill platform={toVkpiPlatform(item.platform)} />
                  </td>
                  <td>{formatNumber(item.followers)}</td>
                  <td>{formatNumber(item.avg_views)}</td>
                  <td>{item.engagement_rate ? `${item.engagement_rate.toFixed(2)}%` : '—'}</td>
                  <td>
                    {item.viltrox_fit_score
                      ? <span className="vkpi-chip">{item.viltrox_fit_score.toFixed(1)}</span>
                      : '—'}
                  </td>
                  <td>
                    {item.linked_main_kol_id ? (
                      <span className="vkpi-chip is-success">已链接 #{item.linked_main_kol_id}</span>
                    ) : (
                      <span className="vkpi-chip">候选</span>
                    )}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--vkpi-color-text-muted)' }}>
                    {item.source_type || '—'}
                  </td>
                  <td>
                    {!item.linked_main_kol_id && onLinkToMain && (
                      <button
                        className="vkpi-button vkpi-button--small"
                        type="button"
                        onClick={() => void handleLinkToMain(item)}
                        disabled={linkingId === item.id}
                      >
                        {linkingId === item.id ? '链接中…' : '链接到主表'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function formatNumber(value: number | undefined | null): string {
  if (value === undefined || value === null) return '—';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function toVkpiPlatform(platform: string): VkpiPlatform {
  const normalized = String(platform || '').toLowerCase();
  const map: Record<string, VkpiPlatform> = {
    instagram: 'Instagram',
    tiktok: 'TikTok',
    youtube: 'YouTube',
    xiaohongshu: 'XHS',
    xhs: 'XHS',
    x: 'X',
    twitter: 'X',
    bilibili: 'Bilibili',
    facebook: 'Facebook',
    reddit: 'Reddit',
    threads: 'Threads',
    pinterest: 'Pinterest',
    website: 'Website',
  };
  return map[normalized] || 'Other';
}
