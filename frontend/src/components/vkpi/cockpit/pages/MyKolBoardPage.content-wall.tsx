import React from "react";

import {
  sortClassifiedVideos,
  summarizeKolVideos,
  trackMyKolExistingVideo,
  type VContentTier,
  type VideoRelationFilter,
  type VkpiKolPoolVideoRow,
  type VkpiRecentVideoItem,
  type VkpiRecentVideosGroup,
} from "../../../../services/vkpi/myKolBoard-api";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { adaptiveRowCount, useModuleSize } from "../components/moduleSize";
import { CHIP, CHIP_OFF, CHIP_ON, MINI_BADGE, V_TIER_META } from "./MyKolBoardPage.libdetail";
import { runDataWatchAction, toggleIdInSet, WALL_SKU_REQUIRED_HINT } from "./MyKolBoardPage.data-watch";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { ReceiptLine } from "./MyKolBoardPage.receipt";
import { RecoveryPollingLine, TrackGuideLine, VideoTaskStatus, VideoTrackActions } from "./MyKolBoardPage.video-tasks";
import { useMyKolVideoRecovery } from "./useMyKolVideoRecovery";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

// 内容墙(MY KOL 板面):两路真端点 —— 全部收藏 = board-ext recent_videos(聚合,封顶 60,无逐条任务态);
//   选中单 KOL = GET /my-kol/{id}/videos(契约 my_kol_video_recovery_v1:keyset 游标「查看更多」+
//   逐条任务态/新鲜度 + 在途轮询)。卡片「追踪播放」= POST /my-kol/{kol_pool_id}/videos(登记持久追踪 +
//   排队抓取),共享只读由服务端判定,失败如实回执。
const WALL_PAGE = 12;
const WALL_KOL_PAGE_SIZE = 24;

// 首屏可见卡数自适应(消灭模块放大后的黑边):几何按真实类名实测——
// 卡片(聚合视图,无逐条任务行):缩略图 92 + pt-2(8) + 标题行(20) + mt-0.5(2)
//   + KOL 行(17) + mt-1(4) + 指标行(17) + mt-1.5(6) + 档位徽行(22)
//   + 追踪钮行 mt-1.5+min-h-8(38) + pb-2(8) + 边框(2) = 236px;网格 gap-2(8) → 行距 244px。
// chrome = 卡头 min-h-11(44) + 汇总条 mb-2+py-2.5+两行 leading-5+边框(70)
//   + 筛选行 min-h-9+mb-2(44) + 追踪引导条 mb-2+py-2+两行 leading-5+边框(66)
//   + 「查看更多」钮 mt-2+min-h-10(48) + 卡体底 pb-4(16) = 288px。
// 上下文缺席(独立挂载/测试)→ 旧口径 WALL_PAGE=12,零行为变化。
const WALL_CARD_PX = 244;
const WALL_CHROME_PX = 288;
// 聚合端点封顶 60 条,行数上限按此收口(封顶值见 board-ext recent_videos 契约)
const WALL_MAX_ROWS = 60;

// 墙网格列数是视口断点驱动(grid-cols-2 md:grid-cols-3 xl:grid-cols-4,Tailwind 缺省
// md=768 / xl=1280),与模块跨列无关——按视口宽估列数是当前最准的口径。
function wallColumnCount(): number {
  if (typeof window === "undefined") return 3;
  const width = window.innerWidth || 0;
  if (width >= 1280) return 4;
  if (width >= 768) return 3;
  return 2;
}

function metricText(value: number | null | undefined): string {
  return value == null ? "未采集" : Number(value).toLocaleString();
}

function WallThumb({ video }: { video: VkpiRecentVideoItem }) {
  const src = proxiedImageUrl(
    String(video.best_thumbnail || video.cached_thumbnail_url || video.thumbnail_url || video.youtube_thumbnail_url || ""),
  );
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]);
  if (!src || failed) {
    return (
      <span
        className="grid h-full w-full place-items-center bg-card text-[16px] text-muted"
        title={failed ? "缩略图加载失败(不摆假图)" : "该视频无可用缩略图(未存 · 缓存无 · 非 youtube)"}
        aria-hidden="true"
      >
        ▶
      </span>
    );
  }
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      className="h-full w-full object-cover"
      onError={() => setFailed(true)}
      onLoad={(event) => {
        const image = event.currentTarget;
        if (image.naturalWidth > 0 && image.naturalHeight > 0 && image.naturalWidth <= 2 && image.naturalHeight <= 2) setFailed(true);
      }}
    />
  );
}

