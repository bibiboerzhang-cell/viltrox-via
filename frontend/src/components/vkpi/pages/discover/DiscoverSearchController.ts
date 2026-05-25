import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type { VkpiKolLookupResult } from '../../vkpiTypes';
import { arrayValue, objectValue, textValue } from '../../shared/vkpiDataUtils';
import { listMarketingKols, searchMarketingKolsNatural, searchPlatformKols } from '../../../../domains/kol';
import { idleSearchProgress, searchProgressState, type SearchProgressState } from './DiscoverProgressModel';
import {
  candidatePostsFromRaw,
  instantCandidateToUiKol,
  lookupToUiKol,
  mergeSearchKols,
  platformSearchItemToUiKol,
  rawToUiKol,
} from './DiscoverKolModel';
import { MAX_SEARCH_HISTORY, saveSearchHistory } from './DiscoverSearchHistory';
import type { SearchHistoryItem, UiKol } from './DiscoverTypes';

type NoticeTone = 'info' | 'warn' | 'error';

interface SearchFilters {
  platform: string;
}

export interface DiscoverSearchContext {
  apiToken?: string;
  query: string;
  platform: string;
  filters: SearchFilters;
  createIfMissing: boolean;
  scanAccount: boolean;
  searchRunRef: MutableRefObject<number>;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; scanAccount?: boolean; maxPosts?: number }) => Promise<VkpiKolLookupResult>;
  onScanKolAccount?: (kolId: string, maxPosts?: number) => Promise<Record<string, unknown>>;
  setActiveTab: Dispatch<SetStateAction<'search' | 'recommendations' | 'pool'>>;
  setQuery: Dispatch<SetStateAction<string>>;
  setPlatform: Dispatch<SetStateAction<string>>;
  setActiveSearchQuery: Dispatch<SetStateAction<string>>;
  setLocalQuery: Dispatch<SetStateAction<string>>;
  setSearchKols: Dispatch<SetStateAction<UiKol[]>>;
  setSelectedKolId: Dispatch<SetStateAction<string>>;
  setLookupResult: Dispatch<SetStateAction<VkpiKolLookupResult | null>>;
  setSearchProgress: Dispatch<SetStateAction<SearchProgressState>>;
  setSearchHistory: Dispatch<SetStateAction<SearchHistoryItem[]>>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setScanBusy: Dispatch<SetStateAction<boolean>>;
  setNotice: (text: string, tone?: NoticeTone) => void;
}

function recordSearchHistory(context: DiscoverSearchContext, entry: Omit<SearchHistoryItem, 'id' | 'searchedAt'>) {
  const cleanQuery = entry.query.trim();
  if (!cleanQuery) return;
  const normalizedPlatform = entry.platform || 'all';
  const nextItem: SearchHistoryItem = {
    ...entry,
    id: `${normalizedPlatform}:${cleanQuery.toLowerCase()}`,
    query: cleanQuery,
    platform: normalizedPlatform,
    searchedAt: new Date().toISOString(),
  };
  context.setSearchHistory((previous) => {
    const next = [nextItem, ...previous.filter((item) => item.id !== nextItem.id)].slice(0, MAX_SEARCH_HISTORY);
    saveSearchHistory(next);
    return next;
  });
}

async function revealSearchKols(context: DiscoverSearchContext, items: UiKol[], runId: number) {
  if (!items.length || context.searchRunRef.current !== runId) return;
  context.setSearchKols([items[0]]);
  context.setSelectedKolId(items[0].id);
  for (let index = 1; index < items.length; index += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, index < 8 ? 80 : 45));
    if (context.searchRunRef.current !== runId) return;
    context.setSearchKols(items.slice(0, index + 1));
  }
}

export async function loadDiscoverKols(context: DiscoverSearchContext, searchText: string, platformFilter = context.filters.platform): Promise<number> {
  if (!context.apiToken) {
    context.setNotice('当前未登录，列表使用已加载的本地真实数据。', 'warn');
    return 0;
  }
  const result = await listMarketingKols(context.apiToken, {
    search: searchText || undefined,
    platform: platformFilter !== 'all' ? platformFilter : undefined,
    limit: 100,
  });
  const rows = (result.kols || []).map(rawToUiKol);
  context.setSearchKols(rows);
  return rows.length;
}

async function runNaturalSearch(context: DiscoverSearchContext, searchText: string, platformFilter = context.filters.platform): Promise<number> {
  const clean = searchText.trim();
  if (!context.apiToken) {
    const count = await loadDiscoverKols(context, clean, platformFilter);
    context.setLocalQuery(clean);
    return count;
  }
  const result = await searchMarketingKolsNatural(context.apiToken, {
    query: clean,
    platform: platformFilter !== 'all' ? platformFilter : undefined,
    limit: 100,
  });
  const nextKols = (result.items || []).map(rawToUiKol);
  context.setSearchKols(nextKols);
  context.setLocalQuery('');
  if (nextKols[0]?.id) context.setSelectedKolId(nextKols[0].id);
  const parsed = objectValue(result.parsed);
  const parsedBits = [
    textValue(parsed.platform, ''),
    textValue(parsed.country, ''),
    textValue(parsed.level, ''),
    arrayValue(parsed.keywords).slice(0, 3).join('/'),
  ].filter(Boolean);
  context.setNotice(nextKols.length
    ? `已用真实规则解析搜索命中 ${nextKols.length} 个红人${parsedBits.length ? `：${parsedBits.join(' · ')}` : ''}。`
    : '规则解析搜索没有命中真实红人；没有伪造结果，可放宽关键词或先补候选池。', nextKols.length ? 'info' : 'warn');
  return nextKols.length;
}

