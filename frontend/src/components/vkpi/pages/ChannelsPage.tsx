import React, { useEffect, useState } from 'react';
import {
  bindEmployeeChannel,
  listEmployeeChannels,
  listTeamChannels,
  syncEmployeeChannel,
} from '../../../services/vkpi.ui-api';
import type { VkpiDashboardData } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { creatorPlatformOptions } from '../shared/vkpiConstants';
import { numberFormatter } from '../shared/vkpiFormatters';
import { platformDisplay, safeNumber } from '../shared/vkpiDataUtils';
import { PageShell } from './PageShell';

interface ChannelsPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
  data: VkpiDashboardData;
}

export function ChannelsPage({ apiToken, viewMode, data }: ChannelsPageProps) {
  const [channelsRows, setChannelsRows] = useState<Array<Record<string, unknown>>>([]);
  const [teamRows, setTeamRows] = useState<Array<Record<string, unknown>>>([]);
  const [platform, setPlatform] = useState('youtube');
  const [handle, setHandle] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [viewAsStaffId, setViewAsStaffId] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!apiToken) return;
    const channelsResult = await listEmployeeChannels(apiToken, viewMode === 'manager' ? viewAsStaffId || undefined : undefined).catch(() => ({ channels: [] }));
    setChannelsRows(channelsResult.channels || []);
    if (viewMode === 'manager') {
      const teamResult = await listTeamChannels(apiToken).catch(() => ({ rows: [] }));
      setTeamRows(teamResult.rows || []);
    }
  };

  useEffect(() => {
    void refresh();
  }, [apiToken, viewAsStaffId, viewMode]);

  const submitChannel = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !handle.trim()) return;
    setBusy(true);
    try {
      await bindEmployeeChannel(apiToken, { platform, account_handle: handle.trim(), account_display_name: displayName.trim() || handle.trim(), api_key: apiKey.trim() || undefined }, viewMode === 'manager' ? viewAsStaffId || undefined : undefined);
      setHandle('');
      setApiKey('');
      setDisplayName('');
      setMessage('平台账号已绑定。API Key 已由后端加密存储；当前前端不会读取完整 Key。');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '平台绑定失败');
    } finally {
      setBusy(false);
    }
  };

  const runSync = async (id: unknown) => {
    if (!apiToken || !id) return;
    setBusy(true);
    try {
      const response = await syncEmployeeChannel(apiToken, String(id));
      setMessage(String(response.message || '同步请求完成。'));
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '同步失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title={viewMode === 'manager' ? '员工平台 / 团队矩阵' : '我的平台'} description="员工绑定自己运营的平台，管理层可看团队矩阵；未配置真实 API 时显示待同步/未配置，不显示假 0 数据。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="绑定平台账号" />
          <form className="vkpi-form-stack" onSubmit={submitChannel}>
            {viewMode === 'manager' ? <select value={viewAsStaffId} onChange={(event) => setViewAsStaffId(event.target.value)}><option value="">当前账号 / 全部</option>{data.staffMembers.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.email}</option>)}</select> : null}
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            <input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder="@handle / 主页 ID" />
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名称（可选）" />
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API Key（可选，后端加密，前端不回显）" />
            <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">绑定平台</button>
          </form>
        </section>
        {viewMode === 'manager' ? <section className="vkpi-card vkpi-action-card"><CardHeader title="团队矩阵" /><InfoBlock label="团队行数" value={String(teamRows.length)} /><InfoBlock label="当前筛选员工" value={viewAsStaffId || '全部'} /></section> : null}
      </section>
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>{viewMode === 'manager' ? '平台绑定列表' : '我的平台列表'}</h2><span>{channelsRows.length} 条</span></div></div>
        <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>平台</th><th>账号</th><th>状态</th><th>同步</th><th>粉丝</th><th>帖子</th><th>播放</th><th>操作</th></tr></thead><tbody>{channelsRows.length ? channelsRows.map((row) => <tr key={String(row.id)}><td>{platformDisplay(row.platform)}</td><td>{String(row.account_display_name || row.account_handle || '-')}<br /><small>{row.api_key_mask ? `Key ${String(row.api_key_mask)}` : '未填写 API Key'}</small></td><td>{String(row.status || '-')}</td><td>{String(row.last_sync_status || row.sync_status || '待同步')}</td><td>{row.latest_followers == null ? '待同步' : numberFormatter.format(safeNumber(row.latest_followers))}</td><td>{row.latest_posts == null ? '待同步' : numberFormatter.format(safeNumber(row.latest_posts))}</td><td>{row.latest_views == null ? '待同步' : numberFormatter.format(safeNumber(row.latest_views))}</td><td><button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void runSync(row.id)}>同步</button></td></tr>) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无平台绑定。</td></tr>}</tbody></table></div>
      </section>
      {viewMode === 'manager' ? <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>团队矩阵</h2><span>{teamRows.length} 人</span></div></div><div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>员工</th><th>活跃平台</th><th>异常</th><th>总粉丝</th><th>总播放</th><th>最近同步</th></tr></thead><tbody>{teamRows.length ? teamRows.map((row, index) => <tr key={String(row.staff_id || index)}><td>{String(row.staff_name || row.staff_id || '-')}</td><td>{String(row.active_channels || 0)}</td><td>{String(row.error_channels || 0)}</td><td>{row.total_followers == null ? '待同步' : numberFormatter.format(safeNumber(row.total_followers))}</td><td>{row.total_views == null ? '待同步' : numberFormatter.format(safeNumber(row.total_views))}</td><td>{String(row.most_recent_sync_at || '-')}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无团队平台数据。</td></tr>}</tbody></table></div></section> : null}
    </PageShell>
  );
}
