import { useEffect, useState } from 'react';

interface ProjectEvidenceFormsProps {
  projectId: string;
  onAddMessage?: (payload: Record<string, unknown>) => Promise<void>;
  onAddContent?: (payload: Record<string, unknown>) => Promise<void>;
  onUpsertTerms?: (payload: Record<string, unknown>) => Promise<void>;
  onAddShipment?: (payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
}

export function ProjectEvidenceForms({
  projectId,
  onAddMessage,
  onAddContent,
  onUpsertTerms,
  onAddShipment,
  onUploadEvidenceFile,
}: ProjectEvidenceFormsProps) {
  const [formBusy, setFormBusy] = useState('');
  const [formError, setFormError] = useState('');
  const [messageForm, setMessageForm] = useState({ source: 'manual', direction: 'outbound', body: '', evidenceUrl: '' });
  const [contentForm, setContentForm] = useState({ postUrl: '', title: '', views: '', likes: '', comments: '', rightsStatus: 'unknown' });
  const [termsForm, setTermsForm] = useState({ cashFeeUsd: '', usageRights: '', sampleTerms: '', dueAt: '', note: '', deliverableType: 'video', deliverableQuantity: '1' });
  const [shipmentForm, setShipmentForm] = useState({ carrier: '', trackingNumber: '', productSku: '', serialNumber: '', shippingCostUsd: '', evidenceUrl: '', note: '' });
  const [messageEvidenceFile, setMessageEvidenceFile] = useState<File | null>(null);
  const [contentAssetFile, setContentAssetFile] = useState<File | null>(null);
  const [termsEvidenceFile, setTermsEvidenceFile] = useState<File | null>(null);
  const [shipmentEvidenceFile, setShipmentEvidenceFile] = useState<File | null>(null);
  const canWriteEvidence = Boolean(onAddMessage || onAddContent || onUpsertTerms || onAddShipment);

  useEffect(() => {
    setFormError('');
    setFormBusy('');
  }, [projectId]);

  const runEvidenceAction = async (kind: string, action: (() => Promise<void>) | undefined) => {
    if (!action) {
      setFormError('当前账号没有该证据写入权限。');
      return;
    }
    setFormError('');
    setFormBusy(kind);
    try {
      await action();
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : '保存失败');
    } finally {
      setFormBusy('');
    }
  };

  const uploadEvidence = async (file: File | null, purpose: string): Promise<string> => {
    if (!file) return '';
    if (!onUploadEvidenceFile) {
      throw new Error('当前页面没有附件上传接口。');
    }
    const result = await onUploadEvidenceFile(file, { entityType: 'project', entityId: projectId, purpose });
    return String(result.file_url || result.fileUrl || result.url || '');
  };

