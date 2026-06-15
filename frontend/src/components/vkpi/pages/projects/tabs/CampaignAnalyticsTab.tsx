import { useState } from 'react';
import { DollarSign, Eye, Heart, MessageCircle, ShoppingCart, Sparkles, TrendingUp } from 'lucide-react';
import { formatLargeNum, formatMoneyShort } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { ProjectStatsSummary } from '../../../../../domains/projects';
import { retrospectiveNumberField, retrospectiveTextField, retrospectiveVideoTitle } from '../ProjectDetailTabs.shared';

function analyticsKolProfileUrl(row: VkpiProjectRow) {
  const explicit = retrospectiveTextField(row, ['kolProfileUrl', 'profile_url', 'channel_url']);
  if (/^https?:\/\//i.test(explicit)) return explicit;
  const handle = String(row.kolHandle || '').trim().replace(/^@/, '');
  if (!handle || /^https?:\/\//i.test(handle)) return handle;
  const platform = String(row.platform || '').toLowerCase();
  if (platform.includes('youtube')) return `https://www.youtube.com/@${handle}`;
  if (platform.includes('instagram')) return `https://www.instagram.com/${handle}`;
  if (platform.includes('tiktok')) return `https://www.tiktok.com/@${handle}`;
  if (platform === 'x' || platform.includes('twitter')) return `https://x.com/${handle}`;
  return explicit;
}

type AnalyticsRankingSort = 'views' | 'published';

function isSpecificEvidenceUrl(value: string) {
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    const path = parsed.pathname.toLowerCase();
    if (host.includes('youtube.com')) return path.includes('/watch') || path.includes('/shorts/') || path.includes('/embed/');
    if (host.includes('youtu.be')) return path.length > 1;
    if (host.includes('instagram.com')) return path.includes('/p/') || path.includes('/reel/') || path.includes('/tv/');
    if (host.includes('tiktok.com')) return path.includes('/video/');
    if (host.includes('facebook.com')) return path.includes('/posts/') || path.includes('/videos/') || path.includes('/watch') || path.includes('/reel/') || path.includes('/photo') || path.includes('/story.php') || path.includes('/permalink.php');
    if (host.includes('x.com') || host.includes('twitter.com')) return path.includes('/status/');
    return path.length > 1;
  } catch {
    return false;
  }
}

function firstSpecificEvidenceUrl(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (typeof value !== 'string') continue;
    const trimmed = value.trim();
    if (trimmed && isSpecificEvidenceUrl(trimmed)) return trimmed;
  }
  return '';
}

function analyticsRankingVideoUrl(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const latestKeys = ['latestVideoUrl', 'latest_video_url', 'latestEvidenceUrl', 'latest_evidence_url'];
  const topKeys = ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url'];
  return firstSpecificEvidenceUrl(row, sort === 'published' ? [...latestKeys, ...topKeys] : [...topKeys, ...latestKeys]);
}

function analyticsRankingVideoTitle(row: VkpiProjectRow, sort: AnalyticsRankingSort, projectTitle: string) {
  const latestTitle = retrospectiveTextField(row, ['latestEvidenceTitle', 'latest_evidence_title', 'latestVideoTitle', 'latest_video_title']);
  if (sort === 'published' && latestTitle) return latestTitle;
  return retrospectiveVideoTitle(row, projectTitle);
}

function analyticsRankingPublishDate(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const latestKeys = ['latestEvidencePublishDate', 'latest_evidence_publish_date', 'latestPublishDate', 'latest_publish_date'];
  const topKeys = ['evidencePublishDate', 'evidence_publish_date', 'publishDate', 'publish_date', 'publishedAt', 'published_at'];
  return retrospectiveTextField(row, sort === 'published' ? [...latestKeys, ...topKeys] : [...topKeys, ...latestKeys]);
}

function analyticsRankingPublishTime(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const raw = analyticsRankingPublishDate(row, sort);
  if (!raw) return Number.NEGATIVE_INFINITY;
  const time = Date.parse(raw);
  return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
}

function analyticsPublishDateLabel(raw: string) {
  if (!raw) return '发布时间待补';
  const time = Date.parse(raw);
  if (!Number.isFinite(time)) return '时间待确认';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(time));
}

function analyticsEvidenceCount(row: VkpiProjectRow) {
  return Math.max(
    row.evidenceCount || 0,
    retrospectiveNumberField(row, ['stageEventCount', 'evidence_count', 'videoEvidenceCount', 'video_evidence_count']),
  );
}

