import type { VkpiContactLink } from '../../vkpiTypes';
import { platformDisplay } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { KolMediaSlot, mediaBadge } from './MyKolMedia';
import {
  categoryForPost,
  compactContactValue,
  compactDate,
  conciseText,
  displayCount,
  initials,
} from './myKolMatrixData';
import {
  CONTENT_FILTER_OPTIONS,
  CONTENT_SORT_OPTIONS,
  CONTENT_WINDOW_OPTIONS,
  type ContactDraft,
  type EffectiveMyKolItem,
  type KolContentDirection,
  type KolContentFilter,
  type KolContentSort,
  type KolContentWindow,
  type KolCommentState,
  type KolPostState,
  type PostPreview,
} from './myKolMatrixTypes';

interface MyKolPostInsights {
  gearLabel: string;
  viltroxCount: number;
  competitorCount: number;
  otherCount: number;
  scanLabel: string;
  avgViews: number;
  engagement: number;
}

interface MyKolContentLayerProps {
  apiToken?: string;
  busyKolId: string;
  collapsed: boolean;
  contentDirection: KolContentDirection;
  contentFilter: KolContentFilter;
  contentSort: KolContentSort;
  contentWindow: KolContentWindow;
  editingContactId: string;
  postInsights: MyKolPostInsights;
  posts: PostPreview[];
  savingContactId: string;
  scanningKolId: string;
  selectedAvatar: string;
  selectedCommentState?: KolCommentState;
  selectedContacts: VkpiContactLink[];
  selectedContentLabel: string;
  selectedDraft?: ContactDraft;
  selectedFollowerLabel: string;
  selectedItem: EffectiveMyKolItem;
  selectedPostState?: KolPostState;
  selectedProfileLoading: boolean;
  selectedTotalPosts: number;
  onCancelContactEdit: () => void;
  onCommentPost: (post: PostPreview) => void;
  onContactDraftChange: (itemId: string, draft: ContactDraft) => void;
  onContentDirectionChange: (direction: KolContentDirection) => void;
  onContentFilterChange: (filter: KolContentFilter) => void;
  onContentSortChange: (sort: KolContentSort) => void;
  onContentWindowChange: (window: KolContentWindow) => void;
  onPreviewPost: (post: PostPreview) => void;
  onSaveContact: (item: EffectiveMyKolItem) => void;
  onScanAccount: (item: EffectiveMyKolItem) => void;
  onStartContactEdit: (item: EffectiveMyKolItem) => void;
  onToggleCollapsed: () => void;
  onToggleFollow: (item: EffectiveMyKolItem) => void;
}

