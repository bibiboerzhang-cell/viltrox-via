import type { VkpiPlatform, VkpiProjectStage } from '../../components/vkpi/vkpiTypes';

export function platformLabel(value: unknown): VkpiPlatform {
  const raw = String(value || '').trim().toLowerCase();
  if (raw.includes('youtube') || raw === 'yt') return 'YouTube';
  if (raw.includes('instagram') || raw === 'ig') return 'Instagram';
  if (raw.includes('tiktok') || raw === 'tt') return 'TikTok';
  if (raw.includes('bilibili') || raw === 'bili') return 'Bilibili';
  if (raw.includes('xhs') || raw.includes('xiaohongshu')) return 'XHS';
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

export function platformDisplayLabel(value: unknown): string {
  const labels: Record<string, string> = {
    YouTube: 'YouTube',
    Instagram: 'Instagram',
    TikTok: 'TikTok',
    Bilibili: 'Bilibili',
    XHS: '小红书',
    Facebook: 'Facebook',
    Reddit: 'Reddit',
    X: 'X',
    Threads: 'Threads',
    Twitch: 'Twitch',
    Pinterest: 'Pinterest',
    Vimeo: 'Vimeo',
    Discord: 'Discord',
    Website: '官网 / 博客',
    Weibo: '微博',
    Douyin: '抖音',
    Zhihu: '知乎',
    LinkedIn: 'LinkedIn',
    Telegram: 'Telegram',
    Newsletter: 'Newsletter',
    Forum: '论坛',
    Email: '邮件',
    Other: '其他',
  };
  const key = String(value || 'Other');
  return labels[key] || key;
}

export function stageValue(value: unknown): VkpiProjectStage {
  const raw = String(value || 'discovery').trim().toLowerCase();
  if (raw === 'negotiating' || raw === 'sample_preparing' || raw === 'content_due') return 'in_discussion';
  if (raw === 'posted' || raw === 'content_published') return 'content_published';
  if (['invited', 'discovery', 'contacted', 'replied', 'agreed', 'shipped', 'received', 'published', 'measured', 'closed', 'stalled', 'lost', 'released', 'cancelled'].includes(raw)) {
    return raw as VkpiProjectStage;
  }
  return 'discovery';
}
