import { useState } from 'react';
import { Boxes, ExternalLink, FileText, Package } from 'lucide-react';
import { ProjectEvidenceForms } from '../../../drawers/ProjectEvidenceForms';
import { ProjectMaterialsLibrary } from '../ProjectMaterialsLibrary';
import { formatLargeNum, formatMoneyShort } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import { stageIndex, type ProjectStatsSummary } from '../../../../../domains/projects';
import { centsValue, costRowAmount, objectValue, productCost, rowProductSent } from '../ProjectDetailTabs.shared';

type MaterialSection = 'assets' | 'logistics';

function buildMaterialBriefText(project: VkpiProjectRow | undefined, rows: VkpiProjectRow[], stats?: ProjectStatsSummary) {
  const projectTitle = project?.campaign || project?.productName || rows[0]?.productName || '未命名项目';
  const product = project?.productName || rows[0]?.productName || projectTitle;
  const sku = project?.productSku || rows[0]?.productSku || '待补充';
  const platforms = Array.from(new Set(rows.map((row) => row.platform).filter(Boolean))).join(', ') || '待确认';
  const published = stats?.published ?? rows.filter((row) => stageIndex(row.stage) >= stageIndex('published')).length;

  return [
    `# ${projectTitle} 项目 Brief`,
    '',
    `产品: ${product}`,
    `SKU: ${sku}`,
    `平台: ${platforms}`,
    `参与 KOL: ${rows.length}`,
    `已发布: ${published}`,
    `当前曝光: ${formatLargeNum(stats?.views || 0)}`,
    '',
    '## 执行口径',
    '- 合同、费用和交付证明以项目详情页当前记录为准。',
  ].join('\n');
}

function trackingUrl(carrier: string, trackingNumber: string) {
  const normalizedCarrier = carrier.toLowerCase();
  const encoded = encodeURIComponent(trackingNumber);
  if (normalizedCarrier.includes('dhl')) return `https://www.dhl.com/us-en/home/tracking/tracking-express.html?submit=1&tracking-id=${encoded}`;
  if (normalizedCarrier.includes('fedex')) return `https://www.fedex.com/fedextrack/?trknbr=${encoded}`;
  if (normalizedCarrier.includes('ups')) return `https://www.ups.com/track?tracknum=${encoded}`;
  if (normalizedCarrier.includes('usps')) return `https://tools.usps.com/go/TrackConfirmAction?tLabels=${encoded}`;
  // 17track 免费聚合页:覆盖国内外承运商,比裸 Google 搜索稳定(2026-06-12 免费查单裁令)
  return `https://t.17track.net/zh-cn#nums=${encoded}`;
}

function deliveredByStageOrStatus(row: VkpiProjectRow) {
  const status = String(row.trackingStatus || '').toLowerCase();
  return stageIndex(row.stage) >= stageIndex('received') || /delivered|signed|received|签收|已送达|已到货/.test(status);
}