export function MyKolContentLayer({
  apiToken,
  busyKolId,
  collapsed,
  contentDirection,
  contentFilter,
  contentSort,
  contentWindow,
  editingContactId,
  postInsights,
  posts,
  savingContactId,
  scanningKolId,
  selectedAvatar,
  selectedCommentState,
  selectedContacts,
  selectedContentLabel,
  selectedDraft,
  selectedFollowerLabel,
  selectedItem,
  selectedPostState,
  selectedProfileLoading,
  selectedTotalPosts,
  onCancelContactEdit,
  onCommentPost,
  onContactDraftChange,
  onContentDirectionChange,
  onContentFilterChange,
  onContentSortChange,
  onContentWindowChange,
  onPreviewPost,
  onSaveContact,
  onScanAccount,
  onStartContactEdit,
  onToggleCollapsed,
  onToggleFollow,
}: MyKolContentLayerProps) {
  const isScanning = scanningKolId === selectedItem.id;

  return (
    <section className="vkpi-my-kol-content-layer">
      <header className="vkpi-my-kol-content-layer__header">
        <div className="vkpi-my-kol-content-layer__identity">
          <div className="vkpi-my-kol-content-layer__avatar">
            {selectedAvatar ? <img src={selectedAvatar} alt="" loading="lazy" /> : <span>{initials(selectedItem.name)}</span>}
          </div>
          <div>
            <span>内容层</span>
            <h3>{selectedItem.name}</h3>
            <p>{selectedItem.handle} · {selectedFollowerLabel} 粉丝 · {selectedContentLabel} 内容</p>
          </div>
        </div>
        <div className="vkpi-my-kol-section-actions">
          <strong>{selectedFollowerLabel}</strong>
          <button
            aria-expanded={!collapsed}
            className="vkpi-my-kol-section-toggle"
            onClick={onToggleCollapsed}
            type="button"
          >
            {collapsed ? '展开' : '折叠'}
          </button>
        </div>
      </header>

      {collapsed ? (
        <div className="vkpi-my-kol-section-collapsed">内容层已折叠 · {numberFormatter.format(selectedTotalPosts)} 条历史内容</div>
      ) : (
        <>
          <div className="vkpi-my-kol-content-toolbar">
            <div className="vkpi-my-kol-content-toolbar__sort" aria-label="KOL内容排序">
              {CONTENT_SORT_OPTIONS.map((option) => (
                <button
                  className={contentSort === option.key ? 'is-active' : ''}
                  key={option.key}
                  onClick={() => onContentSortChange(option.key)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
              <select value={contentWindow} onChange={(event) => onContentWindowChange(event.target.value as KolContentWindow)} aria-label="时间范围">
                {CONTENT_WINDOW_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
              </select>
              <select value={contentDirection} onChange={(event) => onContentDirectionChange(event.target.value as KolContentDirection)} aria-label="排序方向">
                <option value="desc">{contentSort === 'latest' ? '最新优先' : '最高优先'}</option>
                <option value="asc">{contentSort === 'latest' ? '最早优先' : '最低优先'}</option>
              </select>
            </div>
            <div className="vkpi-my-kol-content-toolbar__actions">
              <button type="button" onClick={() => onStartContactEdit(selectedItem)}>{selectedContacts.length ? '编辑联系方式' : '补联系方式'}</button>
              <button type="button" disabled={isScanning || !apiToken || !selectedItem.kolId} onClick={() => onScanAccount(selectedItem)}>
                {isScanning ? '抓取中' : `抓取${platformDisplay(selectedItem.platform)}`}
              </button>
              <button
                className={selectedItem.isFollowed ? 'is-danger' : ''}
                disabled={busyKolId === selectedItem.id || !apiToken || !selectedItem.kolId}
                type="button"
                onClick={() => onToggleFollow(selectedItem)}
              >
                {!selectedItem.kolId ? '缺KOL ID' : selectedItem.isFollowed ? '不关注' : '关注'}
              </button>
            </div>
            <span>{selectedProfileLoading || selectedPostState?.loading ? '加载中' : posts.length ? `1-${numberFormatter.format(posts.length)} / ${numberFormatter.format(selectedTotalPosts)}` : '0 / 0'}</span>
          </div>

          <div className="vkpi-my-kol-content-insights" aria-label="KOL内容分析">
            <span><b>设备使用</b>{postInsights.gearLabel}</span>
            <span><b>Viltrox内容</b>{numberFormatter.format(postInsights.viltroxCount)}</span>
            <span><b>竞品内容</b>{numberFormatter.format(postInsights.competitorCount)}</span>
            <span><b>其它内容</b>{numberFormatter.format(postInsights.otherCount)}</span>
            <span><b>最后抓取</b>{postInsights.scanLabel}</span>
            <span><b>平均播放</b>{displayCount(postInsights.avgViews)}</span>
            <span><b>互动率</b>{postInsights.engagement ? `${postInsights.engagement.toFixed(2)}%` : '-'}</span>
          </div>

          <div className="vkpi-my-kol-content-filters" aria-label="内容分类筛选">
            {CONTENT_FILTER_OPTIONS.map((option) => (
              <button
                className={contentFilter === option.key ? 'is-active' : ''}
                key={option.key}
                onClick={() => onContentFilterChange(option.key)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>

          <div className="vkpi-my-kol-content-contacts">
            <span>联系</span>
            {selectedContacts.length ? selectedContacts.map((contact) => (
              contact.url ? (
                <a href={contact.url} key={`${contact.label}-${contact.value}`} target="_blank" rel="noreferrer">
                  <b>{contact.label}</b>{compactContactValue(contact.value)}
                </a>
              ) : (
                <em key={`${contact.label}-${contact.value}`}>
                  <b>{contact.label}</b>{compactContactValue(contact.value)}
                </em>
              )
            )) : <em><b>暂无</b>未补联系方式</em>}
          </div>

          {editingContactId === selectedItem.id && selectedDraft ? (
            <div className="vkpi-my-kol-contact-editor">
              <label><span>邮箱</span><input value={selectedDraft.contactEmail} onChange={(event) => onContactDraftChange(selectedItem.id, { ...selectedDraft, contactEmail: event.target.value })} placeholder="email@example.com" /></label>
              <label><span>手机号 / WhatsApp</span><input value={selectedDraft.contactPhone} onChange={(event) => onContactDraftChange(selectedItem.id, { ...selectedDraft, contactPhone: event.target.value })} placeholder="+1 ..." /></label>
              <label><span>主页</span><input value={selectedDraft.profileUrl} onChange={(event) => onContactDraftChange(selectedItem.id, { ...selectedDraft, profileUrl: event.target.value })} placeholder="https://..." /></label>
              <div>
                <button className="vkpi-my-kol-action" disabled={savingContactId === selectedItem.id || !apiToken || !selectedItem.kolId} type="button" onClick={() => onSaveContact(selectedItem)}>保存</button>
                <button className="vkpi-my-kol-action is-muted" type="button" onClick={onCancelContactEdit}>取消</button>
              </div>
            </div>
          ) : null}

          {posts.length ? (
            <div className="vkpi-my-kol-content-list">
              {posts.map((post) => (
                <article className="vkpi-my-kol-content-card" key={post.id}>
                  <div
                    className="vkpi-my-kol-content-card__media"
                    onClick={() => onPreviewPost(post)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') onPreviewPost(post);
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <span className={`vkpi-my-kol-content-card__badge is-${categoryForPost(post)}`}>
                      {categoryForPost(post) === 'viltrox' ? 'Viltrox相关' : categoryForPost(post) === 'competitor' ? '竞品相关' : '其它内容'}
                    </span>
                    <span className="vkpi-my-kol-content-card__kind">{mediaBadge(post, selectedItem.platform)}</span>
                    <KolMediaSlot post={post} platform={selectedItem.platform} compact />
                  </div>
                  <div className="vkpi-my-kol-content-card__body">
                    <h3 title={post.title}>{conciseText(post.title, 96)}</h3>
                    {post.gearMentions.length ? <p>设备：{post.gearMentions.join(' / ')}</p> : <p>设备待识别</p>}
                    <div className="vkpi-my-kol-content-card__metrics">
                      <span><strong>播放</strong>{displayCount(post.views)}</span>
                      <span><strong>点赞</strong>{displayCount(post.likes)}</span>
                      <button type="button" onClick={() => onCommentPost(post)}><strong>评论</strong>{displayCount(post.comments)}</button>
                      <span><strong>分享</strong>{displayCount(post.shares)}</span>
                    </div>
                  </div>
                  <footer className="vkpi-my-kol-content-card__footer">
                    <small>{compactDate(post.publishedAt)}</small>
                    <div>
                      <button type="button" onClick={() => onCommentPost(post)}>评论明细</button>
                      {post.url ? <a href={post.url} target="_blank" rel="noreferrer">打开原帖</a> : null}
                    </div>
                  </footer>
                </article>
              ))}
            </div>
          ) : (
            <div className="vkpi-my-kol-content-empty">
              <span>{selectedPostState?.loading ? '主页内容加载中。' : '暂无符合筛选条件的主页视频 / 帖子样本。'}</span>
              <button type="button" onClick={() => onScanAccount(selectedItem)} disabled={isScanning || !apiToken || !selectedItem.kolId}>
                {isScanning ? '抓取中' : '抓取主页内容'}
              </button>
            </div>
          )}
          {selectedPostState?.error ? <div className="vkpi-my-kol-message">{selectedPostState.error}</div> : null}
          {selectedCommentState?.error ? <div className="vkpi-my-kol-message">{selectedCommentState.error}</div> : null}
        </>
      )}
    </section>
  );
}
