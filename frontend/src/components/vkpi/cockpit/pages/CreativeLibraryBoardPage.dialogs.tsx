import React from "react";
import { ProvChain, RecordPreview, type ProvStep } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import { ModalShell, SectionLabel, Drow, platformBadge } from "./MarketVoicePage.dialogs";
import { SegmentThumb, Tag, TAG_TONE, fmtNum, segmentTypeMeta } from "./CreativeLibraryBoardPage.modules";
import type { CreativeSegmentItem } from "../../../../services/vkpi/creativeLibrary-api";

// 创意资产库 · 弹窗族(金样板 = MarketVoicePage.dialogs 同构;弹窗骨架/分区件直接
// 复用金样板 ModalShell/SectionLabel/Drow,零自造样式)。
//   SegmentListModal  全量列表(卡面 6 条之外在这里滚;端点单次封顶 200 如实标注,零假分页)
//   SegmentDetailModal 单条详情:‹ #n/N › + ↑↓ 连续翻 + 缩略图(毒缓存自愈链)+
//     标签行 + 数值行 + 溯源链(每跳可点:原帖 ↗ / KOL 档案跳 / 深析缓存 / 库记录 /
//     段级索引,库节点开 RecordPreview 真字段)+ 底部真动作(原帖外链 / 打开 KOL 档案)。
//     白名单使用权请求 = 外联模板接线后续刀 → 如实注脚,不摆假按钮。
//   CreativeProvModal 模块溯源(SrcChip 点开):口径行 + 通用溯源链(真表名:
//     vkpi_kol_video_evidence → vkpi_analysis_cache(final_v1)→ 实时段级索引,
//     无独立聚合表如实标)+ 底部「查看底层样本」跳段级全量。
// 红线:零直连网络(取数/跳转走调用方回调);不触 viltrox_fit_score / rule_v0;
//        颜色全 token 类零写死色;零 opacity 修饰类;时间戳 = 绝对时间(存 UTC,
//        显示按浏览器时区 formatLocal;UTC 原值进记录预览,禁相对时间)。

// 禁 opacity 修饰类纪律:disabled 态用 token 降级(ink-2 → muted),不走透明度
const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:text-muted disabled:hover:border-line";

/* ============ 全量列表弹窗(单次全量,封顶如实) ============ */
export function SegmentListModal({
  matched,
  loadedCount,
  capNote,
  onClose,
  children,
}: {
  matched: number;
  loadedCount: number;
  /** matched > loaded 时的封顶说明(端点单次封顶,无 offset 分页 —— 如实,零假「载入更多」) */
  capNote?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <ModalShell
      title="段级条目 · 全量"
      sub={`命中 ${matched} 段 · 已加载 ${loadedCount} 段 · 点单条看详情(详情内 ↑↓ 连续翻)· 每条「↗」直跳来源视频`}
      onClose={onClose}
    >
      <SectionLabel>全量条目(按所属视频播放数降序)</SectionLabel>
      {children}
      {capNote ? <div className="mt-2.5 rounded-lg border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-muted">{capNote}</div> : null}
    </ModalShell>
  );
}

/* ============ 单条详情:‹ #n/N › + ↑↓ 连续翻 + 溯源链 ============ */

