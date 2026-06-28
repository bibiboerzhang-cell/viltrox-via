// 纯重构:从 ProjectDetailView.tsx 抽出的纯函数/类型(零 state/effect/hook)。
// 行为零变:函数体逐字搬运。

import type { VkpiProjectRow } from '../../vkpiTypes';
import type { ConfirmAction, NoticeState, ScreenshotTarget } from '../../../../domains/projects';
import { formatNumber } from '../../../../domains/projects';
import { stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage } from '../../shared/vkpiDataUtils';
import {
  confirmProjectContract,
  deleteProjectContract,
  downloadProjectContract,
  extractProjectContract,
  patchProjectContract,
  uploadProjectContract,
} from '../../../../services/vkpi/projects-api';

export function healthFromBackend(scoreValue: number | undefined) {
  const score = Number.isFinite(scoreValue) ? Math.max(0, Math.min(100, Math.round(scoreValue || 0))) : 0;
  if (score >= 85) return { score, className: 'is-good', label: '健康' };
  if (score >= 70) return { score, className: 'is-mid', label: '关注' };
  return { score, className: 'is-bad', label: '风险' };
}

export type DetailActionModal =
  | { kind: 'screenshot'; target: ScreenshotTarget }
  | { kind: 'shipping'; row: VkpiProjectRow }
  | { kind: 'cost'; row: VkpiProjectRow; costType?: 'cash_fee' | 'shipping' | 'product' }
  | { kind: 'video'; row: VkpiProjectRow }
  | { kind: 'contract'; row: VkpiProjectRow }
  | { kind: 'stage-action'; row: VkpiProjectRow; action: 'stalled' | 'lost' | 'released' | 'cancelled' };

export interface CopyFallbackState {
  label: string;
  content: string;
}

export function kolRef(row: VkpiProjectRow) {
  return row.assignmentId || row.kolPoolId || row.kolId || row.id.replace(/^assignment:/, '');
}

export async function writeClipboardText(content: string) {
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
}

