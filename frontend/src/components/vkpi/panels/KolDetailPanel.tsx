import { useState } from 'react';
import type { VkpiKolDetail, VkpiPlatform, VkpiProjectRow } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';
import { DetailSection } from '../shared/DetailSection';
import { PlatformDot } from '../shared/PlatformDot';
import { PlatformPill } from '../shared/PlatformPill';
import { StageBadge } from '../shared/StageBadge';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';

const messageTypeLabels: Record<string, string> = {
  DM: '私信',
  Email: '邮件',
  'Comment reply': '评论回复',
  Note: '备注',
};

function ContentThumbnail({ src, index, title }: { src?: string; index: number; title: string }) {
  const [failed, setFailed] = useState(false);
  const markFailed = () => setFailed(true);
  if (!src || failed) {
    return <div className={`vkpi-content-preview__thumb thumb-${(index % 3) + 1}`}><i>{title.slice(0, 1).toUpperCase() || 'V'}</i></div>;
  }
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={markFailed}
      onLoad={(event) => {
        if (event.currentTarget.naturalWidth <= 1) markFailed();
      }}
    />
  );
}

export function KolDetailPanel({
  kol,
  selectedProject,
  onCopyShortLink,
}: {
  kol: VkpiKolDetail;
  selectedProject?: VkpiProjectRow;
  onCopyShortLink?: (slug: string) => void;
}) {
  return (
    <section className="vkpi-card vkpi-detail-panel">
      <div className="vkpi-detail-panel__chrome">
        <button type="button" aria-label="关闭详情面板">×</button>
        <div>
          <button type="button" aria-label="上一个红人">‹</button>
          <button type="button" aria-label="下一个红人">›</button>
        </div>
      </div>

      <div className="vkpi-profile-card">
        <Avatar name={kol.name} src={kol.avatar} size="lg" />
        <div>
          <h3>{kol.name} {kol.verified ? <span>✓</span> : null}</h3>
          <p><PlatformPill platform={kol.platform} /> <span>{kol.handle}</span></p>
        </div>
      </div>

      <div className="vkpi-profile-stats">
        <div><strong>{kol.subscribersLabel}</strong><span>粉丝</span></div>
        <div><strong>{kol.videosLabel}</strong><span>视频/帖子</span></div>
        <div><strong>{kol.engagementLabel}</strong><span>互动率</span></div>
      </div>

      <div className="vkpi-profile-stats vkpi-profile-stats--secondary">
        <div><strong>{kol.avgViewsLabel || '-'}</strong><span>平均播放</span></div>
        <div><strong>{kol.totalLikesLabel || '-'}</strong><span>总点赞</span></div>
        <div><strong>{kol.accountScoreLabel || '-'}</strong><span>综合评分</span></div>
      </div>

      <DetailSection title="用户画像">
        <div className="vkpi-assessment-card vkpi-assessment-card--persona">
          <div><span>画像</span><strong>{kol.personaLabel || '待判断'}</strong></div>
          <div><span>优先级</span><strong>{kol.priorityLabel || '待评估'}</strong></div>
          <div><span>适配产品</span><strong>{kol.productFitSummary || '-'}</strong></div>
          {kol.personaReason ? <p>{kol.personaReason}</p> : null}
          {kol.contactAction ? <p>{kol.contactAction}</p> : null}
        </div>
      </DetailSection>

      <DetailSection title="联系方式">
        <div className="vkpi-contact-card">
          {kol.contactEmail ? (
            <a href={`mailto:${kol.contactEmail}`}><span>邮箱</span><strong>{kol.contactEmail}</strong></a>
          ) : null}
          {kol.contactPhone ? (
            <div><span>电话 / WhatsApp</span><strong>{kol.contactPhone}</strong></div>
          ) : null}
          {kol.profileUrl ? (
            <a href={kol.profileUrl} target="_blank" rel="noreferrer"><span>主页</span><strong>{kol.profileUrl}</strong></a>
          ) : null}
          {kol.contactLinks?.length ? kol.contactLinks.slice(0, 6).map((link, index) => (
            link.url ? (
              <a href={link.url} target="_blank" rel="noreferrer" key={`${link.url}-${index}`}>
                <span>{link.label}</span><strong>{link.value}</strong>
              </a>
            ) : (
              <div key={`${link.value}-${index}`}><span>{link.label}</span><strong>{link.value}</strong></div>
            )
          )) : null}
          {!kol.contactEmail && !kol.contactPhone && !kol.profileUrl && !kol.contactLinks?.length ? (
            <div className="vkpi-empty-state">暂未抓到公开联系方式；可手动补录邮箱后重新查重。</div>
          ) : null}
        </div>
      </DetailSection>

      <DetailSection title="账号评估">
        <div className="vkpi-assessment-card">
          <div><span>受众匹配</span><strong>{kol.audienceFitLabel || '-'}</strong></div>
          <div><span>产品匹配</span><strong>{kol.productFitLabel || '-'}</strong></div>
          <div><span>风险</span><strong>{kol.riskLevel || '-'}</strong></div>
          <p>{kol.recommendedAction || '暂无真实评估报告。'}</p>
          <small>抓取状态：{kol.scanStatus || '未抓取'}{kol.scannedAt ? ` · ${kol.scannedAt}` : ''}</small>
          {kol.scanError ? <em>{kol.scanError}</em> : null}
        </div>
      </DetailSection>

      {selectedProject ? (
        <div className="vkpi-mini-project-card">
          <span>当前项目</span>
          <strong>{selectedProject.campaign.replace('\n', ' · ')}</strong>
          <div><StageBadge stage={selectedProject.stage} /><em>{selectedProject.ownerName}</em></div>
          <small>项目耗时：{selectedProject.totalDurationLabel || '-'} · 当前阶段：{selectedProject.stageDurationLabel || '-'}</small>
        </div>
      ) : null}

      <DetailSection title="近期内容" action="查看全部">
        {kol.recentContent.length ? (
          <div className="vkpi-content-preview-grid">
            {kol.recentContent.map((content, index) => (
              <article className="vkpi-content-preview" key={content.id}>
                <ContentThumbnail src={content.imageUrl} index={index} title={content.title} />
                {content.duration ? <span>{content.duration}</span> : null}
                <strong>{content.title}</strong>
                {content.engagementLabel ? <small>{content.engagementLabel}</small> : null}
              </article>
            ))}
          </div>
        ) : (
          <div className="vkpi-empty-state">暂无已抓取内容。</div>
        )}
      </DetailSection>

      <DetailSection title="消息记录" action="查看全部">
        <div className="vkpi-message-capture">
          {kol.messages.length ? (
            kol.messages.map((message) => (
              <article key={message.id}>
                <PlatformDot platform={message.source as VkpiPlatform} />
                <div>
                  <header><span>{message.capturedAt}</span><em>{messageTypeLabels[message.type] || message.type}</em></header>
                  <p>{message.snippet}</p>
                </div>
              </article>
            ))
          ) : (
            <div className="vkpi-empty-state">当前红人暂无消息证据。</div>
          )}
        </div>
      </DetailSection>

      <DetailSection title="短链">
        <div className="vkpi-shortlink-card">
          <div>
            <strong>{kol.shortLink.slug}</strong>
            <span>{kol.shortLink.destination}</span>
          </div>
          <button className="vkpi-mini-button" type="button" onClick={() => onCopyShortLink?.(kol.shortLink.slug)}>复制</button>
        </div>
        <div className="vkpi-shortlink-metrics">
          <div><strong>{numberFormatter.format(kol.shortLink.clicks)}</strong><span>点击</span></div>
          <div><strong>{numberFormatter.format(kol.shortLink.orders)}</strong><span>订单</span></div>
          <div><strong>{currencyFormatter.format(kol.shortLink.gmv)}</strong><span>销售额</span></div>
          <div><strong>{selectedProject?.totalDurationLabel || '-'}</strong><span>项目耗时</span></div>
        </div>
      </DetailSection>

      <DetailSection title="跟进备注">
        <div className="vkpi-note-card">
          <p>{kol.followUpNote}</p>
          <button className="vkpi-mini-button" type="button">编辑</button>
        </div>
      </DetailSection>
    </section>
  );
}
