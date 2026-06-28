// 从 ProjectDetailModals.tsx 抽出的独立展示型 modal 组件(函数体/JSX 逐字不变搬运)。
import { useEffect, useRef, useState, type FormEvent, type RefObject } from 'react';
import { AlertCircle, ArrowLeft, DollarSign, FileText, ImageIcon, Package, Sparkles, Upload, Video, X } from 'lucide-react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import type { ScreenshotTarget } from '../../../../domains/projects';
import { filePayload, type ContractSlot, type CostEntryType, type PolishPreviewItem } from './ProjectDetailModals.helpers';

export function UploadScreenshotModal({
  target,
  onClose,
  onSubmit,
}: {
  target: ScreenshotTarget;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState('');
  const [shopifyUrl, setShopifyUrl] = useState(target.row.shopifyLink || '');
  const [previewUrl, setPreviewUrl] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const showShopifyField = target.stage === 'received';

  useEffect(() => {
    if (!file) {
      setPreviewUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const chooseFile = (nextFile?: File | null) => {
    setError('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    const isImage = ['image/png', 'image/jpeg', 'image/webp'].includes(nextFile.type);
    if (!isImage) {
      setError('只支持 PNG / JPG / WebP 截图。');
      setFile(null);
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError('截图不能超过 10MB。');
      setFile(null);
      return;
    }
    setFile(nextFile);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError('请选择截图文件。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit({
        kind: 'screenshot',
        stage: target.stage,
        from: target.from,
        to: target.to,
        note: note.trim(),
        shopify_url: shopifyUrl.trim() || undefined,
        file,
        ...filePayload(file),
      });
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '截图提交失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal" onSubmit={submit} role="dialog" aria-label="上传阶段截图" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>上传{stageLabels[target.stage]}阶段证据</h3>
            <p>{target.row.kolHandle || target.row.kolName} · {target.from}→{target.to} {stageLabels[target.stage]}</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div
          className="vkpi-campaign-drop-zone"
          role="button"
          tabIndex={0}
          onClick={() => fileRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            chooseFile(event.dataTransfer.files?.[0]);
          }}
        >
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => chooseFile(event.target.files?.[0])} />
          {previewUrl ? <img className="vkpi-upload-preview" src={previewUrl} alt="截图预览" /> : <Upload size={24} />}
          <strong>{file ? file.name : '点击或拖拽上传截图'}</strong>
          <span>PNG / JPG / WebP · 最大 10MB · 用于阶段证据归档</span>
        </div>
        <label className="vkpi-campaign-upload-note">备注
          <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：邮件截图 / 发货截图 / 发布截图" />
        </label>
        {showShopifyField ? (
          <label className="vkpi-campaign-upload-note">Shopify 归因 URL
            <input value={shopifyUrl} onChange={(event) => setShopifyUrl(event.target.value)} placeholder="https://shop.viltrox.com/...?ref=kolname" />
          </label>
        ) : null}
        <div className="vkpi-modal-ai-note"><Sparkles size={11} />提交后归档到当前 KOL 阶段，作为项目推进证据。</div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !file}>{busy ? '提交中' : '提交截图'}</button>
        </footer>
      </form>
    </div>
  );
}

export function ShippingInfoModal({
  project,
  row,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  row: VkpiProjectRow;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [carrier, setCarrier] = useState(row.trackingCarrier || 'SF Express');
  const [trackingNo, setTrackingNo] = useState(row.trackingNumber || '');
  const [shippingFee, setShippingFee] = useState('0');
  const [productCost, setProductCost] = useState('0');
  const [productName, setProductName] = useState(row.productName || project.productName || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const carriers = ['SF Express', 'FedEx', 'DHL', 'UPS', 'USPS', 'EMS', 'Yamato', 'Other'];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!trackingNo.trim()) {
      setError('请填写快递单号。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit({
        carrier,
        tracking_number: trackingNo.trim(),
        shipping_status: 'shipped',
        shipping_cost_usd: Number(shippingFee || 0),
        product_cost_usd: Number(productCost || 0),
        products: productName ? [{ product_sku: row.productSku || project.productSku, product_name: productName }] : [],
        note: `KOL 物流录入：${row.kolHandle || row.kolName}`,
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '物流提交失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal is-compact" onSubmit={submit} role="dialog" aria-label="录入快递信息" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>录入快递信息</h3>
            <p>{row.kolHandle || row.kolName} · 记录物流单号、快递费和产品成本</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div className="vkpi-modal-form-grid">
          <label>快递商
            <select value={carrier} onChange={(event) => setCarrier(event.target.value)}>
              {carriers.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>快递单号
            <input value={trackingNo} onChange={(event) => setTrackingNo(event.target.value)} placeholder="SF1234567890" />
          </label>
          <label>快递费 USD
            <input inputMode="decimal" value={shippingFee} onChange={(event) => setShippingFee(event.target.value)} placeholder="0" />
          </label>
          <label>产品成本 USD
            <input inputMode="decimal" value={productCost} onChange={(event) => setProductCost(event.target.value)} placeholder="0" />
          </label>
          <label className="is-full">寄送产品
            <input value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="产品名称 / SKU" />
          </label>
        </div>
        <div className="vkpi-modal-ai-note is-cyan"><Package size={11} />提交后更新当前 KOL 的物流状态，并汇总到项目费用。</div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !trackingNo.trim()}>{busy ? '保存中' : '发货并记录成本'}</button>
        </footer>
      </form>
    </div>
  );
}

export function CostEntryModal({
  project,
  row,
  defaultType = 'cash_fee',
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  row: VkpiProjectRow;
  defaultType?: CostEntryType;
  onClose: () => void;
  onSubmit: (payload: {
    costType: CostEntryType;
    amountUsd: number;
    note?: string;
    sourceRef?: string;
    metadata?: Record<string, unknown>;
  }) => Promise<void>;
}) {
  const [costType, setCostType] = useState<CostEntryType>(defaultType);
  const [amountUsd, setAmountUsd] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const assignmentId = String(row.assignmentId || row.id.replace(/^assignment:/, '') || '').trim();
  const kolPoolId = String(row.kolPoolId || '').trim();
  const sourceBucket = costType === 'shipping' ? 'shipping' : costType === 'product' ? 'product' : 'contract';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const amount = Number(amountUsd || 0);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError('请填写大于 0 的费用金额。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit({
        costType,
        amountUsd: amount,
        note: note.trim() || `${row.kolHandle || row.kolName || 'KOL'} · ${costType}`,
        sourceRef: assignmentId ? `assignment_${sourceBucket}:${assignmentId}` : `project_cost:${project.id}`,
        metadata: {
          assignment_id: assignmentId || undefined,
          kol_pool_id: kolPoolId || undefined,
          kol_name: row.kolName || row.kolHandle,
          platform: row.platform,
          project_id: project.id,
          source: 'projects_finance_tab',
        },
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '费用登记失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal is-compact" onSubmit={submit} role="dialog" aria-label="录入费用" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>录入费用</h3>
            <p>{row.kolHandle || row.kolName} · 写入项目成本账本</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div className="vkpi-modal-form-grid">
          <label>费用类型
            <select value={costType} onChange={(event) => setCostType(event.target.value as CostEntryType)}>
              <option value="cash_fee">合同费</option>
              <option value="shipping">快递费</option>
              <option value="product">产品成本</option>
            </select>
          </label>
          <label>金额 USD
            <input inputMode="decimal" value={amountUsd} onChange={(event) => setAmountUsd(event.target.value)} placeholder="0" />
          </label>
          <label className="is-full">备注
            <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="合同款 / 运费 / 样品成本说明" />
          </label>
        </div>
        <div className="vkpi-modal-ai-note"><DollarSign size={11} />提交后写入 vkpi_cost_ledger，并按 assignment 归到当前 KOL 行。</div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !amountUsd.trim()}>{busy ? '保存中' : '保存费用'}</button>
        </footer>
      </form>
    </div>
  );
}

export function VideoUrlModal({
  row,
  onClose,
  onSubmit,
}: {
  row: VkpiProjectRow;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [videoUrl, setVideoUrl] = useState('');
  const [shopifyUrl, setShopifyUrl] = useState(row.shopifyLink || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!/^https?:\/\//i.test(videoUrl.trim())) {
      setError('请输入有效视频 URL。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit({ kind: 'video', url: videoUrl.trim(), shopify_url: shopifyUrl.trim() || undefined, title: videoUrl.trim().split('/').pop() || 'Video' });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '视频提交失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal is-compact" onSubmit={submit} role="dialog" aria-label="录入视频 URL" onClick={(event) => event.stopPropagation()}>
        <header>
          <div><h3>录入视频 URL</h3><p>{row.kolHandle || row.kolName} · 记录发布链接和归因链接</p></div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div className="vkpi-modal-form-grid">
          <label className="is-full">视频 URL
            <input value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://youtube.com/watch?v=..." />
          </label>
          <label className="is-full">Shopify 归因 URL
            <input value={shopifyUrl} onChange={(event) => setShopifyUrl(event.target.value)} placeholder="https://shop.viltrox.com/...?ref=kolname" />
          </label>
        </div>
        <div className="vkpi-modal-ai-note is-green"><Video size={11} />提交后作为已发布内容证据，并用于项目曝光汇总。</div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !videoUrl.trim()}>{busy ? '提交中' : '发布并开始分析'}</button>
        </footer>
      </form>
    </div>
  );
}

export function ContractUploadModal({
  row,
  onClose,
  onSubmit,
}: {
  row: VkpiProjectRow;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [fee, setFee] = useState('');
  const [duration, setDuration] = useState('');
  const [deliverables, setDeliverables] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement | null>(null);
  const chooseFile = (nextFile?: File | null) => {
    setError('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!/pdf|word|document|msword|officedocument/i.test(nextFile.type) && !/\.(pdf|doc|docx)$/i.test(nextFile.name)) {
      setError('只支持 PDF / DOC / DOCX。');
      return;
    }
    // 与后端上限对齐(25MB):超限在前端就拦下,不再白等上传后被拒。
    if (nextFile.size > 25 * 1024 * 1024) {
      setError('合同文件不能超过 25MB。');
      return;
    }
    setFile(nextFile);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError('请选择合同文件。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit({ kind: 'contract', fee_usd: Number(fee || 0), duration, deliverables, file, ...filePayload(file) });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '合同提交失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal" onSubmit={submit} role="dialog" aria-label="上传合同" onClick={(event) => event.stopPropagation()}>
        <header>
          <div><h3>上传合同</h3><p>{row.kolHandle || row.kolName} · 归档合同、金额、期限和 deliverables</p></div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div className="vkpi-campaign-drop-zone" role="button" tabIndex={0} onClick={() => fileRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files?.[0]); }}>
          <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" onChange={(event) => chooseFile(event.target.files?.[0])} />
          <FileText size={25} />
          <strong>{file ? file.name : '点击或拖拽上传合同 PDF / DOCX'}</strong>
          <span>{file ? `${Math.round(file.size / 1024)} KB · 已选择` : '最大 25MB · PDF / DOCX 合同文件'}</span>
        </div>
        <div className="vkpi-modal-form-grid">
          <label>合同金额 USD<input inputMode="decimal" value={fee} onChange={(event) => setFee(event.target.value)} placeholder="1500" /></label>
          <label>交付期限<input value={duration} onChange={(event) => setDuration(event.target.value)} placeholder="2 周" /></label>
          <label className="is-full">Deliverables<input value={deliverables} onChange={(event) => setDeliverables(event.target.value)} placeholder="YouTube 主视频 + IG Reels" /></label>
        </div>
        <div className="vkpi-modal-ai-note"><Sparkles size={11} />提交后归档合同文件，并同步金额、期限和交付项。</div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !file}>{busy ? '提交中' : '上传合同并完成'}</button>
        </footer>
      </form>
    </div>
  );
}

export function StageActionModal({
  row,
  action,
  onClose,
  onSubmit,
}: {
  row: VkpiProjectRow;
  action: 'stalled' | 'lost' | 'released' | 'cancelled';
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const actionMeta = {
    stalled: { title: '标记停滞', desc: 'KOL 暂时无响应，后续仍可恢复。', icon: AlertCircle, color: '#fb923c' },
    lost: { title: '标记流失', desc: 'KOL 拒绝或不再合作。', icon: X, color: '#ef4444' },
    released: { title: '释放回 KOL Pool', desc: '项目结束，该 KOL 可供其他项目使用。', icon: ArrowLeft, color: '#94a3b8' },
    cancelled: { title: '取消该 KOL', desc: '从当前推广流程移出。', icon: X, color: '#64748b' },
  }[action];
  const Icon = actionMeta.icon;
  const submit = async () => {
    setBusy(true);
    try {
      await onSubmit(reason.trim());
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="vkpi-campaign-upload-modal is-compact" role="dialog" aria-label={actionMeta.title} onClick={(event) => event.stopPropagation()}>
        <header>
          <div className="vkpi-modal-title-row">
            <span style={{ background: `${actionMeta.color}20`, color: actionMeta.color }}><Icon size={17} /></span>
            <div><h3>{actionMeta.title}</h3><p>{actionMeta.desc}</p></div>
          </div>
          <button type="button" onClick={onClose} disabled={busy}><X size={14} />关闭</button>
        </header>
        <div className="vkpi-stage-action-person">
          <ImageIcon size={16} />
          <div><strong>{row.kolHandle || row.kolName}</strong><small>{row.platform} · 当前 {stageLabels[row.stage]}</small></div>
        </div>
        <label className="vkpi-campaign-upload-note">原因
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="例：KOL 3 周未回复 DM / 项目周期结束" />
        </label>
        <div className="vkpi-modal-ai-note"><Sparkles size={11} />提交会真实更新 assignment.stage，并记录 updated_at。</div>
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="button" onClick={() => void submit()} disabled={busy}>{busy ? '提交中' : '确认'}</button>
        </footer>
      </div>
    </div>
  );
}

// 合同生成成功画面(展示型;JSX 逐字搬自 GenerateContractModal 的 doneContractId 分支)。
export function GenerateContractDoneScreen({
  doneContractId,
  selectedRow,
  onDownload,
  onClose,
}: {
  doneContractId: number;
  selectedRow: VkpiProjectRow | null;
  onDownload?: (contractId: number) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" role="presentation" onClick={onClose}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-md p-6 text-center" role="dialog" aria-label="合同已生成" onClick={(event) => event.stopPropagation()}>
        <div className="text-[15px] font-semibold text-emerald-300 mb-1.5">✓ 合同已生成并自动归档</div>
        <p className="text-[11px] text-slate-400 mb-4">已入「合同归档」{selectedRow ? `,关联 ${selectedRow.kolHandle || selectedRow.kolName}` : ''}——可现在下载 DOCX,或稍后到归档 tab 查看/删除。</p>
        <div className="flex items-center justify-center gap-2.5">
          {onDownload ? (
            <button className="px-4 py-2 rounded-md text-[11.5px] font-medium bg-purple-500 hover:bg-purple-400 text-white" type="button" onClick={() => onDownload(doneContractId)}>
              下载 DOCX
            </button>
          ) : null}
          <button className="px-4 py-2 rounded-md border border-white/[0.08] text-[11.5px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={onClose}>完成</button>
        </div>
      </div>
    </div>
  );
}

// 发票回填 / AI 润色工具条(展示型;JSX 逐字搬自 GenerateContractModal 的工具条块)。
export function GenerateContractTools({
  invoiceFileRef,
  invoiceState,
  invoiceFilledKeys,
  invoiceError,
  polishState,
  polishError,
  apiToken,
  onPickInvoice,
  onRunPolish,
}: {
  invoiceFileRef: RefObject<HTMLInputElement | null>;
  invoiceState: 'idle' | 'uploading' | 'extracting' | 'done' | 'failed';
  invoiceFilledKeys: Set<string>;
  invoiceError: string;
  polishState: 'idle' | 'running' | 'failed';
  polishError: string;
  apiToken?: string;
  onPickInvoice: (file?: File | null) => void;
  onRunPolish: () => void;
}) {
  return (
    <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] px-3 py-2 mb-3">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          // prop 类型 RefObject<HTMLInputElement | null>(配 .current 使用);input ref 期望
          // RefObject<HTMLInputElement>(@types/react 严格),此处 cast 对齐,行为不变。
          ref={invoiceFileRef as RefObject<HTMLInputElement>}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(event) => { onPickInvoice(event.target.files?.[0]); event.target.value = ''; }}
        />
        <button
          className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-cyan-500/15 border border-cyan-500/35 text-cyan-200 hover:bg-cyan-500/25 disabled:opacity-50"
          type="button"
          onClick={() => invoiceFileRef.current?.click()}
          disabled={invoiceState === 'uploading' || invoiceState === 'extracting' || !apiToken}
          title={apiToken ? undefined : '缺少 API token,发票回填不可用'}
        >
          {invoiceState === 'uploading' ? '发票上传中…' : invoiceState === 'extracting' ? '发票解析中…(≤90s · 泳道「发票提取」可见)' : '上传发票自动回填'}
        </button>
        <button
          className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-purple-500/15 border border-purple-500/35 text-purple-200 hover:bg-purple-500/25 disabled:opacity-50"
          type="button"
          onClick={onRunPolish}
          disabled={polishState === 'running' || !apiToken}
          title={apiToken ? '整组润色非选择/日期类文本槽,差异预览确认后才写回' : '缺少 API token,AI 润色不可用'}
        >
          {polishState === 'running' ? 'AI 润色中…(≤90s · 队列可见)' : 'AI 润色(整组文本)'}
        </button>
        <span className="text-[10px] text-slate-500">发票 PDF/PNG/JPG → 乙方与收款信息自动回填;两者均为 LLM 产物,请人工核对</span>
      </div>
      {invoiceState === 'done' && invoiceFilledKeys.size ? (
        <div className="mt-1.5 text-[10.5px] text-cyan-300">已回填 {invoiceFilledKeys.size} 个字段(高亮显示)——来自发票,请逐项核对后再生成。</div>
      ) : null}
      {invoiceError ? <div className="mt-1.5 text-[10.5px] text-rose-300">{invoiceError}</div> : null}
      {polishError ? <div className="mt-1.5 text-[10.5px] text-rose-300">{polishError}</div> : null}
    </div>
  );
}

// 合同模板字段区(展示型;JSX 逐字搬自 GenerateContractModal 的 grouped.map 槽位网格)。
export function GenerateContractFields({
  grouped,
  fields,
  invoiceFilledKeys,
  setSlotValue,
}: {
  grouped: Array<[string, ContractSlot[]]>;
  fields: Record<string, string>;
  invoiceFilledKeys: Set<string>;
  setSlotValue: (key: string, value: string) => void;
}) {
  return (
    <>
      {grouped.map(([group, slots]) => (
        <div key={group}>
          <div className="text-[10.5px] text-slate-500 mb-1.5">{group}</div>
          <div className="grid grid-cols-2 gap-2">
            {slots.map((slot) => {
              const fromInvoice = invoiceFilledKeys.has(slot.key);
              const invoiceBorder = fromInvoice ? 'border-cyan-400/60 ring-1 ring-cyan-400/30' : 'border-white/[0.06]';
              return (
                <label className={`text-[10.5px] text-slate-400 ${slot.type === 'multiline' ? 'col-span-2' : ''}`} key={slot.key}>
                  {slot.label}{slot.required ? <span className="text-rose-400"> *</span> : null}
                  {slot.type === 'choice' ? (
                    <select
                      className={`mt-1 w-full px-2.5 py-1.5 rounded-md bg-white/[0.02] border ${invoiceBorder} text-[11px] text-white`}
                      value={fields[slot.key] || ''}
                      onChange={(event) => setSlotValue(slot.key, event.target.value)}
                    >
                      <option value="" style={{ background: '#0a0a0d' }}>请选择</option>
                      {(slot.options || []).map((option) => <option key={option} value={option} style={{ background: '#0a0a0d' }}>{option}</option>)}
                    </select>
                  ) : slot.type === 'multiline' ? (
                    <textarea
                      className={`mt-1 w-full h-16 px-2.5 py-1.5 rounded-md bg-white/[0.02] border ${invoiceBorder} text-[11px] text-white resize-y`}
                      value={fields[slot.key] || ''}
                      onChange={(event) => setSlotValue(slot.key, event.target.value)}
                    />
                  ) : (
                    <input
                      className={`mt-1 w-full px-2.5 py-1.5 rounded-md bg-white/[0.02] border ${invoiceBorder} text-[11px] text-white`}
                      type={slot.type === 'date' ? 'date' : 'text'}
                      value={fields[slot.key] || ''}
                      onChange={(event) => setSlotValue(slot.key, event.target.value)}
                    />
                  )}
                  {fromInvoice ? <span className="block mt-0.5 text-[9.5px] text-cyan-300">来自发票,请核对</span> : null}
                </label>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );
}

// AI 润色差异预览浮层(展示型;JSX 逐字搬自 GenerateContractModal 的 polishPreview 分支)。
export function PolishPreviewOverlay({
  polishPreview,
  onDiscard,
  onApply,
}: {
  polishPreview: PolishPreviewItem[];
  onDiscard: () => void;
  onApply: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" role="presentation" onClick={onDiscard}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-2xl p-5 max-h-[85vh] flex flex-col" role="dialog" aria-label="AI 润色差异预览" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-white">AI 润色差异预览</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">LLM 产物 · 请人工确认——点「应用」才写回表单,放弃则原文不动</p>
          </div>
          <button className="text-slate-500 hover:text-white" type="button" onClick={onDiscard}><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-3">
          {polishPreview.map((item) => (
            <div key={item.key} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-2.5">
              <div className="text-[10.5px] text-slate-400 mb-1.5 flex items-center gap-2">
                {item.label}
                <span className="text-[9px] px-1.5 py-0.5 rounded border border-purple-400/30 bg-purple-400/10 text-purple-300">LLM 产物·请人工确认</span>
              </div>
              <div className="text-[9.5px] text-slate-500 mb-0.5">原文</div>
              <div className="rounded-md bg-black/30 px-2 py-1.5 text-[11px] text-slate-300 whitespace-pre-wrap">{item.original}</div>
              <div className="text-[9.5px] text-emerald-400/80 mt-1.5 mb-0.5">润色后</div>
              <div className="rounded-md bg-emerald-500/[0.06] border border-emerald-500/20 px-2 py-1.5 text-[11px] text-emerald-100 whitespace-pre-wrap">{item.polished}</div>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2 mt-3.5 pt-3 border-t border-white/[0.05]">
          <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={onDiscard}>放弃改写</button>
          <button className="px-3.5 py-1.5 rounded-md text-[11px] font-medium bg-emerald-500/90 hover:bg-emerald-500 text-white" type="button" onClick={onApply}>
            应用 {polishPreview.length} 处改写
          </button>
        </div>
      </div>
    </div>
  );
}
