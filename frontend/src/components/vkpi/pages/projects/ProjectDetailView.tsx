import { useEffect, useMemo, useRef, useState } from 'react';
import { PackageCheck } from 'lucide-react';
import type { VkpiKolOption, VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';
import { writeProjectActionFocus } from '../../intelligence/intelligenceProjectActionFocus';
import { CampaignAnalyticsTab, CampaignContractsTab, CampaignFinanceTab, CampaignMaterialsTab, CampaignRetrospectiveTab, CampaignTimelineTab } from './ProjectDetailTabs';
import { AddKolModal, ContractUploadModal, CostEntryModal, EditProjectModal, ShippingInfoModal, StageActionModal, UploadScreenshotModal, VideoUrlModal } from './ProjectDetailModals';
import { LiveLogisticsBanner } from './LiveLogisticsBanner';
import { ProjectParticipationTab } from './ProjectParticipationTab';
import {
  confirmProjectContract,
  deleteProjectContract,
  downloadProjectContract,
  extractProjectContract,
  generateProjectRetrospective,
  getProjectContracts,
  getProjectRetrospective,
  getProjectVideoAnalysisCacheMulti,
  patchProjectContract,
  uploadProjectContract,
  type VkpiProjectContractsResponse,
  type VkpiProjectRetrospectiveResponse,
  type VkpiProjectVideoAnalysisCacheResponse,
} from '../../../../services/vkpi/projects-api';
import {
  bottleneckForRows,
  buildAnalytics,
  buildContractLines,
  buildExpenseLines,
  buildProjectStatsSummary,
  buildProjectTaskItems,
  buildStageCostSummary,
  defaultTracking,
  detailTabs,
  formatMoney,
  formatNumber,
  formatRatio,
  matchesProjectStageFilter,
  isLostStage,
  parseDays,
  stageCounts,
  stageIndex,
  statusForProject,
  type ConfirmAction,
  type DetailTab,
  type NoticeState,
  type ProjectDetailViewProps,
  type ScreenshotTarget,
  type TaskItem,
  type TrackingState,
} from '../../../../domains/projects';
import {
  healthBg,
  healthColor,
  PROJECT_STAGE_COLOR,
  PROJECT_STAGE_FLOW,
  PROJECT_STATUS_COLOR,
  statusBg,
} from './projectDeliverableStyle';

function healthFromBackend(scoreValue: number | undefined) {
  const score = Number.isFinite(scoreValue) ? Math.max(0, Math.min(100, Math.round(scoreValue || 0))) : 0;
  if (score >= 85) return { score, className: 'is-good', label: '健康' };
  if (score >= 70) return { score, className: 'is-mid', label: '关注' };
  return { score, className: 'is-bad', label: '风险' };
}

type DetailActionModal =
  | { kind: 'screenshot'; target: ScreenshotTarget }
  | { kind: 'shipping'; row: VkpiProjectRow }
  | { kind: 'cost'; row: VkpiProjectRow; costType?: 'cash_fee' | 'shipping' | 'product' }
  | { kind: 'video'; row: VkpiProjectRow }
  | { kind: 'contract'; row: VkpiProjectRow }
  | { kind: 'stage-action'; row: VkpiProjectRow; action: 'stalled' | 'lost' | 'released' | 'cancelled' };

interface CopyFallbackState {
  label: string;
  content: string;
}

function kolRef(row: VkpiProjectRow) {
  return row.assignmentId || row.kolPoolId || row.kolId || row.id.replace(/^assignment:/, '');
}

export function ProjectDetailView({
  apiToken,
  detail,
  project,
  participatingRows = [],
  costRows = [],
  productUnitCosts = {},
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
  const [addingKols, setAddingKols] = useState(false);
  const [availableKolOptions, setAvailableKolOptions] = useState<VkpiKolOption[]>([]);
  const [loadingAvailableKols, setLoadingAvailableKols] = useState(false);
  const [availableKolError, setAvailableKolError] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(false);
  const [actionModal, setActionModal] = useState<DetailActionModal | null>(null);
  const [movingRowId, setMovingRowId] = useState('');
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [copyFallback, setCopyFallback] = useState<CopyFallbackState | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [confirmingAction, setConfirmingAction] = useState(false);
  const [taskReminderOpen, setTaskReminderOpen] = useState(false);
  const contractPollStartRef = useRef(0);
  const contractStallWarnedRef = useRef(false);
  const contractPollStoppedRef = useRef(false);
  const [videoAnalysisCache, setVideoAnalysisCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoQaCache, setVideoQaCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoAnalysisLoading, setVideoAnalysisLoading] = useState(false);
  const [videoAnalysisError, setVideoAnalysisError] = useState('');
  const [videoQaError, setVideoQaError] = useState('');
  const [contractsPayload, setContractsPayload] = useState<VkpiProjectContractsResponse | null>(null);
  const [contractsLoading, setContractsLoading] = useState(false);
  const [contractsError, setContractsError] = useState('');
  const [retrospective, setRetrospective] = useState<VkpiProjectRetrospectiveResponse | null>(null);
  const [retroBusy, setRetroBusy] = useState(false);
  const [contractActionId, setContractActionId] = useState('');

  useEffect(() => {
    setStageOverrides({});
    setMovingRowId('');
  }, [project.id, project.stage]);

  useEffect(() => {
    setVideoAnalysisCache(null);
    setVideoQaCache(null);
    setVideoAnalysisError('');
    setVideoQaError('');
    if (!apiToken || !project.id) {
      setVideoAnalysisLoading(false);
      return;
    }
    let cancelled = false;
    setVideoAnalysisLoading(true);
    // 批5:final_v1 + keyframe_qa 合并为一次请求(后端 by_method 拆分),省一个往返。
    getProjectVideoAnalysisCacheMulti(apiToken, project.id, ['video_analysis_final_v1', 'video_analysis_final_v1_keyframe_qa'])
      .then((payload) => {
        if (cancelled) return;
        setVideoAnalysisCache(payload.by_method?.['video_analysis_final_v1'] ?? null);
        setVideoQaCache(payload.by_method?.['video_analysis_final_v1_keyframe_qa'] ?? null);
      })
      .catch((error) => {
        if (cancelled) return;
        setVideoAnalysisError(error instanceof Error ? error.message : 'final_v1 分析读取失败');
        setVideoQaError(error instanceof Error ? error.message : '关键帧 QA 读取失败');
      })
      .finally(() => {
        if (!cancelled) setVideoAnalysisLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, project.id]);

  const loadContracts = async () => {
    if (!apiToken || !project.id) {
      setContractsPayload(null);
      return;
    }
    setContractsLoading(true);
    setContractsError('');
    try {
      setContractsPayload(await getProjectContracts(apiToken, project.id));
    } catch (error) {
      setContractsError(error instanceof Error ? error.message : '合同列表读取失败');
    } finally {
      setContractsLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setContractsPayload(null);
    setContractsError('');
    if (!apiToken || !project.id) {
      setContractsLoading(false);
      return;
    }
    setContractsLoading(true);
    getProjectContracts(apiToken, project.id)
      .then((payload) => {
        if (!cancelled) setContractsPayload(payload);
      })
      .catch((error) => {
        if (!cancelled) setContractsError(error instanceof Error ? error.message : '合同列表读取失败');
      })
      .finally(() => {
        if (!cancelled) setContractsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, project.id]);

  // 合同提取已改异步(worker 执行):有 processing 行时轮询刷新,直至 ready/failed。
  // m2 轮询契约:前 5 分钟每 2.5s → 之后退避到 10s → 30 分钟封顶停止自动刷新。
  // m1 卡滞文案:worker 是串行队列,排队久 ≠ worker 挂了,文案不做误报断言。
  useEffect(() => {
    if (!apiToken || !project.id) return undefined;
    const hasProcessing = (contractsPayload?.items || []).some((item) => item.extraction_status === 'processing');
    if (!hasProcessing) {
      contractStallWarnedRef.current = false;
      contractPollStoppedRef.current = false;
      contractPollStartRef.current = 0;
      return undefined;
    }
    if (!contractPollStartRef.current) contractPollStartRef.current = Date.now();
    const elapsed = Date.now() - contractPollStartRef.current;
    if (elapsed > 30 * 60 * 1000) {
      if (!contractPollStoppedRef.current) {
        contractPollStoppedRef.current = true;
        setNotice({
          tone: 'warning',
          title: '已暂停合同提取自动刷新',
          body: '排队超过 30 分钟。请确认 worker 是否在运行；恢复后可点「重新提取」重新入队，或刷新页面查看最新状态。',
        });
      }
      return undefined;
    }
    const intervalMs = elapsed < 5 * 60 * 1000 ? 2500 : 10000;
    const timer = window.setInterval(async () => {
      try {
        const payload = await getProjectContracts(apiToken, project.id);
        setContractsPayload(payload);
        if (!contractStallWarnedRef.current && Date.now() - contractPollStartRef.current > 180000) {
          contractStallWarnedRef.current = true;
          setNotice({
            tone: 'info',
            title: '合同提取仍在队列中',
            body: 'worker 串行处理任务，前方可能有长视频分析在跑（见左侧任务泳道）。超过 30 分钟仍未完成时再检查 worker 运行状态。',
          });
        }
      } catch {
        // 轮询单次失败不打断:保留上次列表,下个周期重试;持续无进展由 30 分钟封顶提示兜底
      }
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [apiToken, project.id, contractsPayload]);

  // 复盘项目级聚合(LLM):打开时读一次;生成中由 active_job 驱动轮询,ready/failed 停。
  useEffect(() => {
    let cancelled = false;
    setRetrospective(null);
    if (!apiToken || !project.id) return undefined;
    getProjectRetrospective(apiToken, project.id)
      .then((payload) => { if (!cancelled) setRetrospective(payload); })
      .catch(() => { /* 读失败不阻断:复盘卡降级到模板占位 */ });
    return () => { cancelled = true; };
  }, [apiToken, project.id]);

  useEffect(() => {
    if (!apiToken || !project.id) return undefined;
    const activeJob = retrospective?.active_job;
    if (!activeJob) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const payload = await getProjectRetrospective(apiToken, project.id);
        setRetrospective(payload);
        if (!payload.active_job) {
          setRetroBusy(false);
          const failed = String(payload.last_job?.status || '') === 'failed' || String(payload.last_job?.status || '') === 'blocked';
          if (failed && !payload.retrospective?.result) {
            setNotice({ tone: 'warning', title: '项目复盘生成未完成', body: payload.last_job?.last_error ? `原因：${payload.last_job.last_error}` : '生成失败，请稍后重试或检查 worker。' });
          }
        }
      } catch {
        // 单次轮询失败保留上次状态,下个周期重试
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [apiToken, project.id, retrospective?.active_job]);

  const generateRetrospective = async () => {
    if (!apiToken || !project.id) return;
    setRetroBusy(true);
    try {
      const result = await generateProjectRetrospective(apiToken, project.id);
      const already = String(result?.status || '').startsWith('already');
      setNotice({ tone: 'success', title: already ? '复盘已在生成队列中' : '项目复盘已入队', body: '完成后此处自动更新；进度见左侧任务泳道「复盘聚合 · 总结中」。' });
      setRetrospective(await getProjectRetrospective(apiToken, project.id));
    } catch (error) {
      setRetroBusy(false);
      setNotice({ tone: 'warning', title: '复盘生成入队失败', body: error instanceof Error ? error.message : '请稍后重试。' });
    }
  };

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
  const stats = useMemo(() => buildProjectStatsSummary(rows, detail), [rows, detail]);
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

  const openAddKolModal = async () => {
    setAddKolOpen(true);
    setAvailableKolError('');
    if (!onLoadAvailableKols) {
      setAvailableKolOptions([]);
      return;
    }
    setLoadingAvailableKols(true);
    try {
      setAvailableKolOptions(await onLoadAvailableKols(project));
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

  const submitActionStub = async (kind: 'screenshot' | 'video' | 'contract', row: VkpiProjectRow, payload: Record<string, unknown>) => {
    // 合同走真实归档链(合同归档 tab 的上传可关联本 KOL),不再调审计-only stub。
    if (kind === 'contract') {
      setActionModal(null);
      setActiveTab('合同归档');
      setNotice({ tone: 'info', title: '请用合同归档上传', body: '已切到「合同归档」tab——这里是真实链路(上传 + Claude 提取 + 入账),上传时可在「关联 KOL」选择本红人。' });
      return;
    }
    if (!onSubmitProjectKolActionStub) {
      setNotice({ tone: 'info', title: '暂存提醒', body: '当前环境缺少写入接口或 API token，本次操作不会修改项目数据。' });
      return;
    }
    const result = await onSubmitProjectKolActionStub(project.id, kolRef(row), kind, payload);
    if (kind === 'screenshot') {
      // 后端 stub 只写审计日志、不存文件——不再做乐观 +1 假反馈(诚实化,扫描 #5)。
      setActionModal(null);
      setNotice({ tone: 'info', title: '已记录到审计日志', body: '截图文件存证尚未接入(开发中);如需上传留档,可先用「物料」tab 的条款附件/物流凭证表单。' });
      return;
    }
    if (kind === 'video') {
      const metrics = (result.metrics || {}) as Record<string, unknown>;
      const title = typeof metrics.title === 'string' && metrics.title ? metrics.title : '视频';
      const views = typeof metrics.view_count === 'number' ? formatNumber(metrics.view_count) : '—';
      setEvidenceOverrides((current) => ({ ...current, [row.id]: evidenceCountForRow(row) + 1 }));
      setActionModal(null);
      setNotice({ tone: 'success', title: '视频已写入 evidence', body: `${title} · 播放 ${views}` });
      await onProjectUpdated?.();
    }
  };

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

  const uploadContractFile = async (file: File, assignmentId?: string, kolPoolId?: string) => {
    if (!apiToken) {
      setNotice({ tone: 'warning', title: '无法上传合同', body: '当前缺少 API token。' });
      return;
    }
    const isPdf = /\.pdf$/i.test(file.name);
    setContractActionId('upload');
    try {
      await uploadProjectContract(apiToken, project.id, file, { assignmentId, kolPoolId });
      await loadContracts();
      setNotice({ tone: 'success', title: '合同已归档', body: isPdf ? '条款提取已入队，完成后此处自动回填；进度见左侧任务泳道。' : '文件已存档；DOC/DOCX 暂不自动提取。' });
    } catch (error) {
      setNotice({ tone: 'warning', title: '合同上传失败', body: error instanceof Error ? error.message : '合同上传失败。' });
    } finally {
      setContractActionId('');
    }
  };

  const saveContract = async (contractId: number, payload: Record<string, unknown>) => {
    if (!apiToken) return;
    setContractActionId(`save:${contractId}`);
    try {
      await patchProjectContract(apiToken, project.id, contractId, payload);
      await loadContracts();
      setNotice({ tone: 'success', title: '合同字段已保存', body: '人工修改已写入合同归档。' });
    } catch (error) {
      setNotice({ tone: 'warning', title: '保存失败', body: error instanceof Error ? error.message : '合同字段保存失败。' });
    } finally {
      setContractActionId('');
    }
  };

  const confirmContractArchive = async (contractId: number, payload: Record<string, unknown>) => {
    if (!apiToken) return;
    setContractActionId(`confirm:${contractId}`);
    try {
      await confirmProjectContract(apiToken, project.id, contractId, payload);
      await loadContracts();
      setNotice({ tone: 'success', title: '合同已确认归档', body: '该合同已标记为人工确认，可进入履约复盘。' });
    } catch (error) {
      setNotice({ tone: 'warning', title: '确认失败', body: error instanceof Error ? error.message : '合同确认失败。' });
    } finally {
      setContractActionId('');
    }
  };

  const openContractPdf = async (contractId: number) => {
    if (!apiToken) return;
    setContractActionId(`download:${contractId}`);
    try {
      const result = await downloadProjectContract(apiToken, project.id, contractId);
      window.open(result.url, '_blank', 'noopener,noreferrer');
    } catch (error) {
      setNotice({ tone: 'warning', title: 'PDF 打开失败', body: error instanceof Error ? error.message : '无法生成合同下载链接。' });
    } finally {
      setContractActionId('');
    }
  };

  const deleteContractArchive = (contractId: number, fileName?: string) => {
    if (!apiToken) return;
    setConfirmAction({
      title: '确认删除该合同归档？',
      body: `「${fileName || `合同 ${contractId}`}」将从归档列表移除并删除原文件，提取结果一并清除。LLM 提取的历史成本记录会保留。`,
      confirmLabel: '确认删除',
      confirmVariant: 'danger',
      onConfirm: async () => {
        setContractActionId(`delete:${contractId}`);
        try {
          await deleteProjectContract(apiToken, project.id, contractId);
          await loadContracts();
          setNotice({ tone: 'success', title: '合同已删除', body: '归档记录与原文件已删除。' });
        } catch (error) {
          setNotice({ tone: 'warning', title: '删除失败', body: error instanceof Error ? error.message : '合同删除失败。' });
        } finally {
          setContractActionId('');
        }
      },
    });
  };

  const retryContractExtraction = async (contractId: number) => {
    if (!apiToken) return;
    setContractActionId(`extract:${contractId}`);
    try {
      const result = await extractProjectContract(apiToken, project.id, contractId);
      await loadContracts();
      const already = String(result?.status || '').startsWith('already');
      setNotice({ tone: 'success', title: already ? '提取任务已在队列中' : '提取任务已入队', body: '完成后此处自动回填；进度见左侧任务泳道。' });
    } catch (error) {
      setNotice({ tone: 'warning', title: '重新提取失败', body: error instanceof Error ? error.message : '合同重新提取入队失败。' });
    } finally {
      setContractActionId('');
    }
  };

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

  const writeClipboardText = async (content: string) => {
    const textarea = document.createElement('textarea');
    textarea.value = content;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const copied = document.execCommand('copy');
    document.body.removeChild(textarea);
    if (copied) return;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(content);
        return;
      } catch {
        // The user-facing message below is clearer than browser permission errors.
      }
    }
    throw new Error('浏览器没有允许剪贴板写入。');
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
      <div className="flex items-center gap-3">
        <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" type="button" onClick={onBack}>← 返回项目列表</button>
        <div className="flex-1" />
        <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white flex items-center gap-1.5" type="button" onClick={() => setEditOpen(true)} disabled={!onUpdateProject}>编辑项目</button>
      </div>

      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4 flex items-start gap-4">
        <div className="w-14 h-14 rounded-xl flex items-center justify-center shrink-0" style={{ background: healthBg(health.score), color: currentHealthColor }}>
          <PackageCheck size={26} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <h2 className="text-[20px] font-bold text-white">{project.campaign || '未命名推广'}</h2>
            <span className="text-[10.5px] px-2 py-0.5 rounded font-medium" style={{ background: statusBg(campaignStatus), color: PROJECT_STATUS_COLOR[campaignStatus] || PROJECT_STATUS_COLOR['已结束'] }}>{campaignStatus}</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.04] text-slate-300 flex items-center gap-1">产品 {project.campaign || '-'}</span>
          </div>
          <div className="text-[12px] text-slate-400 mb-2">{bottleneck.text}</div>
          <div className="flex items-center gap-3 flex-wrap">
            <button
              className="flex items-center gap-1.5"
              type="button"
              disabled={!project.ownerId || !onOpenStaffProfile}
              onClick={() => project.ownerId && onOpenStaffProfile?.(project.ownerId, ownerFallback)}
            >
              <div className="w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white" style={{ background: '#a855f7' }}>{ownerInitial}</div>
              <span className="text-[10.5px] text-slate-400">负责人 {project.ownerName || '-'}</span>
            </button>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">最近更新 {shortDateTime(project.updatedAt || project.latestMessageAt)}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">预算 {formatMoney(stats.cost)} · 销售 {formatMoney(stats.gmv)}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">{viewMode === 'manager' ? '上市推广' : '员工跟进'}</span>
          </div>
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" type="button" onClick={() => setEditOpen(true)} disabled={!onUpdateProject}>编辑</button>
            <button className="px-3 py-1.5 rounded-md border border-sky-400/30 bg-sky-400/10 text-[11px] text-sky-200 hover:bg-sky-400/15" type="button" onClick={() => void toggleFollowStatus()} disabled={!onSetFollowStatus}>{project.followStatus === 'paused' ? '重新开启' : '暂停本轮跟进'}</button>
            <button className="px-3 py-1.5 rounded-md border border-red-500/30 bg-red-500/10 text-[11px] text-red-300 hover:bg-red-500/15" type="button" onClick={cancelProject}>取消推广</button>
            {onDeleteProject ? <button className="px-3 py-1.5 rounded-md border border-red-500/30 bg-red-500/10 text-[11px] text-red-300 hover:bg-red-500/15" type="button" onClick={deleteProject}>删除</button> : null}
            <button className="px-3 py-1.5 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-medium flex items-center gap-1.5" type="button" onClick={() => void openAddKolModal()}>+ 添加 KOL</button>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10px] text-slate-500 mb-0.5">健康度</div>
          <div className="text-[40px] font-bold leading-none tabular-nums" style={{ color: currentHealthColor }}>{health.score}</div>
          <div className="mt-2 text-[10.5px] px-2 py-0.5 rounded font-medium inline-flex" style={{ background: healthBg(health.score), color: currentHealthColor }}>{health.label}</div>
        </div>
      </div>

      <LiveLogisticsBanner rows={rows} trackingForRow={trackingForRow} />

      <div className="grid grid-cols-6 gap-2" aria-label="项目 KPI">
        {[
          ['参与 KOL', rows.length, '当前项目行'],
          ['总曝光', formatNumber(stats.views), '自动汇总'],
          ['已发布内容', stats.published, `发布率 ${stats.publishRate}%`],
          ['短链点击', formatNumber(stats.clicks), `${formatNumber(stats.orders)} 单`],
          ['归因销售', formatMoney(stats.gmv), '现有 GMV'],
          ['ROI', formatRatio(stats.roi), `成本 ${formatMoney(stats.cost)}`],
        ].map(([label, value, hint]) => (
          <div className="min-h-[68px] rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2.5" key={label}>
            <span className="block text-slate-500 text-[9.5px] font-medium leading-none">{label}</span>
            <strong className={`block mt-1.5 text-[21px] font-bold leading-none tabular-nums ${label === 'ROI' ? 'text-emerald-400' : 'text-white'}`}>{value}</strong>
            <em className="block mt-1 text-[9.5px] text-slate-500 not-italic leading-tight">{hint}</em>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-white">KOL 进度漏斗</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">项目内所有 KOL 在各阶段的实时分布</p>
          </div>
          <span
            className="text-[10px] px-2 py-0.5 rounded-full border border-emerald-500/20 bg-emerald-500/[0.08] text-emerald-300 inline-flex items-center gap-1 pointer-events-none select-none"
            title="真刷新功能将在视频 URL 每日刷新 job 接入后启用"
          >
            状态 · 每日刷新待接入
          </span>
        </div>
        <div className="grid grid-cols-10 gap-2">
          {PROJECT_STAGE_FLOW.map((stage, index) => {
            const stageNumber = index + 1;
            const stageKey = stage.key as VkpiProjectStage;
            // 末列"已关闭"剔除流失/取消(独立列展示,口径不再失真;扫描 #11)。
            const lostInStage = stageNumber === 9 ? rows.filter((row) => stageIndex(row.stage) === index && isLostStage(row.stage)).length : 0;
            const count = (counts.get(stageNumber) || 0) - lostInStage;
            const nextCount = counts.get(stageNumber + 1) || 0;
            const rate = count && stageNumber < 9 ? `${Math.round(Math.min(nextCount / count, 1) * 100)}%` : '—';
            const average = rows
              .filter((row) => stageIndex(row.stage) === index && !(stageNumber === 9 && isLostStage(row.stage)))
              .map((row) => parseDays(row.stageDurationLabel))
              .filter(Boolean);
            const avgDays = average.length ? `${Math.round(average.reduce((sum, day) => sum + day, 0) / average.length)} 天` : '-';
            const color = PROJECT_STAGE_COLOR[stage.key];
            const isActive = count > 0;
            const isSelected = tableStage === stage.key;
            return (
              <button
                aria-pressed={isSelected}
                className="rounded-lg p-3 text-center transition-all"
                key={stage.key}
                onClick={() => selectFunnelStage(stageKey)}
                style={{
                  background: isSelected ? `${color}1f` : isActive ? `${color}12` : 'rgba(255,255,255,0.015)',
                  border: isSelected ? `1px solid ${color}` : isActive ? `1px solid ${color}45` : '1px solid rgba(255,255,255,0.04)',
                  boxShadow: isSelected ? `0 0 0 1px ${color}22, 0 12px 32px ${color}14` : undefined,
                }}
                title={`筛选 ${stageNumber}. ${stage.label}`}
                type="button"
              >
                <div className="text-[10px] text-slate-400 mb-1">{stageNumber}. {stage.label}</div>
                <div className="text-[28px] font-bold tabular-nums leading-none" style={{ color: isActive ? color : '#475569' }}>{count}</div>
                <div className="mt-1 text-[10px] text-slate-500">平均 {avgDays}</div>
                <div className="text-[10px] text-slate-500">→ {rate}</div>
              </button>
            );
          })}
          {(() => {
            const lostRows = rows.filter((row) => isLostStage(row.stage));
            const lostAvg = lostRows.map((row) => parseDays(row.stageDurationLabel)).filter(Boolean);
            const lostAvgDays = lostAvg.length ? `${Math.round(lostAvg.reduce((sum, day) => sum + day, 0) / lostAvg.length)} 天` : '-';
            return (
              <div
                className="rounded-lg p-3 text-center"
                style={{
                  background: lostRows.length ? 'rgba(244,63,94,0.07)' : 'rgba(255,255,255,0.015)',
                  border: lostRows.length ? '1px solid rgba(244,63,94,0.28)' : '1px solid rgba(255,255,255,0.04)',
                }}
                title="流失(churned)/取消(cancelled/lost/stalled)聚合,不计入主流程与『已关闭』"
              >
                <div className="text-[10px] text-slate-400 mb-1">✕ 流失/取消</div>
                <div className="text-[28px] font-bold tabular-nums leading-none" style={{ color: lostRows.length ? '#fb7185' : '#475569' }}>{lostRows.length}</div>
                <div className="mt-1 text-[10px] text-slate-500">平均 {lostAvgDays}</div>
                <div className="text-[10px] text-slate-500">独立口径</div>
              </div>
            );
          })()}
        </div>
        <div className="mt-3 rounded-xl border border-amber-400/25 bg-amber-400/10 text-amber-300 px-3 py-2 text-[11px] font-medium">当前瓶颈：<b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
      </div>

      <div className="vkpi-campaign-tabs" aria-label="项目详情 tabs">
        {detailTabs.map((tab) => (
          <button key={tab} className={activeTab === tab ? 'is-active' : ''} type="button" onClick={() => setActiveTab(tab)}>
            {tab}
          </button>
        ))}
      </div>

      {activeTab === '参与 KOL' ? (
        <ProjectParticipationTab
          expandedRows={expandedRows}
          evidenceCountForRow={evidenceCountForRow}
          filteredRows={filteredRows}
          movingRowId={movingRowId}
          onAddKol={() => void openAddKolModal()}
          onMoveRowStage={moveRowStage}
          onOpenKolProfile={onOpenKolProfile}
          onOpenScreenshotModal={openStageEvidenceModal}
          onOpenStageActionModal={(row, action) => setActionModal({ kind: 'stage-action', row, action })}
          onSaveShopifyLink={saveShopifyLink}
          onSetShopifyLink={setProjectShopifyLink}
          onSetTablePlatform={setTablePlatform}
          onSetTableQuery={setTableQuery}
          onSetTableStage={setTableStage}
          onToggleRow={toggleRow}
          platformOptions={platformOptions}
          savingShopify={savingShopifyLink}
          shopifyLinkForRow={shopifyLinkForRow}
          tablePlatform={tablePlatform}
          tableQuery={tableQuery}
          tableStage={tableStage}
          trackingForRow={trackingForRow}
        />
      ) : activeTab === '数据汇总' ? (
        <CampaignAnalyticsTab
          rows={rows}
          stats={stats}
          analytics={analytics}
          health={health}
        />
      ) : activeTab === '物料' ? (
        <CampaignMaterialsTab
          project={project}
          rows={rows}
          stats={stats}
          costRows={costRows}
          productUnitCosts={productUnitCosts}
          onCopy={copyMaterialText}
          onPendingAction={(label) => setNotice({ tone: 'info', title: '素材库功能开发中', body: `${label} 功能开发中，敬请期待。` })}
          projectId={project.id}
          onUpsertTerms={onUpsertProjectTerms ? async (payload) => { await onUpsertProjectTerms(project.id, payload); await onProjectUpdated?.(); } : undefined}
          onAddShipment={onAddProjectShipment ? async (payload) => { await onAddProjectShipment(project.id, payload); await onProjectUpdated?.(); } : undefined}
          onUploadEvidenceFile={onUploadEvidenceFile}
        />
      ) : activeTab === '费用' ? (
        <CampaignFinanceTab
          rows={rows}
          stats={stats}
          expenseLines={expenseLines}
          stageCosts={stageCosts}
          costRows={costRows}
          productUnitCosts={productUnitCosts}
          onOpenShippingInfo={() => setActionModal({ kind: 'shipping', row: rows[0] || project })}
          onOpenCostEntry={(row, costType) => setActionModal({ kind: 'cost', row, costType })}
        />
      ) : activeTab === '合同归档' ? (
        <CampaignContractsTab
          rows={rows}
          contractLines={contractLines}
          contracts={contractsPayload}
          loading={contractsLoading}
          error={contractsError}
          busyKey={contractActionId}
          onUploadContract={uploadContractFile}
          onSaveContract={saveContract}
          onConfirmContract={confirmContractArchive}
          onOpenContract={openContractPdf}
          onRetryExtract={retryContractExtraction}
          onDeleteContract={deleteContractArchive}
        />
      ) : activeTab === '复盘' ? (
        <CampaignRetrospectiveTab
          project={project}
          rows={rows}
          stats={stats}
          analytics={analytics}
          health={health}
          bottleneck={bottleneck}
          videoAnalysisCache={videoAnalysisCache}
          videoQaCache={videoQaCache}
          videoAnalysisLoading={videoAnalysisLoading}
          videoAnalysisError={videoAnalysisError}
          videoQaError={videoQaError}
          retrospective={retrospective?.retrospective || null}
          retrospectiveLastJob={retrospective?.last_job || null}
          retrospectiveGenerating={retroBusy || Boolean(retrospective?.active_job)}
          onGenerateRetrospective={generateRetrospective}
          onCopy={copyMaterialText}
          onPendingAction={(label) => setNotice({ tone: 'info', title: '暂存提醒', body: `${label} 已进入项目操作队列，后续同步后更新状态。` })}
        />
      ) : activeTab === '时间轴' ? (
        <CampaignTimelineTab rows={rows} events={detail?.events || []} />
      ) : (
        <div className="vkpi-campaign-placeholder">
          <h3>{activeTab}</h3>
          <p>建设中：这里会显示 {activeTab} 的明细、筛选和归档结果。</p>
        </div>
      )}

      <div className={`vkpi-campaign-task-dock ${taskReminderOpen ? 'is-open' : 'is-collapsed'}`}>
        {taskReminderOpen ? (
          <div className="vkpi-campaign-task-panel">
            <div className="vkpi-campaign-task-panel-header">
              <h3>今日该做什么</h3>
              <button className="vkpi-campaign-task-close" type="button" onClick={dismissTaskReminder}>
                收起
              </button>
            </div>
            {taskItems.map((item) => (
              <button
                key={`${item.title}-${item.subtitle}`}
                type="button"
                onClick={() => jumpToTask(item.rowId, item.tab)}
              >
                <span className={item.className}>{item.level}</span>
                <b>{item.title}</b>
                <small>{item.subtitle}</small>
              </button>
            ))}
            {taskItems[0] ? (
              <button
                className="vkpi-campaign-task-send"
                type="button"
                onClick={() => markTaskFocus(taskItems[0])}
              >
                暂存提醒
              </button>
            ) : null}
          </div>
        ) : (
          <button className="vkpi-campaign-task-trigger" type="button" onClick={() => setTaskReminderOpen(true)}>
            今日提醒
            {reminderTasks.length ? <span>{reminderTasks.length}</span> : null}
          </button>
        )}
      </div>

      {actionModal?.kind === 'screenshot' ? (
        <UploadScreenshotModal
          target={actionModal.target}
          onClose={() => setActionModal(null)}
          onSubmit={(payload) => submitActionStub('screenshot', actionModal.target.row, payload)}
        />
      ) : null}

      {actionModal?.kind === 'shipping' ? (
        <ShippingInfoModal
          project={project}
          row={actionModal.row}
          onClose={() => setActionModal(null)}
          onSubmit={(payload) => submitShippingInfo(actionModal.row, payload)}
        />
      ) : null}
      {actionModal?.kind === 'cost' ? (
        <CostEntryModal
          project={project}
          row={actionModal.row}
          defaultType={actionModal.costType}
          onClose={() => setActionModal(null)}
          onSubmit={(payload) => submitProjectCost(actionModal.row, payload)}
        />
      ) : null}

      {actionModal?.kind === 'video' ? (
        <VideoUrlModal
          row={actionModal.row}
          onClose={() => setActionModal(null)}
          onSubmit={(payload) => submitActionStub('video', actionModal.row, payload)}
        />
      ) : null}

      {actionModal?.kind === 'contract' ? (
        <ContractUploadModal
          row={actionModal.row}
          onClose={() => setActionModal(null)}
          onSubmit={(payload) => submitActionStub('contract', actionModal.row, payload)}
        />
      ) : null}

      {actionModal?.kind === 'stage-action' ? (
        <StageActionModal
          row={actionModal.row}
          action={actionModal.action}
          onClose={() => setActionModal(null)}
          onSubmit={(reason) => submitStageAction(actionModal.row, actionModal.action, reason)}
        />
      ) : null}

      {addKolOpen ? (
        <AddKolModal
          project={project}
          rows={rows}
          kolOptions={availableKolOptions.length || onLoadAvailableKols ? availableKolOptions : kolOptions}
          busy={addingKols || loadingAvailableKols}
          loading={loadingAvailableKols}
          loadError={availableKolError}
          onClose={() => setAddKolOpen(false)}
          onSubmit={addSelectedKols}
        />
      ) : null}

      {editOpen ? (
        <EditProjectModal
          project={project}
          busy={editingProject}
          onClose={() => setEditOpen(false)}
          onSubmit={updateProjectProfile}
        />
      ) : null}

      {notice ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={() => setNotice(null)}>
          <div className={`vkpi-campaign-notice is-${notice.tone}`} role="dialog" aria-label={notice.title} onClick={(event) => event.stopPropagation()}>
            <h3>{notice.title}</h3>
            <p>{notice.body}</p>
            <button type="button" onClick={() => setNotice(null)}>知道了</button>
          </div>
        </div>
      ) : null}

      {copyFallback ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={() => setCopyFallback(null)}>
          <div className="vkpi-campaign-notice is-info" role="dialog" aria-label={`手动复制 ${copyFallback.label}`} onClick={(event) => event.stopPropagation()}>
            <h3>手动复制 {copyFallback.label}</h3>
            <p>当前浏览器阻止了自动写入剪贴板，下面文本已可选中复制。</p>
            <textarea
              className="mt-3 min-h-[220px] w-full rounded-lg border border-white/[0.08] bg-black/40 p-3 text-[11px] text-slate-200 outline-none"
              readOnly
              value={copyFallback.content}
              onFocus={(event) => event.currentTarget.select()}
            />
            <div className="vkpi-campaign-confirm-actions">
              <button type="button" onClick={() => setCopyFallback(null)}>关闭</button>
              <button
                className="is-primary"
                type="button"
                onClick={(event) => {
                  const textarea = event.currentTarget.closest('[role="dialog"]')?.querySelector('textarea');
                  textarea?.focus();
                  textarea?.select();
                }}
              >
                选中文本
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {confirmAction ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={confirmingAction ? undefined : () => setConfirmAction(null)}>
          <div className="vkpi-campaign-notice is-warning" role="dialog" aria-label={confirmAction.title} onClick={(event) => event.stopPropagation()}>
            <h3>{confirmAction.title}</h3>
            <p>{confirmAction.body}</p>
            <div className="vkpi-campaign-confirm-actions">
              <button type="button" onClick={() => setConfirmAction(null)} disabled={confirmingAction}>取消</button>
              <button className={`is-${confirmAction.confirmVariant || 'primary'}`} type="button" onClick={() => void runConfirmAction()} disabled={confirmingAction}>
                {confirmingAction ? '处理中' : confirmAction.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