export async function runDiscoverSearch(context: DiscoverSearchContext, termInput = context.query, platformInput = context.platform) {
  const term = termInput.trim();
  const selectedPlatform = platformInput || context.platform;
  context.setQuery(term);
  context.setPlatform(selectedPlatform);
  if (!term) {
    context.setActiveSearchQuery('');
    context.setSearchProgress(idleSearchProgress);
    await loadDiscoverKols(context, '', context.filters.platform);
    context.setNotice('已刷新红人列表。');
    return;
  }
  const looksLikeHandle = term.startsWith('@') || term.includes('instagram.com') || term.includes('youtube.com') || term.includes('tiktok.com') || term.includes('/');
  const shouldUsePlatformSearch = !looksLikeHandle && selectedPlatform !== 'all' && context.scanAccount && Boolean(context.apiToken);
  const instantCandidate = shouldUsePlatformSearch ? null : instantCandidateToUiKol(term, selectedPlatform);
  const runId = context.searchRunRef.current + 1;
  context.searchRunRef.current = runId;
  context.setActiveTab('search');
  context.setActiveSearchQuery(term);
  context.setLocalQuery('');
  if (instantCandidate) {
    context.setSearchKols((previous) => [instantCandidate, ...previous.filter((kol) => kol.id !== instantCandidate.id)]);
    context.setSelectedKolId(instantCandidate.id);
    context.setSearchProgress(searchProgressState('已生成账号候选，正在进入真实数据源。', 18, 'source', ['candidate']));
  } else {
    context.setSearchKols([]);
    context.setSelectedKolId('');
    context.setSearchProgress(searchProgressState(`正在按「${term}」推荐候选账号。`, 24, 'source', ['candidate']));
  }
  context.setBusy(true);
  context.setNotice('');
  try {
    if (shouldUsePlatformSearch && context.apiToken) {
      let warmKols: UiKol[] = [];
      context.setSearchProgress(searchProgressState('先检索已有 KOL 和 1012 历史合作池。', 30, 'source', ['candidate']));
      try {
        const warmResult = await searchMarketingKolsNatural(context.apiToken, { query: term, platform: selectedPlatform, limit: 30 });
        warmKols = (warmResult.items || []).map(rawToUiKol);
        if (warmKols.length && context.searchRunRef.current === runId) {
          context.setSearchKols(warmKols);
          context.setSelectedKolId(warmKols[0].id);
          context.setNotice(`先命中 ${warmKols.length} 个已有/历史档案；平台实时搜索继续补头像、最近内容和新候选。`);
        }
      } catch (error) {
        console.warn('warm natural search failed', error);
      }
      context.setSearchProgress(searchProgressState(
        warmKols.length ? '已有档案已先显示；平台实时搜索继续补新候选。' : '平台实时搜索中；结果会逐条变成候选卡片。',
        warmKols.length ? 48 : 38,
        'source',
        ['candidate'],
      ));
      if (!warmKols.length) context.setNotice('正在调用平台真实搜索；不会把输入文字伪造成账号。');
      const result = await searchPlatformKols(context.apiToken, { query: term, platform: selectedPlatform, maxResults: 25 });
      context.setSearchProgress(searchProgressState('平台已返回，正在整理头像、账号资料和样本内容。', 68, 'profile', ['candidate', 'source']));
      const candidateIds = Array.isArray(result.candidate_ids) ? result.candidate_ids : [];
      const nextKols = (result.items || []).map((item, index) => platformSearchItemToUiKol(objectValue(item), index, candidateIds[index]));
      const mergedKols = mergeSearchKols(warmKols, nextKols);
      const hasCandidatePosts = mergedKols.some((kol) => candidatePostsFromRaw(kol.raw).length > 0);
      context.setLocalQuery('');
      if (!mergedKols.length) {
        context.setSearchKols([]);
        context.setSelectedKolId('');
        context.setSearchProgress(searchProgressState('平台没有返回候选；没有生成假账号，可换关键词或平台重试。', 100, 'decision', ['candidate', 'source', 'profile', 'decision']));
        context.setNotice(result.message || '平台真实搜索和历史池都没有返回候选；未用本地库伪造结果。', 'warn');
        recordSearchHistory(context, { query: term, platform: selectedPlatform, mode: 'platform_search', resultCount: 0, status: '无候选' });
        return;
      }
      context.setSearchProgress(searchProgressState('平台已返回，正在合并历史档案并逐条显示推荐候选。', hasCandidatePosts ? 84 : 76, 'posts', ['candidate', 'source', 'profile']));
      await revealSearchKols(context, mergedKols, runId);
      if (context.searchRunRef.current !== runId) return;
      context.setSearchProgress(searchProgressState(
        hasCandidatePosts ? '已回填候选和最近内容；可以选择账号后建档或深扫。' : '已返回候选；最近内容需要建档抓取后补齐。',
        hasCandidatePosts ? 100 : 82,
        hasCandidatePosts ? 'decision' : 'posts',
        hasCandidatePosts ? ['candidate', 'source', 'profile', 'posts', 'decision'] : ['candidate', 'source', 'profile'],
      ));
      const historyCount = warmKols.length;
      const message = result.message || `已合并 ${historyCount} 个历史/已有档案 + ${nextKols.length} 个平台实时候选；历史合作会优先标出。`;
      context.setNotice(message, mergedKols.length ? 'info' : 'warn');
      recordSearchHistory(context, { query: term, platform: selectedPlatform, mode: 'platform_search', resultCount: mergedKols.length, status: hasCandidatePosts ? '含最近内容' : (historyCount ? '含历史档案' : '候选已返回') });
    } else if (looksLikeHandle && context.onLookupKol) {
      context.setSearchProgress(searchProgressState('正在查重或建档账号，先保留即时候选。', 45, 'profile', ['candidate', 'source']));
      const result = await context.onLookupKol({ platform: selectedPlatform, handleOrUrl: term, createIfMissing: context.createIfMissing, scanAccount: false, maxPosts: 24 });
      context.setLookupResult(result || null);
      const found = result ? lookupToUiKol(result) : null;
      if (found) {
        context.setSearchKols((previous) => [found, ...previous.filter((kol) => kol.id !== found.id)]);
        context.setSelectedKolId(found.id);
      }
      const kolId = found?.id || String(result?.kol?.id || '');
      if (context.scanAccount && kolId && context.onScanKolAccount) {
        context.setBusy(false);
        context.setScanBusy(true);
        context.setSearchProgress(searchProgressState('账号已建档，正在抓取最近内容。', 76, 'posts', ['candidate', 'source', 'profile']));
        context.setNotice('查重完成，正在用真实接口抓取账号数据。');
        await context.onScanKolAccount(kolId, 24);
        if (context.onLookupKol) {
          const refreshed = await context.onLookupKol({ platform: selectedPlatform, handleOrUrl: term, createIfMissing: false, scanAccount: false, maxPosts: 24 });
          context.setLookupResult(refreshed || result);
          const refreshedKol = refreshed ? lookupToUiKol(refreshed) : null;
          if (refreshedKol) {
            context.setSearchKols((previous) => [refreshedKol, ...previous.filter((kol) => kol.id !== refreshedKol.id)]);
            context.setSelectedKolId(refreshedKol.id);
          }
        }
        context.setSearchProgress(searchProgressState('账号抓取完成；右侧画像和最近内容会刷新。', 100, 'decision', ['candidate', 'source', 'profile', 'posts', 'decision']));
        context.setNotice('账号抓取完成；右侧画像会读取最新 profile / posts。');
        recordSearchHistory(context, { query: term, platform: selectedPlatform, mode: 'account_lookup', resultCount: 1, status: '已抓取' });
      } else {
        context.setSearchProgress(searchProgressState(
          found ? '已命中已有档案；可继续抓取或加入项目。' : '查重完成，但没有可展示档案。',
          found ? 100 : 72,
          found ? 'decision' : 'profile',
          found ? ['candidate', 'source', 'profile', 'decision'] : ['candidate', 'source'],
        ));
        context.setNotice(found ? '查重完成，已打开红人画像。' : '查重完成，但没有返回可展示的红人档案。', found ? 'info' : 'warn');
        recordSearchHistory(context, { query: term, platform: selectedPlatform, mode: 'account_lookup', resultCount: found ? 1 : 0, status: found ? '已命中' : '未命中' });
      }
    } else {
      context.setSearchProgress(searchProgressState('正在检索已有 KOL 档案。', 46, 'source', ['candidate']));
      const count = await runNaturalSearch(context, term, context.filters.platform);
      context.setSearchProgress(searchProgressState('已有档案检索完成；未命中时不会伪造数据。', 100, 'decision', ['candidate', 'source', 'profile', 'decision']));
      recordSearchHistory(context, { query: term, platform: context.filters.platform, mode: 'local_rules', resultCount: count, status: count ? '已有档案' : '未命中' });
    }
  } catch (error) {
    context.setSearchProgress(searchProgressState(
      instantCandidate ? '搜索失败；已保留账号候选，方便换词重试。' : '搜索失败；没有生成假账号，换关键词或平台重试。',
      100,
      'source',
      ['candidate'],
      'source',
    ));
    context.setNotice(error instanceof Error ? error.message : '红人搜索失败', 'error');
    recordSearchHistory(context, { query: term, platform: selectedPlatform, mode: shouldUsePlatformSearch ? 'platform_search' : looksLikeHandle ? 'account_lookup' : 'local_rules', resultCount: 0, status: '失败' });
  } finally {
    context.setBusy(false);
    context.setScanBusy(false);
  }
}
