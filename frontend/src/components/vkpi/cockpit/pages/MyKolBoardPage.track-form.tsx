import React from "react";

import type { KolLibraryRow, VkpiKolPoolVideoRow } from "../../../../services/vkpi/myKolBoard-api";
import { listSku360Options } from "../../../../services/vkpi/sku360-api";
import { ReceiptLine } from "./MyKolBoardPage.receipt";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

// KOL 详情 · 「追踪已有视频」表单(自 MyKolBoardPage.dialogs 拆出,行数纪律 ≤1000/文件)。
//   只排版 + 本地输入态;提交/回执/权限判定仍由 dialogs 的 runTrackVideo 持有(回调注入)。
//   员工反馈 #5:SKU 输入挂 datalist(GET /sku/list 纯读),员工不用再猜 SKU 怎么写;
//   卡片上的「关联 SKU」会把该卡 URL 填进来并把焦点送到 SKU 框(skuInputRef)。

export const TRACK_FIELD = "min-h-9 rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-ink-2 outline-none focus:border-accent";
const TRACK_ACT_BTN =
  "inline-flex min-h-9 items-center justify-center rounded-lg border border-line px-3 py-1.5 text-[11.5px] font-medium text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";

let SKU_OPTIONS_CACHE: Array<{ sku: string; name: string }> | null = null;

