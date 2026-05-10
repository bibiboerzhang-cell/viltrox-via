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
          <EmptyState title="待 Phase 3 启用" body="Donut chart 占位,待 sentiment_analyzer 服务上线。" />
        </DaCard>
        <DaCard title="Sentiment Trend (30d)" eyebrow="时间序列">
          <EmptyState title="待 Phase 3 启用" body="时序图占位,待累积真实情感数据。" />
        </DaCard>
      </section>
    </>
  );
}
