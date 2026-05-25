import type { VkpiKolDetail, VkpiKolOption } from '../../components/vkpi/vkpiTypes';
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
