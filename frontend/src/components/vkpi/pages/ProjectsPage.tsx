import React, { useEffect, useMemo, useState } from 'react';
import type { VkpiDashboardData, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { ProjectFlowStepper } from '../shared/ProjectFlowStepper';
import { ProjectSelect } from '../shared/ProjectSelect';
import { stageLabels } from '../shared/vkpiConstants';
import { nextProjectStage, previousProjectStage } from '../shared/vkpiDataUtils';
import { ProjectTable } from '../tables/ProjectTable';
import { PageShell } from './PageShell';

interface ProjectsPageProps {
  data: VkpiDashboardData;
  filteredProjects: VkpiProjectRow[];
  selectedProjectId?: string;
  selectedProject?: VkpiProjectRow;
  viewMode: 'manager' | 'employee';
  onSelectProject: (project: VkpiProjectRow) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onCreateProject?: (payload: { projectName: string; kolId?: string; productSku?: string; productName?: string; productSkus?: string[]; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; note?: string }) => Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: (projectId: string, reason?: string) => Promise<void>;
  onAddProjectCost?: (payload: { projectId: string; costType: string; amountUsd: number; note?: string; sourceRef?: string }) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
}

export function ProjectsPage({ data, filteredProjects, selectedProjectId, selectedProject, viewMode, onSelectProject, onOpenKolProfile, onOpenStaffProfile, onCreateProject, onMoveProjectStage, onDeleteProject, onAddProjectCost, onUploadEvidenceFile }: ProjectsPageProps) {
  const [projectName, setProjectName] = useState('');
  const [kolId, setKolId] = useState('');
  const [productSku, setProductSku] = useState('');
  const [productName, setProductName] = useState('');
  const [selectedProductSkus, setSelectedProductSkus] = useState<string[]>([]);
  const [stageProjectId, setStageProjectId] = useState(selectedProjectId || '');
  const [stageNote, setStageNote] = useState('');
  const [stageEvidenceFile, setStageEvidenceFile] = useState<File | null>(null);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [contentUrl, setContentUrl] = useState('');
  const [costProjectId, setCostProjectId] = useState(selectedProjectId || '');
  const [shippingAmount, setShippingAmount] = useState('');
  const [promotionAmount, setPromotionAmount] = useState('');
  const [costNote, setCostNote] = useState('');
  const [costEvidenceFile, setCostEvidenceFile] = useState<File | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const activeProject = data.projects.find((project) => project.id === stageProjectId) || selectedProject || filteredProjects[0];
  const nextStage = activeProject ? nextProjectStage(activeProject.stage) : null;
  const previousStage = activeProject ? previousProjectStage(activeProject.stage) : null;
  const productChoices = useMemo(() => {
    const bySku = new Map<string, { id: string; productSku: string; productName: string; active: boolean; sourceLabel: string }>();
    data.productCosts
      .filter((item) => item.active !== false)
      .forEach((item) => {
        if (!item.productSku) return;
        bySku.set(item.productSku, {
          id: item.id || item.productSku,
          productSku: item.productSku,
          productName: item.productName || item.productSku,
          active: item.active,
          sourceLabel: '成本目录',
        });
      });
    data.productLaunches.forEach((launch) => {
      const sku = launch.productSku || launch.id;
      if (!sku || bySku.has(sku)) return;
      bySku.set(sku, {
        id: launch.id || sku,
        productSku: sku,
        productName: launch.productName || launch.launchName || sku,
        active: true,
        sourceLabel: launch.status ? `产品发布 · ${launch.status}` : '产品发布',
      });
    });
    return Array.from(bySku.values()).sort((a, b) => a.productName.localeCompare(b.productName));
  }, [data.productCosts, data.productLaunches]);
  const selectedProductLabels = selectedProductSkus
    .map((sku) => productChoices.find((item) => item.productSku === sku))
    .filter(Boolean)
    .map((item) => `${item?.productName || item?.productSku} (${item?.productSku})`);

  useEffect(() => {
    if (selectedProjectId) {
      setStageProjectId(selectedProjectId);
      setCostProjectId(selectedProjectId);
    }
  }, [selectedProjectId]);

  const submitProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateProject || !projectName.trim()) return;
    const primarySku = productSku.trim() || selectedProductSkus[0] || '';
    const selectedProducts = selectedProductSkus
      .map((sku) => productChoices.find((item) => item.productSku === sku))
      .filter((item): item is { id: string; productSku: string; productName: string; active: boolean; sourceLabel: string } => Boolean(item))
      .map((item) => ({ productSku: item.productSku, productName: item.productName }));
    setBusy(true);
    try {
      await onCreateProject({
        projectName: projectName.trim(),
        kolId: kolId.trim() || undefined,
        productSku: primarySku || undefined,
        productName: (productName.trim() || selectedProducts[0]?.productName) || undefined,
        productSkus: selectedProductSkus.length ? selectedProductSkus : undefined,
        products: selectedProducts.length ? selectedProducts : undefined,
        note: selectedProductLabels.length > 1 ? `关联产品：${selectedProductLabels.join('、')}` : undefined,
      });
      setProjectName('');
      setKolId('');
      setProductSku('');
      setProductName('');
      setSelectedProductSkus([]);
      setMessage('项目已创建。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '项目创建失败');
    } finally {
      setBusy(false);
    }
  };

  const selectPrimaryProduct = (sku: string) => {
    setProductSku(sku);
    const product = productChoices.find((item) => item.productSku === sku);
    setProductName(product?.productName || '');
    setSelectedProductSkus((current) => sku && !current.includes(sku) ? [...current, sku] : current);
  };

  const toggleProduct = (sku: string) => {
    setSelectedProductSkus((current) => current.includes(sku) ? current.filter((item) => item !== sku) : [...current, sku]);
  };

  const moveStage = async (toStage: VkpiProjectStage | null, direction: 'next' | 'prev') => {
    if (!toStage || !onMoveProjectStage || !stageProjectId) return;
    if (toStage === 'shipped' && !trackingNumber.trim()) {
      setMessage('发货阶段需要填写物流单号。');
      return;
    }
    if (toStage === 'published' && !contentUrl.trim()) {
      setMessage('验收视频阶段需要填写视频链接。');
      return;
    }
    setBusy(true);
    try {
      let evidenceUrl = '';
      if (stageEvidenceFile && onUploadEvidenceFile) {
        const upload = await onUploadEvidenceFile(stageEvidenceFile, { entityType: 'project_stage', entityId: stageProjectId, purpose: 'stage_note' });
        evidenceUrl = String(upload.file_url || upload.fileUrl || '');
      }
      const nextNote = [stageNote.trim(), evidenceUrl ? `附件：${evidenceUrl}` : ''].filter(Boolean).join('\n') || undefined;
      await onMoveProjectStage(stageProjectId, toStage, nextNote, {
        trackingNumber: trackingNumber.trim() || undefined,
        sampleStatus: toStage === 'shipped' ? 'shipped' : undefined,
        sourceRefType: toStage === 'published' ? 'content_url' : undefined,
        sourceRefId: toStage === 'published' ? (contentUrl.trim() || evidenceUrl || undefined) : evidenceUrl || undefined,
      });
      setMessage(direction === 'next' ? `已推进到：${stageLabels[toStage]}` : `已回退到：${stageLabels[toStage]}`);
      setStageNote('');
      setStageEvidenceFile(null);
      if (toStage !== 'shipped') setTrackingNumber('');
      if (toStage !== 'published') setContentUrl('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '阶段更新失败');
    } finally {
      setBusy(false);
    }
  };
  const submitCost = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onAddProjectCost || !costProjectId) return;
    const shipping = Number(shippingAmount || 0);
    const promotion = Number(promotionAmount || 0);
    if (!shipping && !promotion) {
      setMessage('请填写快递费或推广费。');
      return;
    }
    setBusy(true);
    try {
      let evidenceUrl = '';
      if (costEvidenceFile && onUploadEvidenceFile) {
        const upload = await onUploadEvidenceFile(costEvidenceFile, { entityType: 'project_cost', entityId: costProjectId, purpose: 'cost_receipt' });
        evidenceUrl = String(upload.file_url || upload.fileUrl || '');
      }
      const noteWithEvidence = [costNote.trim(), evidenceUrl ? `附件：${evidenceUrl}` : ''].filter(Boolean).join('\n') || undefined;
      if (shipping > 0) {
        await onAddProjectCost({ projectId: costProjectId, costType: 'shipping', amountUsd: shipping, note: noteWithEvidence || '快递费', sourceRef: evidenceUrl || undefined });
      }
      if (promotion > 0) {
        await onAddProjectCost({ projectId: costProjectId, costType: 'cash_fee', amountUsd: promotion, note: noteWithEvidence || '推广费用', sourceRef: evidenceUrl || undefined });
      }
      setShippingAmount('');
      setPromotionAmount('');
      setCostNote('');
      setCostEvidenceFile(null);
      setMessage('快递费 / 推广费已计入项目。样品成本由系统在发货阶段自动处理。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成本添加失败');
    } finally {
      setBusy(false);
    }
  };
  const deleteProjectRow = async (project: VkpiProjectRow) => {
    if (!onDeleteProject) return;
    const confirmed = window.confirm(`确认删除项目「${project.campaign}」？\n\n项目会从列表隐藏，相关 live 短链会暂停；历史成本、归因、阶段记录会保留。`);
    if (!confirmed) return;
    setBusy(true);
    try {
      await onDeleteProject(project.id, `删除项目：${project.campaign}`);
      setMessage('项目已删除，历史记录已保留。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '项目删除失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title="项目跟进" description="创建项目、按流程一键推进，系统自动记录从开始到完成的耗时。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="新建项目" />
          <form className="vkpi-form-stack" onSubmit={submitProject}>
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="项目名称，例如 35mm F1.2 Instagram 合作" />
            <label>合作红人
              {data.kolOptions.length ? (
                <select value={kolId} onChange={(event) => setKolId(event.target.value)}>
                  <option value="">选择已有红人</option>
                  {data.kolOptions.map((kol) => <option key={kol.id} value={kol.id}>{kol.name} · {kol.handle} · {kol.platform}</option>)}
                </select>
              ) : (
                <>
                  <input value={kolId} onChange={(event) => setKolId(event.target.value)} placeholder="输入已有 KOL ID（临时兜底）" />
                  <span className="vkpi-help-text">当前没有可选红人。优先从红人搜索认领/导入；临时测试才填写真实 KOL ID。</span>
                </>
              )}
            </label>
            <label>主产品
              {productChoices.length ? (
                <select value={productSku} onChange={(event) => selectPrimaryProduct(event.target.value)}>
                  <option value="">选择产品 SKU</option>
                  {productChoices.map((product) => <option key={product.id || product.productSku} value={product.productSku}>{product.productName || product.productSku} · {product.productSku} · {product.sourceLabel}</option>)}
                </select>
              ) : (
                <input value={productSku} onChange={(event) => setProductSku(event.target.value)} placeholder="产品 SKU" />
              )}
            </label>
            {!productChoices.length ? <input value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="产品名称" /> : null}
            {productChoices.length ? (
              <div className="vkpi-chip-list" aria-label="可关联产品">
                {productChoices.slice(0, 8).map((product) => (
                  <button className={`vkpi-chip ${selectedProductSkus.includes(product.productSku) ? 'is-selected' : ''}`} type="button" key={product.id || product.productSku} onClick={() => toggleProduct(product.productSku)}>
                    {product.productName || product.productSku}
                  </button>
                ))}
              </div>
            ) : null}
            {selectedProductLabels.length ? <span className="vkpi-help-text">已关联：{selectedProductLabels.join('、')}</span> : null}
            <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !onCreateProject}>创建项目</button>
          </form>
        </section>
        <section className="vkpi-card vkpi-action-card vkpi-action-card--wide">
          <CardHeader title="自动推进流程" />
          <div className="vkpi-form-stack">
            <ProjectSelect projects={data.projects} value={stageProjectId} onChange={setStageProjectId} />
            {activeProject ? (
              <>
                <ProjectFlowStepper project={activeProject} />
                <div className="vkpi-stage-current">
                  <div><span>当前阶段</span><strong>{stageLabels[activeProject.stage]}</strong></div>
                  <div><span>项目总耗时</span><strong>{activeProject.totalDurationLabel || '-'}</strong></div>
                  <div><span>本阶段停留</span><strong>{activeProject.stageDurationLabel || '-'}</strong></div>
                </div>
              </>
            ) : <div className="vkpi-empty-state">暂无可推进项目。</div>}
            {nextStage === 'shipped' ? <input value={trackingNumber} onChange={(event) => setTrackingNumber(event.target.value)} placeholder="物流单号（发货必填）" /> : null}
            {nextStage === 'published' ? <input value={contentUrl} onChange={(event) => setContentUrl(event.target.value)} placeholder="验收视频 / 内容链接（发布必填）" /> : null}
            <input value={stageNote} onChange={(event) => setStageNote(event.target.value)} placeholder="备注，例如已邮件联系 / KOL 回复 / 发货说明" />
            <label className="vkpi-upload-row">阶段附件 / PDF / 截图<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setStageEvidenceFile(event.target.files?.[0] || null)} /></label>
            <div className="vkpi-stage-actions">
              <button className="vkpi-button" type="button" onClick={() => void moveStage(previousStage, 'prev')} disabled={busy || !previousStage || !onMoveProjectStage}>返回上一步{previousStage ? `：${stageLabels[previousStage]}` : ''}</button>
              <button className="vkpi-button vkpi-button--primary" type="button" onClick={() => void moveStage(nextStage, 'next')} disabled={busy || !nextStage || !onMoveProjectStage}>完成当前步，进入{nextStage ? `：${stageLabels[nextStage]}` : '下一步'}</button>
            </div>
          </div>
        </section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="登记快递 / 推广费" /><form className="vkpi-form-stack" onSubmit={submitCost}><ProjectSelect projects={data.projects} value={costProjectId} onChange={setCostProjectId} /><input value={shippingAmount} onChange={(event) => setShippingAmount(event.target.value)} placeholder="快递费 USD" inputMode="decimal" /><input value={promotionAmount} onChange={(event) => setPromotionAmount(event.target.value)} placeholder="推广费用 USD（可选）" inputMode="decimal" /><input value={costNote} onChange={(event) => setCostNote(event.target.value)} placeholder="备注 / 单号 / 凭证" /><label className="vkpi-upload-row">凭证附件<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setCostEvidenceFile(event.target.files?.[0] || null)} /></label><span className="vkpi-help-text">员工只登记快递费和推广费；样品内部成本由系统在发货后自动处理。</span><button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !onAddProjectCost}>计入快递 / 推广费</button></form></section>
      </section>
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>项目列表</h2><span>{filteredProjects.length} 条真实项目</span></div></div><ProjectTable projects={filteredProjects} selectedProjectId={selectedProjectId} viewMode={viewMode} onSelectProject={onSelectProject} onOpenKolProfile={onOpenKolProfile} onOpenStaffProfile={onOpenStaffProfile} onDeleteProject={onDeleteProject ? deleteProjectRow : undefined} /></section>
    </PageShell>
  );
}
