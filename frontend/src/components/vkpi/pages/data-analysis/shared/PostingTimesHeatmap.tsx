import React, { useMemo } from 'react';
import type { Row } from '../utils/types';
import { rowString } from '../utils/rowAccessors';
import { EmptyState } from './EmptyState';

interface PostingTimesHeatmapProps {
  posts: Row[];
}

export function PostingTimesHeatmap({ posts }: PostingTimesHeatmapProps) {
  const grid = useMemo(() => {
    const map = new Map<string, number>();
    for (const post of posts) {
      const time = rowString(post, ['published_at', 'posted_at', 'created_at']);
      if (!time) continue;
      const date = new Date(time);
      if (Number.isNaN(date.getTime())) continue;
      const day = date.getDay();
      const hour = Math.floor(date.getHours() / 3) * 3;
      map.set(`${day}-${hour}`, (map.get(`${day}-${hour}`) || 0) + 1);
    }
    return map;
  }, [posts]);

  if (!posts.length) {
    return <EmptyState title="暂无 Posting Times 数据" body="累积真实帖子后展示发布时间热区。" />;
  }
  const dayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const hours = [0, 3, 6, 9, 12, 15, 18, 21];
  return (
    <div className="da-time-grid">
      <span></span>
      {hours.map((hour) => <span key={hour}>{`${hour}:00`}</span>)}
      {dayLabels.slice(1).concat(dayLabels[0]).map((day, dayIndex) => {
        const actualDay = dayIndex === 6 ? 0 : dayIndex + 1;
        return (
          <React.Fragment key={day}>
            <strong>{day}</strong>
            {hours.map((hour) => {
              const value = grid.get(`${actualDay}-${hour}`) || 0;
              return (
                <i
                  key={`${day}-${hour}`}
                  style={{
                    opacity: value ? 0.35 + Math.min(0.65, value / 4) : 0.08,
                    transform: `scale(${value ? 0.75 + Math.min(0.55, value / 4) : 0.45})`,
                  }}
                  title={`${day} ${hour}:00 · ${value} posts`}
                />
              );
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
}
