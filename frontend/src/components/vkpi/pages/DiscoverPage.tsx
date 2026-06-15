import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { VkpiKolLookupResult, VkpiKolProfile } from '../vkpiTypes';
import { arrayValue, compactCount, objectValue, textValue } from '../shared/vkpiDataUtils';
import { clearDiscoverFocus, readDiscoverFocus, type DiscoverFocusPayload } from '../intelligence/intelligenceDiscoveryFocus';
import { clearLatestCandidateDecision, readLatestCandidateDecision, type CandidateDecisionRecord } from '../intelligence/intelligenceCandidateDecision';
import { writeProjectFocus } from '../intelligence/intelligenceProjectFocus';
import { writeIntelligenceReviewCard } from '../intelligence/intelligenceReviewFocus';
import {
  type RecommendationAction,
  type SmartRecommendation,
} from './discover/DiscoverRecommendationPanel';
import {
  idleSearchProgress,
  type SearchProgressState,
} from './discover/DiscoverProgressModel';
import { DiscoverPageLayout } from './discover/DiscoverPageLayout';
import {
  buildDirectionChips,
} from './discover/DiscoverQueueModel';
import {
  apiContactsToItems,
  buildDiscoverReviewCard,
  contactsFromProfile,
  dimensionsFromProfile,
  filterKols,
  isCandidateKol,
  isPlatformSearchCandidate,
  kolOptionToUiKol,
  kolPoolIdForCompetitors,
  lookupToUiKol,
  normalizeHandle,
  platformInputValue,
  profileProjects,
  promotedResultToUiKol,
  rawToUiKol,
  recentPosts,
} from './discover/DiscoverKolModel';
import { loadSearchHistory } from './discover/DiscoverSearchHistory';
import { recommendationToSmart, recommendationToUiKol } from './discover/DiscoverRecommendationModel';
import {
  loadDiscoverKols,
  runDiscoverSearch,
  type DiscoverSearchContext,
} from './discover/DiscoverSearchController';
import { useDiscoveryQueueSources } from './discover/DiscoverQueueSources';
import type { DiscoverPageProps, DiscoverTab, MessageTone } from './discover/DiscoverPageTypes';
import type {
  CompetitorRelation,
  ContactItem,
  Dimensions11Payload,
  ProductFitItem,
  SearchHistoryItem,
  UiKol,
} from './discover/DiscoverTypes';
import {
  addKolContact,
  getKolAssessment,
  getKolPoolCompetitors,
  getKolPoolDimensions11,
  getKolPosts,
  getKolProductFit,
  getKolProfile,
  listKolContacts,
  promoteKolPoolToMain,
  type VkpiKolAssessmentResponse,
} from '../../../domains/kol';
import {
  listProductRecommendations,
  productRecommendationAction,
  runProductRecommendations,
} from '../../../domains/products';
import './discover/discoverDecision.css';