export function TrackExistingVideoForm({
  item,
  apiToken,
  collectedUrlVideos,
  trackUrl,
  setTrackUrl,
  trackSkuInput,
  setTrackSkuInput,
  trackBusy,
  confirmDetected = false,
  paidActionsReadOnly,
  paidActionsReadOnlyHint,
  onSubmit,
  trackingReceipt,
  crawlReceipt,
  crawlBusy,
  onDeepCrawl,
  onOpenProfile,
  skuInputRef,
}: {
  item: KolLibraryRow;
  apiToken: string;
  collectedUrlVideos: Array<{ url: string; video: VkpiKolPoolVideoRow }>;
  trackUrl: string;
  setTrackUrl: (value: string) => void;
  trackSkuInput: string;
  setTrackSkuInput: (value: string) => void;
  trackBusy: boolean;
  confirmDetected?: boolean;
  paidActionsReadOnly: boolean;
  paidActionsReadOnlyHint: string;
  onSubmit: () => void;
  trackingReceipt: FlowReceipt | null;
  crawlReceipt: FlowReceipt | null;
  crawlBusy: boolean;
  onDeepCrawl: () => void;
  onOpenProfile: () => void;
  skuInputRef?: React.MutableRefObject<HTMLInputElement | null>;
}) {
  const selectedCollectedUrl = collectedUrlVideos.some((choice) => choice.url === trackUrl) ? trackUrl : "";
  const [skuOptions, setSkuOptions] = React.useState<Array<{ sku: string; name: string }>>(SKU_OPTIONS_CACHE || []);
  const loadSkuOptions = React.useCallback(() => {
    if (SKU_OPTIONS_CACHE || !apiToken) return;
    listSku360Options(apiToken, "", 200)
      .then((rows) => {
        SKU_OPTIONS_CACHE = rows.filter((row) => row.sku).map((row) => ({ sku: row.sku, name: row.marketing_name || row.model_name }));
        setSkuOptions(SKU_OPTIONS_CACHE);
      })
      .catch(() => { /* 选项只是提示;读不到也能手填 SKU,服务端仍会校验 */ });
  }, [apiToken]);
  const datalistId = React.useId();
  return (
    <div className="mb-3 rounded-[11px] border border-line bg-panel px-3 py-2.5" data-vkpi-track-form="">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[12.5px] font-semibold text-ink-2">追踪已有视频</span>
        <span className="text-[11px] text-muted">只排队刷新快照，不表示实时完成</span>
      </div>
      {collectedUrlVideos.length > 0 ? (
        <select
          aria-label="从已采集内容选择视频"
          value={selectedCollectedUrl}
          disabled={paidActionsReadOnly}
          onChange={(event) => setTrackUrl(event.target.value)}
          className={`mb-2 w-full ${TRACK_FIELD}`}
        >
          <option value="">从当前已采集内容选择视频…</option>
          {collectedUrlVideos.map(({ url, video }) => {
            const evidenceId = Number(video.evidence_id ?? video.id) || 0;
            const label = String(video.title || video.video_title || `视频 #${evidenceId}`);
            return <option key={`${evidenceId}:${url}`} value={url}>{label}{evidenceId ? ` · #${evidenceId}` : ""}</option>;
          })}
        </select>
      ) : null}
      <div className="grid gap-2 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_auto]">
        <input
          aria-label="已有视频 URL"
          type="url"
          value={trackUrl}
          disabled={paidActionsReadOnly}
          onChange={(event) => setTrackUrl(event.target.value)}
          placeholder="粘贴当前 KOL 已采集的视频 URL"
          className={TRACK_FIELD}
        />
        <input
          ref={skuInputRef}
          aria-label="关联产品 SKU"
          type="text"
          list={datalistId}
          value={trackSkuInput}
          disabled={paidActionsReadOnly}
          onFocus={loadSkuOptions}
          onChange={(event) => setTrackSkuInput(event.target.value)}
          placeholder="产品 SKU，逗号分隔（最多 5 个）"
          className={TRACK_FIELD}
        />
        <datalist id={datalistId}>
          {skuOptions.map((option) => <option key={option.sku} value={option.sku}>{option.name}</option>)}
        </datalist>
        <button
          type="button"
          className={TRACK_ACT_BTN}
          disabled={paidActionsReadOnly || trackBusy || !trackUrl.trim() || (confirmDetected && !trackSkuInput.trim())}
          title={paidActionsReadOnly ? paidActionsReadOnlyHint : confirmDetected ? "显式确认系统识别的 SKU 后登记数据关注" : trackUrl.trim() ? "提交已采集视频追踪并排队刷新指标" : "请先从已采集内容选择或粘贴视频 URL"}
          onClick={onSubmit}
        >
          {trackBusy ? "提交中…" : confirmDetected ? "确认系统识别并关注" : "追踪并排队刷新"}
        </button>
      </div>
      <div className="mt-1.5 text-[11px] leading-4 text-muted">
        新 URL 请先通过“账号分析 · 补采”或深爬建立归属证据；是否可写由服务端权限判定，共享只读不会冒充成功。
      </div>
      {paidActionsReadOnly ? <div role="note" className="mt-1 text-[11px] leading-4 text-warn">{paidActionsReadOnlyHint}</div> : null}
      <ReceiptLine msg={trackingReceipt} />
      <div
        data-vkpi-tracking-recovery="account-crawl"
        className="mt-2.5 rounded-lg border border-dashed border-line-strong bg-card px-3 py-2.5"
      >
        <div className="text-[12px] leading-5 text-ink-2">
          {collectedUrlVideos.length > 0
            ? "找不到目标视频？先补采账号内容；完成后回到这里从已采集列表选择。"
            : "当前没有带 URL 的已采集视频。先补采账号内容，再回来建立单视频 / SKU 追踪。"}
        </div>
        {!item.profileUrl ? (
          <div className="mt-1 text-[11px] leading-4 text-warn">该 KOL 缺少主页链接，请先打开 KOL 档案补充或核验主页。</div>
        ) : null}
        {item.isShared ? (
          <div className="mt-1 text-[11px] leading-4 text-warn">共享 KOL 为只读，不能发起会产生外部采集成本的账号补采 / 深爬；请由收藏负责人执行。</div>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={TRACK_ACT_BTN}
            disabled={item.isShared || !item.profileUrl || crawlBusy}
            title={item.isShared ? "共享 KOL 仅可查看，账号补采 / 深爬须由收藏负责人发起" : item.profileUrl ? "入队账号补采 / 深爬；服务端仍会校验当前写权限" : "缺少主页链接，无法发起账号深爬"}
            onClick={onDeepCrawl}
          >
            {crawlBusy ? "入队中…" : "账号补采 / 深爬"}
          </button>
          <button type="button" className={TRACK_ACT_BTN} onClick={onOpenProfile}>打开 KOL 档案</button>
          <span className="text-[10.5px] leading-4 text-muted">入口不改变权限；共享只读等写入边界仍以后端判定为准。</span>
        </div>
        <ReceiptLine msg={crawlReceipt} />
      </div>
    </div>
  );
}