function WallVideoCard({
  video,
  tier,
  fallbackKolName,
  showTasks,
  trackBusy,
  dataWatchBusy,
  onTrack,
  onDataWatch,
}: {
  video: VkpiRecentVideoItem;
  tier: VContentTier;
  fallbackKolName?: string;
  /** 单 KOL 视图(契约行带 tasks)才渲染两层任务态;聚合视图诚实提示 */
  showTasks: boolean;
  trackBusy: boolean;
  dataWatchBusy: boolean;
  onTrack?: (video: VkpiKolPoolVideoRow) => void;
  /** 一键「数据关注」(自动认产品 + 登记追踪 + 排队刷新;认不出产品如实提示去详情补 SKU) */
  onDataWatch?: (video: VkpiKolPoolVideoRow) => void;
}) {
  const eid = Number(video.evidence_id ?? video.id) || 0;
  const title = String(video.title || video.video_title || "未命名视频");
  const kolName = String(video.kol_name || fallbackKolName || video.kol_handle || "—");
  const day = String(video.publish_date || video.posted_at || "").slice(0, 10);
  const meta = V_TIER_META[tier];
  const href = String(video.content_url || "");
  const top = (
    <>
      <div className="h-[92px] w-full overflow-hidden bg-card"><WallThumb video={video} /></div>
      <div className="px-2.5 pt-2">
        <div className="truncate text-[13px] font-medium text-ink" title={title}>{title}</div>
        <div className="mt-0.5 truncate text-[11.5px] text-muted" title="所属收藏 KOL">{kolName}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-muted">
          <span title={video.view_count == null ? "播放未实测(≠ 0)" : "播放(点时实测)"}>▶ {video.view_count == null ? "未实测" : metricText(video.view_count)}</span>
          <span title={video.like_count == null ? "点赞未采集" : "点赞(点时实测)"}>♥ {metricText(video.like_count)}</span>
          {day ? <span title="发布日期(平台原发布日,非采集日)">{day}</span> : null}
        </div>
      </div>
    </>
  );
  return (
    <div key={eid} className="group flex flex-col overflow-hidden rounded-[11px] border border-line bg-panel transition-colors hover:border-accent" data-vkpi-wall-card={eid}>
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" title="点卡直跳原帖" className="block">{top}</a>
      ) : <div title="该条无原帖链接(采集未存 URL)">{top}</div>}
      <div className="px-2.5 pb-2">
        {showTasks ? <VideoTaskStatus tasks={video.tasks} compact /> : null}
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className={`rounded-[5px] border px-1.5 py-0.5 text-[10.5px] font-bold ${meta.cls}`} title={meta.title}>{meta.label}</span>
          {video.has_final_v1_cache ? <span className={`${MINI_BADGE} border-good bg-good-soft text-good`}>已深析</span> : null}
          {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="ml-auto flex-none font-mono text-[10px] text-muted transition-colors hover:text-accent" title="直跳原帖">原帖 ↗</a> : null}
        </div>
        {onTrack || onDataWatch ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1" data-vkpi-track-actions="">
            <VideoTrackActions video={video} busy={trackBusy} onTrack={onTrack} onDataWatch={onDataWatch} dataWatchBusy={dataWatchBusy} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ContentWallModule({
  apiToken,
  group,
  kolOptions,
}: {
  apiToken: string;
  group: VkpiRecentVideosGroup;
  kolOptions: Array<{ poolId: number; name: string }>;
}) {
  const [kolId, setKolId] = React.useState(0);
  const [relationFilter, setRelationFilter] = React.useState<VideoRelationFilter>("all");
  const [sortBy, setSortBy] = React.useState<"time" | "views">("time");
  // 首屏可见卡数 = 列数 × 按模块实际高度实算的行数,保底 WALL_PAGE(上下文缺席 → 旧口径 12);
  // 「查看更多」的增量单独记(extraVisible),筛选/切 KOL 归零,模块改大小不吞掉已点开的增量。
  const moduleSize = useModuleSize();
  const initialVisible = React.useMemo(() => {
    if (!moduleSize) return WALL_PAGE;
    const rowsFit = adaptiveRowCount({
      heightPx: moduleSize.heightPx,
      chromePx: WALL_CHROME_PX,
      rowPx: WALL_CARD_PX,
      min: 0,
      max: WALL_MAX_ROWS,
    });
    return Math.max(WALL_PAGE, wallColumnCount() * rowsFit);
  }, [moduleSize]);
  const [extraVisible, setExtraVisible] = React.useState(0);
  const visible = initialVisible + extraVisible;
  // 单 KOL 视图走契约读模型:keyset 游标 + 逐条任务态 + 在途轮询(kolId=0 时 hook 禁用,零请求)
  const recovery = useMyKolVideoRecovery({ apiToken, kolPoolId: kolId || null, enabled: kolId > 0, pageSize: WALL_KOL_PAGE_SIZE });
  const [trackBusy, setTrackBusy] = React.useState<Set<number>>(new Set());
  const [watchBusyIds, setWatchBusyIds] = React.useState<Set<number>>(new Set());
  const [trackReceipt, setTrackReceipt] = React.useState<FlowReceipt | null>(null);

  React.useEffect(() => setExtraVisible(0), [kolId, relationFilter, sortBy]);
  React.useEffect(() => setTrackReceipt(null), [kolId]);

  const kolName = React.useMemo(() => kolOptions.find((option) => option.poolId === kolId)?.name || "", [kolOptions, kolId]);
  const kolBusy = kolId > 0 && recovery.loading && !recovery.loaded;
  const kolError = kolId > 0 ? recovery.error : "";
  const baseItems: VkpiRecentVideoItem[] = kolId ? (recovery.videos as VkpiRecentVideoItem[]) : Array.isArray(group.items) ? group.items : [];
  const summary = React.useMemo(() => summarizeKolVideos(baseItems), [baseItems]);
  const shown = React.useMemo(
    () => sortClassifiedVideos(summary.classified, relationFilter, sortBy),
    [summary.classified, relationFilter, sortBy],
  );
  const clientPaged = !kolId;
  const visibleRows = clientPaged ? shown.slice(0, visible) : shown;

  // 卡片「追踪播放」:POST /my-kol/{kol_pool_id}/videos(登记持久追踪 + 排队抓取);只排队不冒充完成。
  const trackVideo = async (video: VkpiKolPoolVideoRow) => {
    const eid = Number(video.evidence_id ?? video.id) || 0;
    const poolId = Number(video.kol_pool_id) || kolId;
    const url = String(video.content_url || "").trim();
    if (!apiToken || !eid || !poolId || !url || trackBusy.has(eid)) return;
    setTrackBusy((prev) => new Set(prev).add(eid));
    setTrackReceipt(null);
    try {
      const resp = await trackMyKolExistingVideo(apiToken, poolId, { contentUrl: url });
      const status = String(resp?.status || "");
      if (status !== "queued" && status !== "already_queued") {
        setTrackReceipt({ text: `追踪请求未获服务端确认：${status || "未知状态"}`, tone: "error" });
        return;
      }
      setTrackReceipt({
        text: `${status === "already_queued" ? "该视频已在追踪队列中" : "已登记追踪并排队抓取"}（#${eid}）${kolId ? ";下方状态会自动同步" : ";选中该 KOL 或打开 KOL 详情可看进度"}。`,
        tone: "info",
      });
      if (kolId) void recovery.refresh();
    } catch (error) {
      const code = String((error as { detail?: unknown; message?: unknown })?.detail || (error as Error)?.message || "");
      const text = code === "my_kol_paid_action_write_forbidden"
        ? "共享 KOL 仅可查看,请由收藏负责人发起追踪。"
        : code === "new_video_target_resolution_required"
          ? "当前仅支持已采集视频,请先账号补采/深爬后重试。"
          : code === "video_metric_platform_unsupported"
            ? "该平台暂不支持播放追踪。"
            : `追踪失败：${code.slice(0, 80) || "请重试"}`;
      setTrackReceipt({ text, tone: "error" });
    } finally {
      setTrackBusy((prev) => { const next = new Set(prev); next.delete(eid); return next; });
    }
  };

  // 一键「数据关注」(墙入口):POST …/videos/{evidence_id}/data-watch —— 自动认产品 + 登记追踪 +
  //   排队刷新(流程/文案住 MyKolBoardPage.data-watch.ts);认不出产品时墙上没有 SKU 表单,
  //   如实提示去 KOL 详情用「关联 SKU」补一个,绝不无 SKU 假登记。
  const dataWatchVideo = (video: VkpiKolPoolVideoRow) => runDataWatchAction(video, {
    apiToken,
    kolPoolId: Number(video.kol_pool_id) || kolId,
    isBusy: (eid) => watchBusyIds.has(eid),
    setBusy: toggleIdInSet(setWatchBusyIds),
    setReceipt: setTrackReceipt,
    onSkuRequired: () => setTrackReceipt({ text: WALL_SKU_REQUIRED_HINT, tone: "info" }),
    onTracked: kolId ? () => { void recovery.refresh(); } : undefined,
  });

  return (
    <div>
      <div className="mb-2 rounded-[9px] border border-line bg-card px-3 py-2.5 text-[12px] leading-5 text-muted">
        已采集 {summary.classified.length}｜品牌相关 {summary.vRelatedCount}｜待深析 {summary.undeterminedCount}｜深析未见 V {summary.unrelatedCount}｜播放已实测 {summary.measuredCount}/{summary.classified.length}
        <span className="ml-2">当前为系统已采集窗口，不代表平台频道全量；播放趋势请在 KOL 详情查看。</span>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
        <select
          aria-label="按 KOL 筛选"
          value={String(kolId)}
          onChange={(event) => setKolId(Number(event.target.value) || 0)}
          className="min-h-9 max-w-[210px] rounded-xl border border-line bg-card px-3 py-1.5 text-[12px] text-ink outline-none focus:border-accent [&>option]:bg-[var(--ds-card)]"
          title="选择单个收藏KOL，查看该账号当前已采集内容"
        >
          <option value="0">全部收藏 KOL</option>
          {kolOptions.map((option) => <option key={option.poolId} value={String(option.poolId)}>{option.name}</option>)}
        </select>
        <button type="button" className={`${CHIP} ${relationFilter === "all" ? CHIP_ON : CHIP_OFF}`} onClick={() => setRelationFilter("all")}>全部已采集</button>
        <button type="button" className={`${CHIP} ${relationFilter === "viltrox" ? CHIP_ON : CHIP_OFF}`} onClick={() => setRelationFilter("viltrox")} title="项目关联 / 画面·口播识别 V / 标题提及 V(按结构化证据,不只看标题)">品牌相关</button>
        <button type="button" className={`${CHIP} ${relationFilter === "undetermined" ? CHIP_ON : CHIP_OFF}`} onClick={() => setRelationFilter("undetermined")} title="还没深析或证据不足的内容——不等于不相关">待深析</button>
        <button type="button" className={`${CHIP} ${relationFilter === "not_related" ? CHIP_ON : CHIP_OFF}`} onClick={() => setRelationFilter("not_related")} title="深析完整检查过画面与音频,没有见到 Viltrox">深析未见 V</button>
        <span className="ml-auto flex items-center gap-1.5">
          <button type="button" className={`${CHIP} ${sortBy === "time" ? CHIP_ON : CHIP_OFF}`} onClick={() => setSortBy("time")}>最新</button>
          <button type="button" className={`${CHIP} ${sortBy === "views" ? CHIP_ON : CHIP_OFF}`} onClick={() => setSortBy("views")}>播放</button>
        </span>
      </div>
      <div className="mb-2"><TrackGuideLine compact /></div>
      <ReceiptLine msg={trackReceipt} />
      {kolError ? <ErrorCard title="按 KOL 读取失败" text={kolError} />
        : kolBusy ? <LoadingLine text={`${kolName || "该 KOL"} 已采集内容读取中…`} />
        : baseItems.length === 0 ? <EmptyLine text={kolId ? `${kolName || "该 KOL"} 暂无已采集内容——可在KOL详情发起补采。` : "暂无已采集内容——可在KOL详情发起补采。"} />
        : shown.length === 0 ? <EmptyLine text="当前筛选没有内容，可切回“全部已采集”或继续补采。" />
        : (
          <div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
              {visibleRows.map(({ video, tier }) => (
                <WallVideoCard
                  key={Number(video.evidence_id ?? video.id) || 0}
                  video={video}
                  tier={tier}
                  fallbackKolName={kolId ? kolName : undefined}
                  showTasks={kolId > 0}
                  trackBusy={trackBusy.has(Number(video.evidence_id ?? video.id) || 0)}
                  dataWatchBusy={watchBusyIds.has(Number(video.evidence_id ?? video.id) || 0)}
                  onTrack={apiToken ? trackVideo : undefined}
                  onDataWatch={apiToken ? dataWatchVideo : undefined}
                />
              ))}
            </div>
            {clientPaged && shown.length > visible ? (
              <button type="button" onClick={() => setExtraVisible((value) => value + WALL_PAGE)} className="mt-2 min-h-10 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[11.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft">
                ≡ 查看更多（已显 {Math.min(visible, shown.length)} / 当前已采集 {shown.length}）
              </button>
            ) : null}
            {!clientPaged && recovery.hasMore ? (
              <button
                type="button"
                onClick={() => { void recovery.loadMore(); }}
                disabled={recovery.loadingMore}
                className="mt-2 min-h-10 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[11.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft disabled:cursor-default disabled:text-muted"
                title="按发布时间游标读取下一页(后台刷新不会打乱顺序)"
              >
                {recovery.loadingMore ? "读取中…" : `≡ 查看更多（已加载 ${baseItems.length}${recovery.total ? ` / 共 ${recovery.total}` : ""}）`}
              </button>
            ) : null}
            {kolId ? <RecoveryPollingLine polling={recovery.polling} paused={recovery.pollPaused} onResume={recovery.resumePolling} /> : null}
          </div>
        )}
    </div>
  );
}
