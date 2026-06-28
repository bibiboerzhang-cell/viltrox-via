import { useEffect, useMemo, useState } from 'react';
import type { VkpiKolOption, VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage } from '../../shared/vkpiDataUtils';
import { writeProjectActionFocus } from '../../intelligence/intelligenceProjectActionFocus';
import { getContractTemplates, type VkpiContractTemplate } from '../../../../services/vkpi/projects-api';
import { LiveLogisticsBanner } from './LiveLogisticsBanner';
import { ProjectDetailHeaderCard, ProjectFunnel, ProjectKpiGrid, ProjectTabsBar, ProjectTaskDock } from './ProjectDetailSections';
import { ProjectDetailModalsLayer, ProjectDetailTabContent } from './ProjectDetailView.Sections';
import { createContractActions, createSubmitActionStub, healthFromBackend, kolRef, writeClipboardText, type CopyFallbackState, type DetailActionModal } from './ProjectDetailView.helpers';
import {
  useProjectContracts,
  useProjectGoaffpro,
  useProjectRetrospective,
  useProjectVideoAnalysisCache,
} from './ProjectDetailView.hooks';
import {
  bottleneckForRows,
  buildAnalytics,
  buildContractLines,
  buildExpenseLines,
  buildProjectStatsSummary,
  buildProjectTaskItems,
  buildStageCostSummary,
  defaultTracking,
  matchesProjectStageFilter,
  stageCounts,
  statusForProject,
  type ConfirmAction,
  type DetailTab,
  type NoticeState,
  type ProjectDetailViewProps,
  type ScreenshotTarget,
  type TaskItem,
  type TrackingState,
} from '../../../../domains/projects';
import { healthColor } from './projectDeliverableStyle';

