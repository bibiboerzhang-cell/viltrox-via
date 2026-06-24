export type VkpiDeltaDirection = 'up' | 'down' | 'flat';
export type VkpiDataStatus = 'live' | 'partial' | 'empty';
export type VkpiRangeKey = 'today' | '7d' | '30d' | 'mtd' | 'qtd';
export type VkpiPageKey =
  | 'command'
  | 'v615Replica'
  | 'missionControlV2'
  | 'dashboardPremium'
  | 'glass-demo'
  | 'agents'
  | 'intelligenceCenter'
  | 'repairCenter'
  | 'kolPoolV2'
  | 'discover'
  | 'projects'
  | 'links'
  | 'attribution'
  | 'costs'
  | 'productBattle'
  | 'industryData'
  | 'industryBoard'
  | 'dataExport'
  | 'learning'
  | 'dataAnalysis'
  | 'analytics'
  | 'channels'
  | 'campaigns'
  | 'dataQuality'
  | 'reports'
  | 'audit'
  | 'settings';
export type VkpiMetricEvidenceKey =
  | 'gmv'
  | 'cost'
  | 'roi'
  | 'new_kol'
  | 'published_content'
  | 'valid_clicks'
  | 'net_contribution'
  | 'views'
  | 'active_projects'
  | 'alerts';

export type VkpiProjectStage =
  | 'invited'
  | 'discovery'
  | 'contacted'
  | 'replied'
  | 'in_discussion'
  | 'agreed'
  | 'shipped'
  | 'received'
  | 'content_published'
  | 'published'
  | 'measured'
  | 'closed'
  | 'stalled'
  | 'lost'
  | 'released'
  | 'cancelled';

export type VkpiPlatform =
  | 'YouTube'
  | 'Instagram'
  | 'TikTok'
  | 'XHS'
  | 'Bilibili'
  | 'Facebook'
  | 'Reddit'
  | 'X'
  | 'Threads'
  | 'Twitch'
  | 'Pinterest'
  | 'Vimeo'
  | 'Discord'
  | 'Website'
  | 'Weibo'
  | 'Douyin'
  | 'Zhihu'
  | 'LinkedIn'
  | 'Telegram'
  | 'Newsletter'
  | 'Forum'
  | 'Email'
  | 'Other';

export interface VkpiMetricCard {
  key: string;
  label: string;
  value: string;
  deltaLabel: string;
  deltaDirection: VkpiDeltaDirection;
  metricValueId?: number;
  drilldownUrl?: string;
  sourceCount?: number;
  unit?: string;
  calculation?: Record<string, unknown>;
}

export interface VkpiTrendPoint {
  label: string;
  gmv: number;
  netContribution: number;
  views?: number;
  sales?: number;
  cost?: number;
}

export interface VkpiFunnelItem {
  label: string;
  value: number;
  rateLabel: string;
}

export interface VkpiLeaderboardItem {
  staffId?: string;
  name: string;
  gmv: number;
  avatar?: string;
  isTop?: boolean;
}

export interface VkpiProductRoiItem {
  product: string;
  roi: number;
  gmv: number;
  cost?: number;
  views?: number;
  sales?: number;
}

export interface VkpiShareItem {
  label: string;
  value: number;
}

export interface VkpiAlertItem {
  id: string;
  alertKey?: string;
  label: string;
  count: number;
  severity: 'warning' | 'danger' | 'neutral' | 'info';
  description?: string;
  ruleKey?: string;
  targetType?: string;
  targetId?: string;
  createdAt?: string;
  platform?: string;
  triageGroup?: 'comment_intelligence' | 'workflow' | 'system';
  negativeCount?: number;
  criticalCount?: number;
  hostileCount?: number;
  flaggedComments?: number;
  windowDays?: number;
}

export interface VkpiAlertDetail {
  alert: Record<string, unknown>;
  metadata: Record<string, unknown>;
  post?: Record<string, unknown> | null;
  account?: Record<string, unknown> | null;
  comments: Array<Record<string, unknown>>;
  sourceSummary?: Record<string, unknown>;
}

export interface VkpiExportReport {
  id: string;
  title: string;
  generatedAt: string;
  status: 'Ready' | 'Generating' | 'Failed';
  downloadUrl?: string;
}

export interface VkpiAuditEvent {
  id: string;
  eventId?: string | number;
  eventCategory: string;
  action: string;
  staffId?: string;
  staffName?: string;
  staffEmail?: string;
  targetType: string;
  targetId: string;
  detail: string;
  ip?: string;
  userAgent?: string;
  occurredAt: string;
  metadata?: Record<string, unknown>;
}

export interface VkpiAuditSummary {
  days: number;
  sensitiveAccessCount: number;
  exportCount: number;
  settingsChangeCount: number;
  attributionAdjustmentCount: number;
  businessEventCount: number;
  eventCount: number;
  byCategory: Array<{ label: string; count: number }>;
  byAction: Array<{ label: string; count: number }>;
}

