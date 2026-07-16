import type { VkpiDashboardData, VkpiPageKey, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../vkpiTypes';

export interface ProjectsPageProps {
  data: VkpiDashboardData;
  filteredProjects: VkpiProjectRow[];
  selectedProjectId?: string;
  selectedProject?: VkpiProjectRow;
  openProjectId?: string;
  /** openProjectId 消费回执:页内已按该 id 打开详情后回调,宿主清空管道(同一项目可重复打开)。 */
  onConsumeOpenProject?: () => void;
  viewMode: 'manager' | 'employee';
  onSelectProject: (project: VkpiProjectRow) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; email?: string; contactEmail?: string; notes?: string; scanAccount?: boolean; maxPosts?: number; productSku?: string }) => Promise<{ kol?: Record<string, unknown> | null; created?: boolean }>;
  onCreateProject?: (payload: { projectName: string; kolId?: string; productSku?: string; productName?: string; productSkus?: string[]; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; sourceType?: string; note?: string; metadata?: Record<string, unknown> }) => Promise<Record<string, unknown> | void>;
  onUpdateProject?: (projectId: string, payload: { projectName?: string; productSku?: string; productName?: string; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: (projectId: string, reason?: string) => Promise<void>;
  onAddProjectCost?: (payload: { projectId: string; costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> }) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onSelectPage?: (page: VkpiPageKey) => void;
  onToggleView?: (targetPage?: VkpiPageKey) => void;
  onRefreshData?: () => void | Promise<void>;
  apiToken?: string;
  embeddedModuleKey?: string;
}