export function CampaignMaterialsTab({
  apiToken,
  project,
  rows,
  stats,
  costRows = [],
  productUnitCosts = {},
  onCopy,
  onPendingAction,
  projectId,
  onUpsertTerms,
  onAddShipment,
  onUploadEvidenceFile,
}: {
  apiToken?: string;
  project?: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats?: ProjectStatsSummary;
  costRows?: Array<Record<string, unknown>>;
  productUnitCosts?: Record<string, number>;
  onCopy?: (text: string, label: string) => Promise<void>;
  onPendingAction: (label: string) => void;
  projectId?: string;
  onUpsertTerms?: (payload: Record<string, unknown>) => Promise<void>;
  onAddShipment?: (payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
}) {
  const [section, setSection] = useState<MaterialSection>('assets');
  const shipped = rows.filter((row) => stageIndex(row.stage) >= stageIndex('shipped'));
  const briefText = buildMaterialBriefText(project, rows, stats);

  return (
    <div aria-label="项目物料">
      <div className="flex items-center gap-1 px-4 pt-3 border-b border-white/[0.04]" aria-label="物料子 tab">
        {[
          { key: 'assets' as const, label: '营销物料', icon: Boxes },
          { key: 'logistics' as const, label: '快递追踪 · 公开', icon: Package },
        ].map((item) => {
          const Icon = item.icon;
          const active = section === item.key;
          return (
            <button
              key={item.key}
              onClick={() => setSection(item.key)}
              className={`px-3 py-2 text-[11.5px] font-medium border-b-2 flex items-center gap-1.5 transition-all ${active ? 'text-purple-300 border-purple-500' : 'text-slate-400 border-transparent hover:text-white'}`}
              type="button"
            >
              <Icon size={11} />
              {item.label}
            </button>
          );
        })}
      </div>

      {section === 'assets' ? (
        <div className="p-4 space-y-3">
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3 flex items-start gap-2.5">
            <FileText size={13} className="text-slate-300 mt-0.5 shrink-0" />
            <div className="text-[10.5px] text-slate-300 flex-1">
              项目 Brief 汇总当前项目的产品 / SKU / 平台 / KOL / 曝光,供对外沟通使用。物料文件(产品图、参数手册、脚本等)在下方物料库上传归档。
            </div>
            <button
              className="px-2.5 py-1 rounded-md bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 text-[10.5px] font-medium flex items-center gap-1"
              type="button"
              onClick={() => (onCopy ? void onCopy(briefText, '项目 Brief') : onPendingAction('复制 Brief'))}
            >
              <FileText size={10} />复制 Brief
            </button>
          </div>

          {/* P1 物料库(2026-07-03):上传 + 列表 + 下载,复用 evidence uploads 落盘 + 按 project_id 归档。 */}
          {projectId ? (
            <ProjectMaterialsLibrary
              apiToken={apiToken}
              projectId={projectId}
              onUploadEvidenceFile={onUploadEvidenceFile}
            />
          ) : (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
              <Boxes size={24} className="text-slate-600 mx-auto mb-2" />
              <div className="text-[11.5px] text-slate-400 mb-1">物料库需要项目上下文</div>
              <div className="text-[10.5px] text-slate-500">当前视图缺少项目 ID,进入具体项目详情后可上传 / 下载物料</div>
            </div>
          )}

          {projectId && (onUpsertTerms || onAddShipment) ? (
            <div className="vkpi-campaign-evidence-forms rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
              <ProjectEvidenceForms
                projectId={projectId}
                onUpsertTerms={onUpsertTerms}
                onAddShipment={onAddShipment}
                onUploadEvidenceFile={onUploadEvidenceFile}
              />
              <div className="mt-2 text-[10px] text-slate-500">
                条款附件 / 物流凭证支持真实文件上传(落 evidence 存储)。消息记录与发布内容两个表单的写入接口未开放,按钮已禁用。
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="p-4 space-y-3">
          {shipped.length === 0 ? (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
              <Package size={24} className="text-slate-600 mx-auto mb-2" />
              <div className="text-[11.5px] text-slate-400 mb-1">暂无快递记录</div>
              <div className="text-[10.5px] text-slate-500">KOL 进入「已发货」阶段时录入快递信息自动追踪</div>
            </div>
          ) : (
            shipped.map((row) => {
                const carrier = row.trackingCarrier || '待识别快递';
                const trackingNumber = String(row.trackingNumber || '').trim();
                const tr = trackingNumber ? { carrier, no: trackingNumber } : null;
                // 签收判定与时间轴同口径:阶段 >= 已到货,或 trackingStatus 真值含签收关键字。
                const isDelivered = deliveredByStageOrStatus(row);
                const realTrackingStatus = String(row.trackingStatus || '').trim();
                const shippingCost = costRowAmount(costRows, row, 'shipping');
                const productSent = rowProductSent(row);
                const ledgerProductCost = costRowAmount(costRows, row, 'product');
                const productCostAmount = ledgerProductCost || productCost(productSent, productUnitCosts);
                // 与财务 tab 同口径:估算回退值打"估",不冒充账本真值(扫描 #9)。
                const productCostIsEstimate = !ledgerProductCost && productCostAmount > 0;
                const kolName = row.kolHandle || row.kolName || 'Unknown';
                const handle = row.kolName || row.kolHandle || '-';
                return (
                  <div key={row.id} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
                    <div className="flex items-center gap-3 mb-3">
                      <div className="w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#06b6d4)' }}>
                        {kolName.charAt(0).toUpperCase()}
                      </div>
                      <div className="flex-1">
                        <div className="text-[12.5px] font-semibold text-white">{kolName}</div>
                        <div className="text-[10px] text-slate-500">{row.platform} · {handle}</div>
                      </div>
                      <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${isDelivered ? 'bg-emerald-500/15 text-emerald-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{isDelivered ? '已签收' : '在途中'}</span>
                    </div>
                    {tr ? (
                      <div className="px-3 py-2 rounded bg-black/30 mb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Package size={13} className="text-cyan-400" />
                          <div>
                            <div className="text-[11px] text-white font-medium">{tr.carrier}</div>
                            <div className="text-[10px] text-slate-400 font-mono">{tr.no}</div>
                          </div>
                        </div>
                        <a className="text-[10px] text-cyan-300 hover:text-cyan-200 flex items-center gap-1" href={trackingUrl(tr.carrier, tr.no)} target="_blank" rel="noreferrer">
                          <ExternalLink size={10} />外部追踪
                        </a>
                      </div>
                    ) : null}
                    {/* 物流节点没有逐段真值:有 trackingStatus(17track 等)显示真值,否则只显两态(在途/已签收)并注明按阶段推断,不再画四段假进度。 */}
                    <div className="flex items-center gap-1.5 mb-2">
                      {realTrackingStatus ? (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300">物流状态:{realTrackingStatus}</span>
                      ) : (
                        <>
                          {['在途', '已签收'].map((step, index) => {
                            const stepDone = isDelivered || index === 0;
                            return (
                              <div key={step} className="flex-1 flex items-center gap-1">
                                <div className={`w-2 h-2 rounded-full ${stepDone ? 'bg-emerald-400' : 'bg-white/[0.08]'}`} />
                                <div className={`flex-1 text-[9.5px] ${stepDone ? 'text-emerald-300' : 'text-slate-600'}`}>{step}</div>
                                {index < 1 ? <div className={`h-px flex-1 ${isDelivered ? 'bg-emerald-400/40' : 'bg-white/[0.05]'}`} /> : null}
                              </div>
                            );
                          })}
                          <span className="text-[9px] text-slate-600 shrink-0">按阶段推断</span>
                        </>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-3 pt-2 border-t border-white/[0.04]">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {productSent.map((item) => <span key={item} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-300">{item}</span>)}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        快递费 <span className="text-white tabular-nums">{formatMoneyShort(shippingCost)}</span>
                        {' · '}
                        产品成本 <span className="text-emerald-400 tabular-nums">{formatMoneyShort(productCostAmount)}</span>
                        {productCostIsEstimate ? (
                          <span className="text-[9px] text-amber-300/80 ml-1" title="按 SKU 成本目录单价估算(成本账本暂无该 KOL 产品成本行)">估</span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })
          )}
        </div>
      )}
    </div>
  );
}
