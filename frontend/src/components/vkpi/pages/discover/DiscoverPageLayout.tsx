import type { Dispatch, FormEvent, SetStateAction } from 'react';
import type { VkpiKolLookupResult, VkpiKolProfile, VkpiProjectRow } from '../../vkpiTypes';
import { KolPoolPanel } from '../../panels/KolPoolPanel';
import { creatorPlatformOptions } from '../../shared/vkpiConstants';
import { PageShell } from '../PageShell';
import type { DiscoverFocusPayload } from '../../intelligence/intelligenceDiscoveryFocus';
import type { CandidateDecisionRecord } from '../../intelligence/intelligenceCandidateDecision';
import {
  batchEnrichKolPool,
  enrichKolPoolItem,
  getKolPoolIntelligenceCard,
  getKolPoolItem,
  listKolPool,
  promoteKolPoolToMain,
  type VkpiKolAssessmentResponse,
} from '../../../../domains/kol';
import { SearchPanel, SearchProgress } from './DiscoverSearchPanel';
import { ProfilePanel } from './DiscoverProfilePanel';
import { CandidateDecisionBanner, DiscoverFocusBanner, DiscoveryQueuePanel, type DiscoveryQueueItem } from './DiscoverQueuePanels';
import { RecommendationPanel, type RecommendationAction, type SmartRecommendation } from './DiscoverRecommendationPanel';
import { recommendationToUiKol } from './DiscoverRecommendationModel';
import type { CompetitorRelation, ContactItem, Dimensions11Payload, ProductFitItem, SearchHistoryItem, UiKol } from './DiscoverTypes';
import type { SearchProgressState } from './DiscoverProgressModel';
import type { DirectionChip } from './DiscoverQueueModel';

type DiscoverTab = 'search' | 'recommendations' | 'pool';
type MessageTone = 'info' | 'warn' | 'error';
type SearchFilters = { platform: string; level: string; grade: string; collab: string; risk: string; freshness: string };

interface HeroMetric {
  label: string;
  value: string;
  hint: string;
}

interface DiscoverPageLayoutProps {
  heroMetrics: HeroMetric[];
  message: string;
  messageTone: MessageTone;
  discoverFocus: DiscoverFocusPayload | null;
  candidateDecision: CandidateDecisionRecord | null;
  selectedCandidateDecision: CandidateDecisionRecord | null;
  candidateMaterializing: boolean;
  selectedKol?: UiKol;
  selectedKolPoolId: string;
  onCreateProject?: unknown;
  onLookupKol?: unknown;
  apiToken?: string;
  canOpenProjectFromDecision: boolean;
  canPrepareCandidate: boolean;
  directionChips: DirectionChip[];
  discoveryQueueItems: DiscoveryQueueItem[];
  discoveryQueueLoading: boolean;
  discoveryQueueMessage: string;
  activeTab: DiscoverTab;
  setActiveTab: Dispatch<SetStateAction<DiscoverTab>>;
  query: string;
  setQuery: Dispatch<SetStateAction<string>>;
  platform: string;
  setPlatform: Dispatch<SetStateAction<string>>;
  createIfMissing: boolean;
  setCreateIfMissing: Dispatch<SetStateAction<boolean>>;
  scanAccount: boolean;
  setScanAccount: Dispatch<SetStateAction<boolean>>;
  busy: boolean;
  scanBusy: boolean;
  filters: SearchFilters;
  setFilters: Dispatch<SetStateAction<SearchFilters>>;
  localQuery: string;
  setLocalQuery: Dispatch<SetStateAction<string>>;
  visibleKols: UiKol[];
  searchProgress: SearchProgressState;
  searchHistory: SearchHistoryItem[];
  smartRecommendations: SmartRecommendation[];
  recommendationLoading: boolean;
  recommendationMessage: string;
  localFallbackCount: number;
  selectedProfile: VkpiKolProfile | null;
  selectedAssessment: VkpiKolAssessmentResponse | null;
  selectedDimensions11: Dimensions11Payload | null;
  lookupResult: VkpiKolLookupResult | null;
  contacts: ContactItem[];
  dimensions: Array<{ key: string; label: string; source: string; value: number; pending: boolean }>;
  competitorRelations: CompetitorRelation[];
  competitorLoading: boolean;
  dimensions11Loading: boolean;
  productFits: ProductFitItem[];
  posts: Array<Record<string, unknown>>;
  projects: VkpiProjectRow[];
  profileLoading: boolean;
  internalNote: string;
  setInternalNote: Dispatch<SetStateAction<string>>;
  canClaim: boolean;
  canScan: boolean;
  canUpdate: boolean;
  canCreateProject: boolean;
  contactModalOpen: boolean;
  contactType: string;
  setContactType: Dispatch<SetStateAction<string>>;
  contactValue: string;
  setContactValue: Dispatch<SetStateAction<string>>;
  contactEvidence: string;
  setContactEvidence: Dispatch<SetStateAction<string>>;
  projectModalOpen: boolean;
  projectProductSku: string;
  setProjectProductSku: Dispatch<SetStateAction<string>>;
  projectNote: string;
  setProjectNote: Dispatch<SetStateAction<string>>;
  onDismissDiscoverFocus: () => void;
  onRunDiscoverFocus: (focus: DiscoverFocusPayload) => void;
  onDismissCandidateDecision: () => void;
  onPrepareCandidate: (decision: CandidateDecisionRecord) => void;
  onOpenProjectFromDecision: (decision: CandidateDecisionRecord) => void;
  onRunDecision: (decision: CandidateDecisionRecord) => void;
  runSearch: (query: string, platform: string) => void;
  setNotice: (text: string, tone?: MessageTone) => void;
  onRefreshKols: () => void;
  handleSearch: (event?: FormEvent) => Promise<void>;
  onSelectKol: Dispatch<SetStateAction<string>>;
  onHistorySelect: (item: SearchHistoryItem) => void;
  onSelectRecommendation: (recommendation: SmartRecommendation) => void;
  onRefreshRecommendations: () => void;
  onRegenerateRecommendations: () => void;
  onRecommendationAction: (recommendation: SmartRecommendation, action: RecommendationAction) => void;
  onClaim: () => Promise<void>;
  onDeepScan: () => Promise<void>;
  onSendToIntelligence: () => void;
  onOpenContact: () => void;
  onOpenProject: () => void;
  onCloseContact: () => void;
  saveManualContact: () => Promise<void>;
  onCloseProject: () => void;
  createProjectForKol: () => Promise<void>;
}

