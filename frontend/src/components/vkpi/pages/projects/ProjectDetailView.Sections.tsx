// 纯重构:从 ProjectDetailView.tsx 抽出的纯展示区块(只吃 props,零 state/effect/hook)。
// JSX 逐字搬运,行为零变。所有 state/逻辑/handler 留主函数,经 prop 下传。

import { useCallback, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { VkpiKolOption, VkpiPlatform, VkpiProjectDetail, VkpiProjectRow, VkpiStaffMember } from '../../vkpiTypes';
import {
  generateLaunchPlan,
  type VkpiLaunchPlan,
} from '../../../../services/vkpi/launch-api';
import { FulfillmentObservationPanel } from './FulfillmentObservationPanel';
import {
  CampaignAnalyticsTab,
  CampaignContractsTab,
  CampaignFinanceTab,
  CampaignMaterialsTab,
  CampaignRetrospectiveTab,
  CampaignTimelineTab,
} from './ProjectDetailTabs';
import {
  AddKolModal,
  ContactKolModal,
  ContractUploadModal,
  GenerateContractModal,
  CostEntryModal,
  EditProjectModal,
  ShippingInfoModal,
  StageActionModal,
  UploadScreenshotModal,
  VideoUrlModal,
} from './ProjectDetailModals';
import {
  createMarketingMessage,
  enqueueKolOutreachDraft,
  generateProjectContract,
  getKolOutreachDraft,
  type VkpiContractTemplate,
} from '../../../../services/vkpi/projects-api';
import { ShareModal } from '../../shared/ShareModal';
import { ProjectParticipationTab } from './ProjectParticipationTab';
import type {
  VkpiProjectContractsResponse,
  VkpiProjectRetrospectiveResponse,
  VkpiProjectVideoAnalysisCacheResponse,
} from '../../../../services/vkpi/projects-api';
import type {
  ConfirmAction,
  ContractLine,
  DetailTab,
  ExpenseLine,
  NoticeState,
  ProjectStatsSummary,
  ScreenshotTarget,
  TrackingState,
} from '../../../../domains/projects';
import type { DetailActionModal, CopyFallbackState } from './ProjectDetailView.helpers';

interface HealthState {
  score: number;
  className: string;
  label: string;
}

export interface ProjectDetailTabContentProps {
  activeTab: DetailTab;
  apiToken?: string;
  project: VkpiProjectRow;
  detail?: VkpiProjectDetail | null;
  rows: VkpiProjectRow[];
  filteredRows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  health: HealthState;
  goaffByKol: Record<string, { clicks: number; orders: number; gmv: number }>;
  expandedRows: Set<string>;
  movingRowId: string;
  platformOptions: VkpiPlatform[];
  tableQuery: string;
  tableStage: string;
  tablePlatform: string;
  savingShopify: boolean;
  costRows: Array<Record<string, unknown>>;
  productUnitCosts: Record<string, number>;
  expenseLines: ExpenseLine[];
  contractLines: ContractLine[];
  contractsPayload: VkpiProjectContractsResponse | null;
  contractsLoading: boolean;
  contractsError: string;
  contractActionId: string;
  videoAnalysisCache: VkpiProjectVideoAnalysisCacheResponse | null;
  videoQaCache: VkpiProjectVideoAnalysisCacheResponse | null;
  videoAnalysisLoading: boolean;
  videoAnalysisError: string;
  videoQaError: string;
  retrospective: VkpiProjectRetrospectiveResponse | null;
  retroBusy: boolean;
  evidenceCountForRow: (row: VkpiProjectRow) => number;
  trackingForRow: (row: VkpiProjectRow) => TrackingState;
  shopifyLinkForRow: (row: VkpiProjectRow) => string;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onAddKol: () => void;
  onMoveRowStage: (row: VkpiProjectRow) => void;
  onOpenContactModal: (row: VkpiProjectRow) => void;
  onOpenScreenshotModal: (target: ScreenshotTarget) => void;
  onOpenStageActionModal: (row: VkpiProjectRow, action: 'stalled' | 'lost' | 'released' | 'cancelled') => void;
  onSaveShopifyLink: () => void;
  onSetShopifyLink: (value: string) => void;
  onSetTablePlatform: (value: string) => void;
  onSetTableQuery: (value: string) => void;
  onSetTableStage: Dispatch<SetStateAction<string>>;
  onToggleRow: (rowId: string) => void;
  setActionModal: Dispatch<SetStateAction<DetailActionModal | null>>;
  setNotice: Dispatch<SetStateAction<NoticeState | null>>;
  onCopy: (text: string, label: string) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onProjectUpdated?: () => Promise<void> | void;
  onGenerateRetrospective: () => void;
  uploadContractFile: (file: File, assignmentId?: string, kolPoolId?: string, relatedContractId?: number) => Promise<void>;
  saveContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  confirmContractArchive: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  openContractPdf: (contractId: number) => Promise<void>;
  retryContractExtraction: (contractId: number) => Promise<void>;
  deleteContractArchive: (contractId: number, fileName?: string) => void;
}

// N3 Launch 计划面板:仅 source_type='launch' 项目展示。自管 state(busy/plan/error),
// 点按钮 → POST /api/admin/vkpi/launch/{project_id}/plan → 渲染 KOL 候选 / 内容验证任务 / 观察窗口。
function LaunchPlanPanel({ projectId, apiToken }: { projectId: string; apiToken?: string }) {
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<VkpiLaunchPlan | null>(null);
  const [error, setError] = useState<string>('');

  const onGenerate = useCallback(async () => {
    if (!apiToken) {
      setError('缺少登录凭证,无法生成 Launch 计划。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await generateLaunchPlan(apiToken, projectId);
      setPlan(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成 Launch 计划失败。');
    } finally {
      setBusy(false);
    }
  }, [apiToken, projectId]);

  const candidates = plan?.kol_candidates;
  const tasks = plan?.content_validation_tasks ?? [];
  const windows = plan?.observation_windows ?? [];

  return (
    <section className="vkpi-launch-plan-panel" style={{ marginBottom: 16, padding: 16, border: '1px solid var(--vkpi-border, #e2e8f0)', borderRadius: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0 }}>Launch 计划</h3>
          <p style={{ margin: '4px 0 0', fontSize: 13, opacity: 0.75 }}>
            为新品 Launch 项目生成 KOL 候选、内容验证任务与观察窗口(只读,不写库)。
          </p>
        </div>
        <button className="is-primary" type="button" disabled={busy} onClick={onGenerate}>
          {busy ? '生成中…' : plan ? '重新生成 Launch 计划' : '生成 Launch 计划'}
        </button>
      </div>

      {error ? <p style={{ marginTop: 12, color: 'var(--vkpi-danger, #dc2626)', fontSize: 13 }}>{error}</p> : null}

      {plan ? (
        <div style={{ marginTop: 16, display: 'grid', gap: 16 }}>
          <div style={{ fontSize: 13, opacity: 0.75 }}>
            产品查询:<strong>{plan.product_query || '—'}</strong> · 生成时间 {plan.generated_at}
          </div>

          <div>
            <h4 style={{ margin: '0 0 8px' }}>
              KOL 候选({plan.summary.candidate_count})
            </h4>
            {candidates && !candidates.available ? (
              <p style={{ fontSize: 13, opacity: 0.7 }}>候选暂不可用:{candidates.reason || '前置条件未就绪'}</p>
            ) : candidates && candidates.items.length ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left', opacity: 0.7 }}>
                    <th style={{ padding: '4px 8px' }}>#</th>
                    <th style={{ padding: '4px 8px' }}>名称</th>
                    <th style={{ padding: '4px 8px' }}>平台</th>
                    <th style={{ padding: '4px 8px' }}>账号</th>
                    <th style={{ padding: '4px 8px' }}>国家</th>
                    <th style={{ padding: '4px 8px' }}>评分</th>
                    <th style={{ padding: '4px 8px' }}>需人工</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.items.map((item, idx) => (
                    <tr key={`${item.kol_pool_id ?? item.kol_entity_uid ?? idx}`} style={{ borderTop: '1px solid var(--vkpi-border, #e2e8f0)' }}>
                      <td style={{ padding: '4px 8px' }}>{item.rank ?? idx + 1}</td>
                      <td style={{ padding: '4px 8px' }}>{item.display_name || '—'}</td>
                      <td style={{ padding: '4px 8px' }}>{item.platform || '—'}</td>
                      <td style={{ padding: '4px 8px' }}>{item.handle || '—'}</td>
                      <td style={{ padding: '4px 8px' }}>{item.country || '—'}</td>
                      <td style={{ padding: '4px 8px' }}>{item.score ?? '—'}</td>
                      <td style={{ padding: '4px 8px' }}>{item.review_required ? '是' : '否'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p style={{ fontSize: 13, opacity: 0.7 }}>暂无候选。</p>
            )}
          </div>

          <div>
            <h4 style={{ margin: '0 0 8px' }}>内容验证任务({plan.summary.content_task_count})</h4>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {tasks.map((task) => (
                <li key={task.sequence} style={{ marginBottom: 4 }}>
                  {task.hypothesis}
                  {task.target_audience ? <span style={{ opacity: 0.7 }}> · 受众:{task.target_audience}</span> : null}
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h4 style={{ margin: '0 0 8px' }}>观察窗口({plan.summary.observation_window_count})</h4>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {windows.map((win, idx) => (
                <li key={`${win.market}-${idx}`} style={{ marginBottom: 4 }}>
                  {win.market} · {win.duration_days} 天
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function ProjectDetailTabContent(props: ProjectDetailTabContentProps) {
  const {
    activeTab,
    apiToken,
    project,
    detail,
    rows,
    filteredRows,
    stats,
    health,
    goaffByKol,
    expandedRows,
    movingRowId,
    platformOptions,
    tableQuery,
    tableStage,
    tablePlatform,
    savingShopify,
    costRows,
    productUnitCosts,
    expenseLines,
    contractLines,
    contractsPayload,
    contractsLoading,
    contractsError,
    contractActionId,
    videoAnalysisCache,
    videoQaCache,
    videoAnalysisLoading,
    videoAnalysisError,
    videoQaError,
    retrospective,
    retroBusy,
    evidenceCountForRow,
    trackingForRow,
    shopifyLinkForRow,
    onOpenKolProfile,
    onAddKol,
    onMoveRowStage,
    onOpenContactModal,
    onOpenScreenshotModal,
    onOpenStageActionModal,
    onSaveShopifyLink,
    onSetShopifyLink,
    onSetTablePlatform,
    onSetTableQuery,
    onSetTableStage,
    onToggleRow,
    setActionModal,
    setNotice,
    onCopy,
    onUpsertProjectTerms,
    onAddProjectShipment,
    onUploadEvidenceFile,
    onProjectUpdated,
    onGenerateRetrospective,
    uploadContractFile,
    saveContract,
    confirmContractArchive,
    openContractPdf,
    retryContractExtraction,
    deleteContractArchive,
  } = props;

  const isLaunchProject = String((detail?.project as Record<string, unknown> | undefined)?.source_type ?? '') === 'launch';

  return (
    <>
      {isLaunchProject ? <LaunchPlanPanel projectId={project.id} apiToken={apiToken} /> : null}
      {activeTab === '参与 KOL' ? (
        <ProjectParticipationTab
          apiToken={apiToken}
          goaffByKol={goaffByKol}
          expandedRows={expandedRows}
          evidenceCountForRow={evidenceCountForRow}
          filteredRows={filteredRows}
          movingRowId={movingRowId}
          onAddKol={onAddKol}
          onMoveRowStage={onMoveRowStage}
          onOpenContactModal={onOpenContactModal}
          onOpenKolProfile={onOpenKolProfile}
          onOpenScreenshotModal={onOpenScreenshotModal}
          onOpenStageActionModal={onOpenStageActionModal}
          onSaveShopifyLink={onSaveShopifyLink}
          onSetShopifyLink={onSetShopifyLink}
          onSetTablePlatform={onSetTablePlatform}
          onSetTableQuery={onSetTableQuery}
          onSetTableStage={onSetTableStage}
          onToggleRow={onToggleRow}
          platformOptions={platformOptions}
          savingShopify={savingShopify}
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
        />
      ) : activeTab === '物料' ? (
        <CampaignMaterialsTab
          apiToken={apiToken}
          project={project}
          rows={rows}
          stats={stats}
          costRows={costRows}
          productUnitCosts={productUnitCosts}
          onCopy={onCopy}
          onPendingAction={(label) => setNotice({ tone: 'info', title: '素材库功能开发中', body: `${label} 功能开发中，敬请期待。` })}
          projectId={project.id}
          onUpsertTerms={onUpsertProjectTerms ? async (payload) => { await onUpsertProjectTerms(project.id, payload); await onProjectUpdated?.(); } : undefined}
          onAddShipment={onAddProjectShipment ? async (payload) => { await onAddProjectShipment(project.id, payload); await onProjectUpdated?.(); } : undefined}
          onUploadEvidenceFile={onUploadEvidenceFile}
        />
      ) : activeTab === '费用' ? (
        <CampaignFinanceTab
          rows={rows}
          expenseLines={expenseLines}
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
        <>
          <CampaignRetrospectiveTab
            project={project}
            rows={rows}
            stats={stats}
            health={health}
            apiToken={apiToken}
            videoAnalysisCache={videoAnalysisCache}
            videoQaCache={videoQaCache}
            videoAnalysisLoading={videoAnalysisLoading}
            videoAnalysisError={videoAnalysisError}
            videoQaError={videoQaError}
            retrospective={retrospective?.retrospective || null}
            retrospectiveLastJob={retrospective?.last_job || null}
            retrospectiveGenerating={retroBusy || Boolean(retrospective?.active_job)}
            onGenerateRetrospective={onGenerateRetrospective}
            onCopy={onCopy}
            onPendingAction={(label) => setNotice({ tone: 'info', title: '暂存提醒', body: `${label} 已进入项目操作队列，后续同步后更新状态。` })}
          />
          <FulfillmentObservationPanel projectId={project.id} apiToken={apiToken} />
        </>
      ) : activeTab === '时间轴' ? (
        <CampaignTimelineTab rows={rows} events={detail?.events || []} apiToken={apiToken} projectId={project.id} />
      ) : (
        <div className="vkpi-campaign-placeholder">
          <h3>{activeTab}</h3>
          <p>建设中：这里会显示 {activeTab} 的明细、筛选和归档结果。</p>
        </div>
      )}
    </>
  );
}

export interface ProjectDetailModalsLayerProps {
  apiToken?: string;
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  staff: VkpiStaffMember[];
  actionModal: DetailActionModal | null;
  contactRow: VkpiProjectRow | null;
  addKolOpen: boolean;
  editOpen: boolean;
  shareOpen: boolean;
  generateOpen: boolean;
  generateBusy: boolean;
  editingProject: boolean;
  addingKols: boolean;
  loadingAvailableKols: boolean;
  availableKolError: string;
  availableScope: 'favorites' | 'all';
  availableKolOptions: VkpiKolOption[];
  kolOptions: VkpiKolOption[];
  contractTemplates: VkpiContractTemplate[];
  contractPartyA: string;
  notice: NoticeState | null;
  copyFallback: CopyFallbackState | null;
  confirmAction: ConfirmAction | null;
  confirmingAction: boolean;
  onLoadAvailableKols?: (project: VkpiProjectRow, scope?: string) => Promise<VkpiKolOption[]>;
  setActionModal: Dispatch<SetStateAction<DetailActionModal | null>>;
  setContactRow: Dispatch<SetStateAction<VkpiProjectRow | null>>;
  setAddKolOpen: Dispatch<SetStateAction<boolean>>;
  setEditOpen: Dispatch<SetStateAction<boolean>>;
  setShareOpen: Dispatch<SetStateAction<boolean>>;
  setGenerateOpen: Dispatch<SetStateAction<boolean>>;
  setGenerateBusy: Dispatch<SetStateAction<boolean>>;
  setNotice: Dispatch<SetStateAction<NoticeState | null>>;
  setCopyFallback: Dispatch<SetStateAction<CopyFallbackState | null>>;
  setConfirmAction: Dispatch<SetStateAction<ConfirmAction | null>>;
  submitActionStub: (kind: 'screenshot' | 'video' | 'contract', row: VkpiProjectRow, payload: Record<string, unknown>) => Promise<void>;
  submitShippingInfo: (row: VkpiProjectRow, payload: Record<string, unknown>) => Promise<void>;
  submitProjectCost: (row: VkpiProjectRow, payload: { costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> }) => Promise<void>;
  submitStageAction: (row: VkpiProjectRow, action: 'stalled' | 'lost' | 'released' | 'cancelled', reason: string) => Promise<void>;
  addSelectedKols: (selectedKols: VkpiKolOption[]) => Promise<void>;
  updateProjectProfile: (payload: { projectName?: string; productSku?: string; productName?: string; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  loadAvailableForScope: (scope: 'favorites' | 'all') => Promise<void>;
  loadContracts: () => Promise<void>;
  openContractPdf: (contractId: number) => Promise<void>;
  runConfirmAction: () => Promise<void>;
  onProjectUpdated?: () => Promise<void> | void;
}

export function ProjectDetailModalsLayer(props: ProjectDetailModalsLayerProps) {
  const {
    apiToken,
    project,
    rows,
    staff,
    actionModal,
    contactRow,
    addKolOpen,
    editOpen,
    shareOpen,
    generateOpen,
    generateBusy,
    editingProject,
    addingKols,
    loadingAvailableKols,
    availableKolError,
    availableScope,
    availableKolOptions,
    kolOptions,
    contractTemplates,
    contractPartyA,
    notice,
    copyFallback,
    confirmAction,
    confirmingAction,
    onLoadAvailableKols,
    setActionModal,
    setContactRow,
    setAddKolOpen,
    setEditOpen,
    setShareOpen,
    setGenerateOpen,
    setGenerateBusy,
    setNotice,
    setCopyFallback,
    setConfirmAction,
    submitActionStub,
    submitShippingInfo,
    submitProjectCost,
    submitStageAction,
    addSelectedKols,
    updateProjectProfile,
    loadAvailableForScope,
    loadContracts,
    openContractPdf,
    runConfirmAction,
    onProjectUpdated,
  } = props;

  return (
    <>
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

      {generateOpen && contractTemplates.length ? (
        <GenerateContractModal
          project={project}
          rows={rows}
          templates={contractTemplates}
          partyA={contractPartyA}
          busy={generateBusy}
          apiToken={apiToken}
          onClose={() => setGenerateOpen(false)}
          onSubmit={async ({ templateKey, fields, row }) => {
            if (!apiToken) throw new Error('缺少 API token。');
            setGenerateBusy(true);
            try {
              const resp = await generateProjectContract(apiToken, project.id, {
                template_key: templateKey,
                fields,
                assignment_id: row?.assignmentId || undefined,
                kol_pool_id: row?.kolPoolId || undefined,
              });
              setNotice({ tone: 'success', title: '草稿已生成并归档', body: `${String((resp as Record<string, unknown>).file_name || 'DOCX')} 已入「合同归档」${row ? `,关联 ${row.kolHandle || row.kolName}` : ''};签署后回归档上传签署版(双记录留痕)。` });
              await loadContracts();
              void onProjectUpdated?.();
              const contract = (resp as Record<string, unknown>).contract as Record<string, unknown> | undefined;
              return Number(contract?.id) || null;
            } finally {
              setGenerateBusy(false);
            }
          }}
          onDownload={(contractId) => void openContractPdf(contractId)}
        />
      ) : null}

      {contactRow ? (() => {
        // kolPoolId 非正整数(空/NaN/0)时不传草稿入口:Number('') 会变 0 入队必失败,modal 自行降级。
        const contactPoolId = Number(contactRow.kolPoolId);
        const canDraft = Number.isInteger(contactPoolId) && contactPoolId > 0;
        return (
          <ContactKolModal
            row={contactRow}
            onClose={() => setContactRow(null)}
            onLogOutreach={async ({ message, channel }) => {
              if (!apiToken) throw new Error('缺少 API token,不能记录沟通。');
              await createMarketingMessage(apiToken, {
                project_id: Number(project.id),
                direction: 'outbound',
                source: channel,
                body: message,
                receiver: contactRow.kolHandle || contactRow.kolName || '',
                metadata: { kol_pool_id: contactRow.kolPoolId, assignment_id: contactRow.assignmentId, stage: contactRow.stage },
              });
              setNotice({ tone: 'success', title: '沟通已留档', body: `外联消息已记录到「${contactRow.kolHandle || contactRow.kolName}」的沟通流。` });
            }}
            onRequestDraft={canDraft ? async () => {
              if (!apiToken) throw new Error('缺少 API token。');
              return enqueueKolOutreachDraft(apiToken, contactPoolId, Number(project.id));
            } : undefined}
            onFetchDraft={canDraft ? async () => {
              if (!apiToken) return { state: 'missing' };
              return getKolOutreachDraft(apiToken, contactPoolId);
            } : undefined}
          />
        );
      })() : null}

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
          scope={availableScope}
          onSwitchScope={onLoadAvailableKols ? (next) => void loadAvailableForScope(next) : undefined}
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

      {shareOpen ? (
        <ShareModal
          kind="project"
          targetId={String(project.id)}
          targetName={project.campaign || '项目'}
          staff={staff}
          apiToken={apiToken || ''}
          onClose={() => setShareOpen(false)}
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
    </>
  );
}
