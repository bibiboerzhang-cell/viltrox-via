import { Fragment } from 'react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { PlatformPill } from '../../shared/PlatformPill';
import { primaryStageFlow, stageLabels } from '../../shared/vkpiConstants';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';
import { TrackingWidget } from './ProjectDetailModals';
import {
  formatMoney,
  formatNumber,
  stageDescriptions,
  stageIndex,
  type ScreenshotTarget,
  type TrackingState,
} from './projectDetailModel';

interface ProjectParticipationTabProps {
  expandedRows: Set<string>;
  evidenceCountForRow: (row: VkpiProjectRow) => number;
  filteredRows: VkpiProjectRow[];
  movingRowId: string;
  onAddKol: () => void;
  onMoveRowStage: (row: VkpiProjectRow) => void | Promise<void>;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenScreenshotModal: (target: ScreenshotTarget) => void;
  onSaveShopifyLink: (row: VkpiProjectRow) => void | Promise<void>;
  onSaveShipment: (row: VkpiProjectRow) => void | Promise<void>;
  onSetShopifyLink: (rowId: string, value: string) => void;
  onSetTablePlatform: (value: string) => void;
  onSetTableQuery: (value: string) => void;
  onSetTableStage: (value: string) => void;
  onToggleRow: (rowId: string) => void;
  onUpdateTracking: (row: VkpiProjectRow, key: 'courier' | 'no', value: string) => void;
  platformOptions: Array<VkpiProjectRow['platform']>;
  savingShipmentRowId: string;
  savingShopifyRowId: string;
  shopifyLinkForRow: (row: VkpiProjectRow) => string;
  tablePlatform: string;
  tableQuery: string;
  tableStage: string;
  trackingForRow: (row: VkpiProjectRow) => TrackingState;
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
  onSaveShopifyLink,
  onSaveShipment,
  onSetShopifyLink,
  onSetTablePlatform,
  onSetTableQuery,
  onSetTableStage,
  onToggleRow,
  onUpdateTracking,
  platformOptions,
  savingShipmentRowId,
  savingShopifyRowId,
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
              return (
                <Fragment key={row.id}>
                  <tr className="vkpi-campaign-kol-row" id={`vkpi-project-row-${row.id}`} onClick={() => onToggleRow(row.id)}>
                    <td><span className={`vkpi-campaign-tri ${rowOpen ? 'is-open' : ''}`}>▶</span></td>
                    <td>
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
                    </td>
                    <td><PlatformPill platform={row.platform} /></td>
                    <td>{shortDateTime(row.startedAt || row.createdAt || row.latestMessageAt)}</td>
                    <td><span className="vkpi-campaign-stage-pill">{rowStageNumber}. {stageLabels[row.stage]}</span></td>
                    <td>{row.stageDurationLabel || '-'}</td>
                    <td>{stageIndex(row.stage) >= stageIndex('published') ? '1 / 1' : '0 / 1'}</td>
                    <td><b>{formatNumber(row.views)}</b></td>
                    <td><b>{formatMoney(row.gmv)}</b></td>
                    <td>
                      <span className="vkpi-campaign-evidence-chip">截图 {evidenceCountForRow(row)}</span>
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
                      <td colSpan={11} className="vkpi-campaign-expand-cell">
                        <div className="vkpi-campaign-expand">
                          <div className="vkpi-campaign-kol-ops">
                            <label>Shopify 归因链接
                              <input
                                value={shopifyLinkForRow(row)}
                                onChange={(event) => onSetShopifyLink(row.id, event.target.value)}
                                placeholder="https://your-store.myshopify.com/... 或带 ref 的商品链接"
                              />
                            </label>
                            <button type="button" disabled={savingShopifyRowId === row.id} onClick={() => void onSaveShopifyLink(row)}>
                              {savingShopifyRowId === row.id ? '保存中' : '保存链接'}
                            </button>
                            <span>快递单号在「已发货」物流卡片里输入，查到已送达会自动提醒。</span>
                          </div>

                          <div className="vkpi-campaign-data-strip">
                            <div><span>视频时长</span><b>-</b><em>暂无数据</em></div>
                            <div><span>完播率</span><b>-</b><em>暂无数据</em></div>
                            <div><span>点赞</span><b>-</b><em>暂无数据</em></div>
                            <div><span>评论</span><b>-</b><em>暂无数据</em></div>
                            <div><span>分享</span><b>-</b><em>暂无数据</em></div>
                            <div><span>短链点击</span><b>{formatNumber(row.clicks)}</b><em>现有数据</em></div>
                          </div>

                          <div className="vkpi-campaign-timeline">
                            {primaryStageFlow.slice(1).map((stage, index) => {
                              const toNumber = index + 2;
                              const done = rowStageNumber >= toNumber;
                              return (
                                <div className={`vkpi-campaign-timeline-row ${done ? '' : 'is-todo'}`} key={stage}>
                                  <div><strong>{done ? shortDateTime(row.latestMessageAt) : '-'}</strong><small>停留 {done ? row.stageDurationLabel || '-' : '-'}</small></div>
                                  <div>
                                    <strong>{toNumber - 1} → {toNumber} {stageLabels[stage]}</strong>
                                    <small>{done ? stageDescriptions[stage] : '等待推进'}</small>
                                    {toNumber === 5 ? (
                                      <TrackingWidget
                                        row={row}
                                        tracking={trackingForRow(row)}
                                        saving={savingShipmentRowId === row.id}
                                        onChange={onUpdateTracking}
                                        onSave={onSaveShipment}
                                      />
                                    ) : null}
                                    {toNumber === 7 ? (
                                      <div className="vkpi-campaign-video">
                                        <div className="vkpi-campaign-thumb">▶</div>
                                        <div>
                                          <b>{row.campaign || '待同步发布内容'}</b>
                                          <span>{row.kolHandle || row.kolName} · {row.platform} · -</span>
                                          <small>内容链接会从项目详情 / 内容数据同步。</small>
                                        </div>
                                        <div><strong>{formatNumber(row.views)}</strong><span>观看</span></div>
                                      </div>
                                    ) : null}
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => onOpenScreenshotModal({
                                      row,
                                      from: toNumber - 1,
                                      to: toNumber,
                                      stage,
                                    })}
                                  >
                                    + 截图
                                  </button>
                                </div>
                              );
                            })}
                          </div>
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
          <div className="vkpi-campaign-empty-row">没有匹配的 KOL。调整搜索、阶段或平台筛选后再看。</div>
        ) : null}
      </div>
    </div>
  );
}