export interface VkpiAuditOverview {
  summary: VkpiAuditSummary;
  events: VkpiAuditEvent[];
}

export interface VkpiProjectRow {
  id: string;
  projectId?: string;
  assignmentId?: string;
  kolPoolId?: string;
  kolId?: string;
  kolName: string;
  kolHandle: string;
  kolAvatar?: string;
  kolProfileUrl?: string;
  videoUrl?: string;
  evidenceUrl?: string;
  evidenceTitle?: string;
  evidenceThumbnailUrl?: string;
  evidencePublishDate?: string;
  latestVideoUrl?: string;
  latestEvidenceUrl?: string;
  latestEvidenceTitle?: string;
  latestEvidenceThumbnailUrl?: string;
  latestEvidencePublishDate?: string;
  platform: VkpiPlatform;
  campaign: string;
  stage: VkpiProjectStage;
  followStatus?: 'active' | 'paused';
  isStarred?: boolean;
  starredAt?: string;
  latestMessageAt: string;
  latestMessageSource: 'DM' | 'Email' | 'Comment reply' | 'Manual note' | 'No reply';
  views: number;
  likes?: number;
  comments?: number;
  clicks: number | null;
  orders: number | null;
  // 钱口径:null = 无归因数据/链路不存在(显"—");0 = 有归因链路但值为零(显 $0)。
  gmv: number | null;
  cost: number | null;
  roi: number | null;
  ownerId?: string;
  ownerName: string;
  ownerAvatar?: string;
  productSku?: string;
  productName?: string;
  marketplace?: string;
  priority?: string;
  shopifyLink?: string;
  trackingNumber?: string;
  trackingCarrier?: string;
  trackingStatus?: string;
  isFakeTracking?: boolean;
  kolCount?: number;
  kolWithEvidence?: number;
  evidenceCount?: number;
  platforms?: VkpiPlatform[];
  stageCounts?: Record<string, number>;
  publishedCount?: number;
  healthScore?: number;
  healthBasis?: string;
  healthBreakdown?: Record<string, number>;
  needsFollowupCount?: number | null;
  overdueCount?: number | null;
  currentFocus?: string;
  bottleneck?: string;
  churnedCount?: number;
  createdAt?: string;
  startedAt?: string;
  closedAt?: string;
  currentStageStartedAt?: string;
  totalDurationLabel?: string;
  stageDurationLabel?: string;
  stageEventCount?: number;
  updatedAt: string;
}

export interface VkpiContentAsset {
  id: string;
  title: string;
  duration?: string;
  imageUrl?: string;
  url?: string;
  videoUrl?: string;
  platform: VkpiPlatform;
  postedAt?: string;
  engagementLabel?: string;
}

export interface VkpiMessageCapture {
  id: string;
  source: VkpiPlatform | 'Manual';
  type: 'Comment reply' | 'DM' | 'Email' | 'Note';
  capturedAt: string;
  snippet: string;
  evidenceUrl?: string;
}

export interface VkpiShortLinkSummary {
  slug: string;
  destination: string;
  clicks: number;
  orders: number;
  gmv: number;
  roi: number;
}

export interface VkpiContactLink {
  label: string;
  value: string;
  url?: string;
}

export interface VkpiKolDetail {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  verified?: boolean;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  contactLinks?: VkpiContactLink[];
  subscribersLabel: string;
  videosLabel: string;
  engagementLabel: string;
  totalViewsLabel?: string;
  totalLikesLabel?: string;
  avgViewsLabel?: string;
  personaLabel?: string;
  priorityLabel?: string;
  productFitSummary?: string;
  personaReason?: string;
  contactAction?: string;
  accountScoreLabel?: string;
  audienceFitLabel?: string;
  productFitLabel?: string;
  riskLevel?: string;
  recommendedAction?: string;
  scanStatus?: string;
  scanError?: string;
  scannedAt?: string;
  country?: string;
  claimOwner?: string;
  claimStatus?: string;
  recentContent: VkpiContentAsset[];
  messages: VkpiMessageCapture[];
  shortLink: VkpiShortLinkSummary;
  followUpNote: string;
}

export interface VkpiEvidenceRow {
  id: string;
  metric: VkpiMetricEvidenceKey;
  label: string;
  source: string;
  amount: number | null;
  amountUnit?: 'currency' | 'number' | 'ratio';
  projectId?: string;
  kolName?: string;
  ownerName?: string;
  accountName?: string;
  accountHandle?: string;
  accountUrl?: string;
  staffName?: string;
  platform?: string;
  platformLabel?: string;
  attributionType?: string;
  mediaUrl?: string;
  confidence?: string;
  occurredAt?: string;
  rawRef?: string;
}