function segmentRecordRows(item: CreativeSegmentItem, rec: string): Array<[string, string]> {
  const eid = item.source.evidence_id != null ? `#${item.source.evidence_id}` : "—";
  if (rec === "cache") {
    return [
      ["表", "vkpi_analysis_cache"],
      ["target_type", "video"],
      ["target_id", eid],
      ["derive_method", item.source.derive_method || "—"],
      ["status", "ready(仅 ready 入索引)"],
      ["层路径", item.source.layer || "—"],
      ["说明", "段描述原文回查此表 result JSON 即得"],
    ];
  }
  if (rec === "evidence") {
    return [
      ["表", "vkpi_kol_video_evidence"],
      ["id", eid],
      ["platform", item.video.platform || "—"],
      ["view_count", item.video.view_count != null ? item.video.view_count.toLocaleString() : "未实测(≠ 0)"],
      ["posted_at(UTC)", item.video.posted_at || "—"],
      ["content_url", item.video.content_url || "—"],
      ["kol_pool_id", item.kol.kol_pool_id != null ? `#${item.kol.kol_pool_id}` : "—"],
      ["缩略图链", item.video.cached_thumbnail_url ? "本地缓存(自愈后真图)" : item.video.thumbnail_url ? "原始 URL(经 image-proxy)" : item.video.youtube_thumbnail_url ? "youtube 派生" : "三路皆无 · 诚实占位"],
    ];
  }
  return [
    ["segment_id", item.segment_id || "—"],
    ["段型", segmentTypeMeta(item.segment_type).label],
    ["片内时间戳", item.timestamp || "—"],
    ["方法", "lexicon_segments_v1 · 词表分类零 LLM"],
    ["聚合表", "无独立聚合表 · 请求时实时拆解"],
  ];
}

