import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowLeft, DollarSign, FileText, ImageIcon, Package, Search, ShoppingCart, Sparkles, Upload, Video, X } from 'lucide-react';
import type { VkpiKolOption, VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import {
  editPlatformOptions,
  stageIndex,
  type ScreenshotTarget,
  type TrackingState,
} from '../../../../domains/projects';

function filePayload(file: File | null) {
  return file ? { file_name: file.name, file_type: file.type, file_size: file.size } : {};
}

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

type CostEntryType = 'cash_fee' | 'shipping' | 'product';

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
          <span>{file ? `${Math.round(file.size / 1024)} KB · 已选择` : '最大 20MB · PDF / DOCX 合同文件'}</span>
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

export function AddKolModal({
  project,
  rows,
  kolOptions,
  busy,
  loading = false,
  loadError = '',
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  kolOptions: VkpiKolOption[];
  busy: boolean;
  loading?: boolean;
  loadError?: string;
  onClose: () => void;
  onSubmit: (selectedKols: VkpiKolOption[]) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [platform, setPlatform] = useState('全部平台');
  const [claimFilter, setClaimFilter] = useState('全部');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const joinedIds = useMemo(() => new Set(rows.map((row) => row.kolId).filter(Boolean) as string[]), [rows]);
  const joinedHandles = useMemo(() => new Set(rows.map((row) => String(row.kolHandle || '').toLowerCase()).filter(Boolean)), [rows]);
  const platformOptions = useMemo(() => Array.from(new Set(kolOptions.map((kol) => kol.platform))).filter(Boolean), [kolOptions]);
  const visibleKols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return kolOptions.filter((kol) => {
      const normalizedHandle = String(kol.handle || '').toLowerCase();
      const joined = joinedIds.has(kol.id) || joinedHandles.has(normalizedHandle);
      const matchesQuery = !normalizedQuery || [kol.name, kol.handle, kol.platform, kol.followerLabel].some((value) => String(value || '').toLowerCase().includes(normalizedQuery));
      const matchesPlatform = platform === '全部平台' || kol.platform === platform;
      const matchesClaim = claimFilter === '全部' || (claimFilter === '已关注' ? Boolean(kol.activeClaimId) : !kol.activeClaimId);
      return !joined && matchesQuery && matchesPlatform && matchesClaim;
    }).slice(0, 80);
  }, [claimFilter, joinedHandles, joinedIds, kolOptions, platform, query]);
  const selectedKols = useMemo(() => kolOptions.filter((kol) => selectedIds.has(kol.id)), [kolOptions, selectedIds]);

  const toggleKol = (kolId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(kolId)) next.delete(kolId);
      else next.add(kolId);
      return next;
    });
  };

  const submit = async () => {
    if (!selectedKols.length) return;
    await onSubmit(selectedKols);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-2xl p-5 max-h-[90vh] flex flex-col" role="dialog" aria-label="添加 KOL" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-[14px] font-semibold text-white">从 MY KOL Pool 添加</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">选中后进入「{project.campaign || '当前推广'}」的 1.发现 阶段 · 已选 {selectedKols.length}</p>
          </div>
          <button className="text-slate-500 hover:text-white disabled:opacity-50" type="button" onClick={onClose} disabled={busy}><X size={16} /></button>
        </div>
        <div className="flex items-center gap-2 mb-3">
          <div className="relative flex-1">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              className="w-full pl-8 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索 KOL / handle"
            />
          </div>
          <select className="px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-purple-500/40" value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option style={{ background: '#0a0a0d' }}>全部平台</option>
            {platformOptions.map((item) => <option key={item} style={{ background: '#0a0a0d' }}>{item}</option>)}
          </select>
          <select className="px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-purple-500/40" value={claimFilter} onChange={(event) => setClaimFilter(event.target.value)}>
            <option style={{ background: '#0a0a0d' }}>全部</option>
            <option style={{ background: '#0a0a0d' }}>已关注</option>
            <option style={{ background: '#0a0a0d' }}>未关注</option>
          </select>
        </div>
        <div className="flex-1 overflow-y-auto -mx-1 px-1 max-h-[50vh]">
          {loadError ? (
            <div className="text-center py-8 text-[11px] text-rose-300">候选加载失败：{loadError} —— 关闭重开重试；若提示权限(scope)请重新登录或联系管理员。</div>
          ) : loading ? (
            <div className="text-center py-8 text-[11px] text-slate-500">候选 KOL 加载中…</div>
          ) : visibleKols.length === 0 ? (
            <div className="text-center py-8 text-[11px] text-slate-500">没有匹配的 KOL，或全部已加入</div>
          ) : (
            <div className="space-y-1.5">
              {visibleKols.map((kol) => {
                const selected = selectedIds.has(kol.id);
                return (
                  <label
                    className={`flex items-center gap-3 px-3 py-2 rounded cursor-pointer ${selected ? 'bg-purple-500/10 border border-purple-500/30' : 'border border-white/[0.04] hover:bg-white/[0.02]'}`}
                    key={kol.id}
                  >
                    <input checked={selected} className="accent-purple-500" type="checkbox" onChange={() => toggleKol(kol.id)} />
                    <div className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#06b6d4)' }}>{kol.name.charAt(0).toUpperCase()}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="text-[12px] text-white font-medium truncate">{kol.name}</div>
                        <span className="text-[9.5px] text-slate-500 truncate">{kol.handle || '-'}</span>
                        <span className={`text-[9.5px] px-1.5 py-0.5 rounded font-medium ${kol.activeClaimId ? 'bg-emerald-500/15 text-emerald-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{kol.activeClaimId ? '已关注' : '未关注'}</span>
                      </div>
                      <div className="text-[10px] text-slate-400 mt-0.5">{kol.platform} · {kol.followerLabel || '-'} followers</div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>
        <div className="flex items-center justify-between mt-4 pt-3 border-t border-white/[0.05]">
          <div className="text-[10.5px] text-slate-400">共 {visibleKols.length} 个可选 · 已选 {selectedKols.length}</div>
          <div className="flex items-center gap-2">
            <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={onClose} disabled={busy}>取消</button>
            <button
              className={`px-3.5 py-1.5 rounded-md text-[11px] font-medium ${selectedKols.length > 0 ? 'bg-purple-500 hover:bg-purple-400 text-white' : 'bg-white/[0.05] text-slate-600 cursor-not-allowed'}`}
              type="button"
              onClick={() => void submit()}
              disabled={busy || !selectedKols.length}
            >
              {busy ? '添加中' : `添加 ${selectedKols.length} 个 KOL`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function dateInputValue(value?: string) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

export function EditProjectModal({
  project,
  busy,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: { projectName?: string; productSku?: string; productName?: string; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
}) {
  const [projectName, setProjectName] = useState(project.campaign || '');
  const [productSku, setProductSku] = useState(project.productSku || '');
  const [productName, setProductName] = useState(project.productName || '');
  const [platform, setPlatform] = useState<string>(project.platform || 'Other');
  const [marketplace, setMarketplace] = useState(project.marketplace || '');
  const [priority, setPriority] = useState(project.priority || 'normal');
  const [shopifyLink, setShopifyLink] = useState(project.shopifyLink || '');
  const [targetPostDate, setTargetPostDate] = useState('');
  const [dueAt, setDueAt] = useState(dateInputValue(project.closedAt));
  const [error, setError] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const name = projectName.trim();
    if (!name) {
      setError('推广名称不能为空。');
      return;
    }
    if (shopifyLink.trim() && !/^https?:\/\//i.test(shopifyLink.trim())) {
      setError('Shopify 链接需要以 http:// 或 https:// 开头。');
      return;
    }
    setError('');
    await onSubmit({
      projectName: name,
      productSku: productSku.trim(),
      productName: productName.trim(),
      platform,
      marketplace: marketplace.trim(),
      priority,
      shopifyLink: shopifyLink.trim(),
      targetPostDate: targetPostDate || undefined,
      dueAt: dueAt || undefined,
      note: `编辑推广：${name}`,
    });
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-edit-modal" onSubmit={submit} role="dialog" aria-label="编辑项目" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>编辑推广</h3>
            <p>只更新项目基础资料；阶段推进、费用、物流和证据仍在详情里处理。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-campaign-edit-grid">
          <label className="is-full">推广名称
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="AF 35mm F1.2 LAB FE 上市推广" />
          </label>
          <label>产品 SKU
            <input value={productSku} onChange={(event) => setProductSku(event.target.value)} placeholder="VTX-35-LAB-FE" />
          </label>
          <label>产品名称
            <input value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="35mm F1.2 LAB FE" />
          </label>
          <label>平台
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
              {editPlatformOptions.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>市场 / 店铺
            <input value={marketplace} onChange={(event) => setMarketplace(event.target.value)} placeholder="US / Shopify / Amazon" />
          </label>
          <label>优先级
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="normal">normal</option>
              <option value="high">high</option>
              <option value="urgent">urgent</option>
              <option value="low">low</option>
            </select>
          </label>
          <label>目标发布日期
            <input type="date" value={targetPostDate} onChange={(event) => setTargetPostDate(event.target.value)} />
          </label>
          <label>计划结束
            <input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
          </label>
          <label className="is-full">Shopify 归因链接
            <input value={shopifyLink} onChange={(event) => setShopifyLink(event.target.value)} placeholder="https://your-store.myshopify.com/..." />
          </label>
        </div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy}>{busy ? '保存中' : '保存推广'}</button>
        </footer>
      </form>
    </div>
  );
}

export function TrackingWidget({
  row,
  tracking,
  saving,
  onChange,
  onSave,
}: {
  row: VkpiProjectRow;
  tracking: TrackingState;
  saving: boolean;
  onChange: (row: VkpiProjectRow, key: 'courier' | 'no', value: string) => void;
  onSave: (row: VkpiProjectRow) => void;
}) {
  return (
    <div className={`vkpi-campaign-tracking ${tracking.delivered ? 'is-delivered' : ''}`}>
      <span className={tracking.delivered ? 'is-green' : 'is-red'}>{tracking.delivered ? '已送达' : tracking.courier || '快递'}</span>
      <div>
        <div className="vkpi-campaign-track-form">
          <label>快递商
            <input value={tracking.courier} onChange={(event) => onChange(row, 'courier', event.target.value)} placeholder="SF / DHL / UPS" />
          </label>
          <label>快递单号
            <input value={tracking.no} onChange={(event) => onChange(row, 'no', event.target.value)} placeholder="输入 tracking no." />
          </label>
        </div>
        <strong>{tracking.status}</strong>
        {tracking.delivered ? <em>物流已送达，系统已加入今日提醒：请跟进 KOL 发布排期。</em> : null}
      </div>
      <div className="vkpi-campaign-track-actions">
        <small>状态 · 每日刷新待接入</small>
        <span>上次：{tracking.last}</span>
        <button type="button" disabled={saving} onClick={() => onSave(row)}>{saving ? '保存中' : '保存物流'}</button>
        <span className="vkpi-campaign-track-status" title="下次更新接入真实物流追踪">刷新功能待接入</span>
      </div>
    </div>
  );
}

export function ContactKolModal({
  row,
  onClose,
  onLogOutreach,
  onRequestDraft,
  onFetchDraft,
}: {
  row: VkpiProjectRow;
  onClose: () => void;
  onLogOutreach: (payload: { message: string; channel: string }) => Promise<void>;
  onRequestDraft: () => Promise<Record<string, unknown>>;
  onFetchDraft: () => Promise<{ state?: string; draft?: Record<string, unknown> }>;
}) {
  const [message, setMessage] = useState('');
  const [channel, setChannel] = useState('dm');
  const [busy, setBusy] = useState(false);
  const [draftState, setDraftState] = useState<'idle' | 'generating' | 'ready' | 'failed'>('idle');
  const [draftMeta, setDraftMeta] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    onFetchDraft().then((resp) => {
      if (cancelled || resp.state !== 'ready' || !resp.draft) return;
      setDraftMeta(resp.draft);
      setMessage((current) => current || String(resp.draft?.message_en || ''));
      setDraftState('ready');
    }).catch(() => { /* 无既有草稿属正常 */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const generateDraft = async () => {
    setDraftState('generating');
    setError('');
    try {
      await onRequestDraft();
      for (let i = 0; i < 24; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        const resp = await onFetchDraft();
        if (resp.state === 'ready' && resp.draft) {
          setDraftMeta(resp.draft);
          setMessage(String(resp.draft.message_en || ''));
          setDraftState('ready');
          return;
        }
      }
      setDraftState('failed');
      setError('草稿生成超时——看左侧泳道「联系草稿」状态,完成后重开本窗即见。');
    } catch (draftError) {
      setDraftState('failed');
      setError(draftError instanceof Error ? draftError.message : '草稿生成失败');
    }
  };

  const copyDraft = async () => {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('复制失败——请手动选中文本复制。');
    }
  };

  const logOutreach = async () => {
    if (!message.trim()) { setError('请先填写或生成消息内容。'); return; }
    setBusy(true);
    setError('');
    try {
      await onLogOutreach({ message: message.trim(), channel });
      onClose();
    } catch (logError) {
      setError(logError instanceof Error ? logError.message : '记录失败');
    } finally {
      setBusy(false);
    }
  };

  const talkingPoints = Array.isArray(draftMeta?.talking_points) ? (draftMeta?.talking_points as string[]) : [];
  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-xl p-5 max-h-[90vh] overflow-y-auto" role="dialog" aria-label="联系 KOL" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-white">联系 KOL</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">{row.kolHandle || row.kolName} · {row.platform} · 系统不代发——复制后到平台发送,这里留档沟通记录</p>
          </div>
          <button className="text-slate-500 hover:text-white" type="button" onClick={onClose} disabled={busy}><X size={16} /></button>
        </div>
        <div className="flex items-center gap-2 mb-2.5">
          <select className="px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300" value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="dm" style={{ background: '#0a0a0d' }}>平台私信</option>
            <option value="email" style={{ background: '#0a0a0d' }}>邮件</option>
            <option value="other" style={{ background: '#0a0a0d' }}>其他渠道</option>
          </select>
          <button
            className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-purple-500/15 border border-purple-500/35 text-purple-200 hover:bg-purple-500/25 disabled:opacity-50"
            type="button"
            onClick={() => void generateDraft()}
            disabled={draftState === 'generating'}
          >
            {draftState === 'generating' ? 'AI 草稿生成中…(泳道「联系草稿」可见)' : draftState === 'ready' ? '重新生成 AI 草稿' : 'AI 草稿(队列生成)'}
          </button>
          {draftMeta?.channel_suggestion ? <span className="text-[10px] text-cyan-300/80 truncate">{String(draftMeta.channel_suggestion)}</span> : null}
        </div>
        <textarea
          className="w-full h-44 px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40 resize-y"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="手写外联消息,或点上方「AI 草稿」生成英文私信(基于 KOL 资料与项目背景)…"
        />
        {draftMeta?.subject ? <div className="mt-1.5 text-[10.5px] text-slate-400">邮件主题:<span className="text-slate-200">{String(draftMeta.subject)}</span></div> : null}
        {draftMeta?.message_cn ? (
          <details className="mt-1.5">
            <summary className="text-[10.5px] text-slate-500 cursor-pointer">中文对照(内部审阅)</summary>
            <p className="text-[10.5px] text-slate-300 mt-1 whitespace-pre-wrap">{String(draftMeta.message_cn)}</p>
          </details>
        ) : null}
        {talkingPoints.length ? (
          <div className="mt-1.5 text-[10.5px] text-slate-400">后续要点:{talkingPoints.map((point, index) => <span key={index} className="inline-block mr-2 text-slate-300">· {point}</span>)}</div>
        ) : null}
        {error ? <div className="mt-2 text-[10.5px] text-rose-300">{error}</div> : null}
        <div className="flex items-center justify-between mt-3.5 pt-3 border-t border-white/[0.05]">
          <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={onClose} disabled={busy}>取消</button>
          <div className="flex items-center gap-2">
            <button className="px-3 py-1.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-[11px] text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50" type="button" onClick={() => void copyDraft()} disabled={!message.trim()}>
              {copied ? '已复制 ✓' : '复制内容'}
            </button>
            <button className="px-3.5 py-1.5 rounded-md text-[11px] font-medium bg-purple-500 hover:bg-purple-400 text-white disabled:opacity-50" type="button" onClick={() => void logOutreach()} disabled={busy || !message.trim()}>
              {busy ? '记录中' : '记录沟通(留档)'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function GenerateContractModal({
  project,
  rows,
  templates,
  partyA,
  busy,
  onClose,
  onSubmit,
  onDownload,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  templates: Array<{ key: string; label: string; title: string; slots: Array<{ key: string; label: string; group?: string; type?: string; options?: string[]; required?: boolean }> }>;
  partyA: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: { templateKey: string; fields: Record<string, string>; row: VkpiProjectRow | null }) => Promise<number | null>;
  onDownload?: (contractId: number) => void;
}) {
  const [templateKey, setTemplateKey] = useState(templates[0]?.key || 'paid');
  const [rowId, setRowId] = useState(rows[0]?.id || '');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [doneContractId, setDoneContractId] = useState<number | null>(null);
  const template = templates.find((item) => item.key === templateKey) || templates[0];
  const selectedRow = rows.find((item) => item.id === rowId) || null;

  useEffect(() => {
    // 切 KOL/模板时预填对方名与产品(时间/账户留空待填)
    setFields((current) => ({
      ...current,
      party_b_name: current.party_b_name || selectedRow?.kolName || selectedRow?.kolHandle || '',
      product: current.product || project.productName || project.campaign || '',
      product_models: current.product_models || project.productName || '',
      provided_products: current.provided_products || project.productName || '',
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowId, templateKey]);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof template.slots>();
    (template?.slots || []).forEach((slot) => {
      const group = slot.group || '其他';
      map.set(group, [...(map.get(group) || []), slot]);
    });
    return Array.from(map.entries());
  }, [template]);

  const submit = async () => {
    const missing = (template?.slots || []).filter((slot) => slot.required && !String(fields[slot.key] || '').trim());
    if (missing.length) {
      setError(`缺少必填:${missing.map((slot) => slot.label).join('、')}`);
      return;
    }
    setError('');
    try {
      const contractId = await onSubmit({ templateKey, fields, row: selectedRow });
      if (contractId) setDoneContractId(contractId);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '生成失败');
    }
  };

  if (doneContractId) {
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-2xl p-5 max-h-[92vh] flex flex-col" role="dialog" aria-label="生成合同" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-white">生成合同(模板填槽 · 法务文本冻结)</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">甲方:{partyA} · 生成 DOCX 自动归档并关联所选 KOL;同输入恒同输出,LLM 零参与正文</p>
          </div>
          <button className="text-slate-500 hover:text-white" type="button" onClick={onClose} disabled={busy}><X size={16} /></button>
        </div>
        <div className="flex items-center gap-2 mb-3">
          <select className="px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-200" value={templateKey} onChange={(event) => setTemplateKey(event.target.value)}>
            {templates.map((item) => <option key={item.key} value={item.key} style={{ background: '#0a0a0d' }}>{item.label}</option>)}
          </select>
          <select className="px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-200 flex-1" value={rowId} onChange={(event) => setRowId(event.target.value)}>
            <option value="" style={{ background: '#0a0a0d' }}>不关联 KOL(仅按表单)</option>
            {rows.map((item) => <option key={item.id} value={item.id} style={{ background: '#0a0a0d' }}>{item.kolHandle || item.kolName} · {item.platform}</option>)}
          </select>
        </div>
        <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-3">
          {grouped.map(([group, slots]) => (
            <div key={group}>
              <div className="text-[10.5px] text-slate-500 mb-1.5">{group}</div>
              <div className="grid grid-cols-2 gap-2">
                {slots.map((slot) => (
                  <label className={`text-[10.5px] text-slate-400 ${slot.type === 'multiline' ? 'col-span-2' : ''}`} key={slot.key}>
                    {slot.label}{slot.required ? <span className="text-rose-400"> *</span> : null}
                    {slot.type === 'choice' ? (
                      <select
                        className="mt-1 w-full px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white"
                        value={fields[slot.key] || ''}
                        onChange={(event) => setFields((current) => ({ ...current, [slot.key]: event.target.value }))}
                      >
                        <option value="" style={{ background: '#0a0a0d' }}>请选择</option>
                        {(slot.options || []).map((option) => <option key={option} value={option} style={{ background: '#0a0a0d' }}>{option}</option>)}
                      </select>
                    ) : slot.type === 'multiline' ? (
                      <textarea
                        className="mt-1 w-full h-16 px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white resize-y"
                        value={fields[slot.key] || ''}
                        onChange={(event) => setFields((current) => ({ ...current, [slot.key]: event.target.value }))}
                      />
                    ) : (
                      <input
                        className="mt-1 w-full px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white"
                        type={slot.type === 'date' ? 'date' : 'text'}
                        value={fields[slot.key] || ''}
                        onChange={(event) => setFields((current) => ({ ...current, [slot.key]: event.target.value }))}
                      />
                    )}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
        {error ? <div className="mt-2 text-[10.5px] text-rose-300">{error}</div> : null}
        <div className="flex items-center justify-between mt-3.5 pt-3 border-t border-white/[0.05]">
          <span className="text-[10px] text-slate-500">产出 DOCX 入「合同归档」;PDF 直出候依赖报备</span>
          <div className="flex items-center gap-2">
            <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={onClose} disabled={busy}>取消</button>
            <button className="px-3.5 py-1.5 rounded-md text-[11px] font-medium bg-purple-500 hover:bg-purple-400 text-white disabled:opacity-50" type="button" onClick={() => void submit()} disabled={busy}>
              {busy ? '生成中…' : '生成并归档'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