export function ProjectDetailView({
  apiToken,
  detail,
  project,
  participatingRows = [],
  costRows = [],
  productUnitCosts = {},
  staff = [],
  viewMode,
  onBack,
  onOpenKolProfile,
  onOpenStaffProfile,
  onUpdateProject,
  onSetFollowStatus,
  onMoveProjectStage,
  onAddProjectCost,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onUploadEvidenceFile,
  kolOptions = [],
  onLoadAvailableKols,
  onAddKolsToCampaign,
  onProjectUpdated,
  onDeleteProject,
  onAdvanceProjectKol,
  onUpdateProjectKolShipping,
  onSubmitProjectKolActionStub,
}: ProjectDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('参与 KOL');
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set([project.id]));
  const [trackingById, setTrackingById] = useState<Record<string, TrackingState>>({});
  const [stageOverrides, setStageOverrides] = useState<Record<string, VkpiProjectStage>>({});
  const [evidenceOverrides, setEvidenceOverrides] = useState<Record<string, number>>({});
  const [shopifyLinks, setShopifyLinks] = useState<Record<string, string>>({});
  const [savingShopifyLink, setSavingShopifyLink] = useState(false);
  const [tableQuery, setTableQuery] = useState('');
  const [tableStage, setTableStage] = useState('全部阶段');
  const [tablePlatform, setTablePlatform] = useState('全部平台');
  const [addKolOpen, setAddKolOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [addingKols, setAddingKols] = useState(false);
  const [availableKolOptions, setAvailableKolOptions] = useState<VkpiKolOption[]>([]);
  const [loadingAvailableKols, setLoadingAvailableKols] = useState(false);
  const [availableKolError, setAvailableKolError] = useState('');
  const [availableScope, setAvailableScope] = useState<'favorites' | 'all'>('favorites');
  const [contactRow, setContactRow] = useState<VkpiProjectRow | null>(null);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateBusy, setGenerateBusy] = useState(false);
  const [contractTemplates, setContractTemplates] = useState<VkpiContractTemplate[]>([]);
  const [contractPartyA, setContractPartyA] = useState('SHENZHEN VILTROX TECHNOLOGY CO., LTD.');
  const openGenerateContract = async () => {
    setGenerateOpen(true);
    if (!apiToken || contractTemplates.length) return;
    try {
      const resp = await getContractTemplates(apiToken);
      setContractTemplates((resp.templates || []) as VkpiContractTemplate[]);
      if (resp.party_a) setContractPartyA(String(resp.party_a));
    } catch (error) {
      setNotice({ tone: 'warning', title: '模板加载失败', body: error instanceof Error ? error.message : '无法加载合同模板。' });
    }
  };
  const [editOpen, setEditOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(false);
  const [actionModal, setActionModal] = useState<DetailActionModal | null>(null);
  const [movingRowId, setMovingRowId] = useState('');
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [copyFallback, setCopyFallback] = useState<CopyFallbackState | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [confirmingAction, setConfirmingAction] = useState(false);
  const [taskReminderOpen, setTaskReminderOpen] = useState(false);
  const [contractActionId, setContractActionId] = useState('');

  useEffect(() => {
    setStageOverrides({});
    setMovingRowId('');
  }, [project.id, project.stage]);

  const { videoAnalysisCache, videoQaCache, videoAnalysisLoading, videoAnalysisError, videoQaError } =
    useProjectVideoAnalysisCache(apiToken, project.id);

  const { contractsPayload, contractsLoading, contractsError, loadContracts } =
    useProjectContracts(apiToken, project.id, setNotice);

  const { retrospective, retroBusy, generateRetrospective } =
    useProjectRetrospective(apiToken, project.id, setNotice);

  const baseRows = useMemo(() => (participatingRows.length ? participatingRows : [project]), [participatingRows, project]);
  const rows = useMemo(() => baseRows.map((row) => {
    const overrideStage = stageOverrides[row.id];
    if (!overrideStage) return row;
    return {
      ...row,
      stage: overrideStage,
      latestMessageAt: new Date().toISOString(),
      currentStageStartedAt: new Date().toISOString(),
      stageDurationLabel: '刚刚',
    };
  }), [baseRows, stageOverrides]);
  const baseStats = useMemo(() => buildProjectStatsSummary(rows, detail), [rows, detail]);
  const { goaffTotals, goaffByKol } = useProjectGoaffpro(apiToken, project.id);
  const stats = useMemo(() => {
    // 仅当本项目有已建链 KOL(kol_count>0)才用 GOAFFPRO 覆盖;此时 GOAFFPRO 即归因真源,
    // 三个值统一取 GOAFFPRO(0 也是真零),口径一致避免 null/0 混淆。
    if (!goaffTotals || !(goaffTotals.kol_count ?? 0)) return baseStats;
    const gmv = goaffTotals.gmv_usd ?? 0;
    const cost = baseStats.cost;
    return {
      ...baseStats,
      clicks: goaffTotals.clicks ?? 0,
      orders: goaffTotals.orders ?? 0,
      gmv,
      roi: cost ? gmv / cost : baseStats.roi,
    };
  }, [baseStats, goaffTotals]);
  const analytics = useMemo(() => buildAnalytics(rows), [rows]);
  const expenseLines = useMemo(() => buildExpenseLines(rows), [rows]);
  const stageCosts = useMemo(() => buildStageCostSummary(expenseLines), [expenseLines]);
  const contractLines = useMemo(() => buildContractLines(rows), [rows]);
  const health = useMemo(() => healthFromBackend(project.healthScore), [project.healthScore]);
  const bottleneck = useMemo(() => bottleneckForRows(rows), [rows]);
  const counts = useMemo(() => stageCounts(rows), [rows]);
  const campaignStatus = statusForProject(project);
  const ownerFallback = { name: project.ownerName, avatarUrl: project.ownerAvatar };
  const currentHealthColor = healthColor(health.score);
  const ownerInitial = String(project.ownerName || '-').trim().slice(0, 1).toUpperCase() || '-';
  const platformOptions = useMemo(() => Array.from(new Set(rows.map((row) => row.platform))).filter(Boolean), [rows]);
  const filteredRows = useMemo(() => {
    const query = tableQuery.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesQuery = !query || [row.kolHandle, row.kolName, row.platform, row.campaign].some((value) => String(value || '').toLowerCase().includes(query));
      const matchesStage = tableStage === '全部阶段' || matchesProjectStageFilter(row.stage, tableStage);
      const matchesPlatform = tablePlatform === '全部平台' || row.platform === tablePlatform;
      return matchesQuery && matchesStage && matchesPlatform;
    });
  }, [rows, tablePlatform, tableQuery, tableStage]);
  const taskItems = useMemo<TaskItem[]>(() => buildProjectTaskItems(rows, trackingById), [rows, trackingById]);
  const reminderTasks = useMemo(
    () => taskItems.filter((item) => item.className === 'is-red' || item.className === 'is-yellow'),
    [taskItems],
  );
  useEffect(() => {
    if (!reminderTasks.length) {
      setTaskReminderOpen(false);
    }
  }, [reminderTasks.length]);

  const toggleRow = (rowId: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  };

  const trackingForRow = (row: VkpiProjectRow) => trackingById[row.id] || defaultTracking(row);
  const evidenceCountForRow = (row: VkpiProjectRow) => evidenceOverrides[row.id] ?? row.stageEventCount ?? 0;
  const projectShopifyLink = () => shopifyLinks[project.id] ?? project.shopifyLink ?? rows.find((row) => row.shopifyLink)?.shopifyLink ?? '';
  const shopifyLinkForRow = (_row: VkpiProjectRow) => projectShopifyLink();
  const setProjectShopifyLink = (value: string) => {
    setShopifyLinks((current) => ({ ...current, [project.id]: value }));
  };

  const selectFunnelStage = (stage: VkpiProjectStage) => {
    setActiveTab('参与 KOL');
    setTableStage((current) => (current === stage ? '全部阶段' : stage));
  };

  const moveRowStage = (row: VkpiProjectRow) => {
    if (row.assignmentId && !onAdvanceProjectKol) {
      setNotice({ tone: 'warning', title: '无法推进', body: '当前缺少 KOL 级阶段推进接口。' });
      return;
    }
    if (!row.assignmentId && !onMoveProjectStage) {
      setNotice({ tone: 'warning', title: '无法推进', body: '当前账号没有阶段推进入口，或父层没有传入阶段 API。' });
      return;
    }
    const nextStage = nextProjectStage(row.stage);
    if (!nextStage) {
      setNotice({ tone: 'info', title: '无需推进', body: `${row.kolHandle || row.kolName} 已在最终阶段。` });
      return;
    }
    const previousStage = row.stage;
    setConfirmAction({
      title: '确认推进阶段？',
      body: `将「${row.kolHandle || row.kolName}」从「${stageLabels[previousStage]}」推进到「${stageLabels[nextStage]}」。确认后会调用真实阶段 API，并刷新当前项目详情。`,
      confirmLabel: '确认推进',
      confirmVariant: 'primary',
      onConfirm: async () => {
        setMovingRowId(row.id);
        setStageOverrides((current) => ({ ...current, [row.id]: nextStage }));
        try {
          if (row.assignmentId) {
            await onAdvanceProjectKol?.(project.id, kolRef(row), {
              to_stage: nextStage,
              note: `详情页 KOL 推进：${stageLabels[previousStage]} → ${stageLabels[nextStage]}`,
            });
          } else {
            await onMoveProjectStage?.(row.id, nextStage, `详情页手动推进：${stageLabels[previousStage]} → ${stageLabels[nextStage]}`);
          }
          setNotice({
            tone: 'success',
            title: '阶段已推进',
            body: `${row.kolHandle || row.kolName} 已进入「${stageLabels[nextStage]}」。`,
          });
          await onProjectUpdated?.();
          setStageOverrides((current) => {
            const next = { ...current };
            delete next[row.id];
            return next;
          });
        } catch (error) {
          setStageOverrides((current) => {
            const next = { ...current };
            delete next[row.id];
            return next;
          });
          setNotice({
            tone: 'warning',
            title: '推进失败',
            body: error instanceof Error ? error.message : '阶段推进失败，已回退到原阶段。',
          });
        } finally {
          setMovingRowId('');
        }
      },
    });
  };

  const openStageEvidenceModal = (target: ScreenshotTarget) => {
    if (target.stage === 'agreed') setActionModal({ kind: 'contract', row: target.row });
    else if (target.stage === 'shipped') setActionModal({ kind: 'shipping', row: target.row });
    else if (target.stage === 'published') setActionModal({ kind: 'video', row: target.row });
    else setActionModal({ kind: 'screenshot', target });
  };

  const loadAvailableForScope = async (scope: 'favorites' | 'all') => {
    setAvailableScope(scope);
    setAvailableKolError('');
    if (!onLoadAvailableKols) {
      setAvailableKolOptions([]);
      return;
    }
    setLoadingAvailableKols(true);
    try {
      // P0-2 裁决:默认 favorites 我的收藏子集;'all' 是「从全池查找」逃生门。
      setAvailableKolOptions(await onLoadAvailableKols(project, scope));
    } catch (error) {
      // 错误必须显在弹窗里(静默 catch 禁令)——此前只发 toast,弹窗里伪装成"没有匹配"。
      const message = error instanceof Error ? error.message : '无法加载可添加 KOL。';
      setAvailableKolError(message);
      setNotice({ tone: 'warning', title: '候选 KOL 加载失败', body: message });
      setAvailableKolOptions([]);
    } finally {
      setLoadingAvailableKols(false);
    }
  };

  const openAddKolModal = async () => {
    setAddKolOpen(true);
    await loadAvailableForScope('favorites');
  };

  const submitActionStub = createSubmitActionStub({
    apiToken,
    projectId: project.id,
    onSubmitProjectKolActionStub,
    onUploadEvidenceFile,
    onAdvanceProjectKol,
    onProjectUpdated,
    loadContracts,
    setNotice,
    setActionModal,
    setEvidenceOverrides,
    evidenceCountForRow,
  });

  const submitShippingInfo = async (row: VkpiProjectRow, payload: Record<string, unknown>) => {
    if (!onUpdateProjectKolShipping) {
      setNotice({ tone: 'warning', title: '无法写入物流', body: '当前缺少 KOL 级物流写库接口。' });
      return;
    }
    const trackingNumber = String(payload.tracking_number || '').trim();
    await onUpdateProjectKolShipping(project.id, kolRef(row), payload);
    setTrackingById((current) => ({
      ...current,
      [row.id]: {
        courier: String(payload.carrier || ''),
        no: trackingNumber,
        status: '已保存，等待物流追踪',
        last: '刚刚',
        delivered: false,
      },
    }));
    setActionModal(null);
    setNotice({ tone: 'success', title: '物流已写入', body: `${row.kolHandle || row.kolName} 的 tracking 和运费已保存。` });
    await onProjectUpdated?.();
  };

  const submitProjectCost = async (
    row: VkpiProjectRow,
    payload: { costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> },
  ) => {
    if (!onAddProjectCost) {
      setNotice({ tone: 'warning', title: '费用入口待接入', body: '当前环境没有提供费用写入 API。' });
      setActionModal(null);
      return;
    }
    await onAddProjectCost({
      projectId: project.id,
      costType: payload.costType,
      amountUsd: payload.amountUsd,
      note: payload.note,
      sourceRef: payload.sourceRef,
      metadata: payload.metadata,
    });
    setNotice({
      tone: 'success',
      title: '费用已登记',
      body: `${row.kolHandle || row.kolName || 'KOL'} 的费用已写入成本账本。`,
    });
    setActionModal(null);
    await onProjectUpdated?.();
  };

  const {
    uploadContractFile,
    saveContract,
    confirmContractArchive,
    openContractPdf,
    deleteContractArchive,
    retryContractExtraction,
  } = createContractActions({
    apiToken,
    projectId: project.id,
    loadContracts,
    setContractActionId,
    setNotice,
    setConfirmAction,
  });

  const submitStageAction = async (row: VkpiProjectRow, action: 'stalled' | 'lost' | 'released' | 'cancelled', reason: string) => {
    if (!onAdvanceProjectKol) {
      setNotice({ tone: 'warning', title: '无法提交阶段动作', body: '当前缺少 KOL 级阶段推进接口。' });
      return;
    }
    setMovingRowId(row.id);
    try {
      await onAdvanceProjectKol(project.id, kolRef(row), { to_stage: action, action, reason });
      setStageOverrides((current) => ({ ...current, [row.id]: action }));
      setActionModal(null);
      setNotice({ tone: 'success', title: '阶段动作已写入', body: `${row.kolHandle || row.kolName} 已标记为 ${stageLabels[action]}。` });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '阶段动作失败', body: error instanceof Error ? error.message : '阶段动作提交失败。' });
    } finally {
      setMovingRowId('');
    }
  };

  const saveShopifyLink = async () => {
    if (!onUpsertProjectTerms) {
      setNotice({ tone: 'warning', title: '无法保存链接', body: '当前没有传入项目条款保存接口。' });
      return;
    }
    const link = projectShopifyLink().trim();
    if (!link) {
      setNotice({ tone: 'warning', title: '需要链接', body: '请输入 Shopify 商品链接或带 ref 的归因链接。' });
      return;
    }
    if (!/^https?:\/\//i.test(link)) {
      setNotice({ tone: 'warning', title: '链接格式不对', body: '链接需要以 http:// 或 https:// 开头。' });
      return;
    }
    setSavingShopifyLink(true);
    try {
      await onUpsertProjectTerms(project.id, {
        shopify_url: link,
        shopify_link: link,
        note: `Shopify 归因链接：${link}`,
      });
      setShopifyLinks((current) => ({ ...current, [project.id]: link }));
      setNotice({ tone: 'success', title: '链接已保存', body: `已保存到「${project.campaign || '当前项目'}」的 Shopify 项目归因链接。` });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '保存失败', body: error instanceof Error ? error.message : 'Shopify 链接保存失败。' });
    } finally {
      setSavingShopifyLink(false);
    }
  };

  const jumpToTask = (rowId?: string, tab?: DetailTab) => {
    if (tab) setActiveTab(tab);
    if (rowId) {
      setActiveTab('参与 KOL');
      setExpandedRows((current) => new Set(current).add(rowId));
    }
    window.setTimeout(() => {
      document.getElementById(rowId ? `vkpi-project-row-${rowId}` : 'vkpi-project-participation')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 0);
    setNotice({
      tone: 'info',
      title: '已跳到相关筛选',
      body: rowId ? '已展开对应 KOL 的阶段推进信息。' : `已切换到「${tab || '参与 KOL'}」。`,
    });
  };

  const markTaskFocus = (item: TaskItem) => {
    const row = item.rowId ? rows.find((candidate) => candidate.id === item.rowId) : undefined;
    writeProjectActionFocus({
      source: 'project_detail',
      projectId: project.id,
      projectName: project.campaign || project.productName || '项目跟进',
      rowId: row?.id,
      kolId: row?.kolId,
      kolHandle: row?.kolHandle || row?.kolName,
      productSku: row?.productSku || project.productSku,
      productName: row?.productName || project.productName,
      title: item.title,
      summary: `${project.campaign || project.productName || '项目'} · ${item.subtitle}`,
      priority: item.level === '高' ? 'high' : item.level === '低' ? 'low' : 'medium',
      actionLabel: '进入项目跟进',
      tab: item.tab || (item.rowId ? '参与 KOL' : undefined),
    });
    setNotice({
      tone: 'info',
      title: '已加入本地关注',
      body: '仅保存在本机浏览器(刷新/换设备会丢失)。跨账号的任务派发功能尚未接入。',
    });
  };

  const dismissTaskReminder = () => {
    setTaskReminderOpen(false);
  };

  const copyMaterialText = async (text: string, label: string) => {
    const content = text.trim();
    if (!content) {
      setNotice({ tone: 'warning', title: '没有可复制内容', body: `${label} 还没有可用内容。` });
      return;
    }
    try {
      await writeClipboardText(content);
      setCopyFallback(null);
      setNotice({ tone: 'success', title: '已复制', body: `${label} 已复制到剪贴板。` });
    } catch {
      setCopyFallback({ label, content });
      setNotice(null);
    }
  };

  const addSelectedKols = async (selectedKols: VkpiKolOption[]) => {
    if (!onAddKolsToCampaign) {
      setNotice({ tone: 'warning', title: '无法添加 KOL', body: '当前没有传入项目创建接口，不能追加 KOL。' });
      return;
    }
    setAddingKols(true);
    try {
      await onAddKolsToCampaign(project, selectedKols);
      setAddKolOpen(false);
      setNotice({
        tone: 'success',
        title: 'KOL 已添加',
        body: `已向「${project.campaign || '当前推广'}」追加 ${selectedKols.length} 个 KOL。`,
      });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '添加失败', body: error instanceof Error ? error.message : 'KOL 添加失败。' });
    } finally {
      setAddingKols(false);
    }
  };

  const updateProjectProfile = async (payload: { projectName?: string; productSku?: string; productName?: string; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => {
    if (!onUpdateProject) {
      setNotice({ tone: 'warning', title: '无法编辑', body: '当前没有传入项目编辑接口。' });
      return;
    }
    setEditingProject(true);
    try {
      await onUpdateProject(project.id, payload);
      setEditOpen(false);
      setNotice({ tone: 'success', title: '项目已更新', body: '推广基础信息已保存，并重新读取真实项目详情。' });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '编辑失败', body: error instanceof Error ? error.message : '项目编辑失败。' });
    } finally {
      setEditingProject(false);
    }
  };

  const runConfirmAction = async () => {
    if (!confirmAction) return;
    setConfirmingAction(true);
    try {
      await confirmAction.onConfirm();
      setConfirmAction(null);
    } finally {
      setConfirmingAction(false);
    }
  };

  const toggleFollowStatus = async () => {
    if (!onSetFollowStatus) return;
    await onSetFollowStatus(project, project.followStatus === 'paused' ? 'active' : 'paused');
    await onProjectUpdated?.();
  };

  const cancelProject = () => {
    if (!onDeleteProject) {
      setNotice({ tone: 'warning', title: '无法取消', body: '当前没有传入取消 / 删除项目接口。' });
      return;
    }
    setConfirmAction({
      title: '确认取消推广？',
      body: `取消后「${project.campaign || '当前推广'}」会退出正常推进列表，但保留现有阶段、物流、费用和证据记录。`,
      confirmLabel: '确认取消',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await onDeleteProject(project, `取消推广：${project.campaign}`, '取消推广');
          onBack();
        } catch (error) {
          setNotice({ tone: 'warning', title: '取消失败', body: error instanceof Error ? error.message : '取消推广失败。' });
        }
      },
    });
  };

  const deleteProject = () => {
    if (!onDeleteProject) return;
    setConfirmAction({
      title: '确认删除项目？',
      body: `删除会把「${project.campaign || '当前推广'}」从项目看板移除。请确认这不是误触。`,
      confirmLabel: '确认删除',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await onDeleteProject(project, `删除项目：${project.campaign}`, '删除项目');
          onBack();
        } catch (error) {
          setNotice({ tone: 'warning', title: '删除失败', body: error instanceof Error ? error.message : '项目删除失败。' });
        }
      },
    });
  };

  return (
    <section className="p-3 md:p-4 space-y-3" aria-label="项目详情">
      <ProjectDetailHeaderCard
        project={project}
        health={health}
        currentHealthColor={currentHealthColor}
        ownerInitial={ownerInitial}
        campaignStatus={campaignStatus}
        bottleneck={bottleneck}
        stats={stats}
        viewMode={viewMode}
        canEdit={Boolean(onUpdateProject)}
        canSetFollowStatus={Boolean(onSetFollowStatus)}
        showDelete={Boolean(onDeleteProject)}
        ownerProfileDisabled={!project.ownerId || !onOpenStaffProfile}
        canExport={Boolean(apiToken && project.id)}
        onBack={onBack}
        onEdit={() => setEditOpen(true)}
        onToggleFollowStatus={() => void toggleFollowStatus()}
        onCancelProject={cancelProject}
        onDeleteProject={deleteProject}
        onAddKol={() => void openAddKolModal()}
        onGenerateContract={() => void openGenerateContract()}
        onShare={() => setShareOpen(true)}
        onOpenStaffProfile={() => project.ownerId && onOpenStaffProfile?.(project.ownerId, ownerFallback)}
        onExportKols={async () => {
          if (!apiToken || !project.id) return;
          const { exportVkpiReport } = await import('../../../../services/vkpi/dashboard-api');
          const result = await exportVkpiReport(apiToken, { reportType: 'project_kols', format: 'csv', projectId: project.id });
          const url = result.downloadUrl || result.download_url;
          if (url) window.open(url, '_blank', 'noopener,noreferrer');
        }}
      />

      <LiveLogisticsBanner
        rows={rows}
        trackingForRow={trackingForRow}
        onSyncTracking={apiToken ? async () => {
          const { enqueueLogisticsSync } = await import('../../../../services/vkpi/projects-api');
          const resp = await enqueueLogisticsSync(apiToken, Number(project.id));
          if (resp.status === 'blocked') throw new Error(String(resp.message || '未配置 17track token'));
          return resp.status === 'already_queued' ? '同步已在队列中(泳道「物流同步」)' : '已入队——完成后刷新即见真实轨迹';
        } : undefined}
      />

      <ProjectKpiGrid stats={stats} kolCount={rows.length} />

      <ProjectFunnel
        rows={rows}
        counts={counts}
        tableStage={tableStage}
        bottleneck={bottleneck}
        onSelectStage={selectFunnelStage}
      />

      <ProjectTabsBar activeTab={activeTab} onSelectTab={setActiveTab} />

      <ProjectDetailTabContent
        activeTab={activeTab}
        apiToken={apiToken}
        project={project}
        detail={detail}
        rows={rows}
        filteredRows={filteredRows}
        stats={stats}
        health={health}
        goaffByKol={goaffByKol}
        expandedRows={expandedRows}
        movingRowId={movingRowId}
        platformOptions={platformOptions}
        tableQuery={tableQuery}
        tableStage={tableStage}
        tablePlatform={tablePlatform}
        savingShopify={savingShopifyLink}
        costRows={costRows}
        productUnitCosts={productUnitCosts}
        expenseLines={expenseLines}
        contractLines={contractLines}
        contractsPayload={contractsPayload}
        contractsLoading={contractsLoading}
        contractsError={contractsError}
        contractActionId={contractActionId}
        videoAnalysisCache={videoAnalysisCache}
        videoQaCache={videoQaCache}
        videoAnalysisLoading={videoAnalysisLoading}
        videoAnalysisError={videoAnalysisError}
        videoQaError={videoQaError}
        retrospective={retrospective}
        retroBusy={retroBusy}
        evidenceCountForRow={evidenceCountForRow}
        trackingForRow={trackingForRow}
        shopifyLinkForRow={shopifyLinkForRow}
        onOpenKolProfile={onOpenKolProfile}
        onAddKol={() => void openAddKolModal()}
        onMoveRowStage={moveRowStage}
        onOpenContactModal={(target) => setContactRow(target)}
        onOpenScreenshotModal={openStageEvidenceModal}
        onOpenStageActionModal={(row, action) => setActionModal({ kind: 'stage-action', row, action })}
        onSaveShopifyLink={saveShopifyLink}
        onSetShopifyLink={setProjectShopifyLink}
        onSetTablePlatform={setTablePlatform}
        onSetTableQuery={setTableQuery}
        onSetTableStage={setTableStage}
        onToggleRow={toggleRow}
        setActionModal={setActionModal}
        setNotice={setNotice}
        onCopy={copyMaterialText}
        onUpsertProjectTerms={onUpsertProjectTerms}
        onAddProjectShipment={onAddProjectShipment}
        onUploadEvidenceFile={onUploadEvidenceFile}
        onProjectUpdated={onProjectUpdated}
        onGenerateRetrospective={generateRetrospective}
        uploadContractFile={uploadContractFile}
        saveContract={saveContract}
        confirmContractArchive={confirmContractArchive}
        openContractPdf={openContractPdf}
        retryContractExtraction={retryContractExtraction}
        deleteContractArchive={deleteContractArchive}
      />

      <ProjectTaskDock
        taskReminderOpen={taskReminderOpen}
        taskItems={taskItems}
        reminderTasks={reminderTasks}
        onJumpToTask={jumpToTask}
        onMarkTaskFocus={markTaskFocus}
        onOpen={() => setTaskReminderOpen(true)}
        onDismiss={dismissTaskReminder}
      />

      <ProjectDetailModalsLayer
        apiToken={apiToken}
        project={project}
        rows={rows}
        staff={staff}
        actionModal={actionModal}
        contactRow={contactRow}
        addKolOpen={addKolOpen}
        editOpen={editOpen}
        shareOpen={shareOpen}
        generateOpen={generateOpen}
        generateBusy={generateBusy}
        editingProject={editingProject}
        addingKols={addingKols}
        loadingAvailableKols={loadingAvailableKols}
        availableKolError={availableKolError}
        availableScope={availableScope}
        availableKolOptions={availableKolOptions}
        kolOptions={kolOptions}
        contractTemplates={contractTemplates}
        contractPartyA={contractPartyA}
        notice={notice}
        copyFallback={copyFallback}
        confirmAction={confirmAction}
        confirmingAction={confirmingAction}
        onLoadAvailableKols={onLoadAvailableKols}
        setActionModal={setActionModal}
        setContactRow={setContactRow}
        setAddKolOpen={setAddKolOpen}
        setEditOpen={setEditOpen}
        setShareOpen={setShareOpen}
        setGenerateOpen={setGenerateOpen}
        setGenerateBusy={setGenerateBusy}
        setNotice={setNotice}
        setCopyFallback={setCopyFallback}
        setConfirmAction={setConfirmAction}
        submitActionStub={submitActionStub}
        submitShippingInfo={submitShippingInfo}
        submitProjectCost={submitProjectCost}
        submitStageAction={submitStageAction}
        addSelectedKols={addSelectedKols}
        updateProjectProfile={updateProjectProfile}
        loadAvailableForScope={loadAvailableForScope}
        loadContracts={loadContracts}
        openContractPdf={openContractPdf}
        runConfirmAction={runConfirmAction}
        onProjectUpdated={onProjectUpdated}
      />
    </section>
  );
}
