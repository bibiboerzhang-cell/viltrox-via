import type { VkpiKolProductFitResponse } from '../../../../domains/kol';
import type { VkpiPlatform } from '../../vkpiTypes';

export interface SearchHistoryItem {
  id: string;
  query: string;
  platform: string;
  mode: string;
  resultCount: number;
  status: string;
  searchedAt: string;
}

export interface UiKol {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  followerCount: number;
  followerLabel: string;
  contentCount: number;
  contentCountLabel: string;
  score: number;
  grade: string;
  country: string;
  topic: string;
  claimOwner: string;
  status: string;
  freshness: string;
  contactCount: number;
  projectCount: number;
  hasCollaboration: boolean;
  riskLabel: string;
  sourceKind?: 'kol' | 'platform_search' | 'kol_pool';
  raw: Record<string, unknown>;
}

export interface ContactItem {
  id: string;
  type: string;
  value: string;
  layer: number;
  source: string;
  confidence?: number;
  evidence?: string;
  verified?: boolean;
}

export type ProductFitItem = NonNullable<VkpiKolProductFitResponse['items']>[number];
export type CompetitorRelation = Record<string, unknown>;
export type Dimensions11Payload = Record<string, unknown>;
