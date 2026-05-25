import { Avatar } from '../../shared/Avatar';
import { platformLabels, stageLabels } from '../../shared/vkpiConstants';
import { arrayValue, compactCount, objectValue, safeNumber, textValue } from '../../shared/vkpiDataUtils';
import type { VkpiDashboardData, VkpiKolLookupResult, VkpiKolProfile, VkpiPlatform, VkpiProjectRow } from '../../vkpiTypes';
import type { VkpiKolAssessmentResponse, VkpiKolProductFitResponse } from '../../../../services/vkpi/kol-api';

interface UiKol {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  followerLabel: string;
  contentCountLabel: string;
  score: number;
  grade: string;
  country: string;
  topic: string;
  riskLabel: string;
  sourceKind?: 'kol' | 'platform_search' | 'kol_pool';
  raw: Record<string, unknown>;
}

interface ContactItem {
  id: string;
  type: string;
  value: string;
  layer: number;
  source: string;
  confidence?: number;
  evidence?: string;
  verified?: boolean;
}

type ProductFitItem = NonNullable<VkpiKolProductFitResponse['items']>[number];
type CompetitorRelation = Record<string, unknown>;
type Dimensions11Payload = Record<string, unknown>;

interface DimensionBar {
  key: string;
  label: string;
  source: string;
  value: number;
  pending: boolean;
}

function competitorTierLabel(value: unknown): string {
  const tier = textValue(value, 'opportunity');
  if (tier === 'avoid') return '高风险';
  if (tier === 'caution') return '谨慎';
  if (tier === 'safe') return '可合作';
  return '机会';
}

function competitorTone(value: unknown): string {
  const tier = textValue(value, 'opportunity');
  if (tier === 'avoid') return 'is-risk-avoid';
  if (tier === 'caution') return 'is-risk-caution';
  if (tier === 'safe') return 'is-risk-safe';
  return 'is-risk-opportunity';
}

function visibleCompetitorRelations(relations: CompetitorRelation[]): CompetitorRelation[] {
  return relations
    .filter((relation) => safeNumber(relation.risk_score) > 0 || textValue(relation.collaboration_depth, 'none') !== 'none')
    .sort((a, b) => safeNumber(b.risk_score) - safeNumber(a.risk_score))
    .slice(0, 4);
}

function compactLabel(value: unknown, fallback = '-'): string {
  const parsed = safeNumber(value);
  return parsed ? compactCount(parsed) : fallback;
}

function productFitMetaLine(product: Record<string, unknown>): string {
  const catalog = objectValue(product.catalog_product || product.matched_catalog_product);
  const specs = objectValue(product.specs || catalog.specs);
  const mount = textValue(product.mount || catalog.mount, '');
  const price = safeNumber(product.price_usd || catalog.price_usd);
  const focal = textValue(specs.focal_length, '');
  const aperture = textValue(specs.aperture, '');
  return [mount, price ? `$${price.toLocaleString('en-US')}` : '', focal, aperture].filter(Boolean).join(' · ');
}

function candidatePostsFromRaw(raw: Record<string, unknown>): Array<Record<string, unknown>> {
  const rows = [
    ...arrayValue(raw.posts),
    ...arrayValue(raw.items),
    ...arrayValue(raw.videos),
    ...arrayValue(raw.latest_posts),
    ...arrayValue(raw.latestPosts),
  ].map(objectValue);
  const sampleTitle = textValue(raw.sample_title || raw.title || raw.caption || raw.text, '');
  const sampleUrl = textValue(raw.source_url || raw.post_url || raw.url || raw.content_url, '');
  const sampleViews = safeNumber(raw.views || raw.avg_views || raw.view_count || raw.play_count);
  if (sampleTitle || sampleUrl) {
    rows.unshift({
      id: textValue(raw.source_url || raw.url || raw.candidate_id || raw.search_query, 'platform-sample'),
      title: sampleTitle || '平台搜索样本内容',
      post_url: sampleUrl,
      url: sampleUrl,
      views: sampleViews,
      likes: safeNumber(raw.likes || raw.like_count),
      comments: safeNumber(raw.comments || raw.comment_count),
      published: textValue(raw.published || raw.searched_at, ''),
      source_kind: 'platform_search_sample',
    });
  }
  const seen = new Set<string>();
  return rows
    .map((row, index) => {
      const url = textValue(row.source_url || row.post_url || row.url || row.content_url || row.permalink, '');
      const title = textValue(row.sample_title || row.title || row.caption || row.text || url, '');
      return {
        ...row,
        id: textValue(row.id || row.post_uid || row.shortCode || row.shortcode || url || title, `candidate-post-${index}`),
        title,
        post_url: url,
        url,
        views: safeNumber(row.views || row.avg_views || row.view_count || row.play_count),
      };
    })
    .filter((row) => {
      const key = textValue(row.post_url || row.url || row.title || row.id, '');
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 6);
}

function isPlatformSearchCandidate(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || String(kol?.id || '').startsWith('candidate:'));
}