export function SegmentDetailModal({
  item,
  index,
  total,
  onNav,
  onClose,
  onOpenKol,
}: {
  item: CreativeSegmentItem;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  /** 溯源身份跳:kol_pool_id → KOL 档案页(page 层管道);缺省 = 身份节点纯文本不可点 */
  onOpenKol?: () => void;
}) {
  const [rec, setRec] = React.useState<string | null>(null);

  // 切条时收起记录预览(每条用自己的记录链)
  React.useEffect(() => {
    setRec(null);
  }, [item?.segment_id]);

  // ↑↓(以及 ←→)方向键连续翻(金样板同款);Escape 交给 ModalShell
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
        ev.preventDefault();
        onNav(index + 1);
      } else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        onNav(index - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, onNav]);

  const meta = segmentTypeMeta(item.segment_type);
  const kolName = item.kol.display_name || item.kol.handle || (item.kol.kol_pool_id != null ? `KOL #${item.kol.kol_pool_id}` : "—");
  const uniqueVideoStyles = item.video_styles.filter((t) => !item.styles.some((s) => s.key === t.key));

  const steps: ProvStep[] = [];
  if (item.video.content_url) steps.push({ label: `原帖 · ${platformBadge(item.video.platform)}`, href: item.video.content_url });
  else steps.push({ label: "原帖 URL 缺失(evidence 未存链接)" });
  steps.push(
    onOpenKol && item.kol.kol_pool_id != null
      ? { label: `KOL ${kolName} →跳档案`, rec: "identity" }
      : { label: `KOL ${kolName}` },
  );
  steps.push({ label: `库记录 vkpi_kol_video_evidence ${item.source.evidence_id != null ? `#${item.source.evidence_id}` : ""}`, rec: "evidence" });
  steps.push({ label: "深析缓存 vkpi_analysis_cache", rec: "cache" });
  steps.push({ label: "段级索引 · 实时词表拆解", rec: "segment" });

  return (
    <ModalShell
      title={`段级条目 · ${meta.label}`}
      sub={
        <>
          {platformBadge(item.video.platform)} · {kolName} · 发布{" "}
          {item.video.posted_at ? `${formatLocal(item.video.posted_at, { year: "numeric" })}(按浏览器时区)` : "时间未存"}
        </>
      }
      onClose={onClose}
    >
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <button type="button" className={NAV_BTN} disabled={index <= 0} onClick={() => onNav(index - 1)}>
          ‹ 上一条
        </button>
        <span className="font-mono text-[10.5px] font-bold text-accent">
          #{index + 1} / {total}
        </span>
        <button type="button" className={NAV_BTN} disabled={index >= total - 1} onClick={() => onNav(index + 1)}>
          下一条 ›
        </button>
        <button type="button" className={NAV_BTN} onClick={onClose}>
          ≡ 回列表
        </button>
        <span className="ml-auto font-mono text-[9px] text-muted">↑↓ 方向键连续翻</span>
      </div>

      {/* 来源视频卡:缩略图(毒缓存自愈链)+ 标题 + 平台 + 播放 */}
      <div className="mb-[22px] flex items-center gap-3 rounded-[11px] border border-line bg-panel px-3.5 py-2.5">
        <SegmentThumb video={item.video} className="h-[54px] w-[96px] rounded-[8px]" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] text-ink" title={item.video.title}>
            {item.video.title || "未命名视频"}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-muted">
            <span>{platformBadge(item.video.platform)}</span>
            <span title={item.video.view_count == null ? "未实测(≠ 0 播放)" : "播放(点时实测)"}>
              ▶ {item.video.view_count != null ? item.video.view_count.toLocaleString() : "未实测"}
            </span>
            {item.timestamp ? <span title="段落片内时间戳">⏱ {item.timestamp}</span> : null}
          </div>
        </div>
        {item.video.content_url ? (
          <a
            className="vkpi-prov-pchip vkpi-prov-pchip--ext flex-none"
            href={item.video.content_url}
            target="_blank"
            rel="noopener noreferrer"
            title="直跳来源视频"
          >
            原帖 ↗
          </a>
        ) : null}
      </div>

      <div className="mb-[22px]">
        <SectionLabel>段描述(深析原文索引)</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink-2">{item.description || "—"}</div>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>标签(词表命中 · 识别不了不硬贴)</SectionLabel>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`rounded-md border px-2 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>
          {item.opening_types.map((t) => (
            <Tag key={`o-${t.key}`} text={t.label} tone={TAG_TONE.opening} />
          ))}
          {item.styles.map((t) => (
            <Tag key={`s-${t.key}`} text={t.label} tone={TAG_TONE.style} />
          ))}
          {uniqueVideoStyles.map((t) => (
            <Tag key={`vs-${t.key}`} text={`全片:${t.label}`} tone={TAG_TONE.videoStyle} />
          ))}
          {item.focals.map((f) => (
            <Tag key={`f-${f}`} text={f} tone={TAG_TONE.focal} />
          ))}
          {item.opening_types.length + item.styles.length + uniqueVideoStyles.length + item.focals.length === 0 ? (
            <span className="text-[10.5px] text-muted">词表零命中 —— 如实无标签。</span>
          ) : null}
        </div>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>数值行</SectionLabel>
        <Drow k="所属视频播放" v={item.video.view_count != null ? `${fmtNum(item.video.view_count)} views` : "未实测(≠ 0)"} />
        <Drow k="平台" v={item.video.platform || "—"} />
        <Drow k="KOL" v={kolName} />
        <Drow k="发布(本地)" v={item.video.posted_at ? formatLocal(item.video.posted_at, { year: "numeric" }) : "—"} />
        <Drow k="posted_at(UTC)" v={item.video.posted_at || "—"} />
        <Drow k="片内时间戳" v={item.timestamp || "—"} />
      </div>

      <div>
        <SectionLabel>溯源链 · 每一跳可点(本条专属记录)</SectionLabel>
        <ProvChain
          steps={steps}
          onRecord={(key) => {
            // 身份节点点击 = 真跳转(KOL 档案页),其余节点开记录预览
            if (key === "identity") onOpenKol?.();
            else setRec(key);
          }}
        />
        {rec ? <RecordPreview title="库记录预览 · 点其他节点切换" rows={segmentRecordRows(item, rec)} /> : null}
      </div>

      {/* 闭环动作:原帖外链 / KOL 档案跳 = 真动作;白名单使用权请求 = 外联模板接线后续刀,
          如实注脚不摆假按钮(缺回调零假按钮纪律) */}
      <div className="mt-[22px] flex gap-2 border-t border-line pt-3.5">
        {item.video.content_url ? (
          <a
            href={item.video.content_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 rounded-lg border border-line px-3 py-2 text-center text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
          >
            ↗ 打开来源视频
          </a>
        ) : (
          <span className="flex-1 rounded-lg border border-dashed border-line px-3 py-2 text-center text-[11.5px] text-muted">原帖链接缺失</span>
        )}
        {onOpenKol && item.kol.kol_pool_id != null ? (
          <button
            type="button"
            onClick={onOpenKol}
            title={`打开 KOL 档案(vkpi_kol_pool #${item.kol.kol_pool_id})`}
            className="flex-1 rounded-lg border border-line px-3 py-2 text-center text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
          >
            打开 KOL 档案 →
          </button>
        ) : null}
      </div>
      <div className="mt-1.5 text-right text-[9.5px] text-muted">
        白名单使用权请求 · 外联模板接线 = 后续刀;本页只读检索,零写库
      </div>
    </ModalShell>
  );
}

/* ============ 模块溯源说明弹窗(SrcChip 点开):口径 + 通用链(真表名) ============ */
const GENERIC_CHAIN: ProvStep[] = [
  { label: "原帖 ↗ · 逐条见条目详情" },
  { label: "采集 · 深爬入库 vkpi_kol_video_evidence", rec: "collect" },
  { label: "深析 · vkpi_analysis_cache(六层视觉理解)", rec: "analyze" },
  { label: "段级索引 · 请求时实时拆解(无独立聚合表)", rec: "index" },
];

const GENERIC_RECS: Record<string, Array<[string, string]>> = {
  collect: [
    ["视频库", "vkpi_kol_video_evidence(标题/链接/播放/缩略图/发布时间)"],
    ["KOL 归属", "vkpi_kol_pool(kol_pool_id 外键)"],
    ["缩略图", "本地图缓存自愈(失败占位不作真图)→ 原始 URL → youtube 派生"],
  ],
  analyze: [
    ["表", "vkpi_analysis_cache"],
    ["derive_method", "video_analysis_final_v1(仅 status=ready 入索引)"],
    ["段来源", "layer1 分镜 timeline / 产品露出文本 + layer2 前3秒感受"],
  ],
  index: [
    ["方法", "lexicon_segments_v1 · 词表分类 · 零 LLM 零切片零写库"],
    ["端点", "GET /api/admin/vkpi/creative-segments/search"],
    ["聚合表", "无独立聚合表 · 请求时实时计算"],
    ["排序", "按所属视频播放数降序"],
  ],
};

export function CreativeProvModal({
  title,
  caliber,
  onClose,
  onOpenSamples,
}: {
  title: string;
  caliber: Array<[string, string]>;
  onClose: () => void;
  /** 底部「底层样本」按钮回调(跳段级条目全量弹窗);缺省 = 不渲染按钮 */
  onOpenSamples?: () => void;
}) {
  const [rec, setRec] = React.useState<string | null>(null);
  return (
    <ModalShell title={`${title} · 数据溯源`} sub="口径 + 来源表 + 通用溯源链(链上每跳可点)" onClose={onClose}>
      <div className="mb-[22px]">
        <SectionLabel>口径与来源表</SectionLabel>
        {caliber.map(([k, v], i) => (
          <Drow key={`${k}-${i}`} k={k} v={v} />
        ))}
      </div>
      <div>
        <SectionLabel>溯源链 · 通用(逐条溯源见条目详情)</SectionLabel>
        <ProvChain steps={GENERIC_CHAIN} onRecord={(key) => setRec(key)} />
        {rec ? <RecordPreview title="口径预览 · 点其他节点切换" rows={GENERIC_RECS[rec] || []} /> : null}
      </div>
      {onOpenSamples ? (
        <div className="mt-[22px] border-t border-line pt-3.5">
          <button
            type="button"
            onClick={onOpenSamples}
            className="w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
          >
            ≡ 查看底层样本 · 段级条目全量
          </button>
        </div>
      ) : null}
    </ModalShell>
  );
}
