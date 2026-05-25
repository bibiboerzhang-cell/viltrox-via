import { useEffect, useMemo, useState } from 'react';
import type { VkpiDashboardData } from '../../vkpiTypes';
import { getRecommendationFeedbackBacklog } from '../../../../domains/intelligence';
import { listBrandSignals } from '../../../../domains/market';
import { buildDiscoveryQueue } from './DiscoverQueueModel';

export function useDiscoveryQueueSources(apiToken: string | undefined, projects: VkpiDashboardData['projects']) {
  const [recommendationBacklog, setRecommendationBacklog] = useState<Record<string, unknown>>({});
  const [brandSignals, setBrandSignals] = useState<Array<Record<string, unknown>>>([]);
  const [discoveryQueueLoading, setDiscoveryQueueLoading] = useState(false);
  const [discoveryQueueMessage, setDiscoveryQueueMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!apiToken) {
      setRecommendationBacklog({});
      setBrandSignals([]);
      setDiscoveryQueueMessage('未登录时仅显示本地数据方向；登录后会读取推荐、项目和品牌信号。');
      return () => { cancelled = true; };
    }
    setDiscoveryQueueLoading(true);
    setDiscoveryQueueMessage('');
    Promise.allSettled([
      getRecommendationFeedbackBacklog(apiToken, 8),
      listBrandSignals(apiToken, { status: 'new', limit: 8 }),
    ]).then(([recommendationResult, signalResult]) => {
      if (cancelled) return;
      if (recommendationResult.status === 'fulfilled') setRecommendationBacklog(recommendationResult.value);
      else setRecommendationBacklog({});
      if (signalResult.status === 'fulfilled') setBrandSignals(signalResult.value.signals || []);
      else setBrandSignals([]);
      if (recommendationResult.status === 'rejected' || signalResult.status === 'rejected') {
        setDiscoveryQueueMessage('发现队列部分来源读取失败；已保留项目缺口和可用来源。');
      }
    }).finally(() => {
      if (!cancelled) setDiscoveryQueueLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const discoveryQueueItems = useMemo(
    () => buildDiscoveryQueue({
      recommendationBacklog,
      projects,
      brandSignals,
    }),
    [brandSignals, projects, recommendationBacklog],
  );

  return {
    discoveryQueueItems,
    discoveryQueueLoading,
    discoveryQueueMessage,
  };
}
