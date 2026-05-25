import { useEffect, useMemo, useState } from 'react';
import type { VkpiKolOption, VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { primaryStageFlow, stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';
import { writeProjectActionFocus } from '../../intelligence/intelligenceProjectActionFocus';
import { CampaignAnalyticsTab, CampaignContractsTab, CampaignFinanceTab, CampaignMaterialsTab, CampaignRetrospectiveTab } from './ProjectDetailTabs';
import { AddKolModal, EditProjectModal, UploadScreenshotModal } from './ProjectDetailModals';
import { ProjectParticipationTab } from './ProjectParticipationTab';
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
  healthForRows,
  parseDays,
  stageCounts,
  stageIndex,
  statusClass,
  statusForProject,
  uniqueRelatedProjects,
  type ConfirmAction,
  type DetailTab,
  type NoticeState,
  type ProjectDetailViewProps,
  type ScreenshotTarget,
  type TaskItem,
  type TrackingState,
} from '../../../../domains/projects';

export function ProjectDetailView({
  project,
  projects,
  viewMode,
  onBack,
  onOpenKolProfile,
  onOpenStaffProfile,
  onUpdateProject,
  onMoveProjectStage,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onUploadEvidenceFile,
  kolOptions = [],
  onAddKolsToCampaign,
  onProjectUpdated,
  onDeleteProject,
}: ProjectDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('参与 KOL');
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set([project.id]));
  const [trackingById, setTrackingById] = useState<Record<string, TrackingState>>({});
  const [stageOverrides, setStageOverrides] = useState<Record<string, VkpiProjectStage>>({});
  const [evidenceOverrides, setEvidenceOverrides] = useState<Record<string, number>>({});
  const [shopifyLinks, setShopifyLinks] = useState<Record<string, string>>({});
  const [savingShopifyRowId, setSavingShopifyRowId] = useState('');
  const [savingShipmentRowId, setSavingShipmentRowId] = useState('');
  const [tableQuery, setTableQuery] = useState('');
  const [tableStage, setTableStage] = useState('全部阶段');
  const [tablePlatform, setTablePlatform] = useState('全部平台');
  const [addKolOpen, setAddKolOpen] = useState(false);
  const [addingKols, setAddingKols] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(false);
  const [screenshotTarget, setScreenshotTarget] = useState<ScreenshotTarget | null>(null);
  const [movingRowId, setMovingRowId] = useState('');
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [confirmingAction, setConfirmingAction] = useState(false);
  const [taskReminderOpen, setTaskReminderOpen] = useState(false);
  const [dismissedReminderKey, setDismissedReminderKey] = useState('');

  useEffect(() => {
    setStageOverrides({});
    setMovingRowId('');
  }, [project.id, project.stage]);

  const baseRows = useMemo(() => uniqueRelatedProjects(project, projects), [project, projects]);
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
  const stats = useMemo(() => buildProjectStatsSummary(rows), [rows]);
  const analytics = useMemo(() => buildAnalytics(rows), [rows]);
  const expenseLines = useMemo(() => buildExpenseLines(rows), [rows]);
  const stageCosts = useMemo(() => buildStageCostSummary(expenseLines), [expenseLines]);
  const contractLines = useMemo(() => buildContractLines(rows), [rows]);
  const health = useMemo(() => healthForRows(rows), [rows]);
  const bottleneck = useMemo(() => bottleneckForRows(rows), [rows]);
  const counts = useMemo(() => stageCounts(rows), [rows]);
  const campaignStatus = statusForProject(project);
  const ownerFallback = { name: project.ownerName, avatarUrl: project.ownerAvatar };
  const platformOptions = useMemo(() => Array.from(new Set(rows.map((row) => row.platform))).filter(Boolean), [rows]);
  const filteredRows = useMemo(() => {
    const query = tableQuery.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesQuery = !query || [row.kolHandle, row.kolName, row.platform, row.campaign].some((value) => String(value || '').toLowerCase().includes(query));
      const matchesStage = tableStage === '全部阶段' || row.stage === tableStage;
      const matchesPlatform = tablePlatform === '全部平台' || row.platform === tablePlatform;
      return matchesQuery && matchesStage && matchesPlatform;
    });
  }, [rows, tablePlatform, tableQuery, tableStage]);
  const taskItems = useMemo<TaskItem[]>(() => buildProjectTaskItems(rows, trackingById), [rows, trackingById]);
  const reminderTasks = useMemo(
    () => taskItems.filter((item) => item.className === 'is-red' || item.className === 'is-yellow'),
    [taskItems],
  );
  const reminderKey = useMemo(
    () => [project.id, ...reminderTasks.map((item) => `${item.level}:${item.title}:${item.rowId || item.tab || ''}`)].join('|'),
    [project.id, reminderTasks],
  );

  useEffect(() => {
    if (!reminderTasks.length) {
      setTaskReminderOpen(false);
      return;
    }
    if (dismissedReminderKey !== reminderKey) setTaskReminderOpen(true);
  }, [dismissedReminderKey, reminderKey, reminderTasks.length]);

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
  const shopifyLinkForRow = (row: VkpiProjectRow) => shopifyLinks[row.id] ?? row.shopifyLink ?? '';

  const moveRowStage = (row: VkpiProjectRow) => {
    if (!onMoveProjectStage) {
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
          await onMoveProjectStage(row.id, nextStage, `详情页手动推进：${stageLabels[previousStage]} → ${stageLabels[nextStage]}`);
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

  const openScreenshotModal = (target: ScreenshotTarget) => {
    if (!onUploadEvidenceFile) {
      setNotice({ tone: 'warning', title: '无法上传截图', body: '当前没有传入证据上传接口，请先确认父层 onUploadEvidenceFile。' });
      return;
    }
    setScreenshotTarget(target);
  };

  const completeScreenshotUpload = async (file: File, note: string) => {
    if (!screenshotTarget || !onUploadEvidenceFile) return;
    await onUploadEvidenceFile(file, {
      entityType: 'project_stage',
      entityId: screenshotTarget.row.id,
      purpose: `stage_${screenshotTarget.from}_${screenshotTarget.to}_screenshot${note.trim() ? `:${note.trim()}` : ''}`,
    });
    setEvidenceOverrides((current) => ({
      ...current,
      [screenshotTarget.row.id]: evidenceCountForRow(screenshotTarget.row) + 1,
    }));
    setScreenshotTarget(null);
    setNotice({
      tone: 'success',
      title: '截图已上传',
      body: `${screenshotTarget.row.kolHandle || screenshotTarget.row.kolName} 的 ${screenshotTarget.from}→${screenshotTarget.to} 阶段截图已保存。`,
    });
    await onProjectUpdated?.();
  };

  const saveShopifyLink = async (row: VkpiProjectRow) => {
    if (!onUpsertProjectTerms) {
      setNotice({ tone: 'warning', title: '无法保存链接', body: '当前没有传入项目条款保存接口。' });
      return;
    }
    const link = shopifyLinkForRow(row).trim();
    if (!link) {
      setNotice({ tone: 'warning', title: '需要链接', body: '请输入 Shopify 商品链接或带 ref 的归因链接。' });
      return;
    }
    if (!/^https?:\/\//i.test(link)) {
      setNotice({ tone: 'warning', title: '链接格式不对', body: '链接需要以 http:// 或 https:// 开头。' });
      return;
    }
    setSavingShopifyRowId(row.id);
    try {
      await onUpsertProjectTerms(row.id, {
        shopify_url: link,
        shopify_link: link,
        note: `Shopify 归因链接：${link}`,
      });
      setNotice({ tone: 'success', title: '链接已保存', body: `${row.kolHandle || row.kolName} 的 Shopify 归因链接已保存。` });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '保存失败', body: error instanceof Error ? error.message : 'Shopify 链接保存失败。' });
    } finally {
      setSavingShopifyRowId('');
    }
  };

  const saveShipment = async (row: VkpiProjectRow) => {
    if (!onAddProjectShipment) {
      setNotice({ tone: 'warning', title: '无法保存物流', body: '当前没有传入物流保存接口。' });
      return;
    }
    const tracking = trackingForRow(row);
    const carrier = tracking.courier.trim();
    const trackingNumber = tracking.no.trim();
    if (!trackingNumber) {
      setNotice({ tone: 'warning', title: '需要快递单号', body: '先输入 tracking no.，再保存物流。' });
      return;
    }
    setSavingShipmentRowId(row.id);
    try {
      await onAddProjectShipment(row.id, {
        carrier: carrier || 'SF Express',
        tracking_number: trackingNumber,
        shipping_status: 'shipped',
        note: '项目详情页保存物流单号，等待后续物流追踪刷新。',
      });
      setTrackingById((current) => ({
        ...current,
        [row.id]: {
          ...tracking,
          courier: carrier || 'SF Express',
          no: trackingNumber,
          status: '已保存，等待物流追踪',
          last: '刚刚',
          delivered: false,
        },
      }));
      setNotice({
        tone: 'success',
        title: '物流已保存',
        body: `${row.kolHandle || row.kolName} 的 ${carrier || 'SF Express'} · ${trackingNumber} 已写入项目物流。`,
      });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '物流保存失败', body: error instanceof Error ? error.message : '物流保存失败。' });
    } finally {
      setSavingShipmentRowId('');
    }
  };

  const updateTracking = (row: VkpiProjectRow, key: 'courier' | 'no', value: string) => {
    setTrackingById((current) => ({
      ...current,
      [row.id]: {
        ...(current[row.id] || defaultTracking(row)),
        [key]: value,
      },
    }));
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

  const sendTaskToWorkbench = (item: TaskItem) => {
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
      tone: 'success',
      title: '已送到员工工作台',
      body: viewMode === 'employee'
        ? '已生成今日行动卡，并切换到员工工作台。'
        : '已生成员工工作台动作；切换员工视角后可见。',
    });
    if (typeof window !== 'undefined') {
      window.location.hash = viewMode === 'employee' ? 'command' : 'dashboardPremium';
    }
  };

  const dismissTaskReminder = () => {
    setDismissedReminderKey(reminderKey);
    setTaskReminderOpen(false);
  };

  const jumpFromTaskReminder = (item: TaskItem) => {
    dismissTaskReminder();
    jumpToTask(item.rowId, item.tab);
  };

  const copyMaterialText = async (text: string, label: string) => {
    const content = text.trim();
    if (!content) {
      setNotice({ tone: 'warning', title: '没有可复制内容', body: `${label} 还没有可用内容。` });
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setNotice({ tone: 'success', title: '已复制', body: `${label} 已复制到剪贴板。` });
    } catch (error) {
      setNotice({ tone: 'warning', title: '复制失败', body: error instanceof Error ? error.message : '浏览器没有允许剪贴板写入。' });
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
    <section className="vkpi-campaign-detail" aria-label="项目详情">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>

      <div className="vkpi-campaign-detail-hero">
        <div>
          <div className="vkpi-campaign-detail-top">
            <div>
              <span className="vkpi-campaign-eyebrow">VILTROX MARKETING · PROJECT DETAIL</span>
              <h2>{project.campaign || '未命名推广'}</h2>
            </div>
            <span className={`vkpi-campaign-status ${statusClass(campaignStatus)}`}>{campaignStatus}</span>
          </div>
          <div className="vkpi-campaign-meta">
            <span>产品 {project.campaign || '-'}</span>
            <span>最近更新 {shortDateTime(project.updatedAt || project.latestMessageAt)}</span>
            <span>预算 {formatMoney(stats.cost)} · 销售 {formatMoney(stats.gmv)}</span>
            <button
              type="button"
              disabled={!project.ownerId || !onOpenStaffProfile}
              onClick={() => project.ownerId && onOpenStaffProfile?.(project.ownerId, ownerFallback)}
            >
              负责人 {project.ownerName || '-'}
            </button>
            <span>{viewMode === 'manager' ? '上市推广' : '员工跟进'}</span>
          </div>
          <div className="vkpi-campaign-hero-actions">
            <button type="button" onClick={() => setEditOpen(true)} disabled={!onUpdateProject}>编辑</button>
            <button className="is-danger" type="button" onClick={cancelProject}>取消推广</button>
            {onDeleteProject ? <button className="is-danger" type="button" onClick={deleteProject}>删除</button> : null}
            <button className="is-primary" type="button" onClick={() => setAddKolOpen(true)}>+ 添加 KOL</button>
          </div>
        </div>
        <div className="vkpi-campaign-score-card">
          <div className="vkpi-campaign-score-row">
            <div>
              <span>健康度评分</span>
              <strong className={health.className}>{health.score}</strong>
            </div>
            <em className={health.className}>{health.label}</em>
          </div>
          <div className="vkpi-campaign-score-tooltip">
            <div>• 阶段停留越久，健康度越低</div>
            <div>• 已发布和已统计会提高评分</div>
            <div>• 取消 / 停滞项目会降低评分</div>
          </div>
          <div className="vkpi-campaign-bottleneck"><b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
        </div>
      </div>

      <div className="vkpi-campaign-kpis" aria-label="项目 KPI">
        <div><span>参与 KOL</span><strong>{rows.length}</strong><em>当前项目行</em></div>
        <div><span>总曝光</span><strong>{formatNumber(stats.views)}</strong><em>自动汇总</em></div>
        <div><span>已发布内容</span><strong>{stats.published}</strong><em>发布率 {stats.publishRate}%</em></div>
        <div><span>短链点击</span><strong>{formatNumber(stats.clicks)}</strong><em>{formatNumber(stats.orders)} 单</em></div>
        <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong><em>现有 GMV</em></div>
        <div><span>ROI</span><strong className="is-green">{formatRatio(stats.roi)}</strong><em>成本 {formatMoney(stats.cost)}</em></div>
      </div>

      <div className="vkpi-campaign-panel">
        <div className="vkpi-campaign-panel-head">
          <h3>KOL 进度漏斗</h3>
          <span className="vkpi-campaign-pulse">每日刷新</span>
        </div>
        <div className="vkpi-campaign-funnel">
          {primaryStageFlow.map((stage, index) => {
            const stageNumber = index + 1;
            const count = counts.get(stageNumber) || 0;
            const nextCount = counts.get(stageNumber + 1) || 0;
            const rate = count && stageNumber < 9 ? `${Math.round(Math.min(nextCount / count, 1) * 100)}%` : '—';
            const average = rows
              .filter((row) => stageIndex(row.stage) === index)
              .map((row) => parseDays(row.stageDurationLabel))
              .filter(Boolean);
            const avgDays = average.length ? `${Math.round(average.reduce((sum, day) => sum + day, 0) / average.length)} 天` : '-';
            return (
              <div className={`vkpi-campaign-stage ${stageNumber === bottleneck.from ? 'is-now' : ''}`} key={stage}>
                <span>{stageNumber}. {stageLabels[stage]}</span>
                <strong>{count}</strong>
                <em>平均 {avgDays}</em>
                <small>→ {rate}</small>
              </div>
            );
          })}
        </div>
        <div className="vkpi-campaign-alert">当前瓶颈：<b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
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
          onAddKol={() => setAddKolOpen(true)}
          onMoveRowStage={moveRowStage}
          onOpenKolProfile={onOpenKolProfile}
          onOpenScreenshotModal={openScreenshotModal}
          onSaveShopifyLink={saveShopifyLink}
          onSaveShipment={saveShipment}
          onSetShopifyLink={(rowId, value) => setShopifyLinks((current) => ({ ...current, [rowId]: value }))}
          onSetTablePlatform={setTablePlatform}
          onSetTableQuery={setTableQuery}
          onSetTableStage={setTableStage}
          onToggleRow={toggleRow}
          onUpdateTracking={updateTracking}
          platformOptions={platformOptions}
          savingShipmentRowId={savingShipmentRowId}
          savingShopifyRowId={savingShopifyRowId}
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
          onCopy={copyMaterialText}
        />
      ) : activeTab === '费用' ? (
        <CampaignFinanceTab
          rows={rows}
          stats={stats}
          expenseLines={expenseLines}
          stageCosts={stageCosts}
        />
      ) : activeTab === '合同归档' ? (
        <CampaignContractsTab
          rows={rows}
          contractLines={contractLines}
        />
      ) : activeTab === '复盘' ? (
        <CampaignRetrospectiveTab
          project={project}
          rows={rows}
          stats={stats}
          analytics={analytics}
          health={health}
          bottleneck={bottleneck}
          onCopy={copyMaterialText}
        />
      ) : (
        <div className="vkpi-campaign-placeholder">
          <h3>{activeTab}</h3>
          <p>建设中：这里会显示 {activeTab} 的明细、筛选和归档结果。</p>
        </div>
      )}

      <div className="vkpi-campaign-task-dock">
        <div className="vkpi-campaign-task-panel">
          <h3>今日该做什么</h3>
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
              onClick={() => sendTaskToWorkbench(taskItems[0])}
            >
              送首要任务到员工工作台
            </button>
          ) : null}
        </div>
      </div>

      {screenshotTarget ? (
        <UploadScreenshotModal
          target={screenshotTarget}
          onClose={() => setScreenshotTarget(null)}
          onSubmit={completeScreenshotUpload}
        />
      ) : null}

      {addKolOpen ? (
        <AddKolModal
          project={project}
          rows={rows}
          kolOptions={kolOptions}
          busy={addingKols}
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

      {taskReminderOpen && reminderTasks.length ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={dismissTaskReminder}>
          <div className="vkpi-campaign-task-modal" role="dialog" aria-label="今日项目提醒" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <h3>今日项目提醒</h3>
                <p>根据当前项目行检测到需要处理的物流或阶段推进事项。</p>
              </div>
              <button type="button" onClick={dismissTaskReminder}>稍后</button>
            </header>
            <div className="vkpi-campaign-task-modal-list">
              {reminderTasks.map((item) => (
                <button
                  key={`${item.level}-${item.title}-${item.rowId || item.tab || ''}`}
                  type="button"
                  onClick={() => jumpFromTaskReminder(item)}
                >
                  <span className={item.className}>{item.level}</span>
                  <b>{item.title}</b>
                  <small>{item.subtitle}</small>
                </button>
              ))}
            </div>
          </div>
        </div>
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
