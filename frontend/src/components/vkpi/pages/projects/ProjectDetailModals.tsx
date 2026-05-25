import { useMemo, useState, type FormEvent } from 'react';
import type { VkpiKolOption, VkpiProjectRow } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { stageLabels } from '../../shared/vkpiConstants';
import {
  editPlatformOptions,
  formatNumber,
  stageIndex,
  type ScreenshotTarget,
  type TrackingState,
} from './projectDetailModel';

export function UploadScreenshotModal({
  target,
  onClose,
  onSubmit,
}: {
  target: ScreenshotTarget;
  onClose: () => void;
  onSubmit: (file: File, note: string) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const chooseFile = (nextFile?: File | null) => {
    setError('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    const isImage = ['image/png', 'image/jpeg'].includes(nextFile.type);
    if (!isImage) {
      setError('只支持 PNG / JPG 截图。');
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
      await onSubmit(file, note);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '截图上传失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal" onSubmit={submit} role="dialog" aria-label="上传阶段截图" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>上传阶段截图</h3>
            <p>{target.row.kolHandle || target.row.kolName} · {target.from}→{target.to} {stageLabels[target.stage]}</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <label
          className="vkpi-campaign-drop-zone"
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            chooseFile(event.dataTransfer.files?.[0]);
          }}
        >
          <input type="file" accept="image/png,image/jpeg" onChange={(event) => chooseFile(event.target.files?.[0])} />
          <strong>{file ? file.name : '拖拽 PNG/JPG 到这里，或点击选择'}</strong>
          <span>最大 10MB，保存后会进入现有 evidence upload 接口。</span>
        </label>
        <label className="vkpi-campaign-upload-note">备注
          <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：邮件截图 / 发货截图 / 发布截图" />
        </label>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !file}>{busy ? '上传中' : '保存截图'}</button>
        </footer>
      </form>
    </div>
  );
}

export function AddKolModal({
  project,
  rows,
  kolOptions,
  busy,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  kolOptions: VkpiKolOption[];
  busy: boolean;
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
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="vkpi-campaign-add-kol-modal" role="dialog" aria-label="添加 KOL" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>添加 KOL</h3>
            <p>追加到「{project.campaign || '当前推广'}」，提交后写入现有项目接口。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-campaign-add-kol-tools">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 KOL / handle" />
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option>全部平台</option>
            {platformOptions.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={claimFilter} onChange={(event) => setClaimFilter(event.target.value)}>
            <option>全部</option>
            <option>已关注</option>
            <option>未关注</option>
          </select>
        </div>
        <div className="vkpi-campaign-add-kol-list">
          {visibleKols.map((kol) => {
            const selected = selectedIds.has(kol.id);
            return (
              <button
                key={kol.id}
                type="button"
                className={selected ? 'is-selected' : ''}
                onClick={() => toggleKol(kol.id)}
              >
                <span className="vkpi-campaign-check">{selected ? '✓' : ''}</span>
                <Avatar name={kol.name} src={kol.avatar} size="sm" />
                <span>
                  <b>{kol.name}</b>
                  <small>{kol.handle || '-'} · {kol.platform}</small>
                </span>
                <em>{kol.followerLabel || '-'} 粉丝</em>
                <strong>{kol.activeClaimId ? '已关注' : '未关注'}</strong>
              </button>
            );
          })}
          {!visibleKols.length ? (
            <div className="vkpi-campaign-add-kol-empty">没有可追加的 KOL，可能已经在当前推广里或筛选过窄。</div>
          ) : null}
        </div>
        <footer>
          <span>已选 {selectedKols.length} 个</span>
          <div>
            <button type="button" onClick={onClose} disabled={busy}>取消</button>
            <button className="is-primary" type="button" onClick={() => void submit()} disabled={busy || !selectedKols.length}>
              {busy ? '添加中' : `添加 ${selectedKols.length} 个 KOL`}
            </button>
          </div>
        </footer>
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
        <small>每日刷新</small>
        <span>上次：{tracking.last}</span>
        <button type="button" disabled={saving} onClick={() => onSave(row)}>{saving ? '保存中' : '保存物流'}</button>
        <button type="button" disabled title="下次更新接入真实物流追踪">刷新</button>
      </div>
    </div>
  );
}