const tabLabels: Array<{ key: DiscoverTab; label: string; hint: string }> = [
  { key: 'search', label: '主动搜索', hint: '真实查重 / 抓取 / 画像' },
  { key: 'recommendations', label: '智能推荐', hint: 'Product Analysis 真实推荐' },
  { key: 'pool', label: '候选池', hint: '复用 KOL Pool' },
];

export function DiscoverPageLayout(props: DiscoverPageLayoutProps) {
  const {
    heroMetrics, message, messageTone, discoverFocus, candidateDecision, selectedCandidateDecision,
    candidateMaterializing, selectedKol, selectedKolPoolId, onCreateProject, onLookupKol, apiToken,
    directionChips, discoveryQueueItems, discoveryQueueLoading, discoveryQueueMessage,
    activeTab, setActiveTab, query, setQuery, platform, setPlatform, createIfMissing,
    setCreateIfMissing, scanAccount, setScanAccount, busy, scanBusy, filters, setFilters,
    localQuery, setLocalQuery, visibleKols, searchProgress, searchHistory, smartRecommendations,
    recommendationLoading, recommendationMessage, localFallbackCount, selectedProfile,
    selectedAssessment, selectedDimensions11, lookupResult, contacts, dimensions,
    competitorRelations, competitorLoading, dimensions11Loading, productFits, posts,
    projects, profileLoading, internalNote, setInternalNote, canClaim, canScan, canUpdate,
    canCreateProject, contactModalOpen, contactType, setContactType, contactValue,
    setContactValue, contactEvidence, setContactEvidence, projectModalOpen,
    projectProductSku, setProjectProductSku, projectNote, setProjectNote,
  } = props;

  return (
    <PageShell
      title="红人决策中枢"
      description="主动搜索、智能推荐、深度分析共用 kol_pool：搜 -> 查 -> 评 -> 决 -> 录。"
      eyebrow={null}
      headingExtra={(
        <div className="vkpi-discover-heading-metrics">
          {heroMetrics.map((metric) => (
            <div className="vkpi-discover-metric" key={metric.label}>
              <strong>{metric.value}</strong>
              <span>{metric.label}</span>
            </div>
          ))}
        </div>
      )}
    >
      <div className="vkpi-discover-v2">
        {message ? <div className={`vkpi-discover-notice is-${messageTone}`}>{message}</div> : null}

        {discoverFocus ? (
          <DiscoverFocusBanner focus={discoverFocus} onDismiss={props.onDismissDiscoverFocus} onRun={props.onRunDiscoverFocus} />
        ) : null}

        {candidateDecision ? (
          <CandidateDecisionBanner
            decision={candidateDecision}
            canOpenProject={props.canOpenProjectFromDecision}
            canPrepareCandidate={props.canPrepareCandidate}
            preparing={candidateMaterializing}
            prepareLabel={(selectedKolPoolId || candidateDecision.kolPoolId) ? '先转主 KOL' : '先建档'}
            selectedKolId={selectedKol?.id}
            onDismiss={props.onDismissCandidateDecision}
            onPrepareCandidate={props.onPrepareCandidate}
            onOpenProject={props.onOpenProjectFromDecision}
            onRun={props.onRunDecision}
          />
        ) : null}

        <div className="vkpi-discover-intent-chips" aria-label="数据搜索方向">
          <span className="vkpi-discover-intent-chips__label">数据方向</span>
          {directionChips.map((chip) => (
            <button key={chip.label} type="button" title={chip.source} onClick={() => { setQuery(chip.query); props.runSearch(chip.query, platform); }}>
              {chip.label}
            </button>
          ))}
        </div>

        <DiscoveryQueuePanel
          items={discoveryQueueItems}
          loading={discoveryQueueLoading}
          message={discoveryQueueMessage}
          onOpenRecommendations={() => setActiveTab('recommendations')}
          onRun={(item) => {
            props.setNotice(`已从发现队列进入搜索：${item.title}`);
            props.runSearch(item.query, item.platform || 'all');
          }}
        />

        <nav className="vkpi-discover-tabs" aria-label="红人搜索工作区">
          {tabLabels.map((tab) => (
            <button key={tab.key} type="button" className={activeTab === tab.key ? 'is-active' : ''} onClick={() => setActiveTab(tab.key)}>
              <strong>{tab.label}</strong>
              <span>{tab.hint}</span>
            </button>
          ))}
        </nav>

        <section className="vkpi-discover-command">
          <form className="vkpi-discover-command__search" onSubmit={(event) => void props.handleSearch(event)}>
            <span>⌘K</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="粘贴 URL / @handle / 关键词，例如：美国 35mm 街拍 IG 红人" />
            <select value={platform} onChange={(event) => setPlatform(event.target.value)} aria-label="搜索平台">
              <option value="all">全部平台</option>
              {creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <button className="vkpi-discover-btn is-primary" type="submit" disabled={busy || scanBusy || (!apiToken && !onLookupKol)}>
              {busy || scanBusy ? '处理中' : '搜索'}
            </button>
          </form>
          <div className="vkpi-discover-command__actions">
            <label><input type="checkbox" checked={createIfMissing} onChange={(event) => setCreateIfMissing(event.target.checked)} /> 自动建档</label>
            <label><input type="checkbox" checked={scanAccount} onChange={(event) => setScanAccount(event.target.checked)} /> 抓取账号</label>
            <button className="vkpi-discover-btn" type="button" onClick={props.onRefreshKols} disabled={!apiToken || busy}>刷新</button>
          </div>
        </section>

        <SearchProgress progress={searchProgress} />

        {activeTab === 'pool' ? (
          <KolPoolPanel
            apiToken={apiToken || ''}
            onListPool={(params) => apiToken ? listKolPool(apiToken, params) : Promise.reject(new Error('未登录'))}
            onGetItem={(kolPoolId) => apiToken ? getKolPoolItem(apiToken, kolPoolId) : Promise.reject(new Error('未登录'))}
            onGetIntelligenceCard={(kolPoolId) => apiToken ? getKolPoolIntelligenceCard(apiToken, kolPoolId) : Promise.reject(new Error('未登录'))}
            onEnrichItem={(kolPoolId, maxPosts) => apiToken ? enrichKolPoolItem(apiToken, kolPoolId, maxPosts) : Promise.reject(new Error('未登录'))}
            onBatchEnrich={(payload) => apiToken ? batchEnrichKolPool(apiToken, payload) : Promise.reject(new Error('未登录'))}
            onPromoteToMain={(kolPoolId) => apiToken ? promoteKolPoolToMain(apiToken, kolPoolId) : Promise.reject(new Error('未登录'))}
            onOpenImport={() => setActiveTab('search')}
          />
        ) : (
          <section className="vkpi-discover-workgrid">
            <div className="vkpi-discover-left">
              {activeTab === 'search' ? (
                <SearchPanel
                  localQuery={localQuery}
                  setLocalQuery={setLocalQuery}
                  filters={filters}
                  setFilters={setFilters}
                  visibleKols={visibleKols}
                  selectedKolId={selectedKol?.id || ''}
                  searchProgress={searchProgress}
                  searchHistory={searchHistory}
                  onSelect={props.onSelectKol}
                  onHistorySelect={props.onHistorySelect}
                />
              ) : (
                <RecommendationPanel
                  recommendations={smartRecommendations}
                  loading={recommendationLoading}
                  message={recommendationMessage}
                  localFallbackCount={localFallbackCount}
                  selectedKolId={selectedKol?.id || ''}
                  getActiveId={(recommendation) => recommendation.kolId || recommendationToUiKol(recommendation).id}
                  onSelect={props.onSelectRecommendation}
                  onRefresh={props.onRefreshRecommendations}
                  onRegenerate={props.onRegenerateRecommendations}
                  onAction={props.onRecommendationAction}
                />
              )}
            </div>

            <aside className="vkpi-discover-profile">
              <ProfilePanel
                selectedKol={selectedKol}
                selectedProfile={selectedProfile}
                selectedAssessment={selectedAssessment}
                selectedDimensions11={selectedDimensions11}
                lookupResult={lookupResult}
                contacts={contacts}
                dimensions={dimensions}
                competitorRelations={competitorRelations}
                competitorLoading={competitorLoading}
                dimensions11Loading={dimensions11Loading}
                productFits={productFits}
                posts={posts}
                projects={projects}
                profileLoading={profileLoading}
                scanBusy={scanBusy}
                busy={busy}
                internalNote={internalNote}
                setInternalNote={setInternalNote}
                onClaim={props.onClaim}
                onDeepScan={props.onDeepScan}
                onSendToIntelligence={props.onSendToIntelligence}
                onOpenContact={props.onOpenContact}
                onOpenProject={props.onOpenProject}
                canClaim={canClaim}
                canScan={canScan}
                canSendToIntelligence={Boolean(selectedKol)}
                canUpdate={canUpdate}
                canCreateProject={canCreateProject}
              />
            </aside>
          </section>
        )}
      </div>

      {contactModalOpen ? (
        <div className="vkpi-discover-modal" role="dialog" aria-modal="true" aria-label="手动添加联系方式">
          <div className="vkpi-discover-modal__box">
            <h3>手动添加联系方式</h3>
            <p>UI 预留 5 层漏斗；当前保存仍走现有红人补录接口。</p>
            <label>类型<select value={contactType} onChange={(event) => setContactType(event.target.value)}>
              <option value="email">Email</option>
              <option value="phone">Phone</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="manager_email">经纪人 Email</option>
              <option value="linkedin">LinkedIn</option>
            </select></label>
            <label>联系方式<input value={contactValue} onChange={(event) => setContactValue(event.target.value)} placeholder="business@example.com" /></label>
            <label>证据 / 备注<textarea value={contactEvidence} onChange={(event) => setContactEvidence(event.target.value)} placeholder="人工从官网 / DM / 名片确认" /></label>
            <div className="vkpi-discover-modal__actions">
              <button className="vkpi-discover-btn" type="button" onClick={props.onCloseContact}>取消</button>
              <button className="vkpi-discover-btn is-primary" type="button" onClick={() => void props.saveManualContact()} disabled={busy || !contactValue.trim() || !(apiToken || props.canUpdate)}>保存</button>
            </div>
          </div>
        </div>
      ) : null}

      {projectModalOpen ? (
        <div className="vkpi-discover-modal" role="dialog" aria-modal="true" aria-label="加入推广项目">
          <div className="vkpi-discover-modal__box">
            <h3>加入推广项目</h3>
            <p>当前先用现有项目创建接口建立 KOL 项目；Campaign 聚合保持由「我的项目」页面负责。</p>
            <label>产品 SKU<input value={projectProductSku} onChange={(event) => setProjectProductSku(event.target.value)} placeholder="AF 35mm F1.2 LAB FE" /></label>
            <label>备注<textarea value={projectNote} onChange={(event) => setProjectNote(event.target.value)} placeholder="从红人决策中枢加入项目" /></label>
            <div className="vkpi-discover-modal__actions">
              <button className="vkpi-discover-btn" type="button" onClick={props.onCloseProject}>取消</button>
              <button className="vkpi-discover-btn is-primary" type="button" onClick={() => void props.createProjectForKol()} disabled={busy || !onCreateProject}>创建项目</button>
            </div>
          </div>
        </div>
      ) : null}
    </PageShell>
  );
}