function analyticsHasPublishedEvidence(row: VkpiProjectRow) {
  return (
    analyticsEvidenceCount(row) > 0 ||
    (row.views || 0) > 0 ||
    Boolean(retrospectiveTextField(row, ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url'])) ||
    Boolean((row as unknown as Record<string, unknown>).videoMetrics)
  );
}

function analyticsWatchTime(row: VkpiProjectRow) {
  const direct = retrospectiveTextField(row, ['watchTime', 'watch_time', 'completionRate', 'completion_rate', 'duration', 'durationLabel', 'duration_label']);
  if (direct) return direct;
  const pct = retrospectiveNumberField(row, ['watchTimePct', 'watch_time_pct', 'completionPct', 'completion_pct']);
  if (pct > 0) return `${Math.round(pct)}%`;
  // Project detail rows do not expose watch-time/completion metrics yet.
  return '—';
}

export function CampaignAnalyticsTab({
  rows,
  stats,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
}) {
  const [rankingSort, setRankingSort] = useState<AnalyticsRankingSort>('views');
  const totalLikes = rows.reduce((sum, row) => sum + (row.likes || 0), 0);
  const totalComments = rows.reduce((sum, row) => sum + (row.comments || 0), 0);
  const publishedKols = rows.filter(analyticsHasPublishedEvidence);
  const projectTotalCost = stats.cost || 0;
  // null=归因链路不存在(显"—");0=有链路但值为零(显 $0/0)。
  const roi = stats.gmv != null && projectTotalCost > 0 ? ((stats.gmv / projectTotalCost) * 100).toFixed(1) : '—';
  const kpis: Array<[string, string, typeof Eye, string]> = [
    ['总曝光', formatLargeNum(stats.views), Eye, '#06b6d4'],
    ['总点赞', formatLargeNum(totalLikes), Heart, '#ec4899'],
    ['总评论', formatLargeNum(totalComments), MessageCircle, '#a855f7'],
    ['Shopify 点击', stats.clicks == null ? '—' : formatLargeNum(stats.clicks), ShoppingCart, '#fb923c'],
    ['归因 GMV', formatMoneyShort(stats.gmv), DollarSign, '#10b981'],
    ['ROI', `${roi}${roi !== '—' ? '%' : ''}`, TrendingUp, '#10b981'],
  ];
  const rankedRows = [...publishedKols]
    .sort((a, b) => {
      if (rankingSort === 'published') {
        const aTime = analyticsRankingPublishTime(a, rankingSort);
        const bTime = analyticsRankingPublishTime(b, rankingSort);
        if (aTime !== bTime) return bTime > aTime ? 1 : -1;
      }
      return ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0));
    });

  return (
    <div className="p-4 space-y-4" aria-label="项目数据汇总">
      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <div className="shrink-0 w-7 h-7 rounded-full bg-purple-500/20 flex items-center justify-center">
          <Sparkles size={13} className="text-purple-300" />
        </div>
        <div className="flex-1">
          <div className="text-[11px] font-medium text-purple-200 mb-0.5">AI 项目数据洞察</div>
          <div className="text-[10.5px] text-slate-300 leading-relaxed">
            {`${publishedKols.length}/${rows.length} 已发布,总曝光 ${formatLargeNum(stats.views)} · Shopify 点击 ${formatLargeNum(stats.clicks)},归因 GMV ${formatMoneyShort(stats.gmv)} · ROI ${roi}%。总成本 (当前 stats.cost 口径) ${formatMoneyShort(projectTotalCost)}`}
          </div>
          <div className="mt-1 text-[10px] text-amber-300 leading-relaxed">
            曝光/点赞/评论/排名仅统计已归属当前项目的数据,Shopify / GMV / ROI 等待独立归因链路。
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {kpis.map(([label, value, Icon, color]) => (
          <div key={label} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-slate-500">{label}</span>
              <Icon size={11} style={{ color }} />
            </div>
            <div className="text-[18px] font-bold tabular-nums" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-[12.5px] font-semibold text-white">KOL 性能排名</h4>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-500">{publishedKols.length} 个已发布</span>
            <div className="flex items-center rounded-lg border border-white/[0.06] bg-black/20 p-0.5">
              {[
                ['views', '按播放量'],
                ['published', '按发布时间'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRankingSort(value as AnalyticsRankingSort)}
                  className={`rounded-md px-2 py-1 text-[10px] font-medium transition ${
                    rankingSort === value
                      ? 'bg-purple-500/25 text-purple-200'
                      : 'text-slate-500 hover:text-white'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {publishedKols.length === 0 ? (
          <div className="text-center py-6 text-[11px] text-slate-500">暂无已发布视频</div>
        ) : (
          <div className="space-y-2">
            {rankedRows.map((row, index) => {
              const avatarName = row.kolName || row.kolHandle || '-';
              const watchTime = analyticsWatchTime(row);
              const profileUrl = analyticsKolProfileUrl(row);
              const videoUrl = analyticsRankingVideoUrl(row, rankingSort);
              const videoTitle = analyticsRankingVideoTitle(row, rankingSort, '项目内容');
              const publishDate = analyticsRankingPublishDate(row, rankingSort);
              const avatarNode = row.kolAvatar ? (
                <img
                  src={row.kolAvatar}
                  alt={avatarName}
                  className="w-7 h-7 rounded-full object-cover shrink-0 border border-white/[0.08]"
                />
              ) : (
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0"
                  style={{ background: 'linear-gradient(135deg,#a855f7,#ec4899)' }}
                >
                  {avatarName.charAt(0).toUpperCase()}
                </div>
              );
              return (
                <div key={row.id} className="flex items-center gap-3 px-2 py-2 rounded hover:bg-white/[0.02]">
                  <div className="text-[11px] font-bold text-slate-500 w-5">#{index + 1}</div>
                  {profileUrl ? (
                    <a
                      href={profileUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-3 flex-1 min-w-0 hover:opacity-90"
                      title={`打开 ${row.kolName || row.kolHandle} 主页`}
                    >
                      {avatarNode}
                      <div className="min-w-0">
                        <div className="text-[11.5px] text-white font-medium truncate">{row.kolHandle || row.kolName}</div>
                        <div className="text-[10px] text-slate-500 truncate">{row.platform} · 完播 {watchTime}</div>
                      </div>
                    </a>
                  ) : (
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      {avatarNode}
                      <div className="min-w-0">
                        <div className="text-[11.5px] text-white font-medium truncate">{row.kolHandle || row.kolName}</div>
                        <div className="text-[10px] text-slate-500 truncate">{row.platform} · 完播 {watchTime}</div>
                      </div>
                    </div>
                  )}
                  <div className="text-right">
                    {videoUrl ? (
                      <div className="flex flex-col items-end">
                        <a
                          href={videoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[12px] font-semibold text-white tabular-nums hover:text-cyan-300"
                          title={`打开视频: ${videoTitle}`}
                        >
                          {formatLargeNum(row.views)}
                        </a>
                        <a
                          href={videoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[9.5px] font-medium text-cyan-300 hover:text-cyan-200"
                          title={`打开视频: ${videoTitle}`}
                        >
                          看视频
                        </a>
                      </div>
                    ) : profileUrl ? (
                      <div className="flex flex-col items-end">
                        <a
                          href={profileUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[12px] font-semibold text-slate-300 tabular-nums hover:text-cyan-300"
                          title={`数据库暂无视频 evidence,打开 ${row.kolName || row.kolHandle} 主页继续搜索`}
                        >
                          {formatLargeNum(row.views)}
                        </a>
                        <a
                          href={profileUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[9.5px] font-medium text-amber-300 hover:text-amber-200"
                          title="数据库暂无视频 evidence,先打开主页；Apify 自动搜索产品 URL 需要单独后端任务接入"
                        >
                          主页搜索
                        </a>
                      </div>
                    ) : (
                      <div className="flex flex-col items-end">
                        <div className="text-[12px] font-semibold text-slate-500 tabular-nums">{formatLargeNum(row.views)}</div>
                        <div className="text-[9.5px] font-medium text-slate-600" title="当前项目下没有视频 evidence URL,也没有可用主页">无可用入口</div>
                      </div>
                    )}
                    <div className="text-[9.5px] text-slate-500">
                      {rankingSort === 'published' ? analyticsPublishDateLabel(publishDate) : '播放'}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px] font-semibold tabular-nums" style={{ color: row.shopifyLink ? '#10b981' : '#64748b' }}>
                      {row.shopifyLink ? formatMoneyShort(row.gmv) : '—'}
                    </div>
                    <div className="text-[9.5px] text-slate-500">GMV</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
