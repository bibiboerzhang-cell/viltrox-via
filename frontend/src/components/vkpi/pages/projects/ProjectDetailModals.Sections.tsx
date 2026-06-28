// 从 ProjectDetailModals.tsx 抽出的独立展示型 modal 组件(函数体/JSX 逐字不变搬运)。
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowLeft, DollarSign, FileText, ImageIcon, Package, Sparkles, Upload, Video, X } from 'lucide-react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import type { ScreenshotTarget } from '../../../../domains/projects';
import { filePayload, type CostEntryType } from './ProjectDetailModals.helpers';

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
