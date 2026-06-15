import { useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, FileText, Sparkles, Upload } from 'lucide-react';
import { stageLabels } from '../../../shared/vkpiConstants';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { VkpiProjectContract, VkpiProjectContractsResponse } from '../../../../../services/vkpi/projects-api';
import { stageIndex, type ContractLine } from '../../../../../domains/projects';

function stringValue(value: unknown) {
  return value == null ? '' : String(value);
}

function arrayValue(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [value];
    } catch {
      return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
    }
  }
  return [];
}

function editableJson(value: unknown) {
  const list = arrayValue(value);
  if (!list.length) return '';
  if (list.some((item) => item && typeof item === 'object')) return JSON.stringify(list, null, 2);
  return list.map((item) => String(item)).join('\n');
}

function parseEditableList(value: string): unknown[] {
  const text = value.trim();
  if (!text) return [];
  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      // Fall through to manual line parsing.
    }
  }
  return text.split(/\n|,|，/).map((item) => item.trim()).filter(Boolean);
}

function deliverableLabel(record: Record<string, unknown>): string {
  // 提取出的 deliverable 对象键是 content_type/platform/quantity/deadline/notes(合同提取 schema)。
  const head = stringValue(
    record.description || record.item || record.title || record.name || record.content_type || record.platform,
  );
  const qty = record.quantity != null && stringValue(record.quantity) ? `×${stringValue(record.quantity)}` : '';
  const platform = head !== stringValue(record.platform) ? stringValue(record.platform) : '';
  return [head, platform, qty].filter(Boolean).join(' · ') || stringValue(record.notes) || '交付项';
}

function compactList(value: unknown, fallback = '待确认') {
  const list = arrayValue(value);
  if (!list.length) return fallback;
  return list.map((item) => {
    if (item && typeof item === 'object') {
      return deliverableLabel(item as Record<string, unknown>);
    }
    return stringValue(item);
  }).filter(Boolean).slice(0, 3).join(' / ') || fallback;
}

// 把 deliverables 对象数组整理成人类可读的结构(不再把 JSON 代码塞进输入框)。
function summarizeDeliverables(value: unknown): { line: string; notes: string }[] {
  return arrayValue(value).map((item) => {
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      const parts: string[] = [];
      const type = stringValue(record.content_type || record.type || record.title || record.name);
      const platform = stringValue(record.platform);
      const qty = record.quantity != null && stringValue(record.quantity) ? `×${stringValue(record.quantity)}` : '';
      const deadline = stringValue(record.deadline);
      if (type) parts.push(type);
      if (platform) parts.push(platform);
      if (qty) parts.push(qty);
      if (deadline) parts.push(`截止 ${deadline}`);
      return { line: parts.join(' · ') || '交付项', notes: stringValue(record.notes || record.description) };
    }
    return { line: stringValue(item), notes: '' };
  }).filter((d) => d.line || d.notes);
}

function moneyLabel(amount: unknown, currency: unknown) {
  const numeric = Number(amount);
  if (!Number.isFinite(numeric) || numeric <= 0) return '金额待确认';
  return `${String(currency || 'USD').toUpperCase()} ${numeric.toLocaleString()}`;
}

function contractLinkedRow(contract: VkpiProjectContract, rows: VkpiProjectRow[]) {
  const assignmentId = String(contract.assignment_id || '').trim();
  const kolPoolId = String(contract.kol_pool_id || '').trim();
  return rows.find((row) => assignmentId && String(row.assignmentId || '') === assignmentId)
    || rows.find((row) => kolPoolId && String(row.kolPoolId || '') === kolPoolId);
}

