import { DaCard } from '../shared/DaCard';
import { EmptyState } from '../shared/EmptyState';

export function SentimentTab() {
  return (
    <>
      <DaCard title="Sentiment Analysis" eyebrow="评论级情感" wide>
        <EmptyState
          title="情感分析未启用"
          body={`Phase 3 启用 LLM Gateway + Sentiment Analyzer 后,本页会展示:
• 帖子级 positive / neutral / negative 比例
• 评论级情感聚类
• 30 天情感趋势线
• 触发负面情感告警 (engagement_rate_drop > 30% 等)
当前 V-KPI 不抓取或分析评论数据,避免外部 LLM 成本。`}
        />
      </DaCard>

      <section className="da-two-column">
        <DaCard title="Sentiment Distribution" eyebrow="占比">
          <EmptyState title="待 Phase 3 启用" body="Donut chart 占位,待 sentiment_analyzer 服务上线。" />
        </DaCard>
        <DaCard title="Sentiment Trend (30d)" eyebrow="时间序列">
          <EmptyState title="待 Phase 3 启用" body="时序图占位,待累积真实情感数据。" />
        </DaCard>
      </section>
    </>
  );
}
