import type { Row } from './types';
import { normalizePlatform } from './platformHelpers';

/** 从 row 中按多 key 优先级取字符串 */
export function rowString(row: Row | null | undefined, keys: string[], fallback = ''): string {
  if (!row) return fallback;
  for (const key of keys) {
    const value = row[key];
    if (value !== null && value !== undefined && String(value).trim() !== '') return String(value);
  }
  return fallback;
}

/** 从 row 中按多 key 优先级取数字 */
export function rowNumber(row: Row | null | undefined, keys: string[]): number | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if (value === null || value === undefined || value === '') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/**
 * 稳定 ID 生成 - 不使用 Math.random,避免 React key 不稳定
 * 优先级: id > account_id > profile_id > handle > 拼接 fallback
 */
export function accountId(row: Row): string {
  for (const key of ['id', 'account_id', 'profile_id', 'handle']) {
    const value = row[key];
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      return String(value);
    }
  }
  // 稳定字符串拼接 fallback,绝不用 random
  const platform = String(row.platform || 'other');
  const name = String(row.display_name || row.name || row.username || 'unknown');
  return `tmp:${platform}:${name}`;
}

/** Post 稳定 key 生成 */
export function postKey(post: Row, index: number): string {
  const id = rowString(post, ['id', 'post_uid', 'post_id', 'platform_post_id'], '');
  if (id) return id;
  const url = rowString(post, ['post_url', 'permalink_url', 'video_url'], '');
  if (url) return url;
  const platform = rowString(post, ['platform'], 'other');
  const time = rowString(post, ['published_at', 'posted_at', 'created_at'], '');
  return `idx:${index}:${platform}:${time}`;
}

export function accountName(row: Row | null | undefined): string {
  return rowString(row, ['display_name', 'name', 'handle', 'username', 'profile_name'], '-').replace(/^@/, '');
}

export function normalizeLookupValue(value: unknown): string {
  return String(value || '')
    .toLowerCase()
    .replace(/^@/, '')
    .trim();
}

export function explicitAccountId(row: Row | null | undefined): string {
  return rowString(row, [
    'account_id',
    'industry_account_id',
    'profile_id',
    'accountId',
    'industryAccountId',
    'platform_user_id',
  ]);
}

export function accountHandle(row: Row | null | undefined): string {
  return normalizeLookupValue(rowString(row, [
    'handle',
    'account_handle',
    'username',
    'platform_username',
    'screen_name',
  ]));
}

function postHandle(row: Row | null | undefined): string {
  return normalizeLookupValue(rowString(row, [
    'handle',
    'account_handle',
    'username',
    'ownerUsername',
    'owner_username',
    'authorUsername',
    'author_username',
    'profile_username',
    'profile_handle',
  ]));
}

export function findAccountForPost(post: Row, accounts: Row[]): Row | undefined {
  const postAccount = explicitAccountId(post);
  const postHandleValue = postHandle(post);
  const postPlatform = normalizePlatform(rowString(post, ['platform']));
  return accounts.find((account) => {
    const accountRawId = rowString(account, ['id', 'account_id', 'industry_account_id', 'profile_id', 'platform_user_id']);
    const accountExplicitId = explicitAccountId(account);
    const accountHandleValue = accountHandle(account);
    const accountPlatform = normalizePlatform(rowString(account, ['platform']));
    const platformMatches = !postPlatform || postPlatform === 'other' || !accountPlatform || accountPlatform === 'other' || postPlatform === accountPlatform;
    const idMatches = Boolean(postAccount && (postAccount === accountRawId || postAccount === accountExplicitId));
    const handleMatches = Boolean(platformMatches && postHandleValue && accountHandleValue && postHandleValue === accountHandleValue);
    return idMatches || handleMatches;
  });
}

export function postUrl(row: Row): string {
  return rowString(row, ['post_url', 'permalink_url', 'video_url', 'external_url'], '');
}

export function postTitle(row: Row): string {
  return rowString(row, ['title', 'caption', 'text', 'description', 'post_url'], '未命名内容');
}

export function postAccountName(row: Row, accounts: Row[]): string {
  const account = findAccountForPost(row, accounts);
  return rowString(row, ['display_name', 'account_handle', 'handle', 'username'], account ? accountName(account) : 'tracked.profile');
}

export function postPlatform(row: Row, accounts: Row[]): string {
  const account = findAccountForPost(row, accounts);
  return rowString(row, ['platform'], account ? rowString(account, ['platform'], 'other') : 'other');
}
