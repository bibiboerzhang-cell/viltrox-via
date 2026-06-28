import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Search, X } from 'lucide-react';
import type { VkpiKolOption, VkpiProjectRow } from '../../vkpiTypes';
import { editPlatformOptions } from '../../../../domains/projects';
import { uploadMarketingEvidenceFile } from '../../../../services/vkpi/evidence-api';
import {
  enqueueContractPolish,
  enqueueInvoiceExtract,
  getContractPolish,
  getInvoiceExtract,
} from '../../../../services/vkpi/projects-api';
import {
  dateInputValue,
  INVOICE_FEE_SLOTS,
  INVOICE_SLOT_MAP,
  type PolishPreviewItem,
} from './ProjectDetailModals.helpers';

// 独立 modal 组件已抽到 sibling,集中 re-export 保持原模块对外 API 不变。
export {
  CostEntryModal,
  ContractUploadModal,
  ShippingInfoModal,
  StageActionModal,
  UploadScreenshotModal,
  VideoUrlModal,
} from './ProjectDetailModals.Sections';
export { dateInputValue } from './ProjectDetailModals.helpers';

export function AddKolModal({
  project,
  rows,
  kolOptions,
  busy,
  loading = false,
  loadError = '',
  scope = 'favorites',
  onSwitchScope,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  kolOptions: VkpiKolOption[];
  busy: boolean;
  loading?: boolean;
  loadError?: string;
  scope?: 'favorites' | 'all';
  onSwitchScope?: (scope: 'favorites' | 'all') => void;
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
  // 乙案①(独占体制·项目维):后端 available 接口仅对「被他人认领/在役跟进」的候选行下发
  // claim_staff_id/active_claim_id(buildKolOptions 映射 claimStaffId/activeClaimId/claimOwner),
  // 本人占用不下发——故此处有值即「已被他人跟进」,灰态不可勾。
  const isClaimedByOther = (kol: VkpiKolOption) => Boolean(kol.claimStaffId || kol.activeClaimId);
  const filteredKols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return kolOptions.filter((kol) => {
      const normalizedHandle = String(kol.handle || '').toLowerCase();
      const joined = joinedIds.has(kol.id) || joinedHandles.has(normalizedHandle);
      const matchesQuery = !normalizedQuery || [kol.name, kol.handle, kol.platform, kol.followerLabel].some((value) => String(value || '').toLowerCase().includes(normalizedQuery));
      const matchesPlatform = platform === '全部平台' || kol.platform === platform;
      const matchesClaim = claimFilter === '全部' || (claimFilter === '已被跟进' ? isClaimedByOther(kol) : !isClaimedByOther(kol));
      return !joined && matchesQuery && matchesPlatform && matchesClaim;
    });
  }, [claimFilter, joinedHandles, joinedIds, kolOptions, platform, query]);
  // 诊断 P1-7 前端半:截断显形,不再静默丢弃。
  const RENDER_CAP = 80;
  const visibleKols = useMemo(() => filteredKols.slice(0, RENDER_CAP), [filteredKols]);
  const truncatedCount = Math.max(0, filteredKols.length - visibleKols.length);
  const selectedKols = useMemo(
    () => kolOptions.filter((kol) => selectedIds.has(kol.id) && !isClaimedByOther(kol)),
    [kolOptions, selectedIds],
  );

  const toggleKol = (kolId: string) => {
    const target = kolOptions.find((kol) => kol.id === kolId);
    if (target && isClaimedByOther(target)) return;
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
            <option style={{ background: '#0a0a0d' }}>可添加</option>
            <option style={{ background: '#0a0a0d' }}>已被跟进</option>
          </select>
        </div>
        {onSwitchScope ? (
          <div className="flex items-center justify-between mb-2 px-0.5">
            <span className="text-[10px] text-slate-400">
              {scope === 'favorites'
                ? '范围:我的收藏(跟进归宿)'
                : '范围:全池查找 · 选中者将自动加入我的收藏'}
            </span>
            <button
              type="button"
              className="text-[10px] px-2 py-0.5 rounded border border-white/[0.1] text-cyan-300 hover:text-cyan-200 hover:border-cyan-400/40 disabled:opacity-50"
              disabled={busy}
              onClick={() => onSwitchScope(scope === 'favorites' ? 'all' : 'favorites')}
            >
              {scope === 'favorites' ? '从全池查找 →' : '← 回到我的收藏'}
            </button>
          </div>
        ) : null}
        <div className="flex-1 overflow-y-auto -mx-1 px-1 max-h-[50vh]">
          {loadError ? (
            <div className="text-center py-8 text-[11px] text-rose-300">候选加载失败：{loadError} —— 关闭重开重试；若提示权限(scope)请重新登录或联系管理员。</div>
          ) : loading ? (
            <div className="text-center py-8 text-[11px] text-slate-500">候选 KOL 加载中…</div>
          ) : visibleKols.length === 0 ? (
            <div className="text-center py-8 text-[11px] text-slate-500">
              {scope === 'favorites' && onSwitchScope
                ? '我的收藏里没有可加入的 KOL ——试试右上「从全池查找」'
                : '没有匹配的 KOL，或全部已加入'}
            </div>
          ) : (
            <div className="space-y-1.5">
              {visibleKols.map((kol) => {
                const claimedByOther = isClaimedByOther(kol);
                const selected = selectedIds.has(kol.id) && !claimedByOther;
                return (
                  <label
                    className={`flex items-center gap-3 px-3 py-2 rounded ${claimedByOther ? 'cursor-not-allowed opacity-55 border border-white/[0.04]' : `cursor-pointer ${selected ? 'bg-purple-500/10 border border-purple-500/30' : 'border border-white/[0.04] hover:bg-white/[0.02]'}`}`}
                    key={kol.id}
                  >
                    <input checked={selected} className="accent-purple-500 disabled:opacity-40" disabled={claimedByOther} type="checkbox" onChange={() => toggleKol(kol.id)} />
                    <div className="w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#06b6d4)' }}>{kol.name.charAt(0).toUpperCase()}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="text-[12px] text-white font-medium truncate">{kol.name}</div>
                        <span className="text-[9.5px] text-slate-500 truncate">{kol.handle || '-'}</span>
                        {claimedByOther ? (
                          <span className="text-[9.5px] px-1.5 py-0.5 rounded font-medium bg-amber-500/15 text-amber-300 truncate">已被 {kol.claimOwner || '他人'} 跟进</span>
                        ) : (
                          <span className="text-[9.5px] px-1.5 py-0.5 rounded font-medium bg-cyan-500/15 text-cyan-300">可添加</span>
                        )}
                      </div>
                      <div className="text-[10px] text-slate-400 mt-0.5">{kol.platform} · {kol.followerLabel || '-'} followers</div>
                    </div>
                  </label>
                );
              })}
              {truncatedCount > 0 ? (
                <div className="text-center py-2 text-[10px] text-slate-500">
                  仅显示前 {RENDER_CAP} 个,另有 {truncatedCount} 个未列出——请用搜索缩小范围
                </div>
              ) : null}
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
  // kolPoolId 非正整数时父层不传草稿入口(无 Pool 主体没法入队);modal 自行降级隐藏 AI 草稿。
  onRequestDraft?: () => Promise<Record<string, unknown>>;
  onFetchDraft?: () => Promise<{ state?: string; draft?: Record<string, unknown> }>;
}) {
  const [message, setMessage] = useState('');
  const [channel, setChannel] = useState('dm');
  const [busy, setBusy] = useState(false);
  const [draftState, setDraftState] = useState<'idle' | 'generating' | 'ready' | 'failed'>('idle');
  const [draftMeta, setDraftMeta] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  // 轮询取消位:关闭/卸载后置位,正在跑的 5s×24 轮询立即停手,不再对已卸载组件 setState。
  const cancelledRef = useRef(false);

  const handleClose = () => {
    cancelledRef.current = true;
    onClose();
  };

  useEffect(() => {
    if (!onFetchDraft) return () => { cancelledRef.current = true; };
    onFetchDraft().then((resp) => {
      if (cancelledRef.current || resp.state !== 'ready' || !resp.draft) return;
      setDraftMeta(resp.draft);
      setMessage((current) => current || String(resp.draft?.message_en || ''));
      setDraftState('ready');
    }).catch((fetchError) => {
      if (cancelledRef.current) return;
      // 只吞 404(无既有草稿属正常);其他错误如实提示,不再静默。
      const text = fetchError instanceof Error ? fetchError.message : String(fetchError ?? '');
      if (/404|not found|不存在/i.test(text)) return;
      setError(text || '草稿预取失败');
    });
    return () => { cancelledRef.current = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const canDraft = Boolean(onRequestDraft && onFetchDraft);

  const generateDraft = async () => {
    if (!onRequestDraft || !onFetchDraft) return;
    setDraftState('generating');
    setError('');
    try {
      await onRequestDraft();
      for (let i = 0; i < 24; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        if (cancelledRef.current) return;
        const resp = await onFetchDraft();
        if (cancelledRef.current) return;
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
      if (cancelledRef.current) return;
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
      handleClose();
    } catch (logError) {
      setError(logError instanceof Error ? logError.message : '记录失败');
    } finally {
      setBusy(false);
    }
  };

  const talkingPoints = Array.isArray(draftMeta?.talking_points) ? (draftMeta?.talking_points as string[]) : [];
  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : handleClose}>
      <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-xl p-5 max-h-[90vh] overflow-y-auto" role="dialog" aria-label="联系 KOL" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-white">联系 KOL</h3>
            <p className="text-[10.5px] text-slate-500 mt-0.5">{row.kolHandle || row.kolName} · {row.platform} · 系统不代发——复制后到平台发送,这里留档沟通记录</p>
          </div>
          <button className="text-slate-500 hover:text-white" type="button" onClick={handleClose} disabled={busy}><X size={16} /></button>
        </div>
        <div className="flex items-center gap-2 mb-2.5">
          <select className="px-2.5 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300" value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="dm" style={{ background: '#0a0a0d' }}>平台私信</option>
            <option value="email" style={{ background: '#0a0a0d' }}>邮件</option>
            <option value="other" style={{ background: '#0a0a0d' }}>其他渠道</option>
          </select>
          {canDraft ? (
            <button
              className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-purple-500/15 border border-purple-500/35 text-purple-200 hover:bg-purple-500/25 disabled:opacity-50"
              type="button"
              onClick={() => void generateDraft()}
              disabled={draftState === 'generating'}
            >
              {draftState === 'generating' ? 'AI 草稿生成中…(泳道「联系草稿」可见)' : draftState === 'ready' ? '重新生成 AI 草稿' : 'AI 草稿(队列生成)'}
            </button>
          ) : (
            <span className="text-[10px] text-slate-500">该 KOL 缺少有效 Pool ID,AI 草稿不可用——可手写消息后留档。</span>
          )}
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
          <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={handleClose} disabled={busy}>取消</button>
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
  apiToken,
  onClose,
  onSubmit,
  onDownload,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  templates: Array<{ key: string; label: string; title: string; slots: Array<{ key: string; label: string; group?: string; type?: string; options?: string[]; required?: boolean }> }>;
  partyA: string;
  busy: boolean;
  apiToken?: string;
  onClose: () => void;
  onSubmit: (payload: { templateKey: string; fields: Record<string, string>; row: VkpiProjectRow | null }) => Promise<number | null>;
  onDownload?: (contractId: number) => void;
}) {
  const [templateKey, setTemplateKey] = useState(templates[0]?.key || 'paid');
  const [rowId, setRowId] = useState(rows[0]?.id || '');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [doneContractId, setDoneContractId] = useState<number | null>(null);
  // 发票回填(LLM 经 apify_jobs 队列;失败/超时如实显示,绝不静默)。
  const [invoiceState, setInvoiceState] = useState<'idle' | 'uploading' | 'extracting' | 'done' | 'failed'>('idle');
  const [invoiceError, setInvoiceError] = useState('');
  const [invoiceFilledKeys, setInvoiceFilledKeys] = useState<Set<string>>(() => new Set());
  // AI 润色(整组文本槽;差异预览人工「应用」后才写回)。
  const [polishState, setPolishState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [polishError, setPolishError] = useState('');
  const [polishPreview, setPolishPreview] = useState<PolishPreviewItem[] | null>(null);
  const invoiceFileRef = useRef<HTMLInputElement | null>(null);
  // 轮询取消位:关闭/卸载后正在跑的 5s 轮询立即停手,不再对已卸载组件 setState。
  const cancelledRef = useRef(false);
  useEffect(() => () => { cancelledRef.current = true; }, []);
  const template = templates.find((item) => item.key === templateKey) || templates[0];
  const selectedRow = rows.find((item) => item.id === rowId) || null;

  useEffect(() => {
    // 切 KOL 时乙方名无条件跟随所选 KOL(全盘扫描 P0:`||` 短路致切换后仍是上一个 KOL,
    // 会生成主体写错的法务文本);产品类字段保留手改值。
    setFields((current) => ({
      ...current,
      party_b_name: selectedRow?.kolName || selectedRow?.kolHandle || current.party_b_name || '',
      product: current.product || project.productName || project.campaign || '',
      product_models: current.product_models || project.productName || '',
      provided_products: current.provided_products || project.productName || '',
    }));
    // 模板/KOL 切换后旧的发票高亮不再对应当前槽位,一并清掉。
    setInvoiceFilledKeys(new Set());
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

  // 手改即视为人工接管:写值并摘掉「来自发票」高亮。
  const setSlotValue = (key: string, value: string) => {
    setFields((current) => ({ ...current, [key]: value }));
    setInvoiceFilledKeys((current) => {
      if (!current.has(key)) return current;
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  };

  const applyInvoiceResult = (result: Record<string, unknown>) => {
    const slotKeys = new Set((template?.slots || []).map((slot) => slot.key));
    const filled = new Set<string>();
    setFields((current) => {
      const next = { ...current };
      INVOICE_SLOT_MAP.forEach(([source, targets]) => {
        const value = String(result[source] ?? '').trim();
        if (!value) return;
        targets.forEach((target) => {
          if (!slotKeys.has(target)) return;
          next[target] = value;
          filled.add(target);
        });
      });
      const amount = String(result.amount ?? '').trim();
      if (amount) {
        const feeSlot = INVOICE_FEE_SLOTS.find((key) => slotKeys.has(key));
        if (feeSlot && !String(next[feeSlot] || '').trim()) {
          next[feeSlot] = amount;
          filled.add(feeSlot);
        }
      }
      return next;
    });
    setInvoiceFilledKeys(filled);
    if (!filled.size) setInvoiceError('发票解析完成,但没有能回填到当前模板的字段——请手动填写。');
  };

  const runInvoiceExtract = async (file?: File | null) => {
    if (!file) return;
    if (!apiToken) {
      setInvoiceError('缺少 API token,不能上传发票。');
      setInvoiceState('failed');
      return;
    }
    if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) {
      setInvoiceError('只支持 PDF / PNG / JPG 发票文件。');
      setInvoiceState('failed');
      return;
    }
    setInvoiceError('');
    setInvoiceFilledKeys(new Set());
    setInvoiceState('uploading');
    try {
      const uploaded = await uploadMarketingEvidenceFile(apiToken, file, { entityType: 'project', entityId: String(project.id), purpose: 'invoice' });
      if (cancelledRef.current) return;
      const fileUrl = String((uploaded as Record<string, unknown>).file_url || '');
      if (!fileUrl) throw new Error('发票已上传但未返回文件 URL,无法发起提取。');
      const enq = await enqueueInvoiceExtract(apiToken, String(project.id), fileUrl);
      if (cancelledRef.current) return;
      const extractKey = String(enq.extract_key || enq.key || '');
      if (!extractKey) throw new Error('发票提取入队失败:后端未返回 extract_key。');
      setInvoiceState('extracting');
      // 5s × 18 = 90s 封顶;超时如实说,不假装成功。
      for (let i = 0; i < 18; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        if (cancelledRef.current) return;
        const resp = await getInvoiceExtract(apiToken, extractKey);
        if (cancelledRef.current) return;
        const state = String(resp.state || resp.status || '');
        if (state === 'ready') {
          applyInvoiceResult((resp.result || {}) as Record<string, unknown>);
          setInvoiceState('done');
          return;
        }
        if (state === 'failed') throw new Error(String(resp.error || '发票提取失败(LLM 未产出结果)。'));
      }
      throw new Error('发票提取超时(90 秒)——任务仍在队列(泳道「发票提取」可见),完成后重开本窗再试。');
    } catch (extractError) {
      if (cancelledRef.current) return;
      setInvoiceState('failed');
      setInvoiceError(extractError instanceof Error ? extractError.message : '发票回填失败');
    }
  };

  // 文本类槽(非 choice/date)整组送润色;只送有内容的字段。
  const polishableFields = () => {
    const textFields: Record<string, string> = {};
    (template?.slots || []).forEach((slot) => {
      if (slot.type === 'choice' || slot.type === 'date') return;
      const value = String(fields[slot.key] || '').trim();
      if (value) textFields[slot.key] = value;
    });
    return textFields;
  };

  const runPolish = async () => {
    if (!apiToken) {
      setPolishError('缺少 API token,不能发起润色。');
      setPolishState('failed');
      return;
    }
    const textFields = polishableFields();
    if (!Object.keys(textFields).length) {
      setPolishError('请先填写文本字段,再发起整组润色。');
      setPolishState('failed');
      return;
    }
    setPolishError('');
    setPolishState('running');
    try {
      const enq = await enqueueContractPolish(apiToken, String(project.id), templateKey, textFields);
      if (cancelledRef.current) return;
      const polishKey = String(enq.polish_key || enq.key || '');
      if (!polishKey) throw new Error('润色任务入队失败:后端未返回 polish_key。');
      for (let i = 0; i < 18; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        if (cancelledRef.current) return;
        const resp = await getContractPolish(apiToken, polishKey);
        if (cancelledRef.current) return;
        const state = String(resp.state || resp.status || '');
        if (state === 'ready') {
          const raw = (resp.result || {}) as Record<string, unknown>;
          const polishedFields = (raw.fields && typeof raw.fields === 'object' && !Array.isArray(raw.fields)
            ? raw.fields
            : raw) as Record<string, unknown>;
          const items: PolishPreviewItem[] = Object.entries(textFields)
            .map(([key, original]) => ({
              key,
              label: (template?.slots || []).find((slot) => slot.key === key)?.label || key,
              original,
              polished: String(polishedFields[key] ?? '').trim(),
            }))
            .filter((item) => item.polished && item.polished !== item.original);
          if (!items.length) {
            setPolishState('idle');
            setPolishError('润色完成:LLM 没有给出与原文不同的改写,表单未变。');
            return;
          }
          setPolishPreview(items);
          setPolishState('idle');
          return;
        }
        if (state === 'failed') throw new Error(String(resp.error || '润色失败(LLM 未产出结果)。'));
      }
      throw new Error('润色超时(90 秒)——任务仍在队列(泳道可见),稍后可重试。');
    } catch (polishRunError) {
      if (cancelledRef.current) return;
      setPolishState('failed');
      setPolishError(polishRunError instanceof Error ? polishRunError.message : 'AI 润色失败');
    }
  };

  const applyPolish = () => {
    if (!polishPreview) return;
    setFields((current) => {
      const next = { ...current };
      polishPreview.forEach((item) => { next[item.key] = item.polished; });
      return next;
    });
    setPolishPreview(null);
  };

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
        <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] px-3 py-2 mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <input
              ref={invoiceFileRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              className="hidden"
              onChange={(event) => { void runInvoiceExtract(event.target.files?.[0]); event.target.value = ''; }}
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
              onClick={() => void runPolish()}
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
        <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-3">
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

        {polishPreview ? (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" role="presentation" onClick={() => setPolishPreview(null)}>
            <div className="rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-2xl p-5 max-h-[85vh] flex flex-col" role="dialog" aria-label="AI 润色差异预览" onClick={(event) => event.stopPropagation()}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-[14px] font-semibold text-white">AI 润色差异预览</h3>
                  <p className="text-[10.5px] text-slate-500 mt-0.5">LLM 产物 · 请人工确认——点「应用」才写回表单,放弃则原文不动</p>
                </div>
                <button className="text-slate-500 hover:text-white" type="button" onClick={() => setPolishPreview(null)}><X size={16} /></button>
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
                <button className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" onClick={() => setPolishPreview(null)}>放弃改写</button>
                <button className="px-3.5 py-1.5 rounded-md text-[11px] font-medium bg-emerald-500/90 hover:bg-emerald-500 text-white" type="button" onClick={applyPolish}>
                  应用 {polishPreview.length} 处改写
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
