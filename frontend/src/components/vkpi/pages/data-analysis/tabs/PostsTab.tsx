import { useState } from 'react';
import type { Row } from '../utils/types';
import { findAccountForPost, postKey, rowNumber, rowString } from '../utils/rowAccessors';
import { normalizePlatform } from '../utils/platformHelpers';
import { DaCard } from '../shared/DaCard';
import { EmptyState } from '../shared/EmptyState';
import { PostCard } from '../shared/PostCard';

interface PostsTabProps {
  accounts: Row[];
  posts: Row[];
  onSetSelectedAccount: (account: Row | null) => void;
  onOpenPost: (post: Row) => void;
}

type SortKey = 'recent' | 'views' | 'likes' | 'engagement';

export function PostsTab({ accounts, posts, onSetSelectedAccount, onOpenPost }: PostsTabProps) {
  const [filterPlatform, setFilterPlatform] = useState<string>('all');
  const [sortBy, setSortBy] = useState<SortKey>('recent');
  const [keyword, setKeyword] = useState('');
  const [showAll, setShowAll] = useState(false);

  const filtered = posts.filter((post) => {
    if (filterPlatform !== 'all') {
      const p = normalizePlatform(rowString(post, ['platform']));
      if (p !== filterPlatform) return false;
    }
    if (keyword.trim()) {
      const text = rowString(post, ['title', 'caption', 'text', 'description']).toLowerCase();
      if (!text.includes(keyword.toLowerCase())) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (sortBy === 'recent') {
      const ta = rowString(a, ['published_at', 'posted_at', 'created_at']);
      const tb = rowString(b, ['published_at', 'posted_at', 'created_at']);
      return tb.localeCompare(ta);
    }
    if (sortBy === 'views') {
      return (rowNumber(b, ['views', 'view_count']) || 0) - (rowNumber(a, ['views', 'view_count']) || 0);
    }
    if (sortBy === 'likes') {
      return (rowNumber(b, ['likes', 'like_count']) || 0) - (rowNumber(a, ['likes', 'like_count']) || 0);
    }
    // engagement
    const eA = (rowNumber(a, ['likes', 'like_count']) || 0)
      + (rowNumber(a, ['comments', 'comment_count']) || 0)
      + (rowNumber(a, ['shares', 'share_count']) || 0);
    const eB = (rowNumber(b, ['likes', 'like_count']) || 0)
      + (rowNumber(b, ['comments', 'comment_count']) || 0)
      + (rowNumber(b, ['shares', 'share_count']) || 0);
    return eB - eA;
  });
  const visiblePosts = showAll ? sorted : sorted.slice(0, 30);

  const platforms = Array.from(new Set(posts.map((p) => normalizePlatform(rowString(p, ['platform'])))));

  return (
    <DaCard
      title="所有帖子"
      eyebrow="内容库"
      wide
      side={
        <span style={{ fontSize: 12, color: 'var(--da-text-muted)' }}>
          共 {sorted.length} 条 / 全部 {posts.length}
        </span>
      }
    >
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          type="text"
          placeholder="搜索 caption / title..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{
            flex: '1 1 200px', maxWidth: 320,
            padding: '8px 12px', border: '1px solid var(--da-border)',
            borderRadius: 8, fontSize: 13,
          }}
        />
        <select
          value={filterPlatform}
          onChange={(e) => setFilterPlatform(e.target.value)}
          style={{ padding: '8px 12px', border: '1px solid var(--da-border)', borderRadius: 8, fontSize: 13 }}
        >
          <option value="all">全部平台</option>
          {platforms.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortKey)}
          style={{ padding: '8px 12px', border: '1px solid var(--da-border)', borderRadius: 8, fontSize: 13 }}
        >
          <option value="recent">最新</option>
          <option value="views">播放量</option>
          <option value="likes">点赞</option>
          <option value="engagement">互动总和</option>
        </select>
        {sorted.length > 30 ? (
          <button className="da-text-button" type="button" onClick={() => setShowAll((value) => !value)}>
            {showAll ? '只看前 30' : `显示全部 ${sorted.length} 条`}
          </button>
        ) : null}
      </div>
      {sorted.length ? (
        <div className="da-post-grid">
          {visiblePosts.map((post, idx) => {
            const matchedAccount = findAccountForPost(post, accounts);
            return (
              <PostCard
                key={postKey(post, idx)}
                post={post}
                accounts={accounts}
                onOpenPost={onOpenPost}
                onViewAnalytics={matchedAccount ? () => onSetSelectedAccount(matchedAccount) : undefined}
              />
            );
          })}
        </div>
      ) : (
        <EmptyState
          title="筛选条件下无匹配帖子"
          body={posts.length ? '试试切换平台或清空搜索关键词。' : '暂无真实帖子,导入 Apify 历史或开启平台同步后展示。'}
        />
      )}
      {sorted.length > 30 && !showAll ? (
        <p style={{
          textAlign: 'center', marginTop: 16, color: 'var(--da-text-muted)', fontSize: 12,
        }}>当前显示前 30 条,可点击上方按钮查看全部。</p>
      ) : null}
    </DaCard>
  );
}
