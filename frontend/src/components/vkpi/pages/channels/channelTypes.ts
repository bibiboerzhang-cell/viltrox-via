export interface ChannelContentPost {
  id: string;
  sourceId?: string;
  title: string;
  url: string;
  mediaUrl: string;
  videoUrl?: string;
  imageUrls?: string[];
  mediaUrls?: string[];
  mediaType?: string;
  mediaKind?: string;
  postedAt: string;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  accountLevel?: boolean;
  viewsUnavailable?: boolean;
}

export interface ChannelCommentItem {
  id: number;
  externalCommentId: string;
  externalPostId: string;
  text: string;
  author: string;
  likes: number;
  replyCount: number;
  depth: number;
  parentCommentId: string;
  isOp: boolean;
  createdAt: string;
  fetchedAt: string;
  sentiment?: string;
}

export interface ChannelCommentsResponse {
  channelId: number;
  postId: string;
  platform: string;
  status: string;
  message?: string;
  fetchedCount?: number;
  newCount?: number;
  collectSupported?: boolean;
  comments: ChannelCommentItem[];
}

export interface ChannelPostPagination {
  page: number;
  limit: number;
  total: number;
  pages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

export interface OfficialChannelAccount {
  id: number;
  staffId: number;
  staffName: string;
  staffEmail: string;
  staffAvatarUrl: string;
  staffRole: string;
  staffActive: boolean;
  platform: string;
  platformLabel: string;
  handle: string;
  displayName: string;
  accountUrl: string;
  avatarUrl: string;
  syncStatus: string;
  lastSyncAt: string;
  lastSyncError: string;
  followers: number;
  followersDelta?: number;
  postsCount: number;
  postsDelta?: number;
  totalViews: number;
  viewsDelta?: number;
  totalLikes: number;
  totalComments: number;
  engagementRate: number;
  posts: ChannelContentPost[];
}

export interface OfficialChannelPlatform {
  platform: string;
  label: string;
  totalViews: number;
  totalPosts: number;
  totalFollowers: number;
  followersDelta?: number;
  postsDelta?: number;
  viewsDelta?: number;
  accounts: OfficialChannelAccount[];
}

export interface RedditAssessmentPost extends ChannelContentPost {
  assessmentScore: number;
  assessmentCategory: string;
  assessmentLabel: string;
  score: number;
  upvoteRatio: number;
  author: string;
  flair: string;
  subreddit: string;
}

export interface RedditAssessmentResponse {
  channelId: number;
  source: string;
  account: {
    id: number;
    handle: string;
    displayName: string;
    subscribers: number;
    postsCount: number;
    lastSyncAt: string;
  };
  summary: {
    posts: number;
    comments: number;
    score: number;
    qualityCount: number;
    attentionCount: number;
  };
  distribution: Array<{ key: string; label: string; count: number }>;
  latestQuality: RedditAssessmentPost[];
  needsAttention: RedditAssessmentPost[];
  items: RedditAssessmentPost[];
}

export interface OfficialChannelMatrixSummary {
  accountCount: number;
  postCount: number;
  totalViews: number;
}

export interface ChannelGapIssue {
  key: string;
  label: string;
  priority: number;
}

export interface ChannelGapAccount {
  id: number;
  platform: string;
  platformLabel: string;
  displayName: string;
  handle: string;
  accountUrl: string;
  staffId: number;
  staffName: string;
  followers: number;
  postsCount: number;
  totalViews: number;
  postSampleCount: number;
  provider: string;
  providerReady: boolean;
  autoRefillSupported: boolean;
  syncStatus: string;
  lastSyncAt: string;
  lastSyncError: string;
  recommendedAction: string;
  issues: ChannelGapIssue[];
}
