import React from "react";
import { RecordPreview } from "../components/provenance";
import { SectionLabel } from "./MarketVoicePage.dialogs";
import { GoaffproLinkSection } from "../../shared/GoaffproLinkSection";
import { fmtZhCompact } from "./MyKolBoardPage.charts";
import {
  getMyKolPoolVideos,
  isImageKindVideo,
  summarizeKolVideos,
  V_TIER_LABEL,
  videoRecordRows,
  type ClassifiedVideo,
  type KolLibraryRowProject,
  type VContentTier,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import type { VkpiProjectRow } from "../../vkpiTypes";
import { proxiedImageUrl } from "../../shared/mediaProxy";

// MY KOL · 库详情弹窗分区族(闭环数据补刀,2026-07-12):
//   业务闭环 = KOL Pool 找人 → 收藏 → MY KOL(带全部采集数据)→ 入 Project。
//   旧两栏库(EmployeeKolLibrary 右栏 PoolEvidenceContent)里用户能看到的真数据
//   ——「已采集 N 条 · 合计播放 X」/ GOAFFPRO 追踪链 / 合作项目结果 / 深析进度——
//   在新版行式库里全部同源接回,零丢失:
//   ① useKolEvidenceStats:库行「采集数据列」(视频 N · 实测播放合计 · 深析 n/N)。
//      同源 = GET /kol-pool/{id}/videos(旧右栏与详情弹窗同一端点同一算法
//      summarizeKolVideos),只取当前渲染行 + 模块级缓存 + 并发 4 封顶,
//      绝不整库扫(779 行团队库只拉屏上那几行)。
//   ② GoaffproTrackSection:追踪链分区 —— 共享件 GoaffproLinkSection 原样内嵌
//      (生成/复制/优惠码/佣金调整,功能零改动,与 KOL Pool 抽屉同一组件)。
//   ③ CoopResultsSection:合作项目结果 —— assignments 真阶段 × Projects 板块
//      同一份映射(曝光 views / 证据计数),未挂载读数诚实显 —。
//   ④ KolVideoSection:V 相关内容 —— 全量视频网格 + 筛选 tabs(最新/播放/点赞/
//      评论/分享)+ 汇总条(条数/实测播放/未实测剔除/V 相关/已深析/点赞/评论)+
//      三档徽 + 记录预览 + 未判定一键深析(回执逻辑住 dialogs,本件只发回调)。
//   依赖单向:modules/dialogs → 本文件(反向禁止,防环);金样板件(SectionLabel/
//   RecordPreview)复用零重写。
// 红线:纯展示 + 只读取数;绝不写 fit 分 / 不触 rule_v0;颜色全 token 类零写死色;
//   零 opacity 修饰类;诚实空态(0 采集=—,读取中=…,失败不缓存下次重试,绝不编数)。

/* ============ 共用小件样式(dialogs 同款,单一来源住这里供两侧引) ============ */
export const CHIP = "rounded-full border px-2.5 py-1 text-[10.5px] transition-colors";
export const CHIP_ON = "border-accent bg-accent-soft text-accent";
export const CHIP_OFF = "border-line text-muted hover:text-ink";
export const MINI_BADGE = "flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold";

/* ============ V 相关三档徽(cooperation=accent / title_mention=good / undetermined=muted) ============ */
export const V_TIER_META: Record<VContentTier, { label: string; cls: string }> = {
  cooperation: { label: V_TIER_LABEL.cooperation, cls: "border-accent bg-accent-soft text-accent" },
  title_mention: { label: V_TIER_LABEL.title_mention, cls: "border-good bg-good-soft text-good" },
  undetermined: { label: V_TIER_LABEL.undetermined, cls: "border-line text-muted" },
};

/* ============ ① 库行采集数据(同源 /kol-pool/{id}/videos;可见行懒取 + 模块级缓存) ============ */

export interface KolEvidenceStats {
  /** 已采集 evidence 条数(与详情弹窗「N 条视频」同数) */
  videoCount: number;
  /** 实测播放合计(view_count NULL 剔除,与旧右栏「合计播放」同算法) */
  viewsTotal: number;
  measuredCount: number;
  /** final_v1 深析就绪条数 */
  analyzedCount: number;
}

const STATS_CACHE = new Map<number, KolEvidenceStats>();
const STATS_INFLIGHT = new Set<number>();
const STATS_CONCURRENCY = 4;

/**
 * 只拉「当前渲染行」的采集统计:卡面 6 行 / 全量弹窗当前页。命中缓存零请求;
 * 未命中并发 4 逐个取(每次完成触发一次重渲染,行内 … → 真数逐行点亮)。
 * 读取失败不缓存(行保持 …,下次可见自动重试),绝不摆 0 冒充。
 */
export function useKolEvidenceStats(apiToken: string, poolIds: number[]): ReadonlyMap<number, KolEvidenceStats> {
  const [, setTick] = React.useState(0);
  const key = poolIds.join(",");
  React.useEffect(() => {
    if (!apiToken || !key) return;
    const wanted = key.split(",").map((raw) => Number(raw)).filter((id) => Number.isFinite(id) && id > 0);
    const queue = wanted.filter((id) => !STATS_CACHE.has(id) && !STATS_INFLIGHT.has(id));
    if (!queue.length) return;
    let alive = true;
    queue.forEach((id) => STATS_INFLIGHT.add(id));
    const worker = async () => {
      for (;;) {
        const id = queue.shift();
        if (id == null) return;
        try {
          const resp = await getMyKolPoolVideos(apiToken, id, 200);
          const s = summarizeKolVideos(Array.isArray(resp.items) ? resp.items : []);
          STATS_CACHE.set(id, {
            videoCount: s.classified.length,
            viewsTotal: s.viewsTotal,
            measuredCount: s.measuredCount,
            analyzedCount: s.analyzedCount,
          });
        } catch {
          /* 失败不缓存:行保持 …(下次可见重试),绝不编数 */
        } finally {
          STATS_INFLIGHT.delete(id);
        }
        if (alive) setTick((t) => t + 1);
      }
    };
    void Promise.all(Array.from({ length: Math.min(STATS_CONCURRENCY, queue.length) }, () => worker()));
    return () => {
      alive = false;
    };
  }, [apiToken, key]);
  return STATS_CACHE;
}

/** 行尾采集列文案:读取中=… / 0 采集=— / 有数=「N 视频 · 播放 X · 深析 n/N」 */
export function evidenceStatText(stats: KolEvidenceStats | undefined): string {
  if (!stats) return "…";
  if (stats.videoCount === 0) return "—";
  return `${stats.videoCount} 视频 · ${fmtZhCompact(stats.viewsTotal)} 播放 · 深析 ${stats.analyzedCount}/${stats.videoCount}`;
}

/* ============ ② 追踪链分区(GOAFFPRO 共享件原样内嵌;功能零改动) ============ */

// 共享件自带 px-5 py-2.5 + 底边线(抽屉语境的壳)——分区卡内压平,内容零改动。
const GOAFFPRO_TRIM = "[&>div]:!border-0 [&>div]:!px-0 [&>div]:!py-0";

export function GoaffproTrackSection({ apiToken, kolPoolId }: { apiToken: string; kolPoolId: number }) {
  return (
    <div className="mb-[22px]">
      <SectionLabel>追踪链 · GOAFFPRO</SectionLabel>
      <div className={`rounded-[11px] border border-line bg-panel px-3.5 py-3 ${GOAFFPRO_TRIM}`}>
        <GoaffproLinkSection apiToken={apiToken} kolPoolId={kolPoolId} />
      </div>
    </div>
  );
}

/* ============ ③ 合作项目结果(assignments 真阶段 × Projects 映射曝光/证据) ============ */

// 旧右栏同款读数口径:0/无效 → —(views=0 多为链路未回填,不当真零)。
function coopNum(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function CoopResultsSection({
  assignments,
  projects,
}: {
  /** 行内项目链(aggregate 直连 assignments,真阶段) */
  assignments: KolLibraryRowProject[];
  /** Projects 板块同一份映射(views / evidenceCount),用来补曝光与证据读数 */
  projects: VkpiProjectRow[];
}) {
  const byId = React.useMemo(() => {
    const map = new Map<string, VkpiProjectRow>();
    projects.forEach((project) => map.set(String(project.id), project));
    return map;
  }, [projects]);
  return (
    <div className="mb-[22px]">
      <SectionLabel>合作项目结果 ×{assignments.length}</SectionLabel>
      {assignments.length === 0 ? (
        <div className="rounded-[9px] border border-dashed border-line px-3 py-2.5 text-[11px] text-muted">
          暂无合作项目——底部动作排「入项目」可把该 KOL 挂进项目推进。
        </div>
      ) : (
        <div>
          {assignments.slice(0, 6).map((asg, i) => {
            const matched = asg.project_id != null ? byId.get(String(asg.project_id)) : undefined;
            return (
              <div
                key={`${asg.project_id}-${i}`}
                className="flex items-center justify-between gap-3 border-b border-line py-2 text-[12px] last:border-0"
              >
                <span className="min-w-0 truncate text-ink-2">
                  {String(asg.project_name || matched?.campaign || `项目 ${asg.project_id}`)}
                </span>
                <span
                  className="flex-none font-mono text-[10px] text-muted"
                  title="阶段=项目指派真值;曝光/证据=Projects 板块同一份映射,未回填读数如实显 —"
                >
                  阶段 {String(asg.stage || "—")} · 曝光 {matched ? coopNum(matched.views) : "—"} · 证据{" "}
                  {matched ? String(matched.evidenceCount ?? matched.stageEventCount ?? 0) : "—"}
                </span>
              </div>
            );
          })}
          {assignments.length > 6 ? (
            <div className="mt-1 text-[9.5px] text-muted">…其余 {assignments.length - 6} 个项目在 Projects 板块查看</div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ============ ④ V 相关内容(全量视频网格:五 tabs + 汇总条 + 三档徽 + 记录预览) ============ */

export type VideoSortKey = "latest" | "views" | "likes" | "comments" | "shares";

const VIDEO_TABS: Array<{ key: VideoSortKey; label: string }> = [
  { key: "latest", label: "最新" },
  { key: "views", label: "播放" },
  { key: "likes", label: "点赞" },
  { key: "comments", label: "评论" },
  { key: "shares", label: "分享" },
];

// 指标排序:数值 desc,NULL(未实测/未采集)一律排最后 —— 不当 0 混序。
function videoMetric(video: VkpiKolPoolVideoRow, key: VideoSortKey): number {
  const raw =
    key === "views" ? video.view_count : key === "likes" ? video.like_count : key === "comments" ? video.comment_count : video.share_count;
  return raw != null && Number.isFinite(Number(raw)) ? Number(raw) : -1;
}

export function sortVideosByTab(classified: ClassifiedVideo[], vOnly: boolean, tab: VideoSortKey): ClassifiedVideo[] {
  const base = vOnly ? classified.filter(({ tier }) => tier !== "undetermined") : [...classified];
  if (tab === "latest") {
    base.sort((a, b) =>
      String(b.video.publish_date || b.video.posted_at || "").localeCompare(String(a.video.publish_date || a.video.posted_at || "")),
    );
  } else {
    base.sort((a, b) => videoMetric(b.video, tab) - videoMetric(a.video, tab));
  }
  return base;
}

function KolVideoThumbnail({ rawUrl, title }: { rawUrl: string; title: string }) {
  const src = proxiedImageUrl(rawUrl);
  const [failed, setFailed] = React.useState(!src);
  React.useEffect(() => setFailed(!src), [src]);

  if (!src || failed) {
    return <span className="text-[16px] text-muted" title={`${title} · 缩略图暂不可用`}>▶</span>;
  }
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      className="h-full w-full object-cover"
      onError={() => setFailed(true)}
      onLoad={(event) => {
        const image = event.currentTarget;
        if (image.naturalWidth <= 2 && image.naturalHeight <= 2) setFailed(true);
      }}
    />
  );
}

export function KolVideoSection({
  videos,
  queuedEvidence,
  busyKeys,
  onEnqueueOne,
}: {
  videos: VkpiKolPoolVideoRow[];
  /** 已入队深析的 evidence id(dialogs 持有,回执逻辑不搬家) */
  queuedEvidence: ReadonlySet<number>;
  /** 忙碌键(`deep:{eid}`),按钮禁用与 dialogs 同一份 */
  busyKeys: ReadonlySet<string>;
  onEnqueueOne: (video: VkpiKolPoolVideoRow) => void;
}) {
  const [tab, setTab] = React.useState<VideoSortKey>("latest");
  const [vOnly, setVOnly] = React.useState(false);
  const [recEvidence, setRecEvidence] = React.useState<VkpiKolPoolVideoRow | null>(null);
  const summary = React.useMemo(() => summarizeKolVideos(videos), [videos]);
  const { classified, unmeasuredCount, viewsTotal, vRelatedCount, analyzedCount } = summary;
  const likesTotal = React.useMemo(() => videos.reduce((sum, v) => sum + (Number(v.like_count) || 0), 0), [videos]);
  const commentsTotal = React.useMemo(() => videos.reduce((sum, v) => sum + (Number(v.comment_count) || 0), 0), [videos]);
  const shown = React.useMemo(() => sortVideosByTab(classified, vOnly, tab), [classified, vOnly, tab]);
  return (
    <div>
      {/* 汇总条:全部真实算(播放合计只算实测,NULL 条数如实注明) */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted">
        <span>
          {classified.length} 条视频 · 实测播放合计 {viewsTotal.toLocaleString()}
          {unmeasuredCount > 0 ? `(${unmeasuredCount} 条未实测已剔除)` : ""} · V 相关 {vRelatedCount} 条 · 已深析 {analyzedCount} 条 · ♥{" "}
          {likesTotal.toLocaleString()} · 💬 {commentsTotal.toLocaleString()}
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className={`${CHIP} ${vOnly ? CHIP_ON : CHIP_OFF}`}
            onClick={() => setVOnly((v) => !v)}
            title="只看合作产出与标题提及V(未判定隐藏)"
          >
            仅 V 相关
          </button>
          {VIDEO_TABS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`${CHIP} ${tab === option.key ? CHIP_ON : CHIP_OFF}`}
              onClick={() => setTab(option.key)}
              title={option.key === "latest" ? "按发布时间排序" : `按${option.label}排序(未采集读数排最后)`}
            >
              {option.label}
            </button>
          ))}
        </span>
      </div>
      {shown.length === 0 ? (
        <div className="px-3 py-4 text-center text-[12px] text-muted">该筛选下 0 条(仅 V 相关开启)——诚实空。</div>
      ) : (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          {shown.map(({ video, tier }) => {
            const eid = Number(video.evidence_id ?? video.id) || 0;
            const thumb = String(video.cached_thumbnail_url || video.thumbnail_url || "");
            const title = String(video.title || video.video_title || "未命名视频");
            const meta = V_TIER_META[tier];
            return (
              <div key={eid} className="overflow-hidden rounded-[11px] border border-line bg-panel">
                <div className="grid h-[84px] w-full place-items-center overflow-hidden bg-card">
                  <KolVideoThumbnail rawUrl={thumb} title={title} />
                </div>
                <div className="px-2.5 py-2">
                  <div className="truncate text-[11px] text-ink" title={title}>{title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-muted">
                    <span title={video.view_count == null ? "未实测(≠ 0 播放)" : "播放(点时实测)"}>
                      ▶ {video.view_count != null ? Number(video.view_count).toLocaleString() : "未实测"}
                    </span>
                    <span>♥ {Number(video.like_count ?? 0).toLocaleString()}</span>
                    <span>💬 {Number(video.comment_count ?? 0).toLocaleString()}</span>
                    <span title="分享(点时实测;来源未回传按 0 计)">⤴ {Number(video.share_count ?? 0).toLocaleString()}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1">
                    <span className={`rounded-[5px] border px-1 py-px text-[8px] font-bold ${meta.cls}`}>{meta.label}</span>
                    {video.has_final_v1_cache ? <span className={`${MINI_BADGE} border-good bg-good-soft text-good`}>已深析</span> : null}
                    {video.content_url ? (
                      <a
                        className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none"
                        href={String(video.content_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="直跳原帖"
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        ↗
                      </a>
                    ) : null}
                    <button
                      type="button"
                      className="rounded-[5px] border border-line px-1 py-px font-mono text-[8px] text-muted transition-colors hover:border-accent hover:text-accent"
                      title="库记录预览"
                      onClick={() => setRecEvidence((prev) => ((prev?.evidence_id ?? prev?.id) === eid ? null : video))}
                    >
                      #{eid}
                    </button>
                    {tier === "undetermined" && !video.has_final_v1_cache && !isImageKindVideo(video) ? (
                      <button
                        type="button"
                        className="rounded-[5px] border border-line px-1 py-px text-[8px] text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-default"
                        disabled={queuedEvidence.has(eid) || busyKeys.has(`deep:${eid}`)}
                        title="未判定视频一键入队深析(端点真实返回才标已入队)"
                        onClick={() => onEnqueueOne(video)}
                      >
                        {queuedEvidence.has(eid) ? "已入队" : busyKeys.has(`deep:${eid}`) ? "入队中…" : "深析"}
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {recEvidence ? <RecordPreview title="库记录预览 · 点其他 #id 切换" rows={videoRecordRows(recEvidence)} /> : null}
    </div>
  );
}
