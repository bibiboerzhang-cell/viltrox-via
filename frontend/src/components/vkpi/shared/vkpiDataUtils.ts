import type { VkpiContactLink, VkpiKolDetail, VkpiKolLookupResult, VkpiKolProfile, VkpiLinkRow, VkpiPlatform, VkpiProjectRow, VkpiProjectStage } from '../vkpiTypes';
import { platformLabels, primaryStageFlow, stageLabels } from './vkpiConstants';
import { currencyFormatter } from './vkpiFormatters';

export function safeNumber(value: unknown): number {
  const numberValue = Number(value || 0);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

export function formatMoneyCents(value: unknown): string {
  return currencyFormatter.format(safeNumber(value) / 100);
}

export function compactCount(value: unknown): string {
  const parsed = safeNumber(value);
  return parsed ? new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(parsed) : '-';
}

export function rateLabel(value: unknown): string {
  const parsed = safeNumber(value);
  if (!parsed) return '-';
  return `${(parsed * 100).toFixed(2)}%`;
}

export function scoreLabel(value: unknown): string {
  const parsed = safeNumber(value);
  return parsed ? `${Math.round(parsed)}/100` : '-';
}

export function pickText(fallback = '-', ...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return fallback;
}

export function textValue(value: unknown, fallback = '-'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

export function objectValue(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function arrayValue(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value !== 'string' || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function contactLinksFrom(value: unknown): VkpiContactLink[] {
  const links: VkpiContactLink[] = [];
  arrayValue(value).forEach((item) => {
    if (typeof item === 'string') {
      links.push({
        label: item.includes('@') && !item.startsWith('http') ? 'Email' : '链接',
        value: item,
        url: item.startsWith('http') || item.startsWith('mailto:') ? item : undefined,
      });
      return;
    }
    const row = objectValue(item);
    const url = textValue(row.url || row.href, '');
    const valueText = textValue(row.value || row.label || url, '');
    if (!valueText && !url) return;
    links.push({
      label: textValue(row.label, url.includes('mailto:') ? 'Email' : '链接'),
      value: valueText || url,
      url: url || undefined,
    });
  });
  return links;
}

export function platformFromRaw(value: unknown): VkpiPlatform {
  const raw = String(value || '').trim().toLowerCase();
  if (raw.includes('youtube') || raw === 'yt') return 'YouTube';
  if (raw.includes('instagram') || raw === 'ig') return 'Instagram';
  if (raw.includes('tiktok') || raw === 'tt') return 'TikTok';
  if (raw.includes('xhs') || raw.includes('xiaohongshu')) return 'XHS';
  if (raw.includes('bilibili') || raw === 'bili') return 'Bilibili';
  if (raw.includes('facebook') || raw === 'fb') return 'Facebook';
  if (raw.includes('reddit')) return 'Reddit';
  if (raw === 'x' || raw.includes('twitter')) return 'X';
  if (raw.includes('threads')) return 'Threads';
  if (raw.includes('twitch')) return 'Twitch';
  if (raw.includes('pinterest')) return 'Pinterest';
  if (raw.includes('vimeo')) return 'Vimeo';
  if (raw.includes('discord')) return 'Discord';
  if (raw.includes('website') || raw.includes('blog') || raw.includes('site')) return 'Website';
  if (raw.includes('weibo')) return 'Weibo';
  if (raw.includes('douyin') || raw.includes('抖音')) return 'Douyin';
  if (raw.includes('zhihu') || raw.includes('知乎')) return 'Zhihu';
  if (raw.includes('linkedin')) return 'LinkedIn';
  if (raw.includes('telegram')) return 'Telegram';
  if (raw.includes('newsletter')) return 'Newsletter';
  if (raw.includes('forum') || raw.includes('community')) return 'Forum';
  if (raw.includes('email')) return 'Email';
  return 'Other';
}

export function platformDisplay(value: unknown): string {
  const normalized = platformFromRaw(value);
  return platformLabels[normalized] || String(value || '-');
}

export function lookupResultToKolDetail(result: VkpiKolLookupResult | null, fallback: VkpiKolDetail): VkpiKolDetail {
  if (!result?.kol) return fallback;
  const kol = result.kol;
  const snapshot = result.dossier?.snapshot || {};
  const report = result.dossier?.report || {};
  const reportRaw = objectValue(report.raw_json);
  const posts = result.dossier?.posts || [];
  const comments = result.dossier?.comments || [];
  const snapshotRaw = objectValue(snapshot.raw_json);
  const scanProfile = objectValue(snapshotRaw.profile || result.scan_result?.profile);
  const scanRaw = objectValue(result.scan_result);
  const kolContactRaw = objectValue(kol.contact_raw_json);
  const rawContactLinks = [
    ...contactLinksFrom(kol.contact_links_json),
    ...contactLinksFrom(kolContactRaw.contact_links),
    ...contactLinksFrom(snapshotRaw.contact_links),
    ...contactLinksFrom(scanProfile.contact_links),
  ];
  const contactLinks = rawContactLinks.filter((link, index, all) => {
    const key = String(link.url || link.value || '').toLowerCase();
    return key && all.findIndex((item) => String(item.url || item.value || '').toLowerCase() === key) === index;
  });
  const platform = platformFromRaw(snapshot.platform || kol.platform || result.query?.platform);
  const handle = textValue(snapshot.handle || result.query?.handle || kol.channel_name || kol.channel_url);
  const followerCount = safeNumber(snapshot.follower_count || kol.follower_count);
  const derivedPostMetrics = posts.reduce<{ views: number; likes: number; comments: number }>((acc, post) => {
    const raw = objectValue(post.raw_json);
    return {
      views: acc.views + safeNumber(post.views || post.view_count || post.play_count || raw.views || raw.view_count || raw.play_count),
      likes: acc.likes + safeNumber(post.likes || post.like_count || raw.likes || raw.like_count),
      comments: acc.comments + safeNumber(post.comments || post.comment_count || raw.comments || raw.comment_count),
    };
  }, { views: 0, likes: 0, comments: 0 });
  const contentCount = safeNumber(snapshot.content_count || posts.length);
  const avgViews = safeNumber(snapshot.avg_views || kol.avg_views) || (posts.length ? Math.round(derivedPostMetrics.views / posts.length) : 0);
  const engagement = safeNumber(snapshot.engagement_rate) || (followerCount ? (derivedPostMetrics.likes + derivedPostMetrics.comments) / followerCount : avgViews ? (derivedPostMetrics.likes + derivedPostMetrics.comments) / avgViews : 0);
  const totalLikes = safeNumber(snapshot.total_likes) || derivedPostMetrics.likes;
  const scanStatus = textValue(snapshot.scan_status || result.scan_result?.status, '未抓取');
  const scanError = textValue(snapshot.error_message || result.scan_result?.error, '');
  return {
    id: String(kol.id || ''),
    name: textValue(kol.media_name || kol.owner_name || kol.channel_name || handle, '未命名红人'),
    handle: handle.startsWith('@') || handle === '-' ? handle : `@${handle}`,
    platform,
    avatar: textValue(
      kol.avatar_url ||
      snapshotRaw.avatar_url ||
      snapshotRaw.profile_pic_url ||
      snapshotRaw.profilePicUrl ||
      scanProfile.avatar_url ||
      scanProfile.profile_pic_url ||
      scanProfile.profilePicUrl ||
      scanProfile.profile_pic_url_hd ||
      scanRaw.avatar_url ||
      scanRaw.profile_pic_url ||
      scanRaw.profilePicUrl,
      '',
    ),
    profileUrl: textValue(kol.profile_url || snapshot.account_url || snapshotRaw.profile_url || scanProfile.profile_url || result.scan_result?.profile_url, ''),
    contactEmail: textValue(kol.contact_email || snapshotRaw.contact_email || scanProfile.contact_email || result.scan_result?.contact_email, ''),
    contactPhone: textValue(kol.contact_phone, ''),
    contactLinks,
    verified: false,
    subscribersLabel: compactCount(followerCount),
    videosLabel: compactCount(contentCount),
    engagementLabel: rateLabel(engagement),
    totalViewsLabel: compactCount(snapshot.total_views),
    totalLikesLabel: compactCount(totalLikes),
    avgViewsLabel: compactCount(avgViews),
    personaLabel: pickText('待判断', report.user_persona, report.persona, reportRaw.user_persona, reportRaw.persona),
    priorityLabel: pickText('待评估', report.cooperation_priority, report.priority, reportRaw.cooperation_priority, reportRaw.priority),
    productFitSummary: pickText('-', report.product_fit_summary, report.product_fit_text, reportRaw.product_fit_summary, reportRaw.product_fit_text),
    personaReason: pickText('', report.persona_reason, report.classification_reason, reportRaw.persona_reason, reportRaw.classification_reason),
    contactAction: pickText('', report.contact_action, report.next_action, reportRaw.contact_action, reportRaw.next_action),
    accountScoreLabel: scoreLabel(report.account_score),
    audienceFitLabel: scoreLabel(report.audience_fit),
    productFitLabel: scoreLabel(report.product_fit),
    riskLevel: textValue(report.risk_level, '-'),
    recommendedAction: textValue(report.recommended_action || report.summary_zh, scanError || '当前没有账号评估报告。'),
    scanStatus,
    scanError,
    scannedAt: textValue(snapshot.scanned_at, ''),
    country: textValue(kol.country, ''),
    claimOwner: textValue(result.claim?.staff_name || result.claim?.staff_email, result.can_claim ? '可由当前员工认领' : '未绑定负责人'),
    claimStatus: result.can_claim ? '可认领' : result.claim ? '已认领' : '未认领',
    recentContent: posts.slice(0, 24).map((post, index) => {
      const raw = objectValue(post.raw_json);
      const imageUrl = textValue(
        post.thumbnail_url ||
        post.thumbnail ||
        post.thumbnailUrl ||
        post.cover_url ||
        post.coverUrl ||
        post.image_url ||
        post.imageUrl ||
        post.display_url ||
        post.displayUrl ||
        post.preview_url ||
        post.previewUrl ||
        raw.thumbnail_url ||
        raw.thumbnail ||
        raw.thumbnailUrl ||
        raw.cover_url ||
        raw.coverUrl ||
        raw.image_url ||
        raw.imageUrl ||
        raw.display_url ||
        raw.displayUrl ||
        raw.preview_url ||
        raw.previewUrl,
        '',
      );
      const url = textValue(
        post.post_url ||
        post.postUrl ||
        post.url ||
        post.permalink ||
        post.shortCodeUrl ||
        raw.post_url ||
        raw.postUrl ||
        raw.url ||
        raw.permalink ||
        raw.shortCodeUrl ||
        raw.link,
        '',
      );
      const videoUrl = textValue(
        post.video_url ||
        post.videoUrl ||
        post.videoUrlNoWaterMark ||
        post.video_url_no_watermark ||
        post.video_download_url ||
        post.videoDownloadUrl ||
        post.downloadUrl ||
        post.downloadAddr ||
        post.media_url ||
        post.mediaUrl ||
        post.play_url ||
        post.playUrl ||
        raw.video_url ||
        raw.videoUrl ||
        raw.video_download_url ||
        raw.videoDownloadUrl ||
        raw.media_url ||
        raw.mediaUrl ||
        raw.play_url ||
        raw.playUrl ||
        raw.videoUrlNoWaterMark ||
        raw.video_url_no_watermark,
        '',
      );
      return ({
      id: String(post.id || post.post_url || raw.url || index),
      title: textValue(post.title || post.caption || raw.title || raw.caption, '未命名内容'),
      imageUrl,
      url,
      videoUrl,
      platform: platformFromRaw(post.platform || platform),
      postedAt: textValue(post.published_at, ''),
      engagementLabel: `播放 ${compactCount(post.views || post.view_count || post.play_count || raw.views || raw.view_count || raw.play_count)} · 赞 ${compactCount(post.likes || post.like_count || raw.likes || raw.like_count)} · 评 ${compactCount(post.comments || post.comment_count || raw.comments || raw.comment_count)}`,
    });
    }),
    messages: comments.slice(0, 20).map((comment, index) => ({
      id: String(comment.id || index),
      source: platform,
      type: 'Comment reply',
      capturedAt: textValue(comment.created_at, '-'),
      snippet: textValue(comment.comment_text, '已抓取评论但没有文本。'),
    })),
    shortLink: {
      slug: '暂无短链',
      destination: '-',
      clicks: 0,
      orders: 0,
      gmv: 0,
      roi: 0,
    },
    followUpNote: scanError
      ? `账号抓取未完成：${scanError}`
      : '查重完成后可创建项目、推进状态，并生成 Shopify 短链。所有指标来自真实抓取/导入数据。',
  };
}

export function coerceProjectStage(value: unknown): VkpiProjectStage {
  const raw = String(value || 'discovery').trim().toLowerCase();
  if (raw === 'posted') return 'published';
  if (raw === 'content_published') return 'content_published';
  return (Object.prototype.hasOwnProperty.call(stageLabels, raw) ? raw : 'discovery') as VkpiProjectStage;
}

export function nextProjectStage(stage: VkpiProjectStage): VkpiProjectStage | null {
  const index = primaryStageFlow.indexOf(stage);
  if (index < 0 || index >= primaryStageFlow.length - 1) return null;
  return primaryStageFlow[index + 1];
}

export function previousProjectStage(stage: VkpiProjectStage): VkpiProjectStage | null {
  const index = primaryStageFlow.indexOf(stage);
  if (index <= 0) return null;
  return primaryStageFlow[index - 1];
}

export function shortDateTime(value?: string): string {
  const raw = String(value || '').trim();
  if (!raw || raw === '-') return '—';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.length > 16 ? raw.slice(0, 16) : raw;
  return parsed.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function profileToKolDetail(profile: VkpiKolProfile | null, fallback: VkpiKolDetail, selectedProject?: VkpiProjectRow, links: VkpiLinkRow[] = []): VkpiKolDetail {
  if (!profile?.kol) return fallback;
  const kol = profile.kol;
  const summary = profile.summary || {};
  const snapshot = profile.snapshot || {};
  const snapshotRaw = objectValue(snapshot.raw_json);
  const snapshotProfile = objectValue(snapshotRaw.profile);
  const report = profile.analysis_report || {};
  const contacts = objectValue(profile.contacts);
  const contactLinks = [
    ...contactLinksFrom(contacts.links),
    ...contactLinksFrom(kol.contact_links_json),
  ].filter((link, index, all) => {
    const key = String(link.url || link.value || '').toLowerCase();
    return key && all.findIndex((item) => String(item.url || item.value || '').toLowerCase() === key) === index;
  });
  const platform = platformFromRaw(snapshot.platform || kol.platform || selectedProject?.platform);
  const handle = textValue(snapshot.handle || kol.channel_name || selectedProject?.kolHandle, fallback.handle);
  const followerCount = safeNumber(summary.follower_count || snapshot.follower_count || kol.follower_count);
  const rawPosts = [...(profile.content_posts || []), ...(profile.posts || [])];
  const contentCount = safeNumber(summary.content_count || rawPosts.length);
  const derivedPostMetrics = rawPosts.reduce<{ views: number; likes: number; comments: number }>((acc, post) => {
    const raw = objectValue(post.raw_json);
    return {
      views: acc.views + safeNumber(post.views || post.view_count || post.play_count || raw.views || raw.view_count || raw.play_count || raw.videoViewCount || raw.videoPlayCount),
      likes: acc.likes + safeNumber(post.likes || post.like_count || raw.likes || raw.like_count || raw.likesCount),
      comments: acc.comments + safeNumber(post.comments || post.comment_count || raw.comments || raw.comment_count || raw.commentsCount),
    };
  }, { views: 0, likes: 0, comments: 0 });
  const avgViews = safeNumber(summary.avg_views || snapshot.avg_views || kol.avg_views) || (rawPosts.length ? Math.round(derivedPostMetrics.views / rawPosts.length) : 0);
  const engagement = safeNumber(summary.engagement_rate || snapshot.engagement_rate) || (followerCount ? (derivedPostMetrics.likes + derivedPostMetrics.comments) / followerCount : avgViews ? (derivedPostMetrics.likes + derivedPostMetrics.comments) / avgViews : 0);
  const profileLinks = profile.links || [];
  const firstProfileLink = profileLinks[0];
  const linkedShort = links.find((row) => selectedProject?.id && row.projectId === selectedProject.id);
  const shortLink = firstProfileLink ? {
    slug: textValue(firstProfileLink.slug, '暂无短链'),
    destination: textValue(firstProfileLink.destination_url, '-'),
    clicks: safeNumber(firstProfileLink.valid_click_count || firstProfileLink.click_count),
    orders: safeNumber(firstProfileLink.order_count || firstProfileLink.orders),
    gmv: safeNumber(firstProfileLink.revenue_cents || firstProfileLink.gmv_cents) / 100,
    roi: safeNumber(firstProfileLink.roi),
  } : linkedShort ? {
    slug: linkedShort.slug,
    destination: linkedShort.destination,
    clicks: linkedShort.validClicks,
    orders: linkedShort.orders,
    gmv: linkedShort.gmv,
    roi: selectedProject?.roi || 0,
  } : fallback.shortLink;
  const profileMessages = (profile.messages || []).slice(0, 20).map((message, index) => ({
    id: String(message.id || index),
    source: platformFromRaw(message.source || message.source_platform || platform),
    type: textValue(message.direction, 'Note') === 'inbound' ? 'DM' as const : 'Note' as const,
    capturedAt: textValue(message.captured_at || message.created_at, '-'),
    snippet: textValue(message.snippet || message.body, '已记录消息但没有摘要。'),
    evidenceUrl: textValue(message.evidence_url, '') || undefined,
  }));
  const profileContent = rawPosts.slice(0, 24).map((post, index) => {
    const raw = objectValue(post.raw_json);
    const imageUrl = textValue(
      post.thumbnail_url ||
      post.thumbnail ||
      post.thumbnailUrl ||
      post.cover_url ||
      post.coverUrl ||
      post.image_url ||
      post.imageUrl ||
      post.display_url ||
      post.displayUrl ||
      post.preview_url ||
      post.previewUrl ||
      raw.thumbnail_url ||
      raw.thumbnail ||
      raw.thumbnailUrl ||
      raw.cover_url ||
      raw.coverUrl ||
      raw.image_url ||
      raw.imageUrl ||
      raw.display_url ||
      raw.displayUrl ||
      raw.preview_url ||
      raw.previewUrl,
      '',
    );
    const url = textValue(
      post.post_url ||
      post.postUrl ||
      post.url ||
      post.permalink ||
      post.shortCodeUrl ||
      raw.post_url ||
      raw.postUrl ||
      raw.url ||
      raw.permalink ||
      raw.shortCodeUrl ||
      raw.link,
      '',
    );
    const videoUrl = textValue(
      post.video_url ||
      post.videoUrl ||
      post.videoUrlNoWaterMark ||
      post.video_url_no_watermark ||
      post.video_download_url ||
      post.videoDownloadUrl ||
      post.downloadUrl ||
      post.downloadAddr ||
      post.media_url ||
      post.mediaUrl ||
      post.play_url ||
      post.playUrl ||
      raw.video_url ||
      raw.videoUrl ||
      raw.video_download_url ||
      raw.videoDownloadUrl ||
      raw.media_url ||
      raw.mediaUrl ||
      raw.play_url ||
      raw.playUrl ||
      raw.videoUrlNoWaterMark ||
      raw.video_url_no_watermark,
      '',
    );
    const views = post.views || post.view_count || post.play_count || raw.views || raw.view_count || raw.play_count || raw.videoViewCount || raw.videoPlayCount;
    const likes = post.likes || post.like_count || raw.likes || raw.like_count || raw.likesCount;
    const comments = post.comments || post.comment_count || raw.comments || raw.comment_count || raw.commentsCount;
    return {
      id: String(post.id || post.post_url || raw.url || index),
      title: textValue(post.title || post.caption || raw.title || raw.caption || post.post_url, '未命名内容'),
      imageUrl,
      url,
      videoUrl,
      platform: platformFromRaw(post.platform || platform),
      postedAt: textValue(post.published_at || post.created_at, ''),
      engagementLabel: `播放 ${compactCount(views)} · 赞 ${compactCount(likes)} · 评 ${compactCount(comments)}`,
    };
  });
  return {
    ...fallback,
    id: String(kol.id || fallback.id),
    name: textValue(kol.media_name || kol.owner_name || kol.channel_name || fallback.name, '未命名红人'),
    handle: handle.startsWith('@') || handle === '-' ? handle : `@${handle}`,
    platform,
    avatar: textValue(
      kol.avatar_url ||
      snapshot.avatar_url ||
      snapshotRaw.avatar_url ||
      snapshotRaw.avatarUrl ||
      snapshotRaw.profile_pic_url ||
      snapshotRaw.profilePicUrl ||
      snapshotProfile.avatar_url ||
      snapshotProfile.avatarUrl ||
      snapshotProfile.profile_pic_url ||
      snapshotProfile.profilePicUrl ||
      fallback.avatar,
      '',
    ),
    profileUrl: textValue(contacts.profile_url || kol.profile_url || kol.channel_url || snapshot.account_url || snapshotRaw.profile_url || snapshotProfile.profile_url || fallback.profileUrl, ''),
    contactEmail: textValue(contacts.email || kol.contact_email || snapshotRaw.contact_email || snapshotProfile.contact_email || fallback.contactEmail, ''),
    contactPhone: textValue(contacts.phone || kol.contact_phone || fallback.contactPhone, ''),
    contactLinks,
    subscribersLabel: compactCount(followerCount),
    videosLabel: compactCount(contentCount),
    engagementLabel: rateLabel(engagement),
    totalViewsLabel: compactCount(summary.total_views || snapshot.total_views || derivedPostMetrics.views),
    totalLikesLabel: compactCount(summary.total_likes || snapshot.total_likes || derivedPostMetrics.likes),
    avgViewsLabel: compactCount(avgViews),
    personaLabel: textValue(summary.user_persona || fallback.personaLabel, '待判断'),
    priorityLabel: textValue(report.cooperation_priority || fallback.priorityLabel, '待评估'),
    productFitSummary: textValue(report.product_fit_summary || report.product_fit_text || fallback.productFitSummary, '-'),
    personaReason: textValue(summary.persona_reason || fallback.personaReason, ''),
    accountScoreLabel: scoreLabel(summary.account_score || report.account_score),
    audienceFitLabel: scoreLabel(summary.audience_fit || report.audience_fit),
    productFitLabel: scoreLabel(summary.product_fit || report.product_fit),
    riskLevel: textValue(summary.risk_level || report.risk_level, '-'),
    recommendedAction: textValue(summary.recommended_action || report.recommended_action || report.summary_zh, '暂无真实评估报告。'),
    scanStatus: textValue(snapshot.scan_status || (snapshot.id ? '已同步' : ''), fallback.scanStatus || '未抓取'),
    scannedAt: textValue(snapshot.scanned_at, fallback.scannedAt || ''),
    claimOwner: textValue(profile.active_claim?.staff_name || profile.active_claim?.staff_email || fallback.claimOwner, '-'),
    claimStatus: profile.active_claim ? '已认领' : fallback.claimStatus,
    recentContent: profileContent,
    messages: profileMessages,
    shortLink,
    followUpNote: `Profile 已汇总 ${safeNumber(summary.project_count)} 个项目、${safeNumber(summary.message_count)} 条消息、${safeNumber(summary.published_content_count)} 条内容。`,
  };
}