export interface VkpiProjectDetail {
  project: Record<string, unknown>;
  participating_kols?: Array<Record<string, unknown>>;
  project_kol_assignments?: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  link_clicks?: Array<Record<string, unknown>>;
  link_orders?: Array<Record<string, unknown>>;
  link_summary?: Record<string, unknown>;
  sales_attributions: Array<Record<string, unknown>>;
  costs: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  content_posts?: Array<Record<string, unknown>>;
  content_assets?: Array<Record<string, unknown>>;
  terms?: Record<string, unknown>;
  deliverables?: Array<Record<string, unknown>>;
  samples?: Array<Record<string, unknown>>;
  shipments?: Array<Record<string, unknown>>;
  audit_events?: Array<Record<string, unknown>>;
  roi?: {
    revenue_cents?: number;
    cost_cents?: number;
    net_contribution_cents?: number;
    roi?: number | null;
  };
}

export interface VkpiKolProfile {
  kol: Record<string, unknown>;
  active_claim?: Record<string, unknown>;
  claim_history: Array<Record<string, unknown>>;
  projects: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  link_clicks?: Array<Record<string, unknown>>;
  link_orders?: Array<Record<string, unknown>>;
  link_summary?: Record<string, unknown>;
  sales_attributions: Array<Record<string, unknown>>;
  costs: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  content_posts?: Array<Record<string, unknown>>;
  content_assets?: Array<Record<string, unknown>>;
  kpi_ledger?: Array<Record<string, unknown>>;
  kpi_summary?: Array<Record<string, unknown>>;
  recommendations?: Array<Record<string, unknown>>;
  recommendation_outcomes?: Array<Record<string, unknown>>;
  audit_events?: Array<Record<string, unknown>>;
  contacts?: Record<string, unknown>;
  activity_timeline?: Array<Record<string, unknown>>;
  snapshot?: Record<string, unknown>;
  posts: Array<Record<string, unknown>>;
  analysis_report?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  dossier?: Record<string, unknown>;
}

export interface VkpiStaffProfile {
  staff: Record<string, unknown>;
  summary: Record<string, unknown>;
  projects: Array<Record<string, unknown>>;
  claims: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  attributions: Array<Record<string, unknown>>;
  costs: Array<Record<string, unknown>>;
  kpi_ledger: Array<Record<string, unknown>>;
  kpi_breakdown?: {
    grouped?: Array<Record<string, unknown>>;
    source_rows?: Array<Record<string, unknown>>;
    recommendation_grouped?: Array<Record<string, unknown>>;
    recommendation_source_rows?: Array<Record<string, unknown>>;
    source_count?: number;
  };
  channels: Array<Record<string, unknown>>;
  audit_events: Array<Record<string, unknown>>;
  visibility?: Record<string, unknown>;
}

export interface VkpiLinkDetail {
  link: Record<string, unknown>;
  project?: Record<string, unknown>;
  kol?: Record<string, unknown>;
  owner?: Record<string, unknown>;
  clicks: Array<Record<string, unknown>>;
  sales_attributions: Array<Record<string, unknown>>;
  orders: Array<Record<string, unknown>>;
  summary?: Record<string, unknown>;
  audit_events?: Array<Record<string, unknown>>;
}

export interface VkpiDataQualityIssue {
  id: string;
  issue_type: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info' | string;
  title: string;
  entity_type: string;
  entity_id?: string | number | null;
  staff_id?: string | number | null;
  project_id?: string | number | null;
  kol_id?: string | number | null;
  detail?: string;
  evidence?: Record<string, unknown>;
  created_at?: string;
}

export interface VkpiDataQualityResponse {
  status: string;
  generated_at: string;
  count: number;
  total_count: number;
  issues: VkpiDataQualityIssue[];
  summary?: Record<string, number>;
}

export interface VkpiKolLookupResult {
  query?: Record<string, unknown>;
  kol?: Record<string, unknown> | null;
  claim?: Record<string, unknown> | null;
  dossier?: {
    snapshot?: Record<string, unknown>;
    report?: Record<string, unknown>;
    posts?: Array<Record<string, unknown>>;
    comments?: Array<Record<string, unknown>>;
  };
  scan_result?: Record<string, unknown>;
  analysis_result?: Record<string, unknown>;
  created?: boolean;
  can_claim?: boolean;
}

export interface VkpiLinkRow {
  id: string;
  slug: string;
  destination: string;
  platform: VkpiPlatform;
  projectId?: string;
  projectName?: string;
  kolName?: string;
  ownerName?: string;
  clicks: number;
  validClicks: number;
  botClicks: number;
  orders: number;
  gmv: number;
  status: string;
  healthStatus: string;
  updatedAt: string;
}