export function DiscoverPage({ data, onLookupKol, onScanKolAccount, onClaimKol, onUpdateKol, onCreateProject, onSelectPage, apiToken }: DiscoverPageProps) {
  const baseKols = useMemo(() => data.kolOptions.map(kolOptionToUiKol), [data.kolOptions]);
  const [activeTab, setActiveTab] = useState<DiscoverTab>('search');
  const [query, setQuery] = useState('');
  const [localQuery, setLocalQuery] = useState('');
  const [platform, setPlatform] = useState('youtube');
  const [createIfMissing, setCreateIfMissing] = useState(true);
  const [scanAccount, setScanAccount] = useState(true);
  const [filters, setFilters] = useState({ platform: 'all', level: 'all', grade: 'all', collab: 'all', risk: 'all', freshness: 'all' });
  const [searchKols, setSearchKols] = useState<UiKol[]>([]);
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [lookupResult, setLookupResult] = useState<VkpiKolLookupResult | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<VkpiKolProfile | null>(null);
  const [selectedAssessment, setSelectedAssessment] = useState<VkpiKolAssessmentResponse | null>(null);
  const [selectedProductFits, setSelectedProductFits] = useState<ProductFitItem[]>([]);
  const [selectedContacts, setSelectedContacts] = useState<ContactItem[]>([]);
  const [selectedCompetitors, setSelectedCompetitors] = useState<CompetitorRelation[]>([]);
  const [competitorLoading, setCompetitorLoading] = useState(false);
  const [selectedDimensions11, setSelectedDimensions11] = useState<Dimensions11Payload | null>(null);
  const [dimensions11Loading, setDimensions11Loading] = useState(false);
  const [smartRecommendations, setSmartRecommendations] = useState<SmartRecommendation[]>([]);
  const [discoverFocus, setDiscoverFocus] = useState<DiscoverFocusPayload | null>(null);
  const [candidateDecision, setCandidateDecision] = useState<CandidateDecisionRecord | null>(null);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendationMessage, setRecommendationMessage] = useState('');
  const [profilePosts, setProfilePosts] = useState<Array<Record<string, unknown>>>([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [searchProgress, setSearchProgress] = useState<SearchProgressState>(idleSearchProgress);
  const [searchHistory, setSearchHistory] = useState<SearchHistoryItem[]>(loadSearchHistory);
  const [message, setMessage] = useState('');
  const [messageTone, setMessageTone] = useState<MessageTone>('info');
  const [contactModalOpen, setContactModalOpen] = useState(false);
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [contactType, setContactType] = useState('email');
  const [contactValue, setContactValue] = useState('');
  const [contactEvidence, setContactEvidence] = useState('');
  const [projectProductSku, setProjectProductSku] = useState('');
  const [projectNote, setProjectNote] = useState('');
  const [internalNote, setInternalNote] = useState('');
  const [candidateMaterializing, setCandidateMaterializing] = useState(false);
  const searchRunRef = useRef(0);

  const combinedKols = useMemo(() => {
    const map = new Map<string, UiKol>();
    [...searchKols, ...baseKols].forEach((kol) => {
      if (kol.id) map.set(kol.id, kol);
    });
    return Array.from(map.values());
  }, [baseKols, searchKols]);

  const searchOnly = Boolean(activeSearchQuery || searchProgress.visible);
  const displayedKols = useMemo(() => searchOnly ? searchKols : combinedKols, [combinedKols, searchKols, searchOnly]);
  const visibleKols = useMemo(() => filterKols(displayedKols, filters, localQuery), [displayedKols, filters, localQuery]);
  const selectedKol = useMemo(
    () => displayedKols.find((kol) => kol.id === selectedKolId) || visibleKols[0] || (searchOnly ? undefined : combinedKols[0]),
    [combinedKols, displayedKols, searchOnly, selectedKolId, visibleKols],
  );
  const selectedProjects = useMemo(() => profileProjects(selectedProfile, data, selectedKol), [data, selectedKol, selectedProfile]);
  const contacts = useMemo(() => selectedContacts.length ? selectedContacts : contactsFromProfile(selectedProfile, selectedKol), [selectedContacts, selectedProfile, selectedKol]);
  const dimensions = useMemo(() => dimensionsFromProfile(selectedProfile, selectedKol, selectedAssessment, selectedDimensions11), [selectedAssessment, selectedDimensions11, selectedProfile, selectedKol]);
  const posts = useMemo(() => recentPosts(selectedProfile, profilePosts, selectedKol), [profilePosts, selectedKol, selectedProfile]);
  const localCandidateRecommendations = useMemo(() => visibleKols.filter((kol) => kol.score >= 65 || kol.contactCount || kol.hasCollaboration).slice(0, 8), [visibleKols]);
  const selectedKolPoolId = useMemo(() => kolPoolIdForCompetitors(selectedKol), [selectedKol]);
  const selectedCandidateDecision = useMemo(() => {
    if (!candidateDecision || !selectedKol) return null;
    const selectedHandle = normalizeHandle(selectedKol.handle || selectedKol.name).toLowerCase();
    const decisionHandle = normalizeHandle(candidateDecision.handle).toLowerCase();
    const selectedPoolId = kolPoolIdForCompetitors(selectedKol);
    const matchesMainId = Boolean(candidateDecision.kolId && candidateDecision.kolId === selectedKol.id);
    const matchesPoolId = Boolean(candidateDecision.kolPoolId && selectedPoolId && candidateDecision.kolPoolId === selectedPoolId);
    const matchesHandle = Boolean(decisionHandle && selectedHandle && decisionHandle === selectedHandle);
    return matchesMainId || matchesPoolId || matchesHandle ? candidateDecision : null;
  }, [candidateDecision, selectedKol]);
  const { discoveryQueueItems, discoveryQueueLoading, discoveryQueueMessage } = useDiscoveryQueueSources(apiToken, data.projects);

  useEffect(() => {
    const focus = readDiscoverFocus();
    const decision = readLatestCandidateDecision();
    if (focus) {
      clearDiscoverFocus();
      setDiscoverFocus(focus);
      setQuery(focus.query);
      setPlatform(focus.platform || 'all');
      setFilters((previous) => ({ ...previous, platform: focus.platform || 'all' }));
      setActiveTab('search');
      setNotice(`已带入发现任务：${focus.title}`);
    }
    if (decision) {
      clearLatestCandidateDecision();
      setCandidateDecision(decision);
      if (decision.status === 'accepted') {
        setProjectProductSku(decision.productSku || '');
        setProjectNote(decision.projectNote);
      }
      if (!focus) {
        setQuery(decision.query);
        setPlatform(decision.platform || 'all');
        setNotice(`已从智能中心带回复核结果：${decision.nextActionLabel}`);
      }
    }
  }, []);

  useEffect(() => {
    if (!selectedKolId && visibleKols[0]?.id) setSelectedKolId(visibleKols[0].id);
  }, [selectedKolId, visibleKols]);

  useEffect(() => {
    if (!selectedKol?.id || !apiToken) {
      setSelectedProfile(null);
      setSelectedAssessment(null);
      setSelectedProductFits([]);
      setSelectedContacts([]);
      setProfilePosts([]);
      return;
    }
    if (isPlatformSearchCandidate(selectedKol)) {
      setSelectedProfile(null);
      setSelectedAssessment(null);
      setSelectedContacts([]);
      setProfilePosts([]);
      setProfileLoading(false);
      if (selectedKolPoolId) {
        let cancelled = false;
        void getKolProductFit(apiToken, selectedKolPoolId, 5)
          .then((result) => {
            if (!cancelled) setSelectedProductFits(result.items || []);
          })
          .catch(() => {
            if (!cancelled) setSelectedProductFits([]);
          });
        return () => {
          cancelled = true;
        };
      }
      setSelectedProductFits([]);
      return;
    }
    let cancelled = false;
    setProfileLoading(true);
    void Promise.allSettled([
      getKolProfile(apiToken, selectedKol.id),
      getKolPosts(apiToken, selectedKol.id, { limit: 50 }),
      getKolAssessment(apiToken, selectedKol.id),
      getKolProductFit(apiToken, selectedKol.id, 5),
      listKolContacts(apiToken, selectedKol.id),
    ]).then(([profileResult, postsResult, assessmentResult, productFitResult, contactsResult]) => {
      if (cancelled) return;
      if (profileResult.status === 'fulfilled') setSelectedProfile(profileResult.value);
      else setSelectedProfile(null);
      if (postsResult.status === 'fulfilled') setProfilePosts(postsResult.value.items || []);
      else setProfilePosts([]);
      if (assessmentResult.status === 'fulfilled') setSelectedAssessment(assessmentResult.value);
      else setSelectedAssessment(null);
      if (productFitResult.status === 'fulfilled') setSelectedProductFits(productFitResult.value.items || []);
      else setSelectedProductFits([]);
      if (contactsResult.status === 'fulfilled') setSelectedContacts(apiContactsToItems(contactsResult.value.contacts || []));
      else setSelectedContacts([]);
    }).finally(() => {
      if (!cancelled) setProfileLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedKol?.id, selectedKolPoolId]);

  useEffect(() => {
    if (!apiToken || !selectedKolPoolId) {
      setSelectedCompetitors([]);
      setCompetitorLoading(false);
      setSelectedDimensions11(null);
      setDimensions11Loading(false);
      return;
    }
    let cancelled = false;
    setCompetitorLoading(true);
    setDimensions11Loading(true);
    void getKolPoolCompetitors(apiToken, selectedKolPoolId)
      .then((result) => {
        if (cancelled) return;
        setSelectedCompetitors(arrayValue(result.relations).map(objectValue));
      })
      .catch(() => {
        if (!cancelled) setSelectedCompetitors([]);
      })
      .finally(() => {
        if (!cancelled) setCompetitorLoading(false);
      });
    void getKolPoolDimensions11(apiToken, selectedKolPoolId)
      .then((result) => {
        if (cancelled) return;
        setSelectedDimensions11(objectValue(result));
      })
      .catch(() => {
        if (!cancelled) setSelectedDimensions11(null);
      })
      .finally(() => {
        if (!cancelled) setDimensions11Loading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedKolPoolId]);

  const setNotice = (text: string, tone: MessageTone = 'info') => {
    setMessage(text);
    setMessageTone(tone);
  };

  const buildSearchContext = (): DiscoverSearchContext => ({
    apiToken,
    query,
    platform,
    filters,
    createIfMissing,
    scanAccount,
    searchRunRef,
    onLookupKol,
    onScanKolAccount,
    setActiveTab,
    setQuery,
    setPlatform,
    setActiveSearchQuery,
    setLocalQuery,
    setSearchKols,
    setSelectedKolId,
    setLookupResult,
    setSearchProgress,
    setSearchHistory,
    setBusy,
    setScanBusy,
    setNotice,
  });

  const loadKols = (searchText: string, platformFilter = filters.platform): Promise<number> => (
    loadDiscoverKols(buildSearchContext(), searchText, platformFilter)
  );

  const loadSmartRecommendations = async (silent = false) => {
    if (!apiToken) {
      setSmartRecommendations([]);
      setRecommendationMessage('当前未登录，无法读取 Product Analysis 推荐表。');
      return;
    }
    setRecommendationLoading(true);
    if (!silent) setRecommendationMessage('');
    try {
      const result = await listProductRecommendations(apiToken, { limit: 80 });
      const rows = (result.recommendations || []).map(recommendationToSmart);
      setSmartRecommendations(rows);
      setRecommendationMessage(rows.length ? `已读取 ${rows.length} 条真实推荐。` : '推荐表暂无记录，可用“生成推荐”从 KOL Pool 跑一次规则推荐。');
    } catch (error) {
      setRecommendationMessage(error instanceof Error ? error.message : '推荐列表读取失败');
    } finally {
      setRecommendationLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'recommendations') void loadSmartRecommendations(true);
  }, [activeTab, apiToken]);

  const regenerateSmartRecommendations = async () => {
    if (!apiToken) {
      setRecommendationMessage('当前未登录，无法生成推荐。');
      return;
    }
    setRecommendationLoading(true);
    setRecommendationMessage('正在调用 Product Analysis 规则推荐器。');
    try {
      const result = await runProductRecommendations(apiToken, { limit: 80 });
      const generated = arrayValue(result.recommendations).length;
      await loadSmartRecommendations(true);
      setRecommendationMessage(generated ? `已生成 ${generated} 条真实推荐。` : '推荐器已运行，但 KOL Pool 暂无可推荐候选。');
    } catch (error) {
      setRecommendationMessage(error instanceof Error ? error.message : '推荐生成失败');
    } finally {
      setRecommendationLoading(false);
    }
  };

  const handleRecommendationAction = async (recommendation: SmartRecommendation, action: RecommendationAction) => {
    if (!apiToken) return;
    setRecommendationLoading(true);
    try {
      const result = await productRecommendationAction(apiToken, recommendation.id, action, { source: 'discover_page' });
      const kol = objectValue(result.kol);
      if (kol.id) {
        const next = rawToUiKol(kol);
        setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
        setSelectedKolId(next.id);
      } else if (recommendation.kolId) {
        const next = recommendationToUiKol(recommendation);
        setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
        setSelectedKolId(next.id);
      }
      await loadSmartRecommendations(true);
      setNotice(action === 'reject' ? '已记录推荐拒绝。' : action === 'claim' ? '已通过推荐 action 建档/认领。' : '已将推荐标记为入选。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '推荐 action 失败', 'error');
    } finally {
      setRecommendationLoading(false);
    }
  };

  const runSearch = (termInput = query, platformInput = platform): Promise<void> => (
    runDiscoverSearch(buildSearchContext(), termInput, platformInput)
  );

  const handleSearch = async (event?: React.FormEvent) => {
    event?.preventDefault();
    await runSearch();
  };

  const rerunHistorySearch = (item: SearchHistoryItem) => {
    setActiveTab('search');
    void runSearch(item.query, item.platform);
  };

  const handleClaim = async () => {
    if (!selectedKol?.id || !onClaimKol) return;
    setBusy(true);
    try {
      await onClaimKol(selectedKol.id);
      setNotice('红人已绑定到当前成员账号。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '认领失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleDeepScan = async () => {
    if (!selectedKol?.id || !onScanKolAccount) return;
    setScanBusy(true);
    setNotice('正在运行真实深度抓取；UI 进度不伪造，完成后刷新画像。');
    try {
      await onScanKolAccount(selectedKol.id, 50);
      if (apiToken) {
        const [profileResult, assessmentResult, productFitResult, contactsResult] = await Promise.allSettled([
          getKolProfile(apiToken, selectedKol.id),
          getKolAssessment(apiToken, selectedKol.id),
          getKolProductFit(apiToken, selectedKol.id, 5),
          listKolContacts(apiToken, selectedKol.id),
        ]);
        if (profileResult.status === 'fulfilled') setSelectedProfile(profileResult.value);
        if (assessmentResult.status === 'fulfilled') setSelectedAssessment(assessmentResult.value);
        if (productFitResult.status === 'fulfilled') setSelectedProductFits(productFitResult.value.items || []);
        if (contactsResult.status === 'fulfilled') setSelectedContacts(apiContactsToItems(contactsResult.value.contacts || []));
      }
      setNotice('深度抓取完成。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '深度抓取失败', 'error');
    } finally {
      setScanBusy(false);
    }
  };

  const saveManualContact = async () => {
    if (!selectedKol?.id || !contactValue.trim()) return;
    const nextLink = { label: contactType, value: contactValue.trim(), url: contactValue.trim().startsWith('http') ? contactValue.trim() : undefined };
    setBusy(true);
    try {
      if (apiToken) {
        const result = await addKolContact(apiToken, selectedKol.id, {
          contactType,
          contactValue: contactValue.trim(),
          evidence: contactEvidence.trim() || undefined,
          layer: 5,
          source: 'manual_input',
        });
        setSelectedContacts(apiContactsToItems(result.contacts || []));
      } else if (onUpdateKol) {
        await onUpdateKol(selectedKol.id, {
          contactEmail: contactType === 'email' ? contactValue.trim() : undefined,
          contactPhone: contactType === 'phone' || contactType === 'whatsapp' ? contactValue.trim() : undefined,
          contactLinks: [nextLink],
          notes: contactEvidence.trim() || undefined,
        });
      } else {
        return;
      }
      setContactModalOpen(false);
      setContactValue('');
      setContactEvidence('');
      setNotice('联系方式已通过真实补录接口保存；当前使用现有 KOL 字段桥接 5 层漏斗。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '联系方式保存失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const createProjectForKol = async () => {
    if (!selectedKol?.id || !onCreateProject) return;
    const decision = selectedCandidateDecision?.status === 'accepted' ? selectedCandidateDecision : null;
    const productSku = projectProductSku.trim() || decision?.productSku || '';
    const productName = decision?.productName || undefined;
    const projectName = `${selectedKol.handle} · ${productSku || productName || 'KOL'} 合作`;
    setBusy(true);
    try {
      const result = await onCreateProject({
        projectName,
        kolId: selectedKol.id,
        productSku: productSku || undefined,
        productName,
        platform: platformInputValue(selectedKol.platform),
        note: projectNote.trim() || decision?.projectNote || '从红人决策中枢加入项目',
      });
      const created = objectValue(result || {});
      writeProjectFocus({
        source: decision ? 'discover_candidate_decision' : 'manual',
        projectId: textValue(created.id || created.project_id, '') || undefined,
        projectUid: textValue(created.project_uid || created.projectUid, '') || undefined,
        projectName,
        kolId: selectedKol.id,
        kolHandle: selectedKol.handle,
        productSku: productSku || undefined,
        productName,
        summary: decision
          ? '从智能中心接受候选后创建项目；继续推进阶段、费用、物流和证据。'
          : '从红人搜索创建项目；继续推进阶段、费用、物流和证据。',
      });
      setProjectModalOpen(false);
      setProjectProductSku('');
      setProjectNote('');
      if (decision) setCandidateDecision(null);
      setNotice('已通过真实项目接口创建 KOL 项目。');
      onSelectPage?.('projects');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '加入项目失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const sendSelectedToIntelligence = () => {
    if (!selectedKol) return;
    const reviewCard = buildDiscoverReviewCard({
      kol: selectedKol,
      assessment: selectedAssessment,
      productFits: selectedProductFits,
      competitorRelations: selectedCompetitors,
      contacts,
      posts,
      sourceQuery: activeSearchQuery || query || localQuery || selectedKol.handle,
    });
    writeIntelligenceReviewCard(reviewCard);
    setNotice('已生成本地候选复核卡，正在进入智能中心。');
    onSelectPage?.('intelligenceCenter');
  };

  const prepareProjectFromDecision = (decision: CandidateDecisionRecord) => {
    if (decision.status !== 'accepted') {
      setNotice('只有已接受的候选会进入项目创建。', 'warn');
      return;
    }
    if (!selectedCandidateDecision) {
      setNotice('请先回搜候选并选中对应 KOL，再创建项目。', 'warn');
      return;
    }
    setProjectProductSku(decision.productSku || projectProductSku);
    setProjectNote(decision.projectNote || projectNote);
    setProjectModalOpen(true);
  };

  const materializeAcceptedCandidate = async (decision: CandidateDecisionRecord) => {
    if (decision.status !== 'accepted') {
      setNotice('只有已接受的候选需要建档或转主 KOL。', 'warn');
      return;
    }
    if (!selectedKol || !selectedCandidateDecision) {
      setNotice('请先回搜候选并选中对应 KOL。', 'warn');
      return;
    }
    if (!isCandidateKol(selectedKol)) {
      prepareProjectFromDecision(decision);
      return;
    }

    setCandidateMaterializing(true);
    setBusy(true);
    try {
      const poolId = selectedKolPoolId || decision.kolPoolId || '';
      if (poolId && apiToken) {
        const promoted = await promoteKolPoolToMain(apiToken, Number(poolId));
        const nextKol = promotedResultToUiKol(promoted as Record<string, unknown>, selectedKol);
        if (!nextKol?.id) throw new Error('KOL Pool 已处理，但没有返回可建项目的主 KOL。');
        setSearchKols((previous) => [nextKol, ...previous.filter((item) => item.id !== nextKol.id && item.id !== selectedKol.id)]);
        setSelectedKolId(nextKol.id);
        setProjectProductSku(decision.productSku || projectProductSku);
        setProjectNote(decision.projectNote || projectNote);
        setProjectModalOpen(Boolean(onCreateProject));
        setNotice(`已将历史候选转为主 KOL（${promoted.mode || 'promoted'}），可以继续创建项目。`);
        return;
      }

      if (!onLookupKol) {
        setNotice('当前没有建档接口；请先用搜索/抓取账号把候选转成真实 KOL。', 'warn');
        return;
      }
      const lookup = await onLookupKol({
        platform: platformInputValue(selectedKol.platform),
        handleOrUrl: selectedKol.profileUrl || selectedKol.handle,
        createIfMissing: true,
        scanAccount: false,
        maxPosts: 1,
        productSku: decision.productSku,
      });
      const nextKol = lookupToUiKol(lookup);
      if (!nextKol?.id) throw new Error('建档接口没有返回可建项目的 KOL。');
      setSearchKols((previous) => [nextKol, ...previous.filter((item) => item.id !== nextKol.id && item.id !== selectedKol.id)]);
      setSelectedKolId(nextKol.id);
      setProjectProductSku(decision.productSku || projectProductSku);
      setProjectNote(decision.projectNote || projectNote);
      setProjectModalOpen(Boolean(onCreateProject));
      setNotice('已通过查重/建档接口把平台候选转成真实 KOL，可以继续创建项目。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '候选转正失败', 'error');
    } finally {
      setCandidateMaterializing(false);
      setBusy(false);
    }
  };

  const heroMetrics = [
    { label: 'KOL 池', value: compactCount(combinedKols.length), hint: '当前可见真实档案' },
    { label: '发现队列', value: discoveryQueueLoading ? '...' : compactCount(discoveryQueueItems.length), hint: '推荐 / 项目 / 信号' },
    { label: '今日推荐', value: smartRecommendations.length ? String(smartRecommendations.length) : '-', hint: 'Product Analysis 返回' },
    { label: '已分析内容', value: compactCount(posts.length || selectedKol?.contentCount || 0), hint: 'profile/posts 返回' },
    { label: '联系方式', value: compactCount(contacts.length), hint: 'Layer 1 真实字段' },
  ];
  const directionChips = useMemo(() => buildDirectionChips(combinedKols, data.productLaunches), [combinedKols, data.productLaunches]);

  return (
    <DiscoverPageLayout
      heroMetrics={heroMetrics}
      message={message}
      messageTone={messageTone}
      discoverFocus={discoverFocus}
      candidateDecision={candidateDecision}
      selectedCandidateDecision={selectedCandidateDecision}
      candidateMaterializing={candidateMaterializing}
      selectedKol={selectedKol}
      selectedKolPoolId={selectedKolPoolId}
      onCreateProject={onCreateProject}
      onLookupKol={onLookupKol}
      apiToken={apiToken}
      canOpenProjectFromDecision={Boolean(candidateDecision?.status === 'accepted' && selectedCandidateDecision && onCreateProject && selectedKol && !isCandidateKol(selectedKol))}
      canPrepareCandidate={Boolean(candidateDecision?.status === 'accepted' && selectedCandidateDecision && selectedKol && isCandidateKol(selectedKol) && ((selectedKolPoolId || candidateDecision?.kolPoolId) ? apiToken : onLookupKol))}
      directionChips={directionChips}
      discoveryQueueItems={discoveryQueueItems}
      discoveryQueueLoading={discoveryQueueLoading}
      discoveryQueueMessage={discoveryQueueMessage}
      activeTab={activeTab}
      setActiveTab={setActiveTab}
      query={query}
      setQuery={setQuery}
      platform={platform}
      setPlatform={setPlatform}
      createIfMissing={createIfMissing}
      setCreateIfMissing={setCreateIfMissing}
      scanAccount={scanAccount}
      setScanAccount={setScanAccount}
      busy={busy}
      scanBusy={scanBusy}
      filters={filters}
      setFilters={setFilters}
      localQuery={localQuery}
      setLocalQuery={setLocalQuery}
      visibleKols={visibleKols}
      searchProgress={searchProgress}
      searchHistory={searchHistory}
      smartRecommendations={smartRecommendations}
      recommendationLoading={recommendationLoading}
      recommendationMessage={recommendationMessage}
      localFallbackCount={localCandidateRecommendations.length}
      selectedProfile={selectedProfile}
      selectedAssessment={selectedAssessment}
      selectedDimensions11={selectedDimensions11}
      lookupResult={lookupResult}
      contacts={contacts}
      dimensions={dimensions}
      competitorRelations={selectedCompetitors}
      competitorLoading={competitorLoading}
      dimensions11Loading={dimensions11Loading}
      productFits={selectedProductFits}
      posts={posts}
      projects={selectedProjects}
      profileLoading={profileLoading}
      internalNote={internalNote}
      setInternalNote={setInternalNote}
      canClaim={Boolean(onClaimKol && selectedKol && !selectedKol.claimOwner && !isCandidateKol(selectedKol))}
      canScan={Boolean(onScanKolAccount && selectedKol && !isCandidateKol(selectedKol))}
      canUpdate={Boolean((apiToken || onUpdateKol) && selectedKol && !isCandidateKol(selectedKol))}
      canCreateProject={Boolean(onCreateProject && selectedKol && !isCandidateKol(selectedKol))}
      contactModalOpen={contactModalOpen}
      contactType={contactType}
      setContactType={setContactType}
      contactValue={contactValue}
      setContactValue={setContactValue}
      contactEvidence={contactEvidence}
      setContactEvidence={setContactEvidence}
      projectModalOpen={projectModalOpen}
      projectProductSku={projectProductSku}
      setProjectProductSku={setProjectProductSku}
      projectNote={projectNote}
      setProjectNote={setProjectNote}
      onDismissDiscoverFocus={() => setDiscoverFocus(null)}
      onRunDiscoverFocus={(focus) => { setDiscoverFocus(null); void runSearch(focus.query, focus.platform || 'all'); }}
      onDismissCandidateDecision={() => setCandidateDecision(null)}
      onPrepareCandidate={(decision) => void materializeAcceptedCandidate(decision)}
      onOpenProjectFromDecision={prepareProjectFromDecision}
      onRunDecision={(decision) => void runSearch(decision.query, decision.platform || 'all')}
      runSearch={(nextQuery, nextPlatform) => void runSearch(nextQuery, nextPlatform)}
      setNotice={setNotice}
      onRefreshKols={() => { setActiveSearchQuery(''); setSearchProgress(idleSearchProgress); void loadKols('', filters.platform); }}
      handleSearch={handleSearch}
      onSelectKol={setSelectedKolId}
      onHistorySelect={rerunHistorySearch}
      onSelectRecommendation={(recommendation) => {
        if (!recommendation.kolId) {
          setNotice('这条推荐还没有 linked_main_kol_id；请先点“认领”把它接入主 KOL 表。', 'warn');
          return;
        }
        const next = recommendationToUiKol(recommendation);
        setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
        setSelectedKolId(next.id);
        setActiveTab('search');
      }}
      onRefreshRecommendations={() => void loadSmartRecommendations()}
      onRegenerateRecommendations={() => void regenerateSmartRecommendations()}
      onRecommendationAction={(recommendation, action) => void handleRecommendationAction(recommendation, action)}
      onClaim={handleClaim}
      onDeepScan={handleDeepScan}
      onSendToIntelligence={sendSelectedToIntelligence}
      onOpenContact={() => setContactModalOpen(true)}
      onOpenProject={() => setProjectModalOpen(true)}
      onCloseContact={() => setContactModalOpen(false)}
      saveManualContact={saveManualContact}
      onCloseProject={() => setProjectModalOpen(false)}
      createProjectForKol={createProjectForKol}
    />
  );
}
