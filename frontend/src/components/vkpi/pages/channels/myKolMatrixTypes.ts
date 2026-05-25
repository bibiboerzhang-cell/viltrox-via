import type { VkpiContactLink, VkpiPlatform, VkpiProjectStage } from '../../vkpiTypes';

export type MyKolView = 'watchlist' | 'funnel';
export type PlatformFilter = 'all' | 'Facebook' | 'Instagram' | 'TikTok' | 'YouTube' | 'X' | 'Reddit';
export type FunnelStageKey = 'claimed' | 'contacted' | 'replied' | 'agreed' | 'shipped' | 'received' | 'published' | 'measured';
export type KolContentSort = 'latest' | 'views' | 'likes' | 'comments' | 'shares';
export type KolContentDirection = 'desc' | 'asc';
export type KolContentWindow = 'all' | '7d' | '30d' | '90d' | '180d' | '365d' | 'year';
export type KolContentFilter = 'all' | 'viltrox' | 'competitor' | 'other' | 'gear';

export interface MyKolItem {
  id: string;
  kolId: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  contactLinks: VkpiContactLink[];
  followers: string;
  contentCount: string;
  projectCount: number;
  views: number;
  clicks: number;
  subStatus: string;
  latestStage?: VkpiProjectStage;
  latestProjectAt: number;
  projectStages: VkpiProjectStage[];
  activeClaimId?: string;
  scanStatus?: string;
}

export interface EffectiveMyKolItem extends MyKolItem {
  isFollowed: boolean;
  funnelStage: FunnelStageKey;
}

export interface PlatformEntryMetric {
  platform: Exclude<PlatformFilter, 'all'>;
  kolCount: number;
  projectCount: number;
  views: number;
}

export interface ContactDraft {
  contactEmail: string;
  contactPhone: string;
  profileUrl: string;
}

export interface PostPreview {
  id: string;
  snapshotId: string;
  title: string;
  url: string;
  mediaUrl: string;
  videoUrl: string;
  imageUrl: string;
  imageUrls: string[];
  mediaUrls: string[];
  views: number;
  likes: number;
  comments: number;
  shares: number;
  publishedAt: string;
  contentType: string;
  brandMentions: string[];
  competitorMentions: string[];
  gearMentions: string[];
  rawText: string;
}

export interface KolCommentItem {
  id: string;
  postUrl: string;
  author: string;
  text: string;
  likes: number;
  sentiment: string;
  intentTags: string[];
  createdAt: string;
}

export interface KolPostState {
  items: PostPreview[];
  total: number;
  loading: boolean;
  requested: boolean;
  error: string;
}

export interface KolCommentState {
  items: KolCommentItem[];
  total: number;
  loading: boolean;
  requested: boolean;
  error: string;
}

export const VIEW_TABS: Array<{ key: MyKolView; label: string }> = [
  { key: 'watchlist', label: '关注列表' },
  { key: 'funnel', label: '合作漏斗' },
];

export const FUNNEL_STAGES: Array<{ key: FunnelStageKey; label: string }> = [
  { key: 'claimed', label: '已认领' },
  { key: 'contacted', label: '已联系' },
  { key: 'replied', label: '已回复' },
  { key: 'agreed', label: '已合作' },
  { key: 'shipped', label: '已发货' },
  { key: 'received', label: '已到货' },
  { key: 'published', label: '已发布' },
  { key: 'measured', label: '已统计' },
];

export const PLATFORM_OPTIONS: Array<{ key: PlatformFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'Facebook', label: 'Facebook' },
  { key: 'Instagram', label: 'Instagram' },
  { key: 'TikTok', label: 'TikTok' },
  { key: 'YouTube', label: 'YouTube' },
  { key: 'X', label: 'X' },
  { key: 'Reddit', label: 'Reddit' },
];

export const PLATFORM_ENTRIES: Exclude<PlatformFilter, 'all'>[] = ['Facebook', 'Instagram', 'TikTok', 'YouTube', 'X', 'Reddit'];

export const CONTENT_SORT_OPTIONS: Array<{ key: KolContentSort; label: string }> = [
  { key: 'latest', label: '最新' },
  { key: 'views', label: '播放' },
  { key: 'likes', label: '点赞' },
  { key: 'comments', label: '评论' },
  { key: 'shares', label: '分享' },
];

export const CONTENT_WINDOW_OPTIONS: Array<{ key: KolContentWindow; label: string }> = [
  { key: 'all', label: '全部时间' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 30 天' },
  { key: '90d', label: '近 90 天' },
  { key: '180d', label: '近 180 天' },
  { key: '365d', label: '近 1 年' },
  { key: 'year', label: '今年' },
];

export const CONTENT_FILTER_OPTIONS: Array<{ key: KolContentFilter; label: string }> = [
  { key: 'all', label: '全部内容' },
  { key: 'viltrox', label: 'Viltrox相关' },
  { key: 'competitor', label: '竞品相关' },
  { key: 'gear', label: '设备已识别' },
  { key: 'other', label: '其它内容' },
];

export const VILTROX_TERMS = ['viltrox', 'viltroxthailand', 'viltroxusa', 'viltrox cine', 'viltrox flash'];
export const COMPETITOR_TERMS = ['sigma', 'tamron', 'sony', 'zeiss', 'sirui', 'laowa', 'meike', 'ttartisan', '7artisans'];
export const GEAR_PATTERNS: Array<{ label: string; terms: string[] }> = [
  { label: 'Fuji', terms: ['fuji', 'fujifilm', 'x-t', 'xt5', 'x-h', 'xh2', 'gfx'] },
  { label: 'Sony', terms: ['sony', 'a7', 'fx3', 'fx30', 'zv-e'] },
  { label: 'Canon', terms: ['canon', 'eos', 'rf '] },
  { label: 'Nikon', terms: ['nikon', 'z6', 'z7', 'z8', 'z9'] },
  { label: 'Panasonic', terms: ['panasonic', 'lumix', 's5', 'gh6'] },
  { label: 'Blackmagic', terms: ['blackmagic', 'bmpcc'] },
  { label: '16mm', terms: ['16mm'] },
  { label: '27mm', terms: ['27mm'] },
  { label: '35mm', terms: ['35mm'] },
  { label: '56mm', terms: ['56mm'] },
  { label: '镜头', terms: ['lens', 'lenses', '镜头'] },
];
