import type { Row } from '../utils/types';
import { contentPillars, postTypes, extractHashtags } from '../utils/metricHelpers';
import { DaCard } from '../shared/DaCard';
import { BarChart } from '../shared/BarChart';
import { DonutChart } from '../shared/DonutChart';
import { PostingTimesHeatmap } from '../shared/PostingTimesHeatmap';
import { EmptyState } from '../shared/EmptyState';

interface PillarsTabProps {
  posts: Row[];
}

export function PillarsTab({ posts }: PillarsTabProps) {
  const pillars = contentPillars(posts);
  const types = postTypes(posts);
  const hashtags = extractHashtags(posts);

  if (!posts.length) {
    return (
      <DaCard title="内容支柱" eyebrow="行业类目" wide>
        <EmptyState
          title="暂无内容,无法分析支柱"
          body="同步真实帖子或导入 Apify 历史数据后,会自动按内容支柱分类。Phase 3 启用 LLM 后会有更精细的 AI 标签。"
        />
      </DaCard>
    );
  }

  return (
    <>
      <section className="da-two-column">
        <DaCard title="Posts Breakdown by Content Pillars" eyebrow="行业类目">
          <BarChart data={pillars} valueLabel="次数" />
        </DaCard>
        <DaCard title="Posting Times" eyebrow="热区图">
          <PostingTimesHeatmap posts={posts} />
        </DaCard>
      </section>

      <section className="da-two-column">
        <DaCard title="Post Types" eyebrow="内容格式">
          <DonutChart data={types} />
        </DaCard>
        <DaCard title="Top Hashtags" eyebrow="内容信号">
          <BarChart data={hashtags} valueLabel="次数" />
        </DaCard>
      </section>

      <DaCard title="Pillars 详细分布" eyebrow="数据明细" wide>
        <div className="da-table-wrap">
          <table className="da-table">
            <thead>
              <tr>
                <th>支柱</th>
                <th>帖子数</th>
                <th>占比</th>
                <th>启示</th>
              </tr>
            </thead>
            <tbody>
              {pillars.map((p) => {
                const pct = ((p.value / posts.length) * 100).toFixed(1);
                return (
                  <tr key={p.label}>
                    <td>{p.label}</td>
                    <td>{p.value}</td>
                    <td>{pct}%</td>
                    <td style={{ color: 'var(--da-text-muted)', fontSize: "var(--ds-fs-12)" }}>
                      {p.label === '未分类' ? 'Phase 3 LLM 启用后自动归类' : '已归类'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </DaCard>
    </>
  );
}
