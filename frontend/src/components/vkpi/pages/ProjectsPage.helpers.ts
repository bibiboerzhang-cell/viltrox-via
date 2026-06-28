import type { VkpiKolOption } from '../vkpiTypes';
import type { ProjectsPageProps } from './ProjectsPage.types';

export interface ImportKolRow {
  platform: string;
  handle: string;
  name?: string;
  email?: string;
}

export function normalizeImportPlatform(value: unknown) {
  const text = String(value || '').trim().toLowerCase();
  if (text.includes('youtube') || text === 'yt') return 'youtube';
  if (text.includes('instagram') || text === 'ig') return 'instagram';
  if (text.includes('tiktok')) return 'tiktok';
  if (text.includes('facebook')) return 'facebook';
  if (text === 'x' || text.includes('twitter')) return 'x';
  if (text.includes('reddit')) return 'reddit';
  return text;
}

export function normalizeImportToken(value: unknown) {
  let token = String(value || '').trim().toLowerCase();
  token = token.replace(/^https?:\/\/(www\.)?/, '');
  token = token.replace(/^(instagram|youtube|tiktok|facebook|twitter|x)\.com\//, '');
  token = token.replace(/^youtu\.be\//, '');
  token = token.replace(/^(channel|user|c)\//, '');
  token = token.replace(/^@+/, '');
  token = token.replace(/[?#].*$/, '');
  token = token.replace(/\/(videos|reels|reel|posts|post|tagged)\/?$/i, '');
  token = token.replace(/\/+$/, '');
  return token.replace(/[^a-z0-9一-鿿]+/g, '');
}

export function buildImportSearchTerms(row: ImportKolRow) {
  const rawTerms = [row.handle, row.name].map((value) => String(value || '').trim()).filter(Boolean);
  const normalizedTerms = rawTerms.map(normalizeImportToken).filter(Boolean);
  return Array.from(new Set([...rawTerms, ...normalizedTerms]));
}

export function findImportKolMatch(row: ImportKolRow, candidates: VkpiKolOption[]) {
  const rowPlatform = normalizeImportPlatform(row.platform);
  const rowTokens = [row.handle, row.name].map(normalizeImportToken).filter(Boolean);
  if (!rowTokens.length) return undefined;
  const platformCandidates = candidates.filter((candidate) => {
    const candidatePlatform = normalizeImportPlatform(candidate.platform);
    return !rowPlatform || !candidatePlatform || rowPlatform === candidatePlatform;
  });
  const pool = platformCandidates.length ? platformCandidates : candidates;
  return pool.find((candidate) => {
    const candidateTokens = [candidate.handle, candidate.name, candidate.profileUrl].map(normalizeImportToken).filter(Boolean);
    return rowTokens.some((token) => candidateTokens.includes(token));
  });
}

export async function lookupImportKolPoolOption(
  row: ImportKolRow,
  onLookupKol: NonNullable<ProjectsPageProps['onLookupKol']>,
): Promise<VkpiKolOption | undefined> {
  const handleOrUrl = String(row.handle || row.name || '').trim();
  if (!handleOrUrl) return undefined;
  try {
    const result = await onLookupKol({
      platform: normalizeImportPlatform(row.platform || 'Other') || 'Other',
      handleOrUrl,
      createIfMissing: false,
      scanAccount: false,
      maxPosts: 1,
      contactEmail: row.email,
    });
    const kol = result.kol || {};
    const rawPoolId = kol.kol_pool_id ?? kol.kolPoolId ?? kol.pool_id ?? kol.poolId;
    const poolId = rawPoolId == null ? '' : String(rawPoolId);
    if (!poolId || !/^\d+$/.test(poolId)) return undefined;
    return {
      id: poolId,
      name: String(kol.display_name || kol.channel_name || kol.name || row.name || row.handle || `KOL ${poolId}`),
      handle: String(kol.handle || row.handle || ''),
      platform: platformLabel(row.platform || kol.platform || 'Other'),
      avatar: String(kol.avatar_url || ''),
      profileUrl: String(kol.profile_url || kol.channel_url || ''),
      contactEmail: row.email,
      followerLabel: '-',
      contentCountLabel: '-',
      claimOwner: '',
      scanStatus: 'lookup_pool',
    };
  } catch {
    return undefined;
  }
}

export function platformLabel(value: unknown): VkpiKolOption['platform'] {
  const normalized = normalizeImportPlatform(value);
  if (normalized === 'youtube') return 'YouTube';
  if (normalized === 'instagram') return 'Instagram';
  if (normalized === 'tiktok') return 'TikTok';
  if (normalized === 'facebook') return 'Facebook';
  if (normalized === 'x') return 'X';
  if (normalized === 'reddit') return 'Reddit';
  return 'Other';
}

export function parseKolImportRows(raw: string, fallbackPlatform: string): ImportKolRow[] {
  const rows: ImportKolRow[] = [];
  const seen = new Set<string>();
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line, index) => {
      const cells = line.split(/\t|,/).map((cell) => cell.trim()).filter(Boolean);
      const normalizedHeaderCells = cells.map((cell) => cell.toLowerCase());
      const looksLikeHeader = index === 0 && normalizedHeaderCells.some((cell) => ['platform', '平台'].includes(cell))
        && normalizedHeaderCells.some((cell) => ['handle', '账号', 'kol', 'email', '邮箱', 'name', '名称'].includes(cell));
      if (looksLikeHeader) return;
      if (!cells.length) return;
      let platform = fallbackPlatform;
      let handle = cells[0] || '';
      let name = '';
      let email = '';
      if (cells.length >= 2 && /instagram|youtube|tiktok|facebook|reddit|twitter|^x$|other/i.test(cells[0])) {
        platform = cells[0];
        handle = cells[1] || '';
        name = cells[2] || '';
        email = cells[3] || '';
      } else {
        name = cells[1] || '';
        email = cells[2] || '';
      }
      handle = handle.trim();
      if (!handle) return;
      const dedupeKey = `${platform.toLowerCase()}::${handle.toLowerCase()}`;
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);
      rows.push({ platform, handle, name, email });
    });
  return rows.slice(0, 50);
}
