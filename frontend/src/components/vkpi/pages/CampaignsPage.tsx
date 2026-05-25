import React, { useEffect, useState } from 'react';
import {
  addCampaignProject,
  createBudgetPool,
  createCampaign,
  initiateOffboarding,
  listBudgetPools,
  listCampaigns,
} from '../../../domains/projects';
import type { VkpiDashboardData } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { ProjectSelect } from '../shared/ProjectSelect';
import { currencyFormatter } from '../shared/vkpiFormatters';
import { safeNumber } from '../shared/vkpiDataUtils';
import { PageShell } from './PageShell';

interface CampaignsPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
  data: VkpiDashboardData;
}

export function CampaignsPage({ apiToken, viewMode, data }: CampaignsPageProps) {
  const [campaignsRows, setCampaignsRows] = useState<Array<Record<string, unknown>>>([]);
  const [budgetRows, setBudgetRows] = useState<Array<Record<string, unknown>>>([]);
  const [campaignName, setCampaignName] = useState('');
  const [campaignProduct, setCampaignProduct] = useState('');
  const [campaignId, setCampaignId] = useState('');
  const [projectId, setProjectId] = useState('');
  const [budgetName, setBudgetName] = useState('');
  const [budgetUsd, setBudgetUsd] = useState('');
  const [offboardStaffId, setOffboardStaffId] = useState('');
  const [newOwnerStaffId, setNewOwnerStaffId] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!apiToken || viewMode !== 'manager') return;
    const [campaignResult, budgetResult] = await Promise.all([
      listCampaigns(apiToken).catch(() => ({ campaigns: [] })),
      listBudgetPools(apiToken).catch(() => ({ budget_pools: [] })),
    ]);
    setCampaignsRows(campaignResult.campaigns || []);
    setBudgetRows(budgetResult.budget_pools || []);
  };

  useEffect(() => { void refresh(); }, [apiToken, viewMode]);

  if (viewMode !== 'manager') {
    return <PageShell title="活动预算" description="该页面只对管理层开放。"><section className="vkpi-card"><div className="vkpi-empty-state">员工视角不能查看预算池、镜头单价或离职交接。</div></section></PageShell>;
  }

  const submitCampaign = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !campaignName.trim()) return;
    setBusy(true);
    try {
      await createCampaign(apiToken, { campaign_name: campaignName.trim(), product_sku: campaignProduct.trim() || undefined });
      setCampaignName('');
      setCampaignProduct('');
      setMessage('活动已创建。');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '活动创建失败');
    } finally {
      setBusy(false);
    }
  };

  const submitCampaignProject = async () => {
    if (!apiToken || !campaignId || !projectId) return;
    setBusy(true);
    try {
      await addCampaignProject(apiToken, campaignId, projectId);
      setMessage('项目已归入活动。');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '项目归入活动失败');
    } finally {
      setBusy(false);
    }
  };

  const submitBudget = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !budgetName.trim()) return;
    setBusy(true);
    try {
      await createBudgetPool(apiToken, { pool_name: budgetName.trim(), total_budget_usd: Number(budgetUsd || 0) });
      setBudgetName('');
      setBudgetUsd('');
      setMessage('预算池已创建。预算池只在管理层可见。');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '预算池创建失败');
    } finally {
      setBusy(false);
    }
  };

  const submitOffboarding = async () => {
    if (!apiToken || !offboardStaffId) return;
    setBusy(true);
    try {
      await initiateOffboarding(apiToken, offboardStaffId, newOwnerStaffId || undefined);
      setMessage('已生成离职/转岗交接预案。不会修改已实际发生的成本。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '交接预案生成失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title="活动 / 预算池 / 离职交接" description="P5 只落地 3 个：Campaigns、Budget Pool、Offboarding；成本状态适配 actual/void。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card"><CardHeader title="创建活动" /><form className="vkpi-form-stack" onSubmit={submitCampaign}><input value={campaignName} onChange={(event) => setCampaignName(event.target.value)} placeholder="活动名称，例如 35mm F1.2 Q2 KOL 推广" /><input value={campaignProduct} onChange={(event) => setCampaignProduct(event.target.value)} placeholder="产品 SKU（可选）" /><button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">创建活动</button></form></section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="项目归入活动" /><div className="vkpi-form-stack"><select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">选择活动</option>{campaignsRows.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.campaign_name || row.campaign_uid)}</option>)}</select><ProjectSelect projects={data.projects} value={projectId} onChange={setProjectId} allowEmpty /><button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken || !campaignId || !projectId} type="button" onClick={() => void submitCampaignProject()}>归入活动</button></div></section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="创建预算池" /><form className="vkpi-form-stack" onSubmit={submitBudget}><input value={budgetName} onChange={(event) => setBudgetName(event.target.value)} placeholder="预算池名称" /><input value={budgetUsd} onChange={(event) => setBudgetUsd(event.target.value)} placeholder="总预算 USD" inputMode="decimal" /><button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">创建预算池</button></form></section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="离职 / 转岗交接" /><div className="vkpi-form-stack"><select value={offboardStaffId} onChange={(event) => setOffboardStaffId(event.target.value)}><option value="">选择离职员工</option>{data.staffMembers.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.email}</option>)}</select><select value={newOwnerStaffId} onChange={(event) => setNewOwnerStaffId(event.target.value)}><option value="">新负责人（可选）</option>{data.staffMembers.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.email}</option>)}</select><button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken || !offboardStaffId} type="button" onClick={() => void submitOffboarding()}>生成交接预案</button></div></section>
      </section>
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      <section className="vkpi-card-grid vkpi-card-grid--forms"><section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>活动列表</h2><span>{campaignsRows.length} 个</span></div></div><div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>活动</th><th>产品</th><th>状态</th><th>负责人</th><th>更新</th></tr></thead><tbody>{campaignsRows.length ? campaignsRows.map((row) => <tr key={String(row.id)}><td>{String(row.campaign_name || '-')}</td><td>{String(row.product_sku || '-')}</td><td>{String(row.status || '-')}</td><td>{String(row.owner_staff_id || '-')}</td><td>{String(row.updated_at || '-')}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={5}>暂无活动。</td></tr>}</tbody></table></div></section><section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>预算池</h2><span>{budgetRows.length} 个</span></div></div><div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>预算池</th><th>总预算</th><th>已分配</th><th>已实际发生</th><th>状态</th></tr></thead><tbody>{budgetRows.length ? budgetRows.map((row) => <tr key={String(row.id)}><td>{String(row.pool_name || '-')}</td><td>{currencyFormatter.format(safeNumber(row.total_budget_cents) / 100)}</td><td>{currencyFormatter.format(safeNumber(row.allocated_cents) / 100)}</td><td>{currencyFormatter.format(safeNumber(row.spent_cents) / 100)}</td><td>{String(row.status || '-')}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={5}>暂无预算池。</td></tr>}</tbody></table></div></section></section>
    </PageShell>
  );
}