function formatAssessmentMethod(method?: string) {
  const clean = String(method || '').trim();
  if (!clean) return '等待深度评估';
  if (clean.includes('local_assessment')) return '本地旧评估；建议刷新';
  return clean.replace(/_/g, ' ');
}

function formatProductFitSource(hasFits: boolean, method?: string) {
  if (!hasFits) return '等待产品适配';
  if (String(method || '').includes('local_product_fit')) return '本地规则估算';
  if (String(method || '').includes('rule_dimensions_11')) return '11维规则产品适配';
  return String(method || '产品适配').replace(/_/g, ' ');
}

function cleanProductLabel(value: unknown) {
  let label = textValue(value, '未命名产品');
  label = label.replace(/^viltrox\s+/i, '').replace(/\s+/g, ' ').trim();
  label = label.replace(/\s+(FE|E|Z|X|L|RF)$/i, ' ($1)');
  return label;
}

function searchIntentTags(query: unknown): Array<{ label: string; detail: string }> {
  const raw = textValue(query, '').trim();
  if (!raw) return [];
  const lower = raw.toLowerCase();
  const tags: Array<{ label: string; detail: string }> = [];
  const add = (label: string, detail: string) => {
    if (!tags.some((item) => item.label === label)) tags.push({ label, detail });
  };
  if (/35\s*mm|35mm|evo|f1[.\s]?8|1[.\s]?8/.test(lower)) add('35mm EVO / F1.8', '镜头样片、街拍、人像、测评优先');
  if (/街拍|street/.test(raw) || lower.includes('street')) add('街拍内容', '看真实外拍频率和画面风格');
  if (/测评|评测|review|test/.test(lower)) add('测评账号', '优先看近期评测样片和器材可信度');
  if (/youtube|视频|video|拍摄|creator/.test(lower)) add('视频创作者', '重点看近期内容、播放和频道匹配');
  if (/人像|portrait/.test(lower)) add('人像方向', '检查肤色、人像样片和镜头表达');
  if (/旅行|travel|vlog/.test(lower)) add('旅行 / Vlog', '检查轻量化设备和连续更新能力');
  if (!tags.length) add('搜索关键词', raw.length > 28 ? `${raw.slice(0, 28)}...` : raw);
  return tags.slice(0, 5);
}

function confidenceValue(block: Record<string, unknown>, key: string, fallback = 0): number {
  const confidence = objectValue(block.confidence);
  const parsed = safeNumber(confidence[key]);
  if (parsed) return Math.max(0, Math.min(1, parsed));
  return Math.max(0, Math.min(1, fallback));
}

function productFitsFromDimensions11(payload: Dimensions11Payload | null): ProductFitItem[] {
  if (!payload) return [];
  const block4 = objectValue(payload.block4_specialty);
  const productFit = objectValue(block4.product_fit);
  const productFitConfidence = objectValue(block4.product_fit_confidence);
  const clusters = arrayValue(block4.industry_cluster).map((item) => textValue(item, '')).filter(Boolean);
  return Object.entries(productFit)
    .filter(([sku]) => confidenceValue({ confidence: productFitConfidence }, sku) >= 0.35)
    .map(([sku, score]) => ({
      product_sku: sku,
      product_name: cleanProductLabel(sku),
      score: safeNumber(score),
      method: textValue(payload.method, 'rule_dimensions_11_v0'),
      reasons: clusters.length ? [`行业 ${clusters.slice(0, 3).join(' / ')}`] : ['来自 11 维规则画像'],
      evidence: ['vkpi_kol_pool cached profile/posts'],
    }))
    .filter((item) => item.score)
    .sort((a, b) => safeNumber(b.score) - safeNumber(a.score))
    .slice(0, 5);
}