  return (
    <section className="vkpi-detail-forms" aria-label="项目证据写入">
      <div className="vkpi-detail-forms__header">
        <strong>补充项目证据</strong>
        <span>{canWriteEvidence ? '写入后自动刷新项目详情' : '当前账号只读'}</span>
      </div>
      {formError ? <div className="vkpi-inline-message is-error">{formError}</div> : null}
      <form className="vkpi-form-stack" onSubmit={(event) => {
        event.preventDefault();
        const body = messageForm.body.trim();
        void runEvidenceAction('message', body ? async () => {
          const uploadedUrl = await uploadEvidence(messageEvidenceFile, 'message_evidence');
          await onAddMessage?.({
            source: messageForm.source,
            direction: messageForm.direction,
            body,
            snippet: body.slice(0, 240),
            evidence_url: uploadedUrl || messageForm.evidenceUrl.trim(),
          });
          setMessageForm((current) => ({ ...current, body: '', evidenceUrl: '' }));
          setMessageEvidenceFile(null);
        } : undefined);
      }}>
        <h4>消息记录</h4>
        <div className="vkpi-form-grid">
          <label>来源<select value={messageForm.source} onChange={(event) => setMessageForm((current) => ({ ...current, source: event.target.value }))}><option value="manual">手动记录</option><option value="email">Email</option><option value="dm">DM</option><option value="comment">评论</option><option value="import">导入</option></select></label>
          <label>方向<select value={messageForm.direction} onChange={(event) => setMessageForm((current) => ({ ...current, direction: event.target.value }))}><option value="outbound">我方发出</option><option value="inbound">红人回复</option></select></label>
        </div>
        <textarea value={messageForm.body} onChange={(event) => setMessageForm((current) => ({ ...current, body: event.target.value }))} placeholder="粘贴沟通要点，例如报价、回复、交付承诺。" />
        <input value={messageForm.evidenceUrl} onChange={(event) => setMessageForm((current) => ({ ...current, evidenceUrl: event.target.value }))} placeholder="证据链接 / 邮件链接 / 截图地址（可选）" />
        <label className="vkpi-upload-row">消息附件 / PDF / 截图<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setMessageEvidenceFile(event.target.files?.[0] || null)} /></label>
        <button className="vkpi-mini-button" type="submit" disabled={!onAddMessage || formBusy === 'message' || !messageForm.body.trim()}>{formBusy === 'message' ? '保存中...' : '保存消息'}</button>
      </form>
      <form className="vkpi-form-stack" onSubmit={(event) => {
        event.preventDefault();
        const postUrl = contentForm.postUrl.trim();
        void runEvidenceAction('content', postUrl ? async () => {
          const uploadedUrl = await uploadEvidence(contentAssetFile, 'content_asset');
          await onAddContent?.({
            post_url: postUrl,
            title: contentForm.title.trim(),
            views: Number(contentForm.views || 0),
            likes: Number(contentForm.likes || 0),
            comments: Number(contentForm.comments || 0),
            rights_status: contentForm.rightsStatus,
            asset_url: uploadedUrl || undefined,
            asset_type: contentAssetFile ? 'uploaded_asset' : undefined,
          });
          setContentForm((current) => ({ ...current, postUrl: '', title: '', views: '', likes: '', comments: '' }));
          setContentAssetFile(null);
        } : undefined);
      }}>
        <h4>发布内容</h4>
        <input value={contentForm.postUrl} onChange={(event) => setContentForm((current) => ({ ...current, postUrl: event.target.value }))} placeholder="内容 URL，例如 YouTube / Instagram / TikTok 链接" />
        <input value={contentForm.title} onChange={(event) => setContentForm((current) => ({ ...current, title: event.target.value }))} placeholder="内容标题 / 简短描述" />
        <div className="vkpi-form-grid">
          <input value={contentForm.views} onChange={(event) => setContentForm((current) => ({ ...current, views: event.target.value }))} placeholder="播放量" inputMode="numeric" />
          <input value={contentForm.likes} onChange={(event) => setContentForm((current) => ({ ...current, likes: event.target.value }))} placeholder="点赞" inputMode="numeric" />
          <input value={contentForm.comments} onChange={(event) => setContentForm((current) => ({ ...current, comments: event.target.value }))} placeholder="评论" inputMode="numeric" />
          <select value={contentForm.rightsStatus} onChange={(event) => setContentForm((current) => ({ ...current, rightsStatus: event.target.value }))}><option value="unknown">权限待确认</option><option value="organic_only">仅自然转发</option><option value="ad_allowed">可广告投放</option><option value="blocked">不可二次使用</option></select>
        </div>
        <label className="vkpi-upload-row">内容素材 / 截图 / PDF<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.mp4,.mov,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setContentAssetFile(event.target.files?.[0] || null)} /></label>
        <button className="vkpi-mini-button" type="submit" disabled={!onAddContent || formBusy === 'content' || !contentForm.postUrl.trim()}>{formBusy === 'content' ? '保存中...' : '保存内容'}</button>
      </form>
      <form className="vkpi-form-stack" onSubmit={(event) => {
        event.preventDefault();
        void runEvidenceAction('terms', async () => {
          const uploadedUrl = await uploadEvidence(termsEvidenceFile, 'terms_evidence');
          const note = [termsForm.note.trim(), uploadedUrl ? `附件：${uploadedUrl}` : ''].filter(Boolean).join('\n');
          await onUpsertTerms?.({
            cash_fee_usd: Number(termsForm.cashFeeUsd || 0),
            usage_rights: termsForm.usageRights.trim(),
            sample_terms: termsForm.sampleTerms.trim(),
            due_at: termsForm.dueAt || undefined,
            evidence_url: uploadedUrl || undefined,
            note,
            deliverables: termsForm.deliverableType.trim() ? [{
              deliverable_type: termsForm.deliverableType.trim(),
              quantity: Number(termsForm.deliverableQuantity || 1),
              due_at: termsForm.dueAt || undefined,
              evidence_url: uploadedUrl || undefined,
            }] : [],
          });
          setTermsEvidenceFile(null);
        });
      }}>
        <h4>合作条款</h4>
        <div className="vkpi-form-grid">
          <input value={termsForm.cashFeeUsd} onChange={(event) => setTermsForm((current) => ({ ...current, cashFeeUsd: event.target.value }))} placeholder="现金推广费 USD（可选）" inputMode="decimal" />
          <input value={termsForm.usageRights} onChange={(event) => setTermsForm((current) => ({ ...current, usageRights: event.target.value }))} placeholder="使用权，例如 30 天广告授权" />
          <input value={termsForm.deliverableType} onChange={(event) => setTermsForm((current) => ({ ...current, deliverableType: event.target.value }))} placeholder="交付类型，例如 video / reel" />
          <input value={termsForm.deliverableQuantity} onChange={(event) => setTermsForm((current) => ({ ...current, deliverableQuantity: event.target.value }))} placeholder="交付数量" inputMode="numeric" />
        </div>
        <input value={termsForm.sampleTerms} onChange={(event) => setTermsForm((current) => ({ ...current, sampleTerms: event.target.value }))} placeholder="样品条款，例如不退回 / 需退回 / 评测后保留" />
        <input value={termsForm.dueAt} onChange={(event) => setTermsForm((current) => ({ ...current, dueAt: event.target.value }))} placeholder="预计交付时间 ISO 或日期" />
        <input value={termsForm.note} onChange={(event) => setTermsForm((current) => ({ ...current, note: event.target.value }))} placeholder="条款备注" />
        <label className="vkpi-upload-row">条款附件 / PDF / 报价单<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setTermsEvidenceFile(event.target.files?.[0] || null)} /></label>
        <button className="vkpi-mini-button" type="submit" disabled={!onUpsertTerms || formBusy === 'terms'}>{formBusy === 'terms' ? '保存中...' : '保存条款'}</button>
      </form>
      <form className="vkpi-form-stack" onSubmit={(event) => {
        event.preventDefault();
        const tracking = shipmentForm.trackingNumber.trim();
        void runEvidenceAction('shipment', tracking || shipmentForm.carrier.trim() ? async () => {
          const uploadedUrl = await uploadEvidence(shipmentEvidenceFile, 'shipment_evidence');
          await onAddShipment?.({
            carrier: shipmentForm.carrier.trim(),
            tracking_number: tracking,
            product_sku: shipmentForm.productSku.trim(),
            serial_number: shipmentForm.serialNumber.trim(),
            shipping_cost_usd: Number(shipmentForm.shippingCostUsd || 0),
            evidence_url: uploadedUrl || shipmentForm.evidenceUrl.trim(),
            note: shipmentForm.note.trim(),
          });
          setShipmentForm((current) => ({ ...current, carrier: '', trackingNumber: '', serialNumber: '', shippingCostUsd: '', evidenceUrl: '', note: '' }));
          setShipmentEvidenceFile(null);
        } : undefined);
      }}>
        <h4>物流 / 样品</h4>
        <div className="vkpi-form-grid">
          <input value={shipmentForm.carrier} onChange={(event) => setShipmentForm((current) => ({ ...current, carrier: event.target.value }))} placeholder="快递公司，例如 DHL / FedEx" />
          <input value={shipmentForm.trackingNumber} onChange={(event) => setShipmentForm((current) => ({ ...current, trackingNumber: event.target.value }))} placeholder="物流单号" />
          <input value={shipmentForm.productSku} onChange={(event) => setShipmentForm((current) => ({ ...current, productSku: event.target.value }))} placeholder="SKU（可选，默认项目 SKU）" />
          <input value={shipmentForm.shippingCostUsd} onChange={(event) => setShipmentForm((current) => ({ ...current, shippingCostUsd: event.target.value }))} placeholder="快递费 USD" inputMode="decimal" />
        </div>
        <input value={shipmentForm.serialNumber} onChange={(event) => setShipmentForm((current) => ({ ...current, serialNumber: event.target.value }))} placeholder="样品序列号（可选）" />
        <input value={shipmentForm.evidenceUrl} onChange={(event) => setShipmentForm((current) => ({ ...current, evidenceUrl: event.target.value }))} placeholder="物流凭证链接（可选）" />
        <label className="vkpi-upload-row">物流凭证 / PDF / 截图<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls,.txt,.doc,.docx" onChange={(event) => setShipmentEvidenceFile(event.target.files?.[0] || null)} /></label>
        <input value={shipmentForm.note} onChange={(event) => setShipmentForm((current) => ({ ...current, note: event.target.value }))} placeholder="物流备注" />
        <button className="vkpi-mini-button" type="submit" disabled={!onAddShipment || formBusy === 'shipment' || !(shipmentForm.trackingNumber.trim() || shipmentForm.carrier.trim())}>{formBusy === 'shipment' ? '保存中...' : '保存物流'}</button>
      </form>
    </section>
  );
}
