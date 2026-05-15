import React, { useEffect, useMemo, useState } from 'react';
import type { VkpiDashboardData, VkpiKolLookupResult, VkpiKolOption } from '../vkpiTypes';
import { KolDetailPanel } from '../panels/KolDetailPanel';
import { KolPoolPanel } from '../panels/KolPoolPanel';
import { Avatar } from '../shared/Avatar';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { creatorPlatformOptions } from '../shared/vkpiConstants';
import { lookupResultToKolDetail, objectValue, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { PageShell } from './PageShell';
import { batchEnrichKolPool, enrichKolPoolItem, getKolPoolItem, listKolPool, promoteKolPoolToMain } from '../../../services/vkpi.ui-api';

interface DiscoverPageProps {
  data: VkpiDashboardData;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; email?: string; contactEmail?: string; notes?: string; scanAccount?: boolean; maxPosts?: number; productSku?: string }) => Promise<VkpiKolLookupResult>;
  onScanKolAccount?: (kolId: string, maxPosts?: number) => Promise<Record<string, unknown>>;
  onClaimKol?: (kolId: string) => Promise<void>;
  onUpdateKol?: (kolId: string, payload: { avatarUrl?: string; profileUrl?: string; contactEmail?: string; contactPhone?: string; notes?: string; contactLinks?: Array<{ label?: string; value?: string; url?: string }> }) => Promise<void>;
  apiToken?: string;
}

function platformInputValue(platformLabel: string): string {
  const normalized = String(platformLabel || '').toLowerCase();
  return creatorPlatformOptions.find((option) => option.value === normalized || option.label.toLowerCase() === normalized)?.value || normalized || 'other';
}

function searchNeedle(value: string): string {
  const raw = value.trim().toLowerCase();
  if (!raw) return '';
  try {
    const parsed = new URL(raw.startsWith('http') ? raw : `https://placeholder/${raw}`);
    const parts = parsed.pathname.split('/').filter(Boolean);
    return (parts[parts.length - 1] || parsed.hostname || raw).replace(/^@/, '');
  } catch {
    const parts = raw.split('/').filter(Boolean);
    return (parts[parts.length - 1] || raw).replace(/^@/, '');
  }
}

function existingKolToLookupResult(kol: VkpiKolOption): VkpiKolLookupResult {
  return {
    query: { platform: platformInputValue(kol.platform), handle: kol.handle.replace(/^@/, '') },
    kol: {
      id: kol.id,
      media_name: kol.name,
      channel_name: kol.handle.replace(/^@/, ''),
      platform: platformInputValue(kol.platform),
      avatar_url: kol.avatar || '',
      contact_email: kol.contactEmail || '',
      follower_count: kol.followerLabel,
      content_count: kol.contentCountLabel,
    },
    created: false,
    claim: kol.claimOwner ? { staff_name: kol.claimOwner } : {},
    can_claim: !kol.claimOwner,
    dossier: {
      snapshot: {
        scan_status: kol.scanStatus || 'known_profile',
      },
      posts: [],
      comments: [],
      report: {},
    },
  };
}