interface SubmitActionStubDeps {
  apiToken?: string;
  projectId: string;
  onSubmitProjectKolActionStub?: (projectId: string, kolRef: string, actionKind: 'screenshot' | 'video' | 'contract', payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onAdvanceProjectKol?: (projectId: string, kolRef: string, payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onProjectUpdated?: () => void | Promise<void>;
  loadContracts: () => Promise<void>;
  setNotice: (value: NoticeState | null) => void;
  setActionModal: (value: null) => void;
  setEvidenceOverrides: (updater: (current: Record<string, number>) => Record<string, number>) => void;
  evidenceCountForRow: (row: VkpiProjectRow) => number;
}

// 纯重构:submitActionStub 整体搬出。函数体逐字不变;闭包依赖改由 deps 注入。
export function createSubmitActionStub(deps: SubmitActionStubDeps) {
  const {
    apiToken,
    projectId,
    onSubmitProjectKolActionStub,
    onUploadEvidenceFile,
    onAdvanceProjectKol,
    onProjectUpdated,
    loadContracts,
    setNotice,
    setActionModal,
    setEvidenceOverrides,
    evidenceCountForRow,
  } = deps;
  const project = { id: projectId };

  return async (kind: 'screenshot' | 'video' | 'contract', row: VkpiProjectRow, payload: Record<string, unknown>) => {
    // 合同就地归档(2026-06-12 裁令:上传后自动下一步,不切页;归档 tab 只作查看/补档)。
    if (kind === 'contract') {
      const file = payload.file as File | undefined;
      if (!apiToken || !file) {
        setNotice({ tone: 'warning', title: '无法上传', body: !apiToken ? '缺少 API token。' : '请选择合同文件。' });
        return;
      }
      const resp = await uploadProjectContract(apiToken, project.id, file, { assignmentId: row.assignmentId, kolPoolId: row.kolPoolId });
      setActionModal(null);
      // 全盘扫描 P0(V2):手填金额/期限/交付项随档落库(DOCX 无自动提取时这是唯一来源)
      const contractRecord = (resp as Record<string, unknown>).contract as Record<string, unknown> | undefined;
      const feeUsd = Number(payload.fee_usd) || 0;
      const duration = String(payload.duration || '').trim();
      const deliverables = String(payload.deliverables || '').trim();
      if (contractRecord?.id && (feeUsd > 0 || duration || deliverables)) {
        try {
          await patchProjectContract(apiToken, project.id, Number(contractRecord.id), {
            ...(feeUsd > 0 ? { fee_amount: feeUsd, fee_currency: 'USD' } : {}),
            ...(duration ? { contract_duration: duration } : {}),
            ...(deliverables ? { deliverables: [deliverables] } : {}),
          });
        } catch {
          setNotice({ tone: 'warning', title: '字段回填失败', body: '合同文件已归档,但手填的金额/期限未写入——请到归档 tab 手动补填。' });
        }
      }
      let advanced = '';
      const nextStage = nextProjectStage(row.stage);
      if (nextStage && row.assignmentId && onAdvanceProjectKol) {
        try {
          await onAdvanceProjectKol(project.id, kolRef(row), {
            to_stage: nextStage,
            note: `合同归档自动推进：${stageLabels[row.stage]} → ${stageLabels[nextStage]}`,
          });
          advanced = `,阶段已自动推进到「${stageLabels[nextStage]}」`;
        } catch {
          advanced = ',阶段自动推进失败——可手动点「推进」';
        }
      }
      // 诚实 toast(全盘扫描 P1):仅 PDF 走 Claude 提取;DOC/DOCX 归档不自动提取
      const isPdf = /\.pdf$/i.test(file.name);
      setNotice({ tone: 'success', title: '合同已归档', body: `${isPdf ? '提取已入队(泳道「合同提取」)' : 'DOCX 已归档(不走自动提取,金额/期限以手填为准)'}${advanced}。归档 tab 可查看。` });
      // 全盘扫描 P0(A1):刷新合同列表,提取轮询才能接管新合同
      await loadContracts();
      void onProjectUpdated?.();
      return;
    }
    if (!onSubmitProjectKolActionStub) {
      setNotice({ tone: 'info', title: '暂存提醒', body: '当前环境缺少写入接口或 API token，本次操作不会修改项目数据。' });
      return;
    }
    if (kind === 'screenshot') {
      // 截图真存证(2026-06-12):文件先上传 evidence 存储,再随 stub 落沟通/证据流(vkpi_messages)。
      const file = payload.file as File | undefined;
      let fileUrl = '';
      if (file && onUploadEvidenceFile) {
        const uploaded = await onUploadEvidenceFile(file, { entityType: 'project_kol_stage', entityId: String(row.assignmentId || row.id), purpose: `stage_${String(payload.stage || '')}` });
        fileUrl = String(uploaded.file_url || '');
      }
      const { file: _omit, ...rest } = payload;
      const result = await onSubmitProjectKolActionStub(project.id, kolRef(row), kind, { ...rest, file_url: fileUrl });
      setActionModal(null);
      if (String((result as Record<string, unknown>).status || '') === 'stored') {
        setNotice({ tone: 'success', title: '截图已存证', body: '文件已入证据存储并记入该 KOL 的沟通/证据流(时间轴可查)。' });
      } else {
        setNotice({ tone: 'info', title: '已记录', body: fileUrl ? '文件已上传;记录状态见审计日志。' : '未选择文件,仅记录了备注。' });
      }
      return;
    }
    const result = await onSubmitProjectKolActionStub(project.id, kolRef(row), kind, payload);
    if (kind === 'video') {
      const metrics = (result.metrics || {}) as Record<string, unknown>;
      const title = typeof metrics.title === 'string' && metrics.title ? metrics.title : '视频';
      const views = typeof metrics.view_count === 'number' ? formatNumber(metrics.view_count) : '—';
      setEvidenceOverrides((current) => ({ ...current, [row.id]: evidenceCountForRow(row) + 1 }));
      setActionModal(null);
      setNotice({ tone: 'success', title: '视频已写入 evidence', body: `${title} · 播放 ${views}` });
      await onProjectUpdated?.();
      // 详情已重读真值,本地乐观 +1 退场——否则真值落后/超前时会双计或卡住旧数。
      setEvidenceOverrides((current) => {
        if (!(row.id in current)) return current;
        const next = { ...current };
        delete next[row.id];
        return next;
      });
    }
  };
}

interface ContractActionsDeps {
  apiToken?: string;
  projectId: string;
  loadContracts: () => Promise<void>;
  setContractActionId: (value: string) => void;
  setNotice: (value: NoticeState | null) => void;
  setConfirmAction: (value: ConfirmAction | null) => void;
}

// 纯重构:合同动作 handler 群整体搬出。函数体逐字不变;闭包依赖改由 deps 注入。
export function createContractActions(deps: ContractActionsDeps) {
  const { apiToken, projectId: projectIdRaw, loadContracts, setContractActionId, setNotice, setConfirmAction } = deps;
  const project = { id: projectIdRaw };

  const uploadContractFile = async (file: File, assignmentId?: string, kolPoolId?: string, relatedContractId?: number) => {
    if (!apiToken) {
      setNotice({ tone: 'warning', title: '无法上传合同', body: '当前缺少 API token。' });
      return;
    }
    const isPdf = /\.pdf$/i.test(file.name);
    setContractActionId('upload');
    try {
      await uploadProjectContract(apiToken, project.id, file, { assignmentId, kolPoolId, relatedContractId });
      await loadContracts();
      setNotice({
        tone: 'success',
        title: relatedContractId ? '签署版已归档' : '合同已归档',
        body: `${relatedContractId ? `已关联草稿 #${relatedContractId}(双记录留痕)。` : ''}${isPdf ? '条款提取已入队，完成后此处自动回填；进度见左侧任务泳道。' : '文件已存档；DOC/DOCX 暂不自动提取。'}`,
      });
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

  return {
    uploadContractFile,
    saveContract,
    confirmContractArchive,
    openContractPdf,
    deleteContractArchive,
    retryContractExtraction,
  };
}
