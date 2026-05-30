import { Fragment } from 'react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { PlatformPill } from '../../shared/PlatformPill';
import { primaryStageFlow, stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';
import { KolStageTimeline } from './KolStageTimeline';
import {
  formatMoney,
  formatNumber,
  stageIndex,
  type ScreenshotTarget,
  type TrackingState,
} from '../../../../domains/projects';

interface ProjectParticipationTabProps {
  expandedRows: Set<string>;
  evidenceCountForRow: (row: VkpiProjectRow) => number;
  filteredRows: VkpiProjectRow[];
  movingRowId: string;
  onAddKol: () => void;
  onMoveRowStage: (row: VkpiProjectRow) => void | Promise<void>;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenScreenshotModal: (target: ScreenshotTarget) => void;
  onOpenStageActionModal: (row: VkpiProjectRow, action: 'stalled' | 'lost' | 'released' | 'cancelled') => void;
  onSaveShopifyLink: () => void | Promise<void>;
  onSetShopifyLink: (value: string) => void;
  onSetTablePlatform: (value: string) => void;
  onSetTableQuery: (value: string) => void;
  onSetTableStage: (value: string) => void;
  onToggleRow: (rowId: string) => void;
  platformOptions: Array<VkpiProjectRow['platform']>;
  savingShopify: boolean;
  shopifyLinkForRow: (row: VkpiProjectRow) => string;
  tablePlatform: string;
  tableQuery: string;
  tableStage: string;
  trackingForRow: (row: VkpiProjectRow) => TrackingState;
}

function externalUrl(value?: string) {
  const text = String(value || '').trim();
  if (!text) return '';
  if (/^https?:\/\//i.test(text)) return text;
  return '';
}

function cleanHandle(value: string) {
  return String(value || '')
    .trim()
    .replace(/^@+/, '')
    .replace(/^https?:\/\/[^/]+\//i, '')
    .replace(/^@+/, '')
    .replace(/\/(videos|reels|posts)?\/?$/i, '')
    .replace(/[?#].*$/, '')
    .replace(/\/+$/, '');
}

function kolProfileUrl(row: VkpiProjectRow) {
  const explicit = externalUrl(row.kolProfileUrl);
  if (explicit) return explicit;
  const handle = cleanHandle(row.kolHandle || row.kolName);
  if (!handle || handle === '-') return '';
  if (row.platform === 'YouTube') return `https://www.youtube.com/@${handle}`;
  if (row.platform === 'TikTok') return `https://www.tiktok.com/@${handle}`;
  if (row.platform === 'Instagram') return `https://www.instagram.com/${handle}`;
  if (row.platform === 'X') return `https://x.com/${handle}`;
  if (row.platform === 'Facebook') return `https://www.facebook.com/${handle}`;
  return '';
}

export function ProjectParticipationTab({
  expandedRows,
  evidenceCountForRow,
  filteredRows,
  movingRowId,
  onAddKol,
  onMoveRowStage,
  onOpenKolProfile,
  onOpenScreenshotModal,
  onOpenStageActionModal,
  onSaveShopifyLink,
  onSetShopifyLink,
  onSetTablePlatform,
  onSetTableQuery,
  onSetTableStage,
  onToggleRow,
  platformOptions,
  savingShopify,
  shopifyLinkForRow,
  tablePlatform,
  tableQuery,
  tableStage,
  trackingForRow,
}: ProjectParticipationTabProps) {
  return (
    <div className="vkpi-campaign-table-card" id="vkpi-project-participation">
      <div className="vkpi-campaign-table-toolbar">
        <input value={tableQuery} onChange={(event) => onSetTableQuery(event.target.value)} placeholder="搜索 KOL handle / 平台" />
        <select value={tableStage} onChange={(event) => onSetTableStage(event.target.value)}>
          <option>全部阶段</option>
          {primaryStageFlow.map((stage, index) => <option key={stage} value={stage}>{index + 1}. {stageLabels[stage]}</option>)}
        </select>
        <select value={tablePlatform} onChange={(event) => onSetTablePlatform(event.target.value)}>
          <option>全部平台</option>
          {platformOptions.map((platform) => <option key={platform}>{platform}</option>)}
        </select>
        <button type="button" onClick={onAddKol}>+ 添加 KOL</button>
      </div>
      <div className="vkpi-campaign-table-scroll">
        <table className="vkpi-campaign-table">
          <thead>
            <tr>
              <th />
              <th>KOL</th>
              <th>平台</th>
              <th>负责人</th>
              <th>加入</th>
              <th>当前阶段</th>
              <th>停留</th>
              <th>已发布</th>
              <th>曝光</th>
              <th>归因$</th>
              <th>证据</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row) => {
              const rowOpen = expandedRows.has(row.id);
              const rowStageNumber = stageIndex(row.stage) + 1;
              const rowEvidenceCount = evidenceCountForRow(row);
              const profileUrl = kolProfileUrl(row);
              return (
                <Fragment key={row.id}>
                  <tr className="vkpi-campaign-kol-row" id={`vkpi-project-row-${row.id}`} onClick={() => onToggleRow(row.id)}>
                    <td><span className={`vkpi-campaign-tri ${rowOpen ? 'is-open' : ''}`}>▶</span></td>
                    <td>
                      {profileUrl ? (
                        <a
                          className="vkpi-campaign-kol-cell"
                          href={profileUrl}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(event) => event.stopPropagation()}
                          title="打开平台主页"
                        >
                          <Avatar name={row.kolName} src={row.kolAvatar} size="sm" />
                          <span><b>{row.kolHandle || row.kolName}</b><small>{row.kolName || '-'}</small></span>
                        </a>
                      ) : (
                        <button
                          className="vkpi-campaign-kol-cell"
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            if (onOpenKolProfile) void onOpenKolProfile(row);
                          }}
                        >
                          <Avatar name={row.kolName} src={row.kolAvatar} size="sm" />
                          <span><b>{row.kolHandle || row.kolName}</b><small>{row.kolName || '-'}</small></span>
                        </button>
                      )}
                    </td>
                    <td>
                      {profileUrl ? (
                        <a className="vkpi-campaign-platform-link" href={profileUrl} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()} title="打开平台主页">
                          <PlatformPill platform={row.platform} />
                        </a>
                      ) : <PlatformPill platform={row.platform} />}
                    </td>
                    <td>
                      <span className="vkpi-campaign-evidence-chip">{row.ownerName || '未分配'}</span>
                    </td>
                    <td>{shortDateTime(row.startedAt || row.createdAt || row.latestMessageAt)}</td>
                    <td><span className="vkpi-campaign-stage-pill">{rowStageNumber}. {stageLabels[row.stage]}</span></td>
                    <td>{row.stageDurationLabel || '-'}</td>
                    <td>{rowEvidenceCount ? `${formatNumber(rowEvidenceCount)} 条` : '0 条'}</td>
                    <td><b>{formatNumber(row.views)}</b></td>
                    <td><b>{formatMoney(row.gmv)}</b></td>
                    <td>
                      <span className="vkpi-campaign-evidence-chip">证据 {rowEvidenceCount}</span>
                      {trackingForRow(row).delivered ? <span className="vkpi-campaign-evidence-chip is-warn">到货</span> : null}
                    </td>
                    <td>
                      <button
                        className="vkpi-campaign-small-button is-primary"
                        type="button"
                        disabled={movingRowId === row.id || !nextProjectStage(row.stage)}
                        onClick={(event) => {
                          event.stopPropagation();
                          void onMoveRowStage(row);
                        }}
                      >
                        {movingRowId === row.id ? '推进中' : nextProjectStage(row.stage) ? '推进' : '已完成'}
                      </button>
                    </td>
                  </tr>
                  {rowOpen ? (
                    <tr key={`${row.id}-detail`}>
                      <td colSpan={12} className="vkpi-campaign-expand-cell">
                        <div className="space-y-3">
                          <div className="flex items-center gap-2 rounded-lg border border-white/[0.05] bg-white/[0.012] px-3 py-2">
                            <label className="flex-1 text-[10px] text-slate-500">Shopify 归因链接
                              <input
                                className="mt-1 w-full rounded-md border border-white/[0.06] bg-black/30 px-2 py-1.5 text-[11px] text-white placeholder-slate-600 focus:border-purple-500/40 focus:outline-none"
                                value={shopifyLinkForRow(row)}
                                onChange={(event) => onSetShopifyLink(event.target.value)}
                                placeholder="项目级 Shopify 商品链接或带 ref 的归因链接"
                              />
                            </label>
                            <button className="mt-4 px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" type="button" disabled={savingShopify} onClick={() => void onSaveShopifyLink()}>
                              {savingShopify ? '保存中' : '保存链接'}
                            </button>
                          </div>
                          <KolStageTimeline
                            evidenceCount={rowEvidenceCount}
                            movingRowId={movingRowId}
                            onMoveRowStage={onMoveRowStage}
                            onOpenScreenshotModal={onOpenScreenshotModal}
                            onOpenStageActionModal={onOpenStageActionModal}
                            row={row}
                            tracking={trackingForRow(row)}
                          />
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {!filteredRows.length ? (
          <div className="vkpi-campaign-empty-row">
            {tableStage === '全部阶段' ? '没有匹配的 KOL。调整搜索或平台筛选后再看。' : '当前阶段暂无 KOL。可切换阶段或清除筛选后再看。'}
          </div>
        ) : null}
      </div>
    </div>
  );
}
