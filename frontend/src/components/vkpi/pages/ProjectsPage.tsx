import React, { useEffect, useMemo, useState } from 'react';
import type { VkpiKolOption, VkpiProjectRow } from '../vkpiTypes';
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
import type { ProjectsPageProps } from './ProjectsPage.types';
import {
  type ImportKolRow,
  buildImportSearchTerms,
  findImportKolMatch,
  lookupImportKolPoolOption,
} from './ProjectsPage.helpers';
import {
  ImportKolListModal,
  ProjectDetailError,
  ProjectDetailSkeleton,
  ProjectFocusBanner,
} from './ProjectsPage.Sections';
import './projects/projectBoard.css';

const campaignStatusOptions = ['规划中', '进行中', '收尾中', '已结束', '已取消'];

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
  // N3 新品发布(Launch):勾选后走 source_type='launch' 分支,把结构化卖点/竞品/目标市场
  // 等塞进 metadata.launch(后端 launch_project.normalize_launch_metadata 同款字段)。
  const [isLaunch, setIsLaunch] = useState(false);
  const [launchPriceBand, setLaunchPriceBand] = useState('');
  const [launchTargetCountries, setLaunchTargetCountries] = useState('');
  const [launchSellingPoints, setLaunchSellingPoints] = useState('');
  const [launchCompetitors, setLaunchCompetitors] = useState('');
  const [launchTargetAudience, setLaunchTargetAudience] = useState('');
  const [launchHypotheses, setLaunchHypotheses] = useState('');
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
    setIsLaunch(false);
    setLaunchPriceBand('');
    setLaunchTargetCountries('');
    setLaunchSellingPoints('');
    setLaunchCompetitors('');
    setLaunchTargetAudience('');
    setLaunchHypotheses('');
  };

  // 逗号/换行分隔的多值字段 → 去重保序的非空字符串列表(对齐后端 _as_list 清洗口径)。
  const splitLaunchList = (raw: string): string[] => {
    const out: string[] = [];
    const seen = new Set<string>();
    raw
      .split(/[\n,]/)
      .map((item) => item.trim())
      .forEach((item) => {
        if (item && !seen.has(item)) {
          seen.add(item);
          out.push(item);
        }
      });
    return out;
  };

  // Launch 分支:SKU 是后端 required 字段;无匹配 SKU 时回落主推产品自由文本。
  const launchSku = (matchedProduct?.productSku || productName.trim()).trim();
  const launchSkuMissing = isLaunch && !launchSku;

  const submitProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateProject || !projectName.trim()) return;
    if (launchSkuMissing) {
      setMessage('新品发布(Launch)需要先指定 SKU / 主推产品。');
      return;
    }
    const noteLines = [
      isLaunch ? '类型：新品发布(Launch)' : '',
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

    // N3 launch 元数据:字段名与后端 launch_project.LAUNCH_PROJECT_FIELDS 一一对齐,
    // 落 metadata.launch;source_type='launch' 让 workflow.create_project 写标。
    const launchMetadata = isLaunch
      ? {
          launch: {
            sku: launchSku,
            price_band: launchPriceBand.trim(),
            target_countries: splitLaunchList(launchTargetCountries),
            selling_points: splitLaunchList(launchSellingPoints),
            competitors: splitLaunchList(launchCompetitors),
            target_audience: launchTargetAudience.trim(),
            validation_hypotheses: splitLaunchList(launchHypotheses),
          },
          project_type: 'launch',
        }
      : undefined;

    setBusy(true);
    try {
      await onCreateProject({
        projectName: projectName.trim(),
        kolId: kolId.trim() || undefined,
        productSku: isLaunch ? launchSku : matchedProduct?.productSku,
        productName: productName.trim() || matchedProduct?.productName,
        products: matchedProduct ? [{ productSku: matchedProduct.productSku, productName: matchedProduct.productName }] : undefined,
        sourceType: isLaunch ? 'launch' : 'cockpit_projects_ui',
        note: noteLines.length ? noteLines.join('\n') : undefined,
        metadata: launchMetadata,
      });
      resetCreateForm();
      setCreateOpen(false);
      setMessage(isLaunch
        ? '新品发布(Launch)项目已创建。KOL 候选、内容验证任务和观察窗口请在项目详情里推进。'
        : '推广项目已创建。后续阶段推进、费用、物流和证据请在项目详情里处理。');
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
              <label className="is-full vkpi-project-launch-toggle">
                <input type="checkbox" checked={isLaunch} onChange={(event) => setIsLaunch(event.target.checked)} />
                新品发布(Launch)—— 建成 source_type=launch 项目，带卖点/竞品/目标市场，进项目仪表盘
              </label>
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
              {isLaunch ? (
                <>
                  <label className="is-full">SKU（必填）
                    <input value={launchSku} readOnly placeholder="先在「主推产品」选 SKU" />
                    {launchSkuMissing ? <small className="vkpi-project-launch-hint">请在主推产品里选/填 SKU。</small> : null}
                  </label>
                  <label>价格带
                    <input value={launchPriceBand} onChange={(event) => setLaunchPriceBand(event.target.value)} placeholder="99-149 USD" />
                  </label>
                  <label>目标人群
                    <input value={launchTargetAudience} onChange={(event) => setLaunchTargetAudience(event.target.value)} placeholder="入门级视频创作者" />
                  </label>
                  <label className="is-full">目标国家（逗号/换行分隔）
                    <input value={launchTargetCountries} onChange={(event) => setLaunchTargetCountries(event.target.value)} placeholder="US, JP, DE" />
                  </label>
                  <label className="is-full">核心卖点（逗号/换行分隔）
                    <textarea value={launchSellingPoints} onChange={(event) => setLaunchSellingPoints(event.target.value)} rows={2} placeholder="F1.2 大光圈, LAB 旗舰画质, 紧凑轻量" />
                  </label>
                  <label className="is-full">竞品（逗号/换行分隔）
                    <input value={launchCompetitors} onChange={(event) => setLaunchCompetitors(event.target.value)} placeholder="Sony 35mm F1.4 GM, Sigma 35mm F1.2" />
                  </label>
                  <label className="is-full">验证假设（逗号/换行分隔）
                    <textarea value={launchHypotheses} onChange={(event) => setLaunchHypotheses(event.target.value)} rows={2} placeholder="大光圈人像是核心传播点, 价格带可下探至入门用户" />
                  </label>
                </>
              ) : null}
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
              <button className="vkpi-project-modal-button is-primary" type="submit" disabled={busy || !onCreateProject || !projectName.trim() || launchSkuMissing}>
                {isLaunch ? '创建新品发布' : '创建推广'}
              </button>
            </footer>
          </form>
        </div>
      ) : null}
    </PageShell>
  );
}
