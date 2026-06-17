import React, { useEffect, useMemo, useState } from 'react';
import type { VkpiDashboardData, VkpiKolOption, VkpiPageKey, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../vkpiTypes';
import { clearProjectFocus, readProjectFocus, type IntelligenceProjectFocusPayload } from '../intelligence/intelligenceProjectFocus';
import { useProjectDetail } from '../hooks/useProjectDetail';
import { addKolsToProject, advanceProjectKol, getAvailableProjectKols, submitProjectKolActionStub, updateProjectFollowStatus, updateProjectKolShipping, updateProjectStar } from '../../../domains/projects';
import { listProductCatalog } from '../../../domains/products';
import { buildKolOptions } from '../../../domains/kol';
import { PageShell } from './PageShell';
import { ProjectCampaignBoard } from './projects/ProjectCampaignBoard';
import { ProjectDueListCard } from './projects/ProjectDueListCard';
import { ProjectDetailView } from './projects/ProjectDetailView';
import { useAuth } from '../../../hooks/useAuth';
import './projects/projectBoard.css';

interface ProjectsPageProps {
  data: VkpiDashboardData;
  filteredProjects: VkpiProjectRow[];
  selectedProjectId?: string;
  selectedProject?: VkpiProjectRow;
  openProjectId?: string;
  viewMode: 'manager' | 'employee';
  onSelectProject: (project: VkpiProjectRow) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; email?: string; contactEmail?: string; notes?: string; scanAccount?: boolean; maxPosts?: number; productSku?: string }) => Promise<{ kol?: Record<string, unknown> | null; created?: boolean }>;
  onCreateProject?: (payload: { projectName: string; kolId?: string; productSku?: string; productName?: string; productSkus?: string[]; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; sourceType?: string; note?: string }) => Promise<Record<string, unknown> | void>;
  onUpdateProject?: (projectId: string, payload: { projectName?: string; productSku?: string; productName?: string; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: (projectId: string, reason?: string) => Promise<void>;
  onAddProjectCost?: (payload: { projectId: string; costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> }) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onSelectPage?: (page: VkpiPageKey) => void;
  onToggleView?: (targetPage?: VkpiPageKey) => void;
  onRefreshData?: () => void | Promise<void>;
  apiToken?: string;
}

const campaignStatusOptions = ['规划中', '进行中', '收尾中', '已结束', '已取消'];

function ProjectFocusBanner({
  focus,
  matched,
  onDismiss,
}: {
  focus: IntelligenceProjectFocusPayload;
  matched: boolean;
  onDismiss: () => void;
}) {
  return (
    <section className="vkpi-task-focus-banner is-medium" aria-live="polite">
      <div>
        <span>来自智能中心 / 红人搜索</span>
        <h2>{focus.projectName}</h2>
        <p>{focus.summary}</p>
      </div>
      <div className="vkpi-task-focus-banner__meta">
        <span><b>KOL</b>{focus.kolHandle || focus.kolId || '-'}</span>
        <span><b>产品</b>{focus.productSku || focus.productName || '-'}</span>
        <span><b>状态</b>{matched ? '已定位项目详情' : '等待数据刷新后定位'}</span>
      </div>
      <div className="vkpi-task-focus-banner__actions">
        <button className="vkpi-button vkpi-button--ghost" type="button" onClick={onDismiss}>关闭</button>
      </div>
    </section>
  );
}

export function ProjectsPage({
  data,
  filteredProjects,
  selectedProjectId,
  selectedProject,
  openProjectId,
  viewMode,
  onOpenKolProfile,
  onOpenStaffProfile,
  onLookupKol,
  onCreateProject,
  onUpdateProject,
  onMoveProjectStage,
  onDeleteProject,
  onAddProjectCost,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onUploadEvidenceFile,
  onSelectPage,
  onToggleView,
  onRefreshData,
  apiToken,
}: ProjectsPageProps) {
  // 履约待办卡片消费 admin 端点,token 优先用页面下传的 apiToken,缺失时回退到 useAuth。
  const { token: authToken } = useAuth();
  const dueListToken = apiToken || authToken || undefined;
  const [createOpen, setCreateOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [kolId, setKolId] = useState('');
  const [productName, setProductName] = useState('');
  const [price, setPrice] = useState('');
  const [campaignStatus, setCampaignStatus] = useState('规划中');
  const [targetKolCount, setTargetKolCount] = useState('10');
  const [budgetUsd, setBudgetUsd] = useState('0');
  const [spentUsd, setSpentUsd] = useState('0');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [ownerName, setOwnerName] = useState(selectedProject?.ownerName || data.staffMembers[0]?.name || '');
  const [campaignType, setCampaignType] = useState('上市推广');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [detailProjectId, setDetailProjectId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [projectFocus, setProjectFocus] = useState<IntelligenceProjectFocusPayload | null>(null);

  useEffect(() => {
    if (openProjectId) setDetailProjectId(openProjectId);
  }, [openProjectId]);

  // P-SKU-2(2026-06-16):接真 369 SKU 库(原 datalist 接的 productCosts/productLaunches 实库为空)。
  // 选 SKU → 自动带出价格(catalogChoices 带 price)。非 manager 拉不到时静默回落原文本输入。
  const [skuCatalog, setSkuCatalog] = useState<Array<{ productSku: string; productName: string; price: number | null }>>([]);
  useEffect(() => {
    if (!createOpen || !apiToken) return;
    let cancelled = false;
    void listProductCatalog(apiToken, { limit: 500 })
      .then((resp: any) => {
        if (cancelled) return;
        const rows = Array.isArray(resp?.products) ? resp.products : [];
        setSkuCatalog(rows.map((r: any) => ({
          productSku: String(r.sku || r.product_sku || ""),
          productName: String(r.marketing_name || r.model_name || r.product_name || r.sku || ""),
          price: r.price_usd === null || r.price_usd === undefined || r.price_usd === "" ? null : Number(r.price_usd),
        })).filter((x: any) => x.productSku));
      })
      .catch(() => { if (!cancelled) setSkuCatalog([]); });
    return () => { cancelled = true; };
  }, [createOpen, apiToken]);

  const productChoices = useMemo(() => {
    const bySku = new Map<string, { id: string; productSku: string; productName: string; sourceLabel: string; price?: number | null }>();
    // 真 SKU 库优先(带价格),再补旧来源
    skuCatalog.forEach((item) => {
      bySku.set(item.productSku, { id: item.productSku, productSku: item.productSku, productName: item.productName, sourceLabel: '产品库', price: item.price });
    });
    data.productCosts
      .filter((item) => item.active !== false)
      .forEach((item) => {
        if (!item.productSku || bySku.has(item.productSku)) return; // 不覆盖产品库(含价格)
        bySku.set(item.productSku, {
          id: item.id || item.productSku,
          productSku: item.productSku,
          productName: item.productName || item.productSku,
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
        sourceLabel: launch.status ? `产品发布 · ${launch.status}` : '产品发布',
      });
    });
    return Array.from(bySku.values()).sort((a, b) => a.productName.localeCompare(b.productName));
  }, [data.productCosts, data.productLaunches, skuCatalog]);

  // SKU / 产品名 → 单价(USD),供费用 tab 在 ledger 无产品成本行时做估算
  const productUnitCosts = useMemo(() => {
    const map: Record<string, number> = {};
    data.productCosts
      .filter((item) => item.active !== false)
      .forEach((item) => {
        const cost = Number(item.unitCost) || 0;
        if (!cost) return;
        if (item.productSku) map[item.productSku] = cost;
        if (item.productName) map[item.productName.toLowerCase()] = cost;
      });
    return map;
  }, [data.productCosts]);

  const matchedProduct = useMemo(() => {
    const normalized = productName.trim().toLowerCase();
    if (!normalized) return undefined;
    return productChoices.find((item) => (
      item.productName.toLowerCase() === normalized ||
      item.productSku.toLowerCase() === normalized
    ));
  }, [productChoices, productName]);

  // P-SKU-2:命中 SKU 且库里有价 → 自动带出价格(免手输);未命中(自由文本)不动用户已输入。
  useEffect(() => {
    const p = (matchedProduct as any)?.price;
    if (p != null && Number.isFinite(Number(p))) setPrice(`$${Number(p)}`);
  }, [matchedProduct]);

  useEffect(() => {
    const focus = readProjectFocus();
    if (!focus) return;
    clearProjectFocus();
    setProjectFocus(focus);
    setMessage(focus.summary);
  }, []);

  useEffect(() => {
    if (!projectFocus) return;
    const target = filteredProjects.find((project) => (
      (projectFocus.projectId && project.id === projectFocus.projectId)
      || project.campaign === projectFocus.projectName
    ));
    if (target) setDetailProjectId(target.id);
  }, [filteredProjects, projectFocus]);

  const closeCreateModal = () => {
    if (busy) return;
    setCreateOpen(false);
  };

  const resetCreateForm = () => {
    setProjectName('');
    setKolId('');
    setProductName('');
    setPrice('');
    setCampaignStatus('规划中');
    setTargetKolCount('10');
    setBudgetUsd('0');
    setSpentUsd('0');
    setStartDate('');
    setEndDate('');
    setOwnerName(selectedProject?.ownerName || data.staffMembers[0]?.name || '');
    setCampaignType('上市推广');
  };

  const submitProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateProject || !projectName.trim()) return;
    const noteLines = [
      campaignStatus ? `状态：${campaignStatus}` : '',
      campaignType.trim() ? `推广类型：${campaignType.trim()}` : '',
      price.trim() ? `价格：${price.trim()}` : '',
      targetKolCount.trim() ? `目标 KOL 数：${targetKolCount.trim()}` : '',
      budgetUsd.trim() ? `预算 USD：${budgetUsd.trim()}` : '',
      spentUsd.trim() ? `已花 USD：${spentUsd.trim()}` : '',
      startDate ? `开始时间：${startDate}` : '',
      endDate ? `计划结束：${endDate}` : '',
      ownerName.trim() ? `负责人：${ownerName.trim()}` : '',
    ].filter(Boolean);
    setBusy(true);
    try {
      await onCreateProject({
        projectName: projectName.trim(),
        kolId: kolId.trim() || undefined,
        productSku: matchedProduct?.productSku,
        productName: productName.trim() || matchedProduct?.productName,
        products: matchedProduct ? [{ productSku: matchedProduct.productSku, productName: matchedProduct.productName }] : undefined,
        sourceType: 'v615_projects_ui',
        note: noteLines.length ? noteLines.join('\n') : undefined,
      });
      resetCreateForm();
      setCreateOpen(false);
      setMessage('推广项目已创建。后续阶段推进、费用、物流和证据请在项目详情里处理。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '推广项目创建失败');
    } finally {
      setBusy(false);
    }
  };

  const deleteProjectRow = async (project: VkpiProjectRow, reason?: string, actionLabel = '删除项目') => {
    if (!onDeleteProject) return;
    setBusy(true);
    try {
      await onDeleteProject(project.id, reason || `${actionLabel}：${project.campaign}`);
      setMessage(actionLabel === '取消推广' ? '推广已取消，历史记录已保留。' : '项目已删除，历史记录已保留。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${actionLabel}失败`);
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const loadAvailableKols = async (targetProject: VkpiProjectRow, scope = 'favorites') => {
    if (!apiToken) return data.kolOptions;
    // P0-2 裁决:默认 favorites 子集;scope='all' 是「从全池查找」逃生门。
    const response = await getAvailableProjectKols(apiToken, targetProject.id, '', scope);
    return buildKolOptions(response.kols || []);
  };

  const addKolsToCampaign = async (targetProject: VkpiProjectRow, kols: VkpiKolOption[]) => {
    if (!apiToken) throw new Error('缺少 API token，不能写入项目 KOL。');
    // 负责人=该 KOL 的 active claim owner(后端按 vkpi_kol_claims 决定);
    // 不再把项目负责人 targetProject.ownerId 当 assignedStaffId 强塞,传 undefined 让后端按 claim 归属。
    const result = await addKolsToProject(
      apiToken,
      targetProject.id,
      kols.map((kol) => kol.id),
      undefined,
    );
    setMessage(`已向「${targetProject.campaign}」写入 ${result.inserted || 0} 个 KOL，跳过 ${result.skipped_existing || 0} 个已有项。`);
    await onRefreshData?.();
  };

  // V3 双刷合一:刷新责任收口在 ProjectDetailView 的 onProjectUpdated 一处,
  // 此处不再各自 await refreshProjectData(),否则同一动作触发两次全量刷新。
  const advanceProjectKolRow = async (projectId: string, kolRef: string, payload: Record<string, unknown>) => {
    if (!apiToken) throw new Error('缺少 API token，不能推进 KOL 阶段。');
    return advanceProjectKol(apiToken, projectId, kolRef, payload);
  };

  const updateProjectKolShippingRow = async (projectId: string, kolRef: string, payload: Record<string, unknown>) => {
    if (!apiToken) throw new Error('缺少 API token，不能写入物流。');
    return updateProjectKolShipping(apiToken, projectId, kolRef, payload);
  };

  const submitProjectKolStub = async (projectId: string, kolRef: string, actionKind: 'screenshot' | 'video' | 'contract', payload: Record<string, unknown>) => {
    if (!apiToken) throw new Error('缺少 API token，不能提交该操作。');
    return submitProjectKolActionStub(apiToken, projectId, kolRef, actionKind, payload);
  };

  const setProjectFollowStatus = async (project: VkpiProjectRow, followStatus: 'active' | 'paused') => {
    if (!apiToken) throw new Error('缺少 API token，不能更新跟进状态。');
    await updateProjectFollowStatus(apiToken, project.id, followStatus);
    await onRefreshData?.();
  };

  const setProjectStar = async (project: VkpiProjectRow, starred: boolean) => {
    if (!apiToken) throw new Error('缺少 API token，不能更新重点标记。');
    await updateProjectStar(apiToken, project.id, starred);
    await onRefreshData?.();
  };

  const refreshProjectData = async () => {
    await projectDetailState.refresh();
    // dashboard 全量刷新转后台(2026-06-12 推进卡顿案):详情已刷即解锁交互,不再阻塞等全站数据。
    void onRefreshData?.();
  };

  const importKolRows = async (targetProject: VkpiProjectRow, rows: ImportKolRow[]) => {
    if (!apiToken) throw new Error('缺少 API token，不能向项目批量追加 KOL。');
    setBusy(true);
    try {
      const matchedKols: VkpiKolOption[] = [];
      const unmatchedRows: ImportKolRow[] = [];
      const seenKolIds = new Set<string>();
      for (const row of rows) {
        const queryTerms = buildImportSearchTerms(row);
        const candidateMap = new Map<string, VkpiKolOption>();
        for (const term of queryTerms.length ? queryTerms : [row.handle]) {
          // 批量导入匹配走全池(scope=all):按导入 handle 在全库找人,而非仅本人收藏。
          const response = await getAvailableProjectKols(apiToken, targetProject.id, term, 'all');
          buildKolOptions(response.kols || []).forEach((kol) => candidateMap.set(kol.id, kol));
        }
        let match = findImportKolMatch(row, Array.from(candidateMap.values()));
        if (!match && onLookupKol) {
          match = await lookupImportKolPoolOption(row, onLookupKol);
        }
        if (!match) {
          unmatchedRows.push(row);
          continue;
        }
        if (seenKolIds.has(match.id)) continue;
        seenKolIds.add(match.id);
        matchedKols.push(match);
      }
      if (!matchedKols.length) {
        throw new Error(`没有在 KOL Pool 里匹配到可追加的 KOL。未匹配：${unmatchedRows.slice(0, 5).map((row) => row.handle).join('、') || '全部'}`);
      }
      await addKolsToCampaign(targetProject, matchedKols);
      setImportOpen(false);
      const unmatchedLabel = unmatchedRows.length
        ? `；未匹配 ${unmatchedRows.length} 行：${unmatchedRows.slice(0, 5).map((row) => row.handle).join('、')}${unmatchedRows.length > 5 ? '…' : ''}`
        : '';
      setMessage(`已向「${targetProject.campaign}」追加 ${matchedKols.length} 个 KOL${unmatchedLabel}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'KOL 名单导入失败');
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const detailProject = useMemo(() => {
    if (!detailProjectId) return undefined;
    return filteredProjects.find((project) => project.id === detailProjectId);
  }, [detailProjectId, filteredProjects]);
  const projectDetailState = useProjectDetail({
    apiToken,
    projectId: detailProjectId,
    fallbackProject: detailProject,
  });

  if (detailProjectId) {
    const detailProjectForView = projectDetailState.project;
    // 焦点横幅修复:此前 banner 只在列表分支渲染、matched 恒 false——焦点命中即自动打开详情,
    // 「已定位」状态不可达。现在命中并打开详情时在详情分支也渲染,matched 如实为 true。
    const focusMatched = Boolean(projectFocus && (
      (projectFocus.projectId && detailProjectId === projectFocus.projectId)
      || (detailProjectForView && detailProjectForView.campaign === projectFocus.projectName)
      || (detailProject && detailProject.campaign === projectFocus.projectName)
    ));
    return (
      <PageShell title="项目详情" description="按项目维度查看 KOL 阶段、数据、物流、证据和今日提醒。" hideHeading>
        {projectFocus && focusMatched ? (
          <ProjectFocusBanner
            focus={projectFocus}
            matched
            onDismiss={() => setProjectFocus(null)}
          />
        ) : null}
        {/* 白屏闪案(2026-06-12)+ 全盘扫描 P0 修正:骨架屏只在"首载且连 fallback 都没有"时出现;
            有 fallback/旧 detail 即渲染视图原地更新——三分支互斥,杜绝全 false 空白。 */}
        {projectDetailState.loading && !projectDetailState.detail && !detailProjectForView ? (
          <ProjectDetailSkeleton onBack={() => setDetailProjectId(null)} />
        ) : null}
        {!projectDetailState.loading && projectDetailState.error && !detailProjectForView ? (
          <ProjectDetailError
            message={projectDetailState.notFound ? '项目不存在' : projectDetailState.error}
            onBack={() => setDetailProjectId(null)}
          />
        ) : null}
        {detailProjectForView ? (
          <ProjectDetailView
            key={detailProjectForView.id}
            apiToken={apiToken}
            detail={projectDetailState.detail}
            project={detailProjectForView}
            projects={filteredProjects.map((project) => (project.id === detailProjectForView.id ? detailProjectForView : project))}
            participatingRows={projectDetailState.participatingRows}
            costRows={projectDetailState.detail?.costs || []}
            productUnitCosts={productUnitCosts}
            staff={data.staffMembers}
            viewMode={viewMode}
            onBack={() => setDetailProjectId(null)}
            onOpenKolProfile={onOpenKolProfile}
            onOpenStaffProfile={onOpenStaffProfile}
            onMoveProjectStage={onMoveProjectStage}
            onUpdateProject={onUpdateProject}
            onAddProjectCost={onAddProjectCost}
            onUpsertProjectTerms={onUpsertProjectTerms}
            onAddProjectShipment={onAddProjectShipment}
            onUploadEvidenceFile={onUploadEvidenceFile}
            kolOptions={data.kolOptions}
            onLoadAvailableKols={apiToken ? loadAvailableKols : undefined}
            onAddKolsToCampaign={apiToken ? addKolsToCampaign : undefined}
            onProjectUpdated={refreshProjectData}
            onDeleteProject={onDeleteProject ? deleteProjectRow : undefined}
            onAdvanceProjectKol={apiToken ? advanceProjectKolRow : undefined}
            onUpdateProjectKolShipping={apiToken ? updateProjectKolShippingRow : undefined}
            onSubmitProjectKolActionStub={apiToken ? submitProjectKolStub : undefined}
            onSetFollowStatus={apiToken ? setProjectFollowStatus : undefined}
            onSelectPage={onSelectPage}
            onToggleView={onToggleView}
          />
        ) : null}
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
      </PageShell>
    );
  }

  return (
    <PageShell title="项目跟进" description="创建项目、按流程一键推进，系统自动记录从开始到完成的耗时。" hideHeading>
      {projectFocus ? (
        <ProjectFocusBanner
          focus={projectFocus}
          matched={Boolean(detailProjectId)}
          onDismiss={() => setProjectFocus(null)}
        />
      ) : null}
      <ProjectDueListCard
        apiToken={dueListToken}
        daysOverdue={7}
        onOpenProject={(projectId) => setDetailProjectId(projectId)}
      />
      <ProjectCampaignBoard
        projects={filteredProjects}
        selectedProjectId={selectedProjectId}
        viewMode={viewMode}
        onOpenProjectDetail={(project) => setDetailProjectId(project.id)}
        onOpenStaffProfile={onOpenStaffProfile}
        onOpenCreateProject={() => setCreateOpen(true)}
        onOpenImportKols={apiToken && filteredProjects.length > 0 ? () => setImportOpen(true) : undefined}
        onSetFollowStatus={apiToken ? setProjectFollowStatus : undefined}
        onSetProjectStar={apiToken ? setProjectStar : undefined}
      />
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {importOpen ? (
        <ImportKolListModal
          projects={filteredProjects}
          selectedProject={selectedProject || filteredProjects[0]}
          busy={busy}
          onClose={() => setImportOpen(false)}
          onSubmit={importKolRows}
        />
      ) : null}
      {createOpen ? (
        <div className="vkpi-project-modal-backdrop" role="presentation">
          <form className="vkpi-project-create-modal" onSubmit={submitProject} role="dialog" aria-label="新建推广">
            <header>
              <div>
                <h2>新建推广</h2>
                <p>提交后写入现有项目接口；阶段、费用、物流和证据在项目详情继续处理。</p>
              </div>
              <button type="button" onClick={closeCreateModal}>关闭</button>
            </header>
            <div className="vkpi-project-create-grid">
              <label className="is-full">推广名称
                <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="例：AF 35mm F1.2 LAB FE 上市推广" />
              </label>
              <label>主推产品
                <input list="vkpi-project-product-options" value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="35mm F1.2 LAB FE" />
                <datalist id="vkpi-project-product-options">
                  {productChoices.map((product) => <option key={product.id || product.productSku} value={product.productName}>{product.productSku} · {product.sourceLabel}{(product as any).price != null ? ` · $${(product as any).price}` : ''}</option>)}
                </datalist>
              </label>
              <label>价格
                <input value={price} onChange={(event) => setPrice(event.target.value)} placeholder="$999" />
              </label>
              <label>状态
                <select value={campaignStatus} onChange={(event) => setCampaignStatus(event.target.value)}>
                  {campaignStatusOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </label>
              <label>目标 KOL 数
                <input value={targetKolCount} onChange={(event) => setTargetKolCount(event.target.value)} inputMode="numeric" placeholder="10" />
              </label>
              <label>预算 USD
                <input value={budgetUsd} onChange={(event) => setBudgetUsd(event.target.value)} inputMode="decimal" placeholder="0" />
              </label>
              <label>已花 USD
                <input value={spentUsd} onChange={(event) => setSpentUsd(event.target.value)} inputMode="decimal" placeholder="0" />
              </label>
              <label>开始时间
                <input value={startDate} onChange={(event) => setStartDate(event.target.value)} type="date" />
              </label>
              <label>计划结束
                <input value={endDate} onChange={(event) => setEndDate(event.target.value)} type="date" />
              </label>
              <label>负责人
                <input value={ownerName} onChange={(event) => setOwnerName(event.target.value)} placeholder="Jianbo" />
              </label>
              <label>推广类型
                <input value={campaignType} onChange={(event) => setCampaignType(event.target.value)} placeholder="上市推广" />
              </label>
              {data.kolOptions.length ? (
                <label className="is-full">合作 KOL（可选）
                  <select value={kolId} onChange={(event) => setKolId(event.target.value)}>
                    <option value="">稍后从项目详情或 KOL 库添加</option>
                    {data.kolOptions.map((kol) => <option key={kol.id} value={kol.id}>{kol.name} · {kol.handle} · {kol.platform}</option>)}
                  </select>
                </label>
              ) : null}
            </div>
            <footer>
              <button className="vkpi-project-modal-button" type="button" onClick={closeCreateModal} disabled={busy}>取消</button>
              <button className="vkpi-project-modal-button is-primary" type="submit" disabled={busy || !onCreateProject || !projectName.trim()}>
                创建推广
              </button>
            </footer>
          </form>
        </div>
      ) : null}
    </PageShell>
  );
}

interface ImportKolRow {
  platform: string;
  handle: string;
  name?: string;
  email?: string;
}

function normalizeImportPlatform(value: unknown) {
  const text = String(value || '').trim().toLowerCase();
  if (text.includes('youtube') || text === 'yt') return 'youtube';
  if (text.includes('instagram') || text === 'ig') return 'instagram';
  if (text.includes('tiktok')) return 'tiktok';
  if (text.includes('facebook')) return 'facebook';
  if (text === 'x' || text.includes('twitter')) return 'x';
  if (text.includes('reddit')) return 'reddit';
  return text;
}

function normalizeImportToken(value: unknown) {
  let token = String(value || '').trim().toLowerCase();
  token = token.replace(/^https?:\/\/(www\.)?/, '');
  token = token.replace(/^(instagram|youtube|tiktok|facebook|twitter|x)\.com\//, '');
  token = token.replace(/^youtu\.be\//, '');
  token = token.replace(/^(channel|user|c)\//, '');
  token = token.replace(/^@+/, '');
  token = token.replace(/[?#].*$/, '');
  token = token.replace(/\/(videos|reels|reel|posts|post|tagged)\/?$/i, '');
  token = token.replace(/\/+$/, '');
  return token.replace(/[^a-z0-9\u4e00-\u9fff]+/g, '');
}

function buildImportSearchTerms(row: ImportKolRow) {
  const rawTerms = [row.handle, row.name].map((value) => String(value || '').trim()).filter(Boolean);
  const normalizedTerms = rawTerms.map(normalizeImportToken).filter(Boolean);
  return Array.from(new Set([...rawTerms, ...normalizedTerms]));
}

function findImportKolMatch(row: ImportKolRow, candidates: VkpiKolOption[]) {
  const rowPlatform = normalizeImportPlatform(row.platform);
  const rowTokens = [row.handle, row.name].map(normalizeImportToken).filter(Boolean);
  if (!rowTokens.length) return undefined;
  const platformCandidates = candidates.filter((candidate) => {
    const candidatePlatform = normalizeImportPlatform(candidate.platform);
    return !rowPlatform || !candidatePlatform || rowPlatform === candidatePlatform;
  });
  const pool = platformCandidates.length ? platformCandidates : candidates;
  return pool.find((candidate) => {
    const candidateTokens = [candidate.handle, candidate.name, candidate.profileUrl].map(normalizeImportToken).filter(Boolean);
    return rowTokens.some((token) => candidateTokens.includes(token));
  });
}

async function lookupImportKolPoolOption(
  row: ImportKolRow,
  onLookupKol: NonNullable<ProjectsPageProps['onLookupKol']>,
): Promise<VkpiKolOption | undefined> {
  const handleOrUrl = String(row.handle || row.name || '').trim();
  if (!handleOrUrl) return undefined;
  try {
    const result = await onLookupKol({
      platform: normalizeImportPlatform(row.platform || 'Other') || 'Other',
      handleOrUrl,
      createIfMissing: false,
      scanAccount: false,
      maxPosts: 1,
      contactEmail: row.email,
    });
    const kol = result.kol || {};
    const rawPoolId = kol.kol_pool_id ?? kol.kolPoolId ?? kol.pool_id ?? kol.poolId;
    const poolId = rawPoolId == null ? '' : String(rawPoolId);
    if (!poolId || !/^\d+$/.test(poolId)) return undefined;
    return {
      id: poolId,
      name: String(kol.display_name || kol.channel_name || kol.name || row.name || row.handle || `KOL ${poolId}`),
      handle: String(kol.handle || row.handle || ''),
      platform: platformLabel(row.platform || kol.platform || 'Other'),
      avatar: String(kol.avatar_url || ''),
      profileUrl: String(kol.profile_url || kol.channel_url || ''),
      contactEmail: row.email,
      followerLabel: '-',
      contentCountLabel: '-',
      claimOwner: '',
      scanStatus: 'lookup_pool',
    };
  } catch {
    return undefined;
  }
}

function platformLabel(value: unknown): VkpiKolOption['platform'] {
  const normalized = normalizeImportPlatform(value);
  if (normalized === 'youtube') return 'YouTube';
  if (normalized === 'instagram') return 'Instagram';
  if (normalized === 'tiktok') return 'TikTok';
  if (normalized === 'facebook') return 'Facebook';
  if (normalized === 'x') return 'X';
  if (normalized === 'reddit') return 'Reddit';
  return 'Other';
}

function parseKolImportRows(raw: string, fallbackPlatform: string): ImportKolRow[] {
  const rows: ImportKolRow[] = [];
  const seen = new Set<string>();
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line, index) => {
      const cells = line.split(/\t|,/).map((cell) => cell.trim()).filter(Boolean);
      const normalizedHeaderCells = cells.map((cell) => cell.toLowerCase());
      const looksLikeHeader = index === 0 && normalizedHeaderCells.some((cell) => ['platform', '平台'].includes(cell))
        && normalizedHeaderCells.some((cell) => ['handle', '账号', 'kol', 'email', '邮箱', 'name', '名称'].includes(cell));
      if (looksLikeHeader) return;
      if (!cells.length) return;
      let platform = fallbackPlatform;
      let handle = cells[0] || '';
      let name = '';
      let email = '';
      if (cells.length >= 2 && /instagram|youtube|tiktok|facebook|reddit|twitter|^x$|other/i.test(cells[0])) {
        platform = cells[0];
        handle = cells[1] || '';
        name = cells[2] || '';
        email = cells[3] || '';
      } else {
        name = cells[1] || '';
        email = cells[2] || '';
      }
      handle = handle.trim();
      if (!handle) return;
      const dedupeKey = `${platform.toLowerCase()}::${handle.toLowerCase()}`;
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);
      rows.push({ platform, handle, name, email });
    });
  return rows.slice(0, 50);
}

function ImportKolListModal({
  projects,
  selectedProject,
  busy,
  onClose,
  onSubmit,
}: {
  projects: VkpiProjectRow[];
  selectedProject?: VkpiProjectRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (project: VkpiProjectRow, rows: ImportKolRow[]) => Promise<void>;
}) {
  const [projectId, setProjectId] = useState(selectedProject?.id || projects[0]?.id || '');
  const [fallbackPlatform, setFallbackPlatform] = useState<string>(selectedProject?.platform || projects[0]?.platform || 'Instagram');
  const [rawText, setRawText] = useState('Instagram, @creator.handle, Creator Name, creator@email.com');
  const [error, setError] = useState('');
  const targetProject = projects.find((project) => project.id === projectId) || projects[0];
  const parsedRows = useMemo(() => parseKolImportRows(rawText, fallbackPlatform), [fallbackPlatform, rawText]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!targetProject) {
      setError('请先选择目标推广。');
      return;
    }
    if (!parsedRows.length) {
      setError('没有解析到可导入的 KOL 行。');
      return;
    }
    setError('');
    await onSubmit(targetProject, parsedRows);
  };

  return (
    <div className="vkpi-project-modal-backdrop" role="presentation">
      <form className="vkpi-project-import-modal" onSubmit={submit} role="dialog" aria-label="导入 KOL 名单">
        <header>
          <div>
            <h2>导入 KOL 名单</h2>
            <p>从 KOL Pool 匹配账号，批量追加到目标推广；最多 50 行，不再创建孤立项目。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-project-import-grid">
          <label>目标推广
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.campaign} · {project.kolHandle || project.kolName}</option>)}
            </select>
          </label>
          <label>默认平台
            <select value={fallbackPlatform} onChange={(event) => setFallbackPlatform(event.target.value)}>
              {['Instagram', 'YouTube', 'TikTok', 'Facebook', 'Reddit', 'X', 'Other'].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="is-full">名单内容
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder={`支持格式：\nInstagram, @handle, Name, email@example.com\n@handle, Name, email@example.com`}
            />
          </label>
        </div>
        <div className="vkpi-project-import-preview">
          <strong>预览 {parsedRows.length} 行</strong>
          <div>
            {parsedRows.slice(0, 6).map((row) => <span key={`${row.platform}-${row.handle}`}>{row.platform} · {row.handle}</span>)}
            {parsedRows.length > 6 ? <span>还有 {parsedRows.length - 6} 行</span> : null}
          </div>
        </div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button className="vkpi-project-modal-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="vkpi-project-modal-button is-primary" type="submit" disabled={busy || !parsedRows.length}>
            {busy ? '导入中' : `导入 ${parsedRows.length} 个 KOL`}
          </button>
        </footer>
      </form>
    </div>
  );
}

function ProjectDetailSkeleton({ onBack }: { onBack: () => void }) {
  return (
    <section className="vkpi-campaign-detail" aria-label="项目详情加载中">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>
      <div className="vkpi-campaign-detail-hero vkpi-campaign-skeleton">
        <div>
          <span />
          <strong />
          <p />
          <p />
        </div>
        <div />
      </div>
      <div className="vkpi-campaign-kpis vkpi-campaign-skeleton-kpis">
        {Array.from({ length: 6 }).map((_, index) => <div key={index} />)}
      </div>
      <div className="vkpi-campaign-panel vkpi-campaign-skeleton-panel" />
    </section>
  );
}

function ProjectDetailError({ message, onBack }: { message: string; onBack: () => void }) {
  return (
    <section className="vkpi-campaign-detail" aria-label="项目详情错误">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>
      <div className="vkpi-campaign-placeholder is-error">
        <h3>{message}</h3>
        <p>请返回项目列表后刷新数据，或确认该项目仍在当前账号权限范围内。</p>
      </div>
    </section>
  );
}