export function ProfilePanel({
  selectedKol,
  selectedProfile,
  selectedAssessment,
  selectedDimensions11,
  lookupResult,
  contacts,
  dimensions,
  competitorRelations,
  competitorLoading,
  dimensions11Loading,
  productFits,
  posts,
  projects,
  profileLoading,
  scanBusy,
  busy,
  internalNote,
  setInternalNote,
  onClaim,
  onDeepScan,
  onSendToIntelligence,
  onOpenContact,
  onOpenProject,
  canClaim,
  canScan,
  canSendToIntelligence,
  canUpdate,
  canCreateProject,
}: {
  selectedKol?: UiKol;
  selectedProfile: VkpiKolProfile | null;
  selectedAssessment: VkpiKolAssessmentResponse | null;
  selectedDimensions11: Dimensions11Payload | null;
  lookupResult: VkpiKolLookupResult | null;
  contacts: ContactItem[];
  dimensions: DimensionBar[];
  competitorRelations: CompetitorRelation[];
  competitorLoading: boolean;
  dimensions11Loading: boolean;
  productFits: ProductFitItem[];
  posts: Array<Record<string, unknown>>;
  projects: VkpiProjectRow[];
  profileLoading: boolean;
  scanBusy: boolean;
  busy: boolean;
  internalNote: string;
  setInternalNote: (value: string) => void;
  onClaim: () => Promise<void>;
  onDeepScan: () => Promise<void>;
  onSendToIntelligence: () => void;
  onOpenContact: () => void;
  onOpenProject: () => void;
  canClaim: boolean;
  canScan: boolean;
  canSendToIntelligence: boolean;
  canUpdate: boolean;
  canCreateProject: boolean;
}) {
  if (!selectedKol) return <div className="vkpi-discover-empty">选择一个 KOL 查看右侧完整画像。</div>;
  const summary = selectedProfile?.summary || {};
  const assessmentScore = safeNumber(selectedAssessment?.score) || selectedKol.score;
  const assessmentGrade = textValue(selectedAssessment?.grade, selectedKol.grade);
  const candidateOnly = isPlatformSearchCandidate(selectedKol);
  const historicalMatch = objectValue(selectedKol.raw.historical_match || selectedKol.raw.history_match);
  const historicalCooperationCount = safeNumber(historicalMatch.cooperation_count || selectedKol.raw.cooperation_count || selectedKol.raw.history_cooperation_count);
  const historicalRecentCooperations = arrayValue(historicalMatch.recent_cooperations).map(objectValue);
  const candidateIntentTags = searchIntentTags(selectedKol.raw.search_query || selectedKol.topic || selectedKol.raw.sample_title);
  const candidateFocus = candidateIntentTags.length ? candidateIntentTags.map((item) => item.label).join(' / ') : '近期内容';
  const candidateSampleCount = candidatePostsFromRaw(selectedKol.raw).length;
  const dimensions11ProductFits = productFitsFromDimensions11(selectedDimensions11);
  const productRows: Array<Record<string, unknown>> = productFits.length ? productFits as unknown as Array<Record<string, unknown>> : dimensions11ProductFits as unknown as Array<Record<string, unknown>>;
  const productFitMethod = textValue(productRows[0]?.method, '');
  const productFitScore = safeNumber(productRows[0]?.score) || safeNumber(summary.product_fit || selectedKol.raw.product_fit);
  const decision = candidateOnly
    ? (historicalCooperationCount
      ? `历史合作命中 ${historicalCooperationCount} 条；优先核对最近内容、负责人和产品线后复用。`
      : candidateSampleCount
      ? `可观察；平台已返回样本内容，先围绕 ${candidateFocus} 建档抓取。`
      : `待抓取；这是平台候选，建议先抓取账号再评估 ${candidateFocus}。`)
    : textValue(selectedAssessment?.recommended_action || summary.recommended_action, assessmentScore >= 80 ? '优先合作；建议补齐产品适配和联系方式证据。' : assessmentScore ? '可观察；先补齐近期内容与联系方式。' : '待评估；请先运行真实抓取。');
  const profileStatus = historicalCooperationCount ? `历史合作 ${historicalCooperationCount} 条` : candidateOnly ? '候选 / 未深扫 / 可分析' : profileLoading ? '画像加载中' : selectedProfile ? '真实 profile 已加载' : lookupResult ? '查重结果已加载' : '列表档案';
  const hasDimensions11 = Boolean(selectedDimensions11 && textValue(selectedDimensions11.method, ''));
  const overallDimensionsScore = safeNumber(selectedDimensions11?.overall_score);
  const competitorRows = visibleCompetitorRelations(competitorRelations);
  return (
    <div className="vkpi-discover-profile__stack">
      <section className="vkpi-discover-profile-head">
        <Avatar name={selectedKol.name || selectedKol.handle} src={selectedKol.avatar} size="lg" />
        <div>
          <strong>{selectedKol.handle}</strong>
          <span>{platformLabels[selectedKol.platform] || selectedKol.platform} · {selectedKol.followerLabel} 粉 · {selectedKol.country}</span>
          <div>
            <em>{profileStatus}</em>
            <em>内容 {selectedKol.contentCountLabel}</em>
          </div>
        </div>
        <div className="vkpi-discover-profile-score">
          <b>{assessmentScore || '-'}</b>
          <span className={`is-${assessmentGrade}`}>{assessmentGrade}</span>
        </div>
      </section>

      <section className="vkpi-discover-card is-decision">
        <b>建议：{decision}</b>
        <span>产品适配 {productFitScore ? `${productFitScore}/100` : '待接 Product Fit'} · 风险 {textValue(summary.risk_level || selectedKol.riskLabel, '暂无高风险')}</span>
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>竞品关系</b>
          <span>{competitorLoading ? '读取历史池' : competitorRows.length ? `${competitorRows.length} 条命中` : '历史池规则检测'}</span>
        </div>
        {competitorLoading ? (
          <>
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          </>
        ) : competitorRows.length ? competitorRows.map((relation) => (
          <div className={`vkpi-discover-fit is-competitor-risk ${competitorTone(relation.risk_tier)}`} key={`${relation.competitor_brand}-${relation.risk_score}`}>
            <div>
              <strong>{textValue(relation.competitor_brand, 'competitor').toUpperCase()} · {competitorTierLabel(relation.risk_tier)}</strong>
              <span>
                {textValue(relation.collaboration_depth, 'mentioned')} · 90 天 {safeNumber(relation.collaboration_count_90d)} 条 ·
                历史 {safeNumber(relation.collaboration_count_total)} 条
              </span>
            </div>
            <b>{safeNumber(relation.risk_score).toFixed(1)}</b>
          </div>
        )) : (
          <div className="vkpi-discover-empty is-compact">1012 历史池和已缓存资料暂无竞品证据；后续 deep scan 会补最近内容。</div>
        )}
      </section>

      {scanBusy ? (
        <section className="vkpi-discover-card">
          <b>真实抓取运行中</b>
          <span>后端接口同步返回前不伪造 6 步进度。完成后会刷新 profile / posts。</span>
          <div className="vkpi-discover-progress"><i /></div>
        </section>
      ) : null}

      {profileLoading ? (
        <section className="vkpi-discover-card vkpi-discover-profile-loading" aria-live="polite">
          <div className="vkpi-discover-card__title"><b>正在读取画像</b><span>资料 / 近期内容 / 历史合作</span></div>
          <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          <div className="vkpi-discover-mini-grid">
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
          </div>
        </section>
      ) : null}

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>{hasDimensions11 ? '11 维评估' : '8 维评估'}</b>
          <span>{dimensions11Loading ? '读取规则画像' : hasDimensions11 ? `规则画像 ${overallDimensionsScore || '-'} · ${formatAssessmentMethod(textValue(selectedDimensions11?.method, ''))}` : formatAssessmentMethod(selectedAssessment?.method)}</span>
        </div>
        <div className="vkpi-discover-bars">
          {dimensions.map((dimension) => (
            <div className={`vkpi-discover-bar ${dimension.pending ? 'is-pending' : ''}`} key={dimension.key}>
              <span>{dimension.label}</span>
              <div><i style={{ width: `${dimension.pending ? 48 : dimension.value}%` }} /></div>
              <b>{dimension.pending ? '待接' : dimension.value}</b>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>Top 5 产品适配</b><span>{formatProductFitSource(Boolean(productRows.length), productFitMethod)}</span></div>
        {productRows.length ? productRows.map((product, index) => {
          const metaLine = productFitMetaLine(product);
          return (
            <div className="vkpi-discover-fit" key={String(product.launch_id || product.id || product.product_sku || product.productSku || index)}>
              <div>
                <strong>{cleanProductLabel(product.product_name || product.productName || product.product_sku || product.productSku)}</strong>
                <span>{Array.isArray(product.reasons) && product.reasons.length ? textValue(product.reasons[0], '') : textValue(product.launch_name || product.launchName || product.category, 'Viltrox 产品')}</span>
                {metaLine ? <small>{metaLine}</small> : null}
              </div>
              <b>{safeNumber(product.score) || productFitScore || '待接'}</b>
            </div>
          );
        }) : candidateOnly && candidateIntentTags.length ? (
          <div className="vkpi-discover-fit is-query">
            <div>
              <strong>搜索方向</strong>
              <span>{candidateIntentTags.map((item) => `${item.label}：${item.detail}`).join(' / ')}</span>
            </div>
            <b>待抓取</b>
          </div>
        ) : <div className="vkpi-discover-empty is-compact">暂无真实产品适配。先建档并运行深度评估，再选择具体产品方向。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>5 层联系方式</b>
          <button type="button" onClick={onOpenContact} disabled={!canUpdate}>+ 手动添加</button>
        </div>
        {contacts.length ? contacts.map((contact) => (
          <div className="vkpi-discover-contact" key={contact.id}>
            <div>
              <span>Layer {contact.layer}</span>
              <b>{contact.type}</b>
              <em>{contact.verified ? '已验证' : contact.confidence ? `置信 ${contact.confidence}%` : '待验证'}</em>
              <strong>{contact.value}</strong>
              <small>{contact.source}{contact.evidence ? ` · ${contact.evidence}` : ''}</small>
            </div>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无真实联系方式。可先运行抓取或手动补录。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>全部视频深度分析</b><span>读取 profile/posts 真实返回</span></div>
        <div className="vkpi-discover-mini-grid">
          <div><strong>{compactCount(posts.length)}</strong><span>内容条数</span></div>
          <div><strong>{compactCount(summary.total_views || selectedKol.raw.total_views)}</strong><span>总播放</span></div>
          <div><strong>{compactCount(summary.total_likes || selectedKol.raw.total_likes)}</strong><span>总点赞</span></div>
          <div><strong>{summary.user_persona ? '已识别' : '待接'}</strong><span>受众画像</span></div>
        </div>
        {summary.user_persona ? <p className="vkpi-discover-muted">{String(summary.user_persona)}</p> : null}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>Viltrox 合作历史</b><span>{projects.length || historicalCooperationCount} 个记录</span></div>
        {projects.length ? projects.slice(0, 4).map((project) => (
          <div className="vkpi-discover-history" key={project.id}>
            <strong>{project.campaign}</strong>
            <span>{stageLabels[project.stage] || project.stage} · {project.productName || project.productSku || '未绑定产品'} · ROI {project.roi ?? '-'}</span>
          </div>
        )) : historicalCooperationCount ? (
          <div className="vkpi-discover-history is-legacy">
            <div>
              <strong>{textValue(historicalMatch.display_name || selectedKol.name || selectedKol.handle, selectedKol.handle)}</strong>
              <span>
                {textValue(historicalMatch.source_type, 'vkpi_kol_pool')} · {historicalCooperationCount} 条历史合作
                {safeNumber(historicalMatch.profile_rows) ? ` · ${safeNumber(historicalMatch.profile_rows)} 条档案证据` : ''}
              </span>
              {historicalRecentCooperations.length ? (
                <small>
                  {historicalRecentCooperations.slice(0, 2).map((row) => textValue(row.product || row.project || row.status, '')).filter(Boolean).join(' / ')}
                </small>
              ) : null}
            </div>
            <b>可复用</b>
          </div>
        ) : <div className="vkpi-discover-empty is-compact">暂无合作历史。首次合作建议先走小预算测试。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>最近内容</b><span>{posts.length} 条{candidateOnly && posts.length ? ' · 平台样本' : ''}</span></div>
        {posts.length ? posts.slice(0, 5).map((post, index) => (
          <div className="vkpi-discover-post" key={String(post.id || post.post_url || post.url || index)}>
            <div>▶</div>
            <span><strong>{textValue(post.title || post.caption || post.post_url || post.url, '未命名内容')}</strong><em>{textValue(post.post_url || post.url || post.content_url, '-')}</em></span>
            <b>{compactLabel(post.views || post.view_count || post.play_count)}</b>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无最近内容。平台未返回样本时，先建档并运行抓取账号。</div>}
      </section>

      <section className="vkpi-discover-card">
        <b>操作</b>
        <div className="vkpi-discover-actions">
          <button className="vkpi-discover-btn is-primary" type="button" onClick={onOpenProject} disabled={!canCreateProject || busy}>加入项目</button>
          <button className="vkpi-discover-btn" type="button" onClick={onSendToIntelligence} disabled={!canSendToIntelligence || busy}>送智能中心复核</button>
          <button className="vkpi-discover-btn" type="button" onClick={() => void onDeepScan()} disabled={!canScan || scanBusy}>深度评估</button>
          <button className="vkpi-discover-btn" type="button" onClick={() => void onClaim()} disabled={!canClaim || busy}>认领</button>
        </div>
        <textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} placeholder="内部备注（UI 本地态，后续接消息/备注接口）" />
      </section>
    </div>
  );
}
