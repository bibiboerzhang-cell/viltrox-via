import type { VkpiKolProfile } from '../../vkpiTypes';
import { platformDisplay } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import {
  contactItems,
  displayCount,
  initials,
  latestSnapshotPosts,
  postPreviews,
  summarizePostInsights,
  textField,
} from './myKolMatrixData';
import type { ContactDraft, EffectiveMyKolItem, KolPostState, PlatformFilter } from './myKolMatrixTypes';

interface MyKolAccountsProps {
  collapsed: boolean;
  contactOverrides: Record<string, Partial<ContactDraft>>;
  devicePopoverId: string;
  items: EffectiveMyKolItem[];
  kolPosts: Record<string, KolPostState>;
  kolProfiles: Record<string, VkpiKolProfile>;
  onCloseDevicePopover: () => void;
  onSelectItem: (id: string) => void;
  onToggleCollapsed: () => void;
  onToggleDevicePopover: (id: string) => void;
  platformFilter: PlatformFilter;
  selectedItem?: EffectiveMyKolItem;
}

export function MyKolAccounts({
  collapsed,
  contactOverrides,
  devicePopoverId,
  items,
  kolPosts,
  kolProfiles,
  onCloseDevicePopover,
  onSelectItem,
  onToggleCollapsed,
  onToggleDevicePopover,
  platformFilter,
  selectedItem,
}: MyKolAccountsProps) {
  return (
    <section className="vkpi-my-kol-accounts">
      <div className="vkpi-my-kol-accounts__header">
        <div>
          <span>账号层</span>
          <h3>{platformFilter === 'all' ? '我的 KOL 账号' : `${platformFilter} KOL 账号`}</h3>
        </div>
        <div className="vkpi-my-kol-section-actions">
          <strong>{numberFormatter.format(items.length)} 个账号</strong>
          <button
            aria-expanded={!collapsed}
            className="vkpi-my-kol-section-toggle"
            onClick={onToggleCollapsed}
            type="button"
          >
            {collapsed ? '展开' : '折叠'}
          </button>
        </div>
      </div>

      {collapsed ? (
        <div className="vkpi-my-kol-section-collapsed">账号层已折叠 · {numberFormatter.format(items.length)} 个账号</div>
      ) : (
        <div className="vkpi-my-kol-account-grid">
          {items.map((item) => {
            const profile = kolProfiles[item.kolId];
            const contacts = contactItems(item, profile, contactOverrides[item.id]);
            const avatar = proxiedImageUrl(textField(profile?.kol, 'avatar_url') || item.avatar);
            const summary = profile?.summary || {};
            const followerLabel = displayCount(summary.follower_count || 0) !== '0' ? displayCount(summary.follower_count) : item.followers;
            const contentLabel = displayCount(summary.content_count || 0) !== '0' ? displayCount(summary.content_count) : item.contentCount;
            const accountPosts = latestSnapshotPosts(kolPosts[item.kolId]?.items?.length ? kolPosts[item.kolId].items : postPreviews(profile), profile);
            const accountInsights = summarizePostInsights(accountPosts, profile);
            const active = selectedItem?.id === item.id;
            return (
              <article
                className={`vkpi-my-kol-account-card${active ? ' is-active' : ''}`}
                key={item.id}
                onClick={() => onSelectItem(item.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onSelectItem(item.id);
                }}
                role="button"
                tabIndex={0}
              >
                <div className="vkpi-my-kol-account-card__avatar">
                  {avatar ? <img src={avatar} alt="" loading="lazy" /> : <span>{initials(item.name)}</span>}
                </div>
                <div className="vkpi-my-kol-account-card__main">
                  <div className="vkpi-my-kol-account-card__title">
                    <h3>{item.name}</h3>
                    <strong>{followerLabel}</strong>
                  </div>
                  <p>{item.handle} · {platformDisplay(item.platform)} · {contentLabel} 内容</p>
                  <div className="vkpi-my-kol-account-card__metrics">
                    <span>播放 {displayCount(accountInsights.totalViews)}</span>
                    <span>评论 {displayCount(accountInsights.totalComments)}</span>
                    <span>互动率 {accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</span>
                  </div>
                  <div className="vkpi-my-kol-account-card__chips">
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onToggleDevicePopover(item.id);
                      }}
                    >
                      设备分析
                    </button>
                    <span>V内容 {numberFormatter.format(accountInsights.viltroxCount)}</span>
                    <span>竞品 {numberFormatter.format(accountInsights.competitorCount)}</span>
                    <span>均播 {displayCount(accountInsights.avgViews)}</span>
                  </div>
                  {devicePopoverId === item.id ? (
                    <div className="vkpi-my-kol-account-popover" onClick={(event) => event.stopPropagation()} role="dialog" aria-label={`${item.name} 设备与内容分析`}>
                      <header>
                        <strong>设备与内容分析</strong>
                        <button type="button" onClick={onCloseDevicePopover} aria-label="关闭">×</button>
                      </header>
                      <dl>
                        <div><dt>设备使用</dt><dd>{accountInsights.gearLabel}</dd></div>
                        <div><dt>Viltrox内容</dt><dd>{numberFormatter.format(accountInsights.viltroxCount)}</dd></div>
                        <div><dt>竞品内容</dt><dd>{numberFormatter.format(accountInsights.competitorCount)}</dd></div>
                        <div><dt>其它内容</dt><dd>{numberFormatter.format(accountInsights.otherCount)}</dd></div>
                        <div><dt>最后抓取</dt><dd>{accountInsights.scanLabel}</dd></div>
                        <div><dt>平均播放</dt><dd>{displayCount(accountInsights.avgViews)}</dd></div>
                        <div><dt>互动率</dt><dd>{accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</dd></div>
                      </dl>
                    </div>
                  ) : null}
                  <small>{profile ? '已抓取账号数据' : '待抓取账号数据'} · {item.subStatus} · {item.isFollowed ? '已关注' : '未关注'} · {contacts.length ? `联系 ${contacts.length}` : '待补联系方式'}</small>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
