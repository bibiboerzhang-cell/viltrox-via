import { useEffect, useState } from 'react';
import {
  claimOutreachSuggestion,
  createProjectFromOutreachSuggestion,
  dismissOutreachSuggestion,
  getDailyOutreachDigestStatus,
  getKolPoolSummary,
  getProductRecommendationOutcomeSummary,
  listAnalyticsProducts,
  listDailyOutreachDigest,
  listOutreachSuggestions,
  listProductLaunches,
  listProductRecommendations,
  type ProductDomainRow,
} from '../../../domains/products';
import { AnalyticsMonitorPanel } from './analytics/AnalyticsMonitorPanel';
import { OutreachTables } from './analytics/OutreachTables';
import { ProductRecommendationPanel } from './analytics/ProductRecommendationPanel';
import { PageShell } from './PageShell';

interface ProductBattlePageProps {
  apiToken?: string;
}

type Row = ProductDomainRow;

export function ProductBattlePage({ apiToken }: ProductBattlePageProps) {
  const [platform, setPlatform] = useState('youtube');
  const [products, setProducts] = useState<Row[]>([]);
  const [suggestions, setSuggestions] = useState<Row[]>([]);
  const [digestItems, setDigestItems] = useState<Row[]>([]);
  const [digestDate, setDigestDate] = useState('');
  const [digestStatus, setDigestStatus] = useState<Row>({});
  const [launches, setLaunches] = useState<Row[]>([]);
  const [recommendations, setRecommendations] = useState<Row[]>([]);
  const [recommendationOutcomeSummary, setRecommendationOutcomeSummary] = useState<Row>({});
  const [kolPoolSummary, setKolPoolSummary] = useState<Row>({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!apiToken) return;
    const [productResult, suggestionResult, digestResult, digestStatusResult, launchResult, recommendationResult, outcomeResult, kolPoolResult] = await Promise.all([
      listAnalyticsProducts(apiToken).catch(() => ({ products: [] })),
      listOutreachSuggestions(apiToken).catch(() => ({ suggestions: [] })),
      listDailyOutreachDigest(apiToken).catch(() => ({ items: [], digest_date: '' })),
      getDailyOutreachDigestStatus(apiToken).catch(() => ({})),
      listProductLaunches(apiToken).catch(() => ({ launches: [] })),
      listProductRecommendations(apiToken).catch(() => ({ recommendations: [] })),
      getProductRecommendationOutcomeSummary(apiToken).catch(() => ({ totals: {}, conversion: {}, source_rows: [] })),
      getKolPoolSummary(apiToken).catch(() => ({})),
    ]);
    setProducts(productResult.products || []);
    setSuggestions(suggestionResult.suggestions || []);
    setDigestItems(digestResult.items || []);
    setDigestDate(String(digestResult.digest_date || ''));
    setDigestStatus(digestStatusResult as Row);
    setLaunches(launchResult.launches || []);
    setRecommendations(recommendationResult.recommendations || []);
    setRecommendationOutcomeSummary(outcomeResult as Row);
    setKolPoolSummary(kolPoolResult as Row);
  };

  useEffect(() => {
    void refresh();
  }, [apiToken]);

  const updateSuggestion = async (id: unknown, action: 'claim' | 'dismiss' | 'create_project') => {
    if (!apiToken || !id) return;
    setBusy(true);
    try {
      if (action === 'create_project') {
        const response = await createProjectFromOutreachSuggestion(apiToken, String(id));
        const project = (response.project || {}) as Row;
        const link = (response.link || {}) as Row;
        setMessage(`已创建项目并生成短链：${String(project.project_name || project.id || id)} / ${String(link.slug || response.short_url || '无短链')}`);
      } else if (action === 'claim') {
        const response = await claimOutreachSuggestion(apiToken, String(id));
        const kol = (response.kol || {}) as Row;
        const claimStatus = String(response.claim_status || 'created');
        setMessage(`已认领并绑定主 KOL：${String(kol.channel_name || kol.id || id)}（${claimStatus}）。`);
      } else {
        await dismissOutreachSuggestion(apiToken, String(id), '暂不联系');
        setMessage('已忽略该建议联系。');
      }
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '建议联系更新失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell
      title="产品作战"
      description="围绕新品 / SKU 做产品对比、KOL 池导入、推荐排序和每日未联系候选。未配置 API 时只显示待同步，不展示假视频或假红人。"
    >
      <AnalyticsMonitorPanel
        apiToken={apiToken}
        busy={busy}
        platform={platform}
        productsCount={products.length}
        suggestionsCount={suggestions.length}
        digestCount={digestItems.length}
        digestStatus={digestStatus}
        kolPoolSummary={kolPoolSummary}
        onBusyChange={setBusy}
        onPlatformChange={setPlatform}
        onMessage={setMessage}
        onRefresh={refresh}
      />
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      <ProductRecommendationPanel
        apiToken={apiToken}
        busy={busy}
        platform={platform}
        launches={launches}
        recommendations={recommendations}
        outcomeSummary={recommendationOutcomeSummary}
        onBusyChange={setBusy}
        onPlatformChange={setPlatform}
        onMessage={setMessage}
        onRefresh={refresh}
        onRecommendationsChange={setRecommendations}
      />
      <OutreachTables busy={busy} suggestions={suggestions} digestItems={digestItems} digestDate={digestDate} onUpdateSuggestion={updateSuggestion} />
    </PageShell>
  );
}