export function DiscoverPage({ data, onLookupKol, onScanKolAccount, onClaimKol, onUpdateKol, apiToken }: DiscoverPageProps) {
  const [activeDiscoverTab, setActiveDiscoverTab] = useState<'lookup' | 'pool'>('lookup');
  const [platform, setPlatform] = useState('youtube');
  const [handleOrUrl, setHandleOrUrl] = useState('');
  const [email, setEmail] = useState('');
  const [createIfMissing, setCreateIfMissing] = useState(true);
  const [scanAccount, setScanAccount] = useState(true);
  const [lookupResult, setLookupResult] = useState<VkpiKolLookupResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [messageTone, setMessageTone] = useState<'info' | 'warn' | 'error'>('info');
  const [manualAvatarUrl, setManualAvatarUrl] = useState('');
  const [manualProfileUrl, setManualProfileUrl] = useState('');
  const [manualEmail, setManualEmail] = useState('');
  const [manualPhone, setManualPhone] = useState('');
  const [manualLink, setManualLink] = useState('');
  const [manualNotes, setManualNotes] = useState('');
  const result = lookupResult;
  const detailKol = lookupResultToKolDetail(result, data.selectedKol);
  const kolId = result?.kol?.id ? String(result.kol.id) : '';
  const claimOwner = String(result?.claim?.staff_name || result?.claim?.staff_email || '');
  const snapshot = result?.dossier?.snapshot || {};
  const report = result?.dossier?.report || {};
  const posts = result?.dossier?.posts || [];
  const existingKols = useMemo(() => {
    const platformFilter = platformInputValue(platform);
    const query = searchNeedle(handleOrUrl);
    return data.kolOptions
      .filter((kol) => {
        const platformMatch = !platformFilter || platformInputValue(kol.platform) === platformFilter;
        if (!query) return platformMatch;
        return platformMatch && [kol.name, kol.handle, kol.contactEmail].join(' ').toLowerCase().includes(query);
      })
      .slice(0, 10);
  }, [data.kolOptions, handleOrUrl, platform]);

  useEffect(() => {
    if (!kolId) return;
    setManualAvatarUrl(detailKol.avatar || '');
    setManualProfileUrl(detailKol.profileUrl || '');
    setManualEmail(detailKol.contactEmail || email || '');
    setManualPhone(detailKol.contactPhone || '');
    const firstLink = detailKol.contactLinks?.find((link) => link.url || link.value);
    setManualLink(firstLink?.url || firstLink?.value || '');
    setManualNotes(detailKol.followUpNote || '');
  }, [detailKol.avatar, detailKol.contactEmail, detailKol.contactLinks, detailKol.contactPhone, detailKol.followUpNote, detailKol.profileUrl, email, kolId]);

  const setNotice = (text: string, tone: 'info' | 'warn' | 'error' = 'info') => {
    setMessage(text);
    setMessageTone(tone);
  };

  const handleLookup = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onLookupKol || !handleOrUrl.trim()) return;
    setBusy(true);
    setNotice('');
    try {
      const basePayload = { platform, handleOrUrl: handleOrUrl.trim(), createIfMissing, email: email.trim() || undefined };
      const next = await onLookupKol({ ...basePayload, scanAccount: false, maxPosts: 24 });
      setLookupResult(next || null);
      const nextKolId = next?.kol?.id ? String(next.kol.id) : '';
      if (!nextKolId) {
        setNotice(
          scanAccount && !createIfMissing
            ? '未找到已建档红人；当前未勾选“新红人自动建档”，系统不会创建档案或抓取平台数据。勾选后可继续抓取。'
            : '未找到可展示的红人档案；如果平台返回为空或权限不足，系统不会生成假数据。',
          'warn',
        );
        return;
      }
      if (scanAccount && nextKolId && onScanKolAccount) {
        setBusy(false);
        setScanBusy(true);
        setNotice('查重完成，正在抓取账号数据和生成评估报告。Apify / AI 通常需要 30-90 秒。');
        try {
          const scanResponse = await onScanKolAccount(nextKolId, 24);
          const refreshed = await onLookupKol({ ...basePayload, createIfMissing: false, scanAccount: false, maxPosts: 24 });
          setLookupResult(refreshed || next || null);
          const scanPayload = objectValue(scanResponse.scan || scanResponse);
          const scanStatus = String(refreshed?.dossier?.snapshot?.scan_status || scanPayload.status || refreshed?.scan_result?.status || 'done');
          const contentCount = safeNumber(scanPayload.content_count || refreshed?.dossier?.snapshot?.content_count);
          setNotice(
            contentCount
              ? `账号数据抓取完成。状态：${scanStatus}，已抓取 ${contentCount} 条内容。`
              : `账号抓取已返回。状态：${scanStatus}；平台未返回内容时不会展示假头像或假帖子。`,
            contentCount ? 'info' : 'warn',
          );
        } catch (scanError) {
          setNotice(`查重已完成，但账号抓取失败或超时：${scanError instanceof Error ? scanError.message : '未知错误'}`, 'error');
        } finally {
          setScanBusy(false);
        }
        return;
      }
      setNotice(scanAccount && !onScanKolAccount ? '红人查重完成；当前前端未接入账号抓取接口。' : '红人查重完成。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '红人搜索失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleClaim = async () => {
    if (!kolId || !onClaimKol) return;
    setBusy(true);
    setNotice('');
    try {
      await onClaimKol(kolId);
      setNotice('红人已绑定到当前员工账号。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '认领失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleManualSave = async () => {
    if (!kolId || !onUpdateKol) return;
    setBusy(true);
    setNotice('');
    try {
      await onUpdateKol(kolId, {
        avatarUrl: manualAvatarUrl.trim() || undefined,
        profileUrl: manualProfileUrl.trim() || undefined,
        contactEmail: manualEmail.trim() || undefined,
        contactPhone: manualPhone.trim() || undefined,
        notes: manualNotes.trim() || undefined,
        contactLinks: manualLink.trim() ? [{ label: '补录链接', value: manualLink.trim(), url: manualLink.trim() }] : undefined,
      });
      setNotice('红人资料已补录。');
      if (onLookupKol && handleOrUrl.trim()) {
        const refreshed = await onLookupKol({ platform, handleOrUrl: handleOrUrl.trim(), createIfMissing: false, scanAccount: false, maxPosts: 24 });
        setLookupResult(refreshed || lookupResult);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '红人资料保存失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const chooseExistingKol = async (kol: VkpiKolOption) => {
    setPlatform(platformInputValue(kol.platform));
    const nextHandle = kol.handle && kol.handle !== '-' ? kol.handle : '';
    setHandleOrUrl(nextHandle);
    setCreateIfMissing(false);
    setLookupResult(existingKolToLookupResult(kol));
    if (!onLookupKol || !nextHandle) {
      setNotice('已选中已有红人；当前没有可用的详情刷新接口。', 'warn');
      return;
    }
    setBusy(true);
    setNotice('正在加载已有红人档案和最近抓取数据。');
    try {
      const next = await onLookupKol({
        platform: platformInputValue(kol.platform),
        handleOrUrl: nextHandle,
        createIfMissing: false,
        scanAccount: false,
        maxPosts: 24,
      });
      setLookupResult(next || existingKolToLookupResult(kol));
      setNotice('已加载已有红人档案。');
    } catch (error) {
      setNotice(error instanceof Error ? `已有红人档案加载失败：${error.message}` : '已有红人档案加载失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell
      title="红人搜索 / 查重 / 认领"
      description="输入平台 URL 或 handle，先查重，再抓取粉丝、内容、互动和综合评估数据。"
      side={<KolDetailPanel kol={detailKol} selectedProject={data.projects[0]} />}
    >
      <section className="vkpi-card vkpi-action-card">
        <div className="vkpi-form-row">
          <button className={`vkpi-button ${activeDiscoverTab === 'lookup' ? 'vkpi-button--primary' : ''}`} type="button" onClick={() => setActiveDiscoverTab('lookup')}>
            查重 / 认领
          </button>
          <button className={`vkpi-button ${activeDiscoverTab === 'pool' ? 'vkpi-button--primary' : ''}`} type="button" onClick={() => setActiveDiscoverTab('pool')}>
            KOL Pool 候选池
          </button>
        </div>
      </section>

      {activeDiscoverTab === 'pool' ? (
        <KolPoolPanel
          apiToken={apiToken || ''}
          onListPool={(params) => {
            if (!apiToken) return Promise.reject(new Error('未登录'));
            return listKolPool(apiToken, params);
          }}
          onGetItem={(kolPoolId) => {
            if (!apiToken) return Promise.reject(new Error('未登录'));
            return getKolPoolItem(apiToken, kolPoolId);
          }}
          onEnrichItem={(kolPoolId, maxPosts) => {
            if (!apiToken) return Promise.reject(new Error('未登录'));
            return enrichKolPoolItem(apiToken, kolPoolId, maxPosts);
          }}
          onBatchEnrich={(payload) => {
            if (!apiToken) return Promise.reject(new Error('未登录'));
            return batchEnrichKolPool(apiToken, payload);
          }}
          onPromoteToMain={(kolPoolId) => {
            if (!apiToken) return Promise.reject(new Error('未登录'));
            return promoteKolPoolToMain(apiToken, kolPoolId);
          }}
          onOpenImport={() => setActiveDiscoverTab('lookup')}
        />
      ) : (
        <>
      <section className="vkpi-card vkpi-action-card">
        <form className="vkpi-form-grid" onSubmit={handleLookup}>
          <label>平台<select value={platform} onChange={(event) => setPlatform(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label>URL / ID / Handle<input value={handleOrUrl} onChange={(event) => setHandleOrUrl(event.target.value)} placeholder="https://... 或 @handle" /></label>
          <label>联系邮箱（可选）<input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" /></label>
          <label className="vkpi-checkbox"><input type="checkbox" checked={createIfMissing} onChange={(event) => setCreateIfMissing(event.target.checked)} /> 新红人自动建档</label>
          <label className="vkpi-checkbox"><input type="checkbox" checked={scanAccount} onChange={(event) => setScanAccount(event.target.checked)} /> 查重后抓取账号数据</label>
          <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || scanBusy || !onLookupKol}>
            {scanAccount ? '查重并抓取' : '搜索并查重'}
          </button>
        </form>
        {message ? <div className={`vkpi-inline-message ${messageTone === 'error' ? 'is-error' : messageTone === 'warn' ? 'is-warn' : ''}`}>{message}</div> : null}
        {scanBusy ? <div className="vkpi-inline-message">正在后台抓取粉丝、内容、互动、联系方式和账号评估；查重结果已经先返回，不是卡死。</div> : null}
      </section>

      <section className="vkpi-card">
        <CardHeader title="已有红人" />
        {existingKols.length ? (
          <div className="vkpi-existing-kol-list">
            {existingKols.map((kol) => (
              <button className="vkpi-existing-kol-row" type="button" key={kol.id} onClick={() => void chooseExistingKol(kol)}>
                <Avatar name={kol.name || kol.handle || 'KOL'} src={kol.avatar} size="sm" />
                <strong>{kol.name}</strong>
                <em>{kol.handle}</em>
                <small>{kol.followerLabel || '-'} 粉丝 · {kol.contentCountLabel || '-'} 内容 · {kol.claimOwner || '未分配'}</small>
              </button>
            ))}
          </div>
        ) : (
          <div className="vkpi-empty-state">当前筛选下没有已有红人。查重创建后会出现在这里，项目创建时也可直接选择。</div>
        )}
      </section>

      <section className="vkpi-card">
        <CardHeader title="查重结果" />
        {result ? (
          <div className="vkpi-result-grid">
            <InfoBlock label="红人 ID" value={kolId || '未找到'} />
            <InfoBlock label="平台 Handle" value={String(result.query?.handle || result.kol?.channel_name || '-')} />
            <InfoBlock label="状态" value={result.can_claim ? '可认领' : claimOwner ? `已被 ${claimOwner} 认领` : '不可认领'} tone={result.can_claim ? 'good' : 'warn'} />
            <InfoBlock label="创建状态" value={result.created ? '本次新建档案' : '已有档案 / 未创建'} />
            <InfoBlock label="粉丝" value={detailKol.subscribersLabel} />
            <InfoBlock label="帖子 / 视频" value={detailKol.videosLabel} />
            <InfoBlock label="总点赞" value={detailKol.totalLikesLabel || '-'} />
            <InfoBlock label="平均播放" value={detailKol.avgViewsLabel || '-'} />
            <InfoBlock label="互动率" value={detailKol.engagementLabel} />
            <InfoBlock label="用户画像" value={detailKol.personaLabel || '待判断'} />
            <InfoBlock label="优先级" value={detailKol.priorityLabel || '待评估'} tone={String(detailKol.priorityLabel || '').startsWith('P0') ? 'good' : undefined} />
            <InfoBlock label="综合评分" value={detailKol.accountScoreLabel || '-'} tone={safeNumber(report.account_score) >= 70 ? 'good' : undefined} />
            <InfoBlock label="抓取状态" value={textValue(snapshot.scan_status || result.scan_result?.status, '未抓取')} tone={String(snapshot.scan_status || result.scan_result?.status || '').includes('done') ? 'good' : 'warn'} />
            <div className="vkpi-lookup-summary">
              <strong>综合评估</strong>
              <p>{detailKol.recommendedAction}</p>
              {detailKol.productFitSummary && detailKol.productFitSummary !== '-' ? <span>适配产品：{detailKol.productFitSummary}</span> : null}
              {detailKol.personaReason ? <span>画像依据：{detailKol.personaReason}</span> : null}
              {posts.length ? <span>已抓取 {posts.length} 条近期内容，右侧可查看视频/帖子表现。</span> : <span>当前还没有可展示的真实内容数据；如果平台权限或 Apify 配置不足，会显示抓取状态和错误原因。</span>}
            </div>
            {result.can_claim && kolId ? <button className="vkpi-button vkpi-button--primary" type="button" onClick={handleClaim} disabled={busy || !onClaimKol}>认领此红人</button> : null}
          </div>
        ) : (
          <div className="vkpi-empty-state">还没有搜索结果。真实接口没有数据时不会显示假红人或假头像。</div>
        )}
      </section>

      {kolId ? (
        <section className="vkpi-card">
          <CardHeader title="手动补录红人资料" />
          <div className="vkpi-form-grid">
            <label>头像 URL<input value={manualAvatarUrl} onChange={(event) => setManualAvatarUrl(event.target.value)} placeholder="https://.../avatar.jpg" /></label>
            <label>主页 URL<input value={manualProfileUrl} onChange={(event) => setManualProfileUrl(event.target.value)} placeholder="https://www.instagram.com/..." /></label>
            <label>联系邮箱<input value={manualEmail} onChange={(event) => setManualEmail(event.target.value)} placeholder="name@example.com" /></label>
            <label>电话 / WhatsApp<input value={manualPhone} onChange={(event) => setManualPhone(event.target.value)} placeholder="+1..." /></label>
            <label>其他联系链接<input value={manualLink} onChange={(event) => setManualLink(event.target.value)} placeholder="Website / Linktree / YouTube about" /></label>
            <label>备注<textarea value={manualNotes} onChange={(event) => setManualNotes(event.target.value)} placeholder="手动补录的联系方式、合作偏好、下一步动作" /></label>
            <button className="vkpi-button vkpi-button--primary" type="button" onClick={() => void handleManualSave()} disabled={busy || !onUpdateKol}>保存补录</button>
          </div>
        </section>
      ) : null}
        </>
      )}
    </PageShell>
  );
}
