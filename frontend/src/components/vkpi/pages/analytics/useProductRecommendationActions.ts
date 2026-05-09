import type { FormEvent } from 'react';
import {
  createProductLaunch,
  importProductKolPool,
  productRecommendationAction,
  runProductRecommendations,
} from '../../../../services/vkpi.ui-api';

type Row = Record<string, unknown>;
type RecommendationAction = 'shortlist' | 'reject' | 'claim' | 'create_project';

interface UseProductRecommendationActionsArgs {
  apiToken?: string;
  platform: string;
  launchName: string;
  launchSku: string;
  launchCategory: string;
  poolHandle: string;
  poolFollowers: string;
  poolAvgViews: string;
  poolEngagement: string;
  poolJson: string;
  selectedLaunchId: string;
  onBusyChange: (busy: boolean) => void;
  onMessage: (message: string) => void;
  onRefresh: () => Promise<void>;
  onRecommendationsChange: (rows: Row[]) => void;
  setSelectedLaunchId: (value: string) => void;
  setLaunchName: (value: string) => void;
  setLaunchSku: (value: string) => void;
  setLaunchCategory: (value: string) => void;
  setPoolHandle: (value: string) => void;
  setPoolFollowers: (value: string) => void;
  setPoolAvgViews: (value: string) => void;
  setPoolEngagement: (value: string) => void;
  setPoolJson: (value: string) => void;
  setSelectedRecommendation: (row: Row | null) => void;
  refreshRecommendationEvidence: (recommendationId: unknown) => Promise<void>;
}

function parseJsonRows(raw: string): Row[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  const parsed = JSON.parse(trimmed);
  if (Array.isArray(parsed)) return parsed.filter((item) => item && typeof item === 'object') as Row[];
  if (parsed && typeof parsed === 'object') return [parsed as Row];
  return [];
}

export function useProductRecommendationActions({
  apiToken,
  platform,
  launchName,
  launchSku,
  launchCategory,
  poolHandle,
  poolFollowers,
  poolAvgViews,
  poolEngagement,
  poolJson,
  selectedLaunchId,
  onBusyChange,
  onMessage,
  onRefresh,
  onRecommendationsChange,
  setSelectedLaunchId,
  setLaunchName,
  setLaunchSku,
  setLaunchCategory,
  setPoolHandle,
  setPoolFollowers,
  setPoolAvgViews,
  setPoolEngagement,
  setPoolJson,
  setSelectedRecommendation,
  refreshRecommendationEvidence,
}: UseProductRecommendationActionsArgs) {
  const submitLaunch = async (event: FormEvent) => {
    event.preventDefault();
    if (!apiToken || !launchName.trim()) return;
    onBusyChange(true);
    try {
      const response = await createProductLaunch(apiToken, {
        name: launchName.trim(),
        product_sku: launchSku.trim(),
        product_name: launchName.trim(),
        category: launchCategory.trim(),
        target_platforms: [platform],
        status: 'active',
      });
      const launch = (response.launch || {}) as Row;
      setSelectedLaunchId(String(launch.id || ''));
      setLaunchName('');
      setLaunchSku('');
      setLaunchCategory('');
      onMessage('产品发布项目已创建，可导入 KOL 池后运行推荐。');
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '产品发布项目创建失败');
    } finally {
      onBusyChange(false);
    }
  };

  const importPoolItem = async (event: FormEvent) => {
    event.preventDefault();
    if (!apiToken || !poolHandle.trim()) return;
    onBusyChange(true);
    try {
      await importProductKolPool(apiToken, {
        platform,
        source_type: 'manual',
        source_ref: 'frontend_manual',
        items: [{
          platform,
          handle: poolHandle.trim().replace(/^@/, ''),
          followers: Number(poolFollowers || 0) || undefined,
          avg_views: Number(poolAvgViews || 0) || undefined,
          engagement_rate: Number(poolEngagement || 0) || undefined,
        }],
      });
      setPoolHandle('');
      setPoolFollowers('');
      setPoolAvgViews('');
      setPoolEngagement('');
      onMessage('KOL 池已导入真实/手工数据；未填字段不会显示假 0。');
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'KOL 池导入失败');
    } finally {
      onBusyChange(false);
    }
  };

  const importPoolJson = async () => {
    if (!apiToken || !poolJson.trim()) return;
    onBusyChange(true);
    try {
      const rows = parseJsonRows(poolJson);
      if (!rows.length) throw new Error('JSON 里没有可导入的账号行');
      await importProductKolPool(apiToken, {
        platform,
        source_type: 'apify_or_json',
        source_ref: 'frontend_json_import',
        items: rows,
      });
      setPoolJson('');
      onMessage(`历史 KOL 数据已导入：${rows.length} 行。`);
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'JSON 导入失败');
    } finally {
      onBusyChange(false);
    }
  };

  const runRecommendations = async () => {
    if (!apiToken || !selectedLaunchId) return;
    onBusyChange(true);
    try {
      const response = await runProductRecommendations(apiToken, { launch_id: Number(selectedLaunchId), platform, limit: 100 });
      const rows = (response.recommendations || []) as Row[];
      onRecommendationsChange(rows);
      onMessage(`推荐已生成：${rows.length} 条，当前为规则评分 rule_v0。`);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '推荐生成失败');
    } finally {
      onBusyChange(false);
    }
  };

  const updateRecommendation = async (id: unknown, action: RecommendationAction) => {
    if (!apiToken || !id) return;
    onBusyChange(true);
    try {
      const response = await productRecommendationAction(apiToken, String(id), action, action === 'reject' ? { reason: '前端手动忽略' } : {});
      if (action === 'claim') onMessage('推荐候选已落入主 KOL 库并完成认领。');
      if (action === 'create_project') {
        const project = (response.project || {}) as Row;
        const link = (response.link || {}) as Row;
        const linkLabel = link.slug ? `/go/${String(link.slug)}` : (response.link_error ? `短链未生成：${String(response.link_error)}` : '短链未生成');
        onMessage(`推荐候选已创建项目：${String(project.project_uid || project.id || '-')}；Shopify 短链：${linkLabel}`);
      }
      if (response.recommendation && typeof response.recommendation === 'object') {
        setSelectedRecommendation(response.recommendation as Row);
        await refreshRecommendationEvidence((response.recommendation as Row).id || id);
      } else {
        await refreshRecommendationEvidence(id);
      }
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '推荐状态更新失败');
    } finally {
      onBusyChange(false);
    }
  };

  return {
    submitLaunch,
    importPoolItem,
    importPoolJson,
    runRecommendations,
    updateRecommendation,
  };
}