export interface VkpiAttributionRow {
  id: string;
  source: string;
  sourceRef: string;
  projectId?: string;
  linkId?: string;
  kolId?: string;
  staffId?: string;
  productSku?: string;
  orderId?: string;
  revenue: number;
  commission: number;
  confidence: string;
  occurredAt: string;
}

export interface VkpiCostRow {
  id: string;
  projectId?: string;
  kolId?: string;
  staffId?: string;
  costType: string;
  amount: number;
  currency: string;
  status: string;
  incurredAt: string;
  sourceRef?: string;
  note?: string;
  projectName?: string;
  productSku?: string;
  kolName?: string;
  staffName?: string;
  approvedByStaffId?: string;
  approvedAt?: string;
  voidedByStaffId?: string;
  voidedAt?: string;
  updatedAt?: string;
}

export interface VkpiCostDetail {
  cost: Record<string, unknown>;
  project?: Record<string, unknown>;
  kol?: Record<string, unknown>;
  owner?: Record<string, unknown>;
  audit_events?: Array<Record<string, unknown>>;
}

export interface VkpiStaffMember {
  id: string;
  userId?: string;
  name: string;
  email: string;
  role: string;
  active: boolean;
  avatarUrl?: string;
  employeeCode?: string;
  vkpiPermission: string;
  permissions?: Record<string, string>;
  verificationStatus?: string;
  deliveryMethod?: string;
  inviteTokenActive?: boolean;
  lastActiveAt?: string;
  invitedAt?: string;
  acceptedAt?: string;
  online?: boolean;
}

export interface VkpiKpiLedgerEntry {
  id: string;
  ledgerDate: string;
  staffId?: string;
  staffName?: string;
  employeeCode?: string;
  kolId?: string;
  kolName?: string;
  projectId?: string;
  projectName?: string;
  productSku?: string;
  metricKey: string;
  metricLabel?: string;
  metricValue: number;
  sourceType: string;
  sourceRef: string;
  confidence: string;
  createdAt: string;
}

export interface VkpiProductCostRow {
  id: string;
  productSku: string;
  productName: string;
  unitCost: number;
  currency: string;
  active: boolean;
  note?: string;
  updatedAt?: string;
}

export interface VkpiProductCatalogItem {
  sku: string;
  categoryMain: string;
  categoryDetail?: string;
  modelName: string;
  marketingName?: string;
  priceUsd?: number | null;
  status: string;
  description?: string;
  sourceFile?: string;
  series?: string;
  mount?: string;
  productUrl?: string;
  specs?: Record<string, unknown>;
  fitTags?: string[];
  sourceUrl?: string;
  sourceCheckedAt?: string;
  sourceConfidence?: number;
}

export interface VkpiProductLaunchOption {
  id: string;
  productSku: string;
  productName: string;
  launchName: string;
  status?: string;
  category?: string;
  updatedAt?: string;
}

export interface VkpiKolOption {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  contactLinks?: VkpiContactLink[];
  followerLabel?: string;
  contentCountLabel?: string;
  activeClaimId?: string;
  claimStaffId?: string;
  claimOwner?: string;
  scanStatus?: string;
}

export interface VkpiScopeContext {
  actorStaffId?: string;
  requestedStaffId?: string;
  effectiveStaffId?: string;
  canViewAll: boolean;
  scopeMode: 'anonymous' | 'own' | 'all' | 'requested_staff' | string;
  role?: string;
  isOwner?: boolean;
  domain?: string;
}

export interface VkpiDashboardData {
  rangeLabel: string;
  windowDays?: number;
  lastSyncedAt?: string;
  dataStatus: VkpiDataStatus;
  dataNotice: string;
  metrics: VkpiMetricCard[];
  revenueTrend: VkpiTrendPoint[];
  funnel: VkpiFunnelItem[];
  staffLeaderboard: VkpiLeaderboardItem[];
  productRoi: VkpiProductRoiItem[];
  platformShare: VkpiShareItem[];
  contentTypePerformance: VkpiShareItem[];
  alerts: VkpiAlertItem[];
  weeklySummary: string;
  exportReport: VkpiExportReport;
  projects: VkpiProjectRow[];
  starredProjects?: VkpiProjectRow[];
  links: VkpiLinkRow[];
  attributions: VkpiAttributionRow[];
  unmatchedAttributions: VkpiAttributionRow[];
  costs: VkpiCostRow[];
  evidence: Partial<Record<VkpiMetricEvidenceKey, VkpiEvidenceRow[]>>;
  staffMembers: VkpiStaffMember[];
  kpiLedger: VkpiKpiLedgerEntry[];
  productCosts: VkpiProductCostRow[];
  productLaunches: VkpiProductLaunchOption[];
  kolOptions: VkpiKolOption[];
  selectedKol: VkpiKolDetail;
  scopes?: {
    projects?: VkpiScopeContext;
    kols?: VkpiScopeContext;
  };
}
