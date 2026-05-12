import { DaCard } from '../shared/DaCard';
import { EmptyState } from '../shared/EmptyState';
import { CommentIntelligencePanel } from '../shared/CommentIntelligencePanel';

interface SentimentTabProps {
  apiToken?: string;
}

export function SentimentTab({ apiToken }: SentimentTabProps) {
  return (
    <>
      <CommentIntelligencePanel apiToken={apiToken} />

      <DaCard title="Sentiment Analysis" eyebrow="评论级情感" wide>
        <EmptyState
          title="情感分析已接入后端"
          body={`上方概览来自真实评论智能链路:评论采集、sentiment_analyzer、pillar_classifier 和 run history。
下一步只补图表视图,不再重复建设后端。`}
        />
      </DaCard>

      <section className="da-two-column">
        <DaCard title="Sentiment Distribution" eyebrow="占比">
          <EmptyState
            title="图表视图未接入"
            body="当前已有评论智能概览和分布列表,但 Donut 图还没有绑定真实 chart renderer；上线前保持明确空态,不伪装成已完成图表。"
          />
        </DaCard>
        <DaCard title="Sentiment Trend (30d)" eyebrow="时间序列">
          <EmptyState
            title="趋势图未接入"
            body="需要评论情感按日聚合接口后再渲染趋势线；当前不展示模拟曲线。"
          />
        </DaCard>
      </section>
    </>
  );
}
