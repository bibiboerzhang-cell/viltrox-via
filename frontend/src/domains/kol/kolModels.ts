import type {
  VkpiKolDetail,
  VkpiKolOption,
  VkpiLinkRow,
  VkpiProjectRow,
} from '../../components/vkpi/vkpiTypes';
import {
  compact,
  numberValue,
  parseContactLinks,
  platformLabel,
} from '../dashboard';

type Row = Record<string, unknown>;

export const emptyKol: VkpiKolDetail = {
  id: 'none',
  name: '未选择红人',
  handle: '-',
  platform: 'Other',
  verified: false,
  subscribersLabel: '0',
  videosLabel: '0',
  engagementLabel: '0%',
  country: '',
  claimOwner: '未分配',
  claimStatus: '暂无项目',
  recentContent: [],
  messages: [],
  shortLink: { slug: '暂无短链', destination: '-', clicks: 0, orders: 0, gmv: 0, roi: 0 },
  followUpNote: '请选择或创建项目，以查看红人详情、消息记录、短链、归因和备注。',
};

export function buildKolOptions(rows: Row[]): VkpiKolOption[] {
  return rows.map((row) => {
    const name = String(row.media_name || row.owner_name || row.channel_name || row.handle || `KOL ${row.id || ''}`).trim();
    const handle = String(row.channel_name || row.handle || row.channel_url || '').trim();
    const followerCount = numberValue(row.snapshot_follower_count || row.follower_count);
    const contentCount = numberValue(row.snapshot_content_count || row.content_count);
    return {
      id: String(row.id || ''),
      name: name || '未命名 KOL',
      handle: handle ? (handle.startsWith('@') ? handle : `@${handle}`) : '-',
      platform: platformLabel(row.platform),
      avatar: String(row.avatar_url || ''),
      profileUrl: String(row.profile_url || row.channel_url || ''),
      contactEmail: String(row.contact_email || ''),
      contactPhone: String(row.contact_phone || ''),
      contactLinks: parseContactLinks(row.contact_links_json),
      followerLabel: compact(followerCount),
      contentCountLabel: compact(contentCount),
      activeClaimId: row.active_claim_id ? String(row.active_claim_id) : undefined,
      claimStaffId: row.claim_staff_id ? String(row.claim_staff_id) : undefined,
      claimOwner: String(row.claim_staff_name || row.claim_staff_email || row.assigned_staff_id || ''),
      scanStatus: String(row.snapshot_scan_status || row.contact_status || ''),
    };
  }).filter((row) => row.id);
}

export function buildSelectedKol(projects: VkpiProjectRow[], links: VkpiLinkRow[], messages: Row[]): VkpiKolDetail {
  const first = projects[0];
  if (!first) return emptyKol;
  const link = links.find((row) => row.projectId === first.id) || links[0];
  return {
    id: first.kolId || first.id,
    name: first.kolName,
    handle: first.kolHandle,
    platform: first.platform,
    verified: false,
    subscribersLabel: '-',
    videosLabel: '-',
    engagementLabel: '-',
    country: '',
    claimOwner: first.ownerName,
    claimStatus: '进行中项目',
    recentContent: [],
    messages: messages.slice(0, 5).map((message, index) => ({
      id: String(message.id || index),
      source: platformLabel(message.source_platform || message.source) === 'Email' ? 'Email' : 'Manual',
      type: 'Note',
      capturedAt: String(message.created_at || message.occurred_at || '-'),
      snippet: String(message.title || message.body || message.note || '暂无消息摘要。'),
      evidenceUrl: String(message.evidence_url || '') || undefined,
    })),
    shortLink: {
      slug: link?.slug || '暂无短链',
      destination: link?.destination || '-',
      clicks: link?.validClicks || 0,
      orders: first.orders || 0,
      gmv: first.gmv,
      roi: first.roi || 0,
    },
    followUpNote: `项目总耗时 ${first.totalDurationLabel || '-'}，当前阶段已停留 ${first.stageDurationLabel || '-'}。请按流程继续推进。`,
  };
}
