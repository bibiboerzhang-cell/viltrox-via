import React from "react";

import {
  classifyVideoRow,
  getMyKolPoolVideos,
  sortClassifiedVideos,
  summarizeKolVideos,
  type VContentTier,
  type VideoRelationFilter,
  type VkpiRecentVideoItem,
  type VkpiRecentVideosGroup,
} from "../../../../services/vkpi/myKolBoard-api";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { CHIP, CHIP_OFF, CHIP_ON, MINI_BADGE, V_TIER_META } from "./MyKolBoardPage.libdetail";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";

const WALL_PAGE = 12;
const WALL_KOL_VIDEOS_LIMIT = 200;
const WALL_KOL_CACHE = new Map<number, VkpiRecentVideoItem[]>();

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

function WallVideoCard({ video, tier, fallbackKolName }: { video: VkpiRecentVideoItem; tier: VContentTier; fallbackKolName?: string }) {
  const eid = Number(video.evidence_id ?? video.id) || 0;
  const title = String(video.title || video.video_title || "未命名视频");
  const kolName = String(video.kol_name || fallbackKolName || video.kol_handle || "—");
  const day = String(video.publish_date || video.posted_at || "").slice(0, 10);
  const meta = V_TIER_META[tier];
  const href = String(video.content_url || "");
  const body = (
    <>
      <div className="h-[92px] w-full overflow-hidden bg-card"><WallThumb video={video} /></div>
      <div className="px-2.5 py-2">
        <div className="truncate text-[13px] font-medium text-ink" title={title}>{title}</div>
        <div className="mt-0.5 truncate text-[11.5px] text-muted" title="所属收藏 KOL">{kolName}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-muted">
          <span title={video.view_count == null ? "播放未实测(≠ 0)" : "播放(点时实测)"}>▶ {video.view_count == null ? "未实测" : metricText(video.view_count)}</span>
          <span title={video.like_count == null ? "点赞未采集" : "点赞(点时实测)"}>♥ {metricText(video.like_count)}</span>
          {day ? <span title="发布日期(平台原发布日,非采集日)">{day}</span> : null}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className={`rounded-[5px] border px-1.5 py-0.5 text-[10.5px] font-bold ${meta.cls}`} title={meta.title}>{meta.label}</span>
          {video.has_final_v1_cache ? <span className={`${MINI_BADGE} border-good bg-good-soft text-good`}>已深析</span> : null}
          {href ? <span className="ml-auto flex-none font-mono text-[10px] text-muted transition-colors group-hover:text-accent" aria-hidden="true">原帖 ↗</span> : null}
        </div>
      </div>
    </>
  );
  const cardCls = "block overflow-hidden rounded-[11px] border border-line bg-panel";
  return href ? (
    <a key={eid} href={href} target="_blank" rel="noopener noreferrer" title="点卡直跳原帖" className={`group ${cardCls} transition-colors hover:border-accent`}>
      {body}
    </a>
  ) : <div key={eid} className={cardCls} title="该条无原帖链接(采集未存 URL)">{body}</div>;
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
  const [visible, setVisible] = React.useState(WALL_PAGE);
  const [kolRows, setKolRows] = React.useState<VkpiRecentVideoItem[] | null>(null);
  const [kolBusy, setKolBusy] = React.useState(false);
  const [kolError, setKolError] = React.useState("");

  React.useEffect(() => setVisible(WALL_PAGE), [kolId, relationFilter, sortBy]);
  React.useEffect(() => {
    if (!apiToken || !kolId) {
      setKolRows(null);
      setKolError("");
      return;
    }
    const cached = WALL_KOL_CACHE.get(kolId);
    if (cached) {
      setKolRows(cached);
      setKolError("");
      return;
    }
    let alive = true;
    setKolBusy(true);
    setKolError("");
    setKolRows(null);
    getMyKolPoolVideos(apiToken, kolId, WALL_KOL_VIDEOS_LIMIT)
      .then((response) => {
        if (!alive) return;
        const items = (Array.isArray(response.items) ? response.items : []) as VkpiRecentVideoItem[];
        WALL_KOL_CACHE.set(kolId, items);
        setKolRows(items);
      })
      .catch((error: unknown) => {
        if (alive) setKolError(String((error as { detail?: unknown; message?: unknown })?.detail || (error as Error)?.message || "读取失败").slice(0, 120));
      })
      .finally(() => { if (alive) setKolBusy(false); });
    return () => { alive = false; };
  }, [apiToken, kolId]);

  const kolName = React.useMemo(() => kolOptions.find((option) => option.poolId === kolId)?.name || "", [kolOptions, kolId]);
  const baseItems: VkpiRecentVideoItem[] = kolId ? kolRows || [] : Array.isArray(group.items) ? group.items : [];
  const summary = React.useMemo(() => summarizeKolVideos(baseItems), [baseItems]);
  const shown = React.useMemo(
    () => sortClassifiedVideos(summary.classified, relationFilter, sortBy),
    [summary.classified, relationFilter, sortBy],
  );

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
      {kolError ? <ErrorCard title="按 KOL 读取失败" text={kolError} />
        : kolId && kolBusy && !kolRows ? <LoadingLine text={`${kolName || "该 KOL"} 已采集内容读取中…`} />
        : baseItems.length === 0 ? <EmptyLine text={kolId ? `${kolName || "该 KOL"} 暂无已采集内容——可在KOL详情发起补采。` : "暂无已采集内容——可在KOL详情发起补采。"} />
        : shown.length === 0 ? <EmptyLine text="当前筛选没有内容，可切回“全部已采集”或继续补采。" />
        : (
          <div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
              {shown.slice(0, visible).map(({ video, tier }) => <WallVideoCard key={Number(video.evidence_id ?? video.id) || 0} video={video} tier={tier} fallbackKolName={kolId ? kolName : undefined} />)}
            </div>
            {shown.length > visible ? (
              <button type="button" onClick={() => setVisible((value) => value + WALL_PAGE)} className="mt-2 min-h-10 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[11.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft">
                ≡ 查看更多（已显 {Math.min(visible, shown.length)} / 当前已采集 {shown.length}）
              </button>
            ) : null}
          </div>
        )}
    </div>
  );
}
