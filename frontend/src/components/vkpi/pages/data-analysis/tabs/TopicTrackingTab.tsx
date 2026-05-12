import { DaCard } from '../shared/DaCard';
import { EmptyState } from '../shared/EmptyState';

export function TopicTrackingTab() {
  return (
    <>
      <DaCard title="Topic Tracking" eyebrow="行业话题监控" wide>
        <EmptyState
          title="话题监控未启用"
          body={`Phase 2 启用 Topic Tracking 服务后,本页会展示:
• 监控 keywords 列表 + 趋势分数
• mention_count_30d / avg_engagement_30d
• Trending Score 排序
• 跨平台话题热度对比
• 话题级 hashtag 增长 > 50% 自动告警
当前 V-KPI 不主动抓取多平台数据,避免外部 Apify / Decodo 成本。`}
        />
      </DaCard>

      <section className="da-two-column">
        <DaCard title="Trending Topics" eyebrow="热门话题">
          <EmptyState
            title="话题排行未接入"
            body="需要 topic_tracking 服务写入真实话题统计后展示；当前不放假榜单。"
          />
        </DaCard>
        <DaCard title="Hashtag Growth (30d)" eyebrow="标签增长">
          <EmptyState
            title="标签趋势未接入"
            body="需要多平台 hashtag 历史快照后展示增长曲线；当前不放模拟趋势。"
          />
        </DaCard>
      </section>
    </>
  );
}
