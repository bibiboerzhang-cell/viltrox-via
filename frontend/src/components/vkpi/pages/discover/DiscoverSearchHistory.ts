import { objectValue, safeNumber, textValue } from '../../shared/vkpiDataUtils';
import type { SearchHistoryItem } from './DiscoverTypes';

const SEARCH_HISTORY_STORAGE_KEY = 'vkpi.discover.searchHistory.v1';
export const MAX_SEARCH_HISTORY = 12;

export function loadSearchHistory(): SearchHistoryItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SEARCH_HISTORY_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => {
      const row = objectValue(item);
      const query = textValue(row.query, '');
      if (!query) return null;
      return {
        id: textValue(row.id, `${textValue(row.platform, 'all')}:${query.toLowerCase()}`),
        query,
        platform: textValue(row.platform, 'all'),
        mode: textValue(row.mode, 'search'),
        resultCount: safeNumber(row.resultCount),
        status: textValue(row.status, ''),
        searchedAt: textValue(row.searchedAt, new Date().toISOString()),
      };
    }).filter(Boolean).slice(0, MAX_SEARCH_HISTORY) as SearchHistoryItem[];
  } catch {
    return [];
  }
}

export function saveSearchHistory(items: SearchHistoryItem[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(items.slice(0, MAX_SEARCH_HISTORY)));
  } catch {
    // Ignore private-mode storage failures; search itself should still work.
  }
}
