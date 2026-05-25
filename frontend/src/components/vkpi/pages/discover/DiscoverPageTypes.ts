import type { VkpiDashboardData, VkpiKolLookupResult, VkpiPageKey } from '../../vkpiTypes';

export interface DiscoverPageProps {
  data: VkpiDashboardData;
  onLookupKol?: (payload: {
    platform: string;
    handleOrUrl: string;
    createIfMissing?: boolean;
    email?: string;
    contactEmail?: string;
    notes?: string;
    scanAccount?: boolean;
    maxPosts?: number;
    productSku?: string;
  }) => Promise<VkpiKolLookupResult>;
  onScanKolAccount?: (kolId: string, maxPosts?: number) => Promise<Record<string, unknown>>;
  onClaimKol?: (kolId: string) => Promise<void>;
  onUpdateKol?: (kolId: string, payload: {
    avatarUrl?: string;
    profileUrl?: string;
    contactEmail?: string;
    contactPhone?: string;
    notes?: string;
    contactLinks?: Array<{ label?: string; value?: string; url?: string }>;
  }) => Promise<void>;
  onCreateProject?: (payload: {
    projectName: string;
    kolId?: string;
    productSku?: string;
    productName?: string;
    productSkus?: string[];
    products?: Array<{ productSku: string; productName?: string }>;
    platform?: string;
    marketplace?: string;
    note?: string;
  }) => Promise<Record<string, unknown> | void>;
  onSelectPage?: (page: VkpiPageKey) => void;
  apiToken?: string;
}

export type DiscoverTab = 'search' | 'recommendations' | 'pool';
export type MessageTone = 'info' | 'warn' | 'error';