function contractStatusMeta(contract: VkpiProjectContract) {
  const status = String(contract.status || '').toLowerCase();
  const extraction = String(contract.extraction_status || '').toLowerCase();
  if (status === 'confirmed') return { label: '已确认', className: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20' };
  if (extraction === 'failed' || status === 'failed') return { label: '提取失败', className: 'bg-red-500/15 text-red-300 border-red-500/20' };
  if (extraction === 'processing') return { label: '智能提取中', className: 'bg-purple-500/15 text-purple-300 border-purple-500/20' };
  if (extraction === 'skipped') return { label: '已归档 · 未自动提取', className: 'bg-slate-500/15 text-slate-300 border-slate-500/20' };
  return { label: '待人工确认', className: 'bg-amber-500/15 text-amber-300 border-amber-500/20' };
}

function confidenceValue(contract: VkpiProjectContract, key: string) {
  const value = Number(contract.field_confidence_json?.[key]);
  return Number.isFinite(value) ? value : null;
}

function ConfidenceBadge({ contract, field }: { contract: VkpiProjectContract; field: string }) {
  const value = confidenceValue(contract, field);
  if (value == null) return <span className="text-[9px] text-slate-600">conf —</span>;
  const low = value < 0.7;
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${low ? 'border-amber-400/30 bg-amber-400/10 text-amber-300' : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'}`}>
      conf {Math.round(value * 100)}%
    </span>
  );
}

interface ContractDraft {
  fee_amount: string;
  fee_currency: string;
  contract_duration: string;
  start_date: string;
  end_date: string;
  platforms: string;
  deliverable_count: string;
  deliverables: string;
  must_include: string;
  usage_rights: string;
  exclusivity: string;
  buyout_rights: string;
  breach_terms: string;
  payment_terms: string;
  cancellation_terms: string;
  revision_terms: string;
}

function initialContractDraft(contract: VkpiProjectContract): ContractDraft {
  return {
    fee_amount: stringValue(contract.fee_amount),
    fee_currency: stringValue(contract.fee_currency || 'USD'),
    contract_duration: stringValue(contract.contract_duration),
    start_date: stringValue(contract.start_date),
    end_date: stringValue(contract.end_date),
    platforms: arrayValue(contract.platforms_json).map((item) => stringValue(item)).join(', '),
    deliverable_count: stringValue(contract.deliverable_count),
    deliverables: editableJson(contract.deliverables_json),
    must_include: editableJson(contract.must_include_json),
    usage_rights: stringValue(contract.usage_rights),
    exclusivity: stringValue(contract.exclusivity),
    buyout_rights: stringValue(contract.buyout_rights),
    breach_terms: stringValue(contract.breach_terms),
    payment_terms: stringValue(contract.payment_terms),
    cancellation_terms: stringValue(contract.cancellation_terms),
    revision_terms: stringValue(contract.revision_terms),
  };
}

function contractPayload(draft: ContractDraft) {
  const payload = {
    fee_amount: draft.fee_amount ? Number(draft.fee_amount) : null,
    fee_currency: draft.fee_currency.trim() || 'USD',
    contract_duration: draft.contract_duration.trim(),
    start_date: draft.start_date.trim() || null,
    end_date: draft.end_date.trim() || null,
    platforms: draft.platforms.split(/,|，|\n/).map((item) => item.trim()).filter(Boolean),
    deliverable_count: draft.deliverable_count ? Number(draft.deliverable_count) : null,
    deliverables: parseEditableList(draft.deliverables),
    must_include: parseEditableList(draft.must_include),
    usage_rights: draft.usage_rights.trim(),
    exclusivity: draft.exclusivity.trim(),
    buyout_rights: draft.buyout_rights.trim(),
    breach_terms: draft.breach_terms.trim(),
    payment_terms: draft.payment_terms.trim(),
    cancellation_terms: draft.cancellation_terms.trim(),
    revision_terms: draft.revision_terms.trim(),
  };
  return { ...payload, manual_overrides: payload };
}

// GEN-/草稿行判定:生成器产出(文件名 GEN- 前缀)或 status=draft,可挂签署版。
function isGeneratedDraftContract(contract: VkpiProjectContract) {
  return /^GEN-/i.test(String(contract.file_name || '')) || String(contract.status || '') === 'draft';
}

function ContractArchiveCard({
  contract,
  row,
  busyKey,
  onSave,
  onConfirm,
  onOpen,
  onRetry,
  onDelete,
  onUploadSignedVersion,
}: {
  contract: VkpiProjectContract;
  row?: VkpiProjectRow;
  busyKey: string;
  onSave: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirm: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpen: (contractId: number) => Promise<void>;
  onRetry: (contractId: number) => Promise<void>;
  onDelete?: (contractId: number, fileName?: string) => void;
  onUploadSignedVersion?: (contract: VkpiProjectContract) => void;
}) {
  const [draft, setDraft] = useState<ContractDraft>(() => initialContractDraft(contract));
  // 用户是否手改过本表单:手改后不让后台轮询/重提取的回填覆盖编辑中的内容。
  const editedRef = useRef(false);
  // 提取结果指纹:仅当服务端真值变化时变(纯本地编辑期间稳定,不会触发回填)。
  const extractionSig = useMemo(
    () =>
      JSON.stringify([
        contract.fee_amount, contract.fee_currency, contract.contract_duration,
        contract.start_date, contract.end_date, contract.platforms_json,
        contract.deliverable_count, contract.deliverables_json, contract.must_include_json,
        contract.usage_rights, contract.exclusivity, contract.buyout_rights,
        contract.breach_terms, contract.payment_terms, contract.cancellation_terms, contract.revision_terms,
      ]),
    [
      contract.fee_amount, contract.fee_currency, contract.contract_duration,
      contract.start_date, contract.end_date, contract.platforms_json,
      contract.deliverable_count, contract.deliverables_json, contract.must_include_json,
      contract.usage_rights, contract.exclusivity, contract.buyout_rights,
      contract.breach_terms, contract.payment_terms, contract.cancellation_terms, contract.revision_terms,
    ],
  );
  // 重新提取开始(processing)时清掉脏标记,让新结果可以回填。
  useEffect(() => {
    if (contract.extraction_status === 'processing') editedRef.current = false;
  }, [contract.extraction_status]);
  // 提取真值变化(上传后异步提取完成 / 重新提取)→ 未手改则把结果灌进表单。
  // 修复:此前用 useState 初始化函数只在挂载跑一次,processing 时挂载→提取完成后表单永远空。
  useEffect(() => {
    if (editedRef.current) return;
    setDraft(initialContractDraft(contract));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extractionSig]);
  const meta = contractStatusMeta(contract);
  // 一次解析存变量,避免渲染里对同一份 deliverables_json 双调 summarizeDeliverables。
  const deliverableSummaries = summarizeDeliverables(contract.deliverables_json);
  const saving = busyKey === `save:${contract.id}`;
  const confirming = busyKey === `confirm:${contract.id}`;
  const extracting = busyKey === `extract:${contract.id}` || contract.extraction_status === 'processing';
  const downloading = busyKey === `download:${contract.id}`;
  const deleting = busyKey === `delete:${contract.id}`;
  const update = (key: keyof ContractDraft, value: string) => {
    editedRef.current = true;
    setDraft((current) => ({ ...current, [key]: value }));
  };

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.018] p-3 space-y-3">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg bg-purple-500/10 border border-purple-400/20 flex items-center justify-center shrink-0">
          <FileText size={18} className="text-purple-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <strong className="text-[13px] text-white truncate">{contract.file_name || '合同文件'}</strong>
            <span className={`text-[9.5px] px-2 py-0.5 rounded-full border ${meta.className}`}>{meta.label}</span>
            {contract.status === 'confirmed' ? <span className="text-[9.5px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300">人工确认</span> : null}
            {contract.signed_version_of ? (
              <span className="text-[9.5px] px-2 py-0.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300" title={`本文件是合同 #${contract.signed_version_of} 的签署版`}>
                签署版 · 关联 #{contract.signed_version_of}
              </span>
            ) : null}
            {contract.superseded_by ? (
              <span className="text-[9.5px] px-2 py-0.5 rounded-full border border-slate-400/25 bg-slate-400/10 text-slate-300" title={`该草稿已有签署版,见合同 #${contract.superseded_by}`}>
                已有签署版 #{contract.superseded_by}
              </span>
            ) : null}
          </div>
          <div className="text-[10.5px] text-slate-400 mt-1">
            {row?.kolHandle || row?.kolName || `KOL ${contract.kol_pool_id || '-'}`} · {row?.platform || compactList(contract.platforms_json, '平台待确认')} · {moneyLabel(contract.fee_amount, contract.fee_currency)}
          </div>
          <div className="text-[10px] text-slate-500 mt-1 truncate">
            交付: {compactList(contract.deliverables_json)} · 授权: {contract.usage_rights || contract.buyout_rights || '授权范围待确认'}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {onUploadSignedVersion && isGeneratedDraftContract(contract) && !contract.superseded_by ? (
            <button
              className="px-2.5 py-1.5 rounded-md border border-emerald-400/30 bg-emerald-400/10 text-[10px] text-emerald-200 hover:bg-emerald-400/20"
              type="button"
              onClick={() => onUploadSignedVersion(contract)}
              title="签署完成后上传签署版,与本草稿双记录留痕"
            >
              上传签署版
            </button>
          ) : null}
          <button className="px-2.5 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[10px] text-slate-300 hover:text-white" type="button" onClick={() => void onOpen(contract.id)} disabled={downloading}>
            <ExternalLink size={11} className="inline mr-1" />查看PDF
          </button>
          <button className="px-2.5 py-1.5 rounded-md border border-purple-400/25 bg-purple-400/10 text-[10px] text-purple-200 disabled:opacity-50" type="button" onClick={() => void onRetry(contract.id)} disabled={extracting || contract.extraction_status === 'skipped'} title={contract.extraction_status === 'skipped' ? 'DOC/DOCX v1 仅归档不提取，请上传 PDF 版本触发条款提取' : undefined}>
            {extracting ? '提取中' : '重新提取'}
          </button>
          {onDelete ? (
            <button className="px-2.5 py-1.5 rounded-md border border-red-400/25 bg-red-400/10 text-[10px] text-red-300 hover:text-red-200 disabled:opacity-50" type="button" onClick={() => onDelete(contract.id, contract.file_name || undefined)} disabled={deleting} title="删除该合同归档（原文件与提取结果一并删除）">
              {deleting ? '删除中' : '删除'}
            </button>
          ) : null}
        </div>
      </div>

      {contract.extraction_status === 'skipped' ? (
        <div className="rounded-lg border border-slate-500/20 bg-slate-500/10 p-3 text-[11px] text-slate-300">DOC/DOCX 已归档，v1 仅自动提取 PDF。可查看原文件，或上传 PDF 版本触发条款提取。</div>
      ) : null}
      {contract.extraction_status === 'failed' ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-[11px] text-red-300">
          提取失败{(contract.raw_extracted_json as Record<string, unknown> | undefined)?.error ? `：${String((contract.raw_extracted_json as Record<string, unknown>).error).slice(0, 200)}` : ''}。可点「重新提取」重新入队。
        </div>
      ) : null}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {[
          ['金额', moneyLabel(contract.fee_amount, contract.fee_currency), 'fee_amount'],
          ['期限', contract.start_date || contract.end_date ? `${contract.start_date || '?'} → ${contract.end_date || '?'}` : (contract.contract_duration || '待确认'), 'contract_duration'],
          ['平台', compactList(contract.platforms_json), 'platforms'],
          ['承诺条数', contract.deliverable_count || '待确认', 'deliverable_count'],
        ].map(([label, value, field]) => (
          <div key={label} className="rounded-lg border border-white/[0.05] bg-black/20 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[9px] text-slate-500">{label}</span>
              <ConfidenceBadge contract={contract} field={String(field)} />
            </div>
            <strong className="block mt-1 text-[12px] text-white truncate">{value}</strong>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <label className="text-[10px] text-slate-400">金额<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.fee_amount} onChange={(event) => update('fee_amount', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">币种<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.fee_currency} onChange={(event) => update('fee_currency', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">开始日期<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.start_date} onChange={(event) => update('start_date', event.target.value)} placeholder="YYYY-MM-DD" /></label>
        <label className="text-[10px] text-slate-400">结束日期<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.end_date} onChange={(event) => update('end_date', event.target.value)} placeholder="YYYY-MM-DD" /></label>
        <label className="text-[10px] text-slate-400">期限<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.contract_duration} onChange={(event) => update('contract_duration', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">平台<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.platforms} onChange={(event) => update('platforms', event.target.value)} placeholder="YouTube, Instagram" /></label>
        <label className="text-[10px] text-slate-400">承诺条数<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.deliverable_count} onChange={(event) => update('deliverable_count', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">Exclusivity<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.exclusivity} onChange={(event) => update('exclusivity', event.target.value)} /></label>
      </div>

      <div className="text-[10px] text-slate-400">
        <span className="flex items-center justify-between gap-2 mb-1">Deliverables 交付物<ConfidenceBadge contract={contract} field="deliverables" /></span>
        <div className="w-full rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-2 space-y-2">
          {deliverableSummaries.length ? (
            deliverableSummaries.map((d, index) => (
              <div key={index} className="flex gap-2">
                <span className="text-[10px] text-purple-300/70 mt-0.5 tabular-nums shrink-0">{index + 1}.</span>
                <div className="min-w-0">
                  <div className="text-[11px] text-white font-medium">{d.line}</div>
                  {d.notes && <div className="text-[10px] text-slate-400 mt-0.5 leading-relaxed">{d.notes}</div>}
                </div>
              </div>
            ))
          ) : (
            <div className="text-[11px] text-slate-500">—</div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {[
          ['must_include', '必须包含', draft.must_include, 'must_include'],
          ['usage_rights', 'Usage rights', draft.usage_rights, 'usage_rights'],
          ['buyout_rights', '买断授权', draft.buyout_rights, 'buyout_rights'],
          ['breach_terms', '违约条款', draft.breach_terms, 'breach_terms'],
          ['payment_terms', '付款条款', draft.payment_terms, 'payment_terms'],
          ['cancellation_terms', '解约条款', draft.cancellation_terms, 'cancellation_terms'],
          ['revision_terms', '返工/修改条款', draft.revision_terms, 'revision_terms'],
        ].map(([key, label, value, field]) => (
          <label key={key} className="text-[10px] text-slate-400">
            <span className="flex items-center justify-between gap-2 mb-1">{label}<ConfidenceBadge contract={contract} field={String(field)} /></span>
            <textarea className="w-full min-h-[74px] rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white resize-y" value={String(value)} onChange={(event) => update(key as keyof ContractDraft, event.target.value)} />
          </label>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-[10px] text-slate-500">低 confidence 字段会用黄色标出；LLM 结果默认待人工确认，不作为最终合同真值。</div>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[11px] text-slate-300 disabled:opacity-50" type="button" onClick={() => void onSave(contract.id, contractPayload(draft))} disabled={saving || confirming}>{saving ? '保存中' : '保存修改'}</button>
          <button className="px-3 py-1.5 rounded-md bg-emerald-500/90 hover:bg-emerald-500 text-[11px] font-semibold text-white disabled:opacity-50" type="button" onClick={() => void onConfirm(contract.id, contractPayload(draft))} disabled={saving || confirming}>{confirming ? '确认中' : '确认归档'}</button>
        </div>
      </div>
    </div>
  );
}

export function CampaignContractsTab({
  rows,
  contractLines,
  contracts,
  loading,
  error,
  busyKey,
  onUploadContract,
  onSaveContract,
  onConfirmContract,
  onOpenContract,
  onRetryExtract,
  onDeleteContract,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
  contracts: VkpiProjectContractsResponse | null;
  loading: boolean;
  error: string;
  busyKey: string;
  onUploadContract: (file: File, assignmentId?: string, kolPoolId?: string, relatedContractId?: number) => Promise<void>;
  onSaveContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirmContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpenContract: (contractId: number) => Promise<void>;
  onRetryExtract: (contractId: number) => Promise<void>;
  onDeleteContract?: (contractId: number, fileName?: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const uploadZoneRef = useRef<HTMLDivElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState('');
  const [linkedRowId, setLinkedRowId] = useState('');
  // 签署版上传:点 GEN-/草稿行「上传签署版」后记录被关联草稿,复用本上传表单携带 related_contract_id。
  const [relatedContract, setRelatedContract] = useState<VkpiProjectContract | null>(null);
  const uploadBusy = busyKey === 'upload';
  const items = contracts?.items || [];
  const expectedRows = contractLines.filter((line) => line.statusLabel !== '未触发');
  const selectedRow = rows.find((row) => row.id === linkedRowId) || rows.find((row) => stageIndex(row.stage) >= stageIndex('agreed')) || rows[0];
  const chooseFile = (nextFile?: File | null) => {
    if (!nextFile) return;
    // 非法类型不再静默吞掉:给出明确提示,让用户知道为什么没选上。
    if (!/\.(pdf|doc|docx)$/i.test(nextFile.name)) {
      setFileError(`「${nextFile.name}」类型不支持,仅接受 PDF / DOC / DOCX 合同文件。`);
      return;
    }
    setFileError('');
    setFile(nextFile);
  };
  const startSignedVersionUpload = (contract: VkpiProjectContract) => {
    setRelatedContract(contract);
    uploadZoneRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    fileRef.current?.click();
  };
  const submitUpload = async () => {
    if (!file) return;
    await onUploadContract(file, selectedRow?.assignmentId, selectedRow?.kolPoolId, relatedContract?.id);
    setFile(null);
    setRelatedContract(null);
  };

  return (
    <div className="p-4 space-y-4" aria-label="项目合同归档">
      <div className="rounded-xl border border-purple-500/25 bg-purple-500/[0.06] p-3 flex items-start gap-2.5">
        <Sparkles size={14} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          合同归档 v1 · PDF 上传后自动提取条款；提取字段必须人工确认后才进入正式履约复盘口径。
        </div>
      </div>

      <div
        ref={uploadZoneRef}
        className={`rounded-xl border-2 border-dashed ${relatedContract ? 'border-emerald-400/40 bg-emerald-400/[0.04]' : 'border-white/[0.08] bg-white/[0.012]'} p-4`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files?.[0]); }}
      >
        <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" className="hidden" onChange={(event) => { chooseFile(event.target.files?.[0]); event.target.value = ''; }} />
        {relatedContract ? (
          <div className="mb-3 flex items-center gap-2 flex-wrap text-[10.5px]">
            <span className="px-2 py-1 rounded-md border border-emerald-400/30 bg-emerald-400/10 text-emerald-200">
              签署版上传 · 将关联草稿 #{relatedContract.id}{relatedContract.file_name ? `(${relatedContract.file_name})` : ''}
            </span>
            <button className="px-2 py-1 rounded-md border border-white/[0.08] text-slate-300 hover:text-white" type="button" onClick={() => setRelatedContract(null)}>
              取消关联(改为普通归档)
            </button>
          </div>
        ) : null}
        <div className="grid md:grid-cols-[minmax(0,1fr)_220px_128px] gap-3 items-end">
          <button className="rounded-lg border border-white/[0.06] bg-black/20 p-4 text-left hover:border-purple-400/35" type="button" onClick={() => fileRef.current?.click()} disabled={uploadBusy}>
            <Upload size={20} className="text-purple-300 mb-2" />
            <strong className="block text-[12px] text-white">{file ? file.name : '拖拽或选择合同 PDF / DOCX'}</strong>
            <span className="block mt-1 text-[10.5px] text-slate-500">{file ? `${Math.round(file.size / 1024)} KB · ${/\.pdf$/i.test(file.name) ? '将自动提取' : '只存档不提取'}` : 'PDF 自动提取条款；DOCX 先存档，v1 不自动识别'}</span>
          </button>
          <label className="text-[10px] text-slate-400">关联 KOL
            <select className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/30 px-2 py-2 text-[11px] text-white" value={linkedRowId} onChange={(event) => setLinkedRowId(event.target.value)}>
              <option value="">自动选择 / 项目级</option>
              {rows.map((row) => (
                <option key={row.id} value={row.id}>{row.kolHandle || row.kolName || row.id} · {stageLabels[row.stage] || row.stage}</option>
              ))}
            </select>
          </label>
          <button className="rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-semibold px-3 py-2 disabled:opacity-50" type="button" onClick={() => void submitUpload()} disabled={!file || uploadBusy}>
            {uploadBusy ? '正在提取条款' : relatedContract ? '上传签署版' : '上传归档'}
          </button>
        </div>
        {fileError ? <div className="mt-2 text-[10.5px] text-red-300">{fileError}</div> : null}
      </div>

      <div className="grid grid-cols-3 gap-2">
        {[
          ['已归档', items.length],
          ['待确认', items.filter((item) => item.status !== 'confirmed' && item.extraction_status !== 'failed').length],
          ['提取失败', items.filter((item) => item.extraction_status === 'failed').length],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <span className="block text-[9.5px] text-slate-500">{label}</span>
            <strong className="block mt-1 text-[20px] text-white tabular-nums">{value}</strong>
          </div>
        ))}
      </div>

      {loading ? <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-4 text-[11px] text-slate-400">正在读取合同归档...</div> : null}
      {error ? <div className="rounded-lg border border-red-500/25 bg-red-500/10 p-4 text-[11px] text-red-300">{error}</div> : null}

      {!loading && !items.length ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <FileText size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">暂无真实合同 · 当前有 {expectedRows.length} 个 KOL 已到可归档阶段</div>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((contract) => (
            <ContractArchiveCard
              key={contract.id}
              contract={contract}
              row={contractLinkedRow(contract, rows)}
              busyKey={busyKey}
              onSave={onSaveContract}
              onConfirm={onConfirmContract}
              onOpen={onOpenContract}
              onRetry={onRetryExtract}
              onDelete={onDeleteContract}
              onUploadSignedVersion={startSignedVersionUpload}
            />
          ))}
        </div>
      )}
    </div>
  );
}
