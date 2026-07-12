import React from "react";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import type { CreativeSegmentItem, CreativeSegmentVideo } from "../../../../services/vkpi/creativeLibrary-api";

// 创意资产库 · 板块页范式辅助件(CreativeLibraryBoardPage 专用,页内拆件不入公共桶;
// 金样板 = MarketVoicePage.modules / MyKolBoardPage.modules 同构)。
//   本文件住:模块 SrcChip 口径注册表(真表名)/ 段型·标签徽元数据(全 token 色)/
//   三路筛选条(旧页功能零丢失:自由词防抖在 page 层,拍法下拉吃 facets 真计数,
//   焦段输入带库内最多提示)/ 段级条目行(卡面与全量弹窗共用)/ 缩略图件。
//   缩略图 = 后端毒缓存自愈链产物(cached_image_url 语义:失败占位 SVG 不算真图):
//   best_thumbnail(cached → raw → youtube 派生)→ 前端 proxiedImageUrl 兜受墙 CDN;
//   onError / 三路皆无 → 诚实 ▶ 占位,绝不编图。
// 红线:本文件零直连网络(取数/动作全在 page 层);不触 viltrox_fit_score / rule_v0;
//        颜色全 token 类零写死色;零 opacity 修饰类;时间只走绝对时间戳(page/dialogs)。

/* ============ 每模块 SrcChip 口径(label=真实表名;rows=侦察核实口径,禁编造;
   旧页头两行长句 + 页脚 note/generated_at 全部收进这里 —— 卡面去术语,口径不丢) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiC: {
    label: "creative-segments · 实时索引",
    rows: [
      ["方法", "词表拆段索引(lexicon_segments_v1)· 零 LLM · 零切片文件 · 零写库"],
      ["深析缓存", "vkpi_analysis_cache(derive_method=video_analysis_final_v1 · status=ready)"],
      ["视频库", "vkpi_kol_video_evidence(播放/链接/缩略图/发布时间)"],
      ["KOL 归属", "vkpi_kol_pool(covered = 扫描窗口 distinct kol_pool_id)"],
      ["端点", "GET /api/admin/vkpi/creative-segments/search(请求时实时拆解,无独立聚合表)"],
      ["使用权", "白名单使用权请求进外联模板 = 后续刀;本页只读检索"],
      ["独立性", "独立展示信号,不参与 V6 Fit 评分"],
    ],
  },
  segments: {
    label: "vkpi_analysis_cache · 段级索引",
    rows: [
      ["开头段", "scene_timeline 首镜 + layer2 前3秒感受 → 开头类型词表分类"],
      ["分镜段", "scene_timeline 每镜(what / timestamp / why_it_matters)→ 拍法词表标签"],
      ["产品露出段", "product_presence + brand_exposure 描述文本"],
      ["排序", "按所属视频播放数降序(再 evidence_id / 段序稳定排序)"],
      ["溯源", "每条带 evidence_id + 层路径 + derive_method,回查 vkpi_analysis_cache 即得原文"],
      ["缩略图", "evidence.thumbnail_url → 本地缓存自愈(失败占位不作真图)→ youtube 派生"],
    ],
  },
  types: {
    label: "段级索引 · 段型构成",
    rows: [
      ["口径", "全索引过滤前计数(与 facets 同窗)"],
      ["开头段", "每视频至多 1 条(首镜 + 前3秒)"],
      ["分镜段", "timeline 每镜一条(单视频封顶 12 镜)"],
      ["产品露出段", "有露出描述文本的视频各 1 条"],
      ["点分段", "段型过滤在已加载条目内进行(端点无此参数,如实)"],
    ],
  },
  styles: {
    label: "SHOOTING_MODES 词表",
    rows: [
      ["词表", "signature_profile.SHOOTING_MODES(与深析画像同源,只 import 不复制)"],
      ["计数", "段级 styles + 全片 video_styles 合并去重(每段计一次)"],
      ["点行", "= 拍法精确过滤(端点 style 参数,键/中文标签均命中)"],
    ],
  },
  openings: {
    label: "OPENING_TYPES 词表",
    rows: [
      ["词表", "signature_profile.OPENING_TYPES(开头类型五类)"],
      ["计数", "开头段词表命中数(识别不了不硬贴 → 未分类不计)"],
      ["点行", "= 以标签词填入自由词检索(端点无 opening 参数,如实近似)"],
    ],
  },
  focals: {
    label: "焦段正则 · 6-800mm",
    rows: [
      ["提取", "段描述 + 视频标题正则(85mm / 85.5mm;负回顾防误抠)"],
      ["计数", "含该焦段的段级条目数 · facets 封顶 Top 24"],
      ["点行", "= 焦段精确过滤(端点 focal 参数)"],
    ],
  },
  health: {
    label: "creative-segments · 索引健康",
    rows: [
      ["扫描上限", "800 条深析视频(当前 ready 库量之上留余量)"],
      ["缩略图口径", "best_thumbnail 三链:本地缓存(自愈)→ 原始 URL → youtube 派生"],
      ["空态", "深析库空 = status:empty + 原因;过滤零命中 = matched 0,不硬凑"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiC: "资产总览",
  segments: "段级条目流",
  types: "段型构成",
  styles: "拍法构成",
  openings: "开头类型",
  focals: "焦段热度",
  health: "索引健康",
};

/* ============ 段型 / 标签徽元数据(旧页硬编码 sky/amber/violet/emerald → 全 token) ============ */
export const SEGMENT_TYPE_META: Record<string, { label: string; cls: string; color: string }> = {
  opening: { label: "开头段", cls: "border-accent bg-accent-soft text-accent", color: "var(--ds-accent)" },
  scene: { label: "分镜段", cls: "border-line bg-card text-ink-2", color: "var(--ds-accent-2)" },
  product_exposure: { label: "产品露出段", cls: "border-warn bg-warn-soft text-warn", color: "var(--ds-warn)" },
};

export function segmentTypeMeta(type: string) {
  return SEGMENT_TYPE_META[type] || { label: type || "段", cls: "border-line bg-card text-ink-2", color: "var(--ds-muted)" };
}

// 标签 tone(开头类型 / 拍法 / 全片拍法 / 焦段;旧页四色系 → token 四系)
export const TAG_TONE = {
  opening: "border-accent-2 text-accent-2",
  style: "border-good bg-good-soft text-good",
  videoStyle: "border-line bg-card text-ink-2",
  focal: "border-info bg-info-soft text-info",
} as const;

export function Tag({ text, tone }: { text: string; tone?: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${tone || "border-line bg-card text-ink-2"}`}>{text}</span>
  );
}

// 静态兜底(旧页零丢失:与后端 SHOOTING_MODES 词表同口径);facets 回来后用真实计数覆盖
export const STYLE_FALLBACK: Array<{ key: string; label: string }> = [
  { key: "cinematic_broll", label: "电影感B-roll" },
  { key: "talking_review", label: "口播评测" },
  { key: "pov", label: "POV第一视角" },
  { key: "tutorial", label: "教程演示" },
  { key: "street", label: "街拍实测" },
  { key: "unboxing", label: "开箱上手" },
  { key: "sample_showcase", label: "样片展示" },
];

export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

/* ============ 缩略图件:毒缓存自愈链读端(best_thumbnail = cached→raw→youtube;
   cached 为本地 /api/vkpi-media/image-cache/<digest>,proxiedImageUrl 原样放行;
   raw 受墙 CDN 由 proxiedImageUrl 转 image-proxy;加载失败 / 三路皆无 → 诚实 ▶ 占位) ============ */
export function SegmentThumb({ video, className }: { video: CreativeSegmentVideo; className: string }) {
  const src = proxiedImageUrl(
    String(video.best_thumbnail || video.cached_thumbnail_url || video.thumbnail_url || video.youtube_thumbnail_url || ""),
  );
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]);
  if (!src || failed) {
    return (
      <span
        className={`grid flex-none place-items-center bg-card text-[13px] text-muted ${className}`}
        title={failed ? "缩略图加载失败(不摆假图)" : "该视频无可用缩略图(evidence 未存 · 缓存无 · 非 youtube)"}
        aria-hidden="true"
      >
        ▶
      </span>
    );
  }
  return <img src={src} alt="" loading="lazy" className={`flex-none object-cover ${className}`} onError={() => setFailed(true)} />;
}

/* ============ 三路筛选条(旧页功能零丢失;受控值/回调全在 page 层,防抖也在 page 层) ============ */
export interface CreativeFilterState {
  query: string;
  style: string;
  focal: string;
}

export function FilterBar({
  filter,
  onChange,
  styleOptions,
  focalHint,
  typeFilter,
  onClearType,
}: {
  filter: CreativeFilterState;
  onChange: (next: CreativeFilterState) => void;
  styleOptions: Array<{ key: string; label: string; count?: number }>;
  /** 库里最多的焦段提示(facets 真值;空 = 不提示) */
  focalHint: string[];
  /** 段型过滤(段型构成环图点入;已加载条目内过滤,如实) */
  typeFilter: string;
  onClearType: () => void;
}) {
  const inputCls =
    "w-full rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-ink placeholder:text-muted outline-none focus:border-accent";
  return (
    <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-3">
      <input
        value={filter.query}
        onChange={(ev) => onChange({ ...filter, query: ev.target.value })}
        placeholder="自由词,如 悬念 / 对比 / bokeh / 夜景…"
        className={inputCls}
      />
      <select
        value={filter.style}
        onChange={(ev) => onChange({ ...filter, style: ev.target.value })}
        className={`${inputCls} [&>option]:bg-[var(--ds-card)]`}
      >
        <option value="">全部拍法</option>
        {styleOptions.map((opt) => (
          <option key={opt.key} value={opt.label}>
            {opt.label}
            {opt.count != null && opt.count > 0 ? `(${opt.count} 段)` : ""}
          </option>
        ))}
      </select>
      <div className="flex items-center gap-1.5">
        <input
          value={filter.focal}
          onChange={(ev) => onChange({ ...filter, focal: ev.target.value })}
          placeholder={`焦段,如 85mm${focalHint.length > 0 ? ` · 库里最多:${focalHint.slice(0, 3).join(" / ")}` : ""}`}
          className={inputCls}
        />
        {typeFilter ? (
          <button
            type="button"
            onClick={onClearType}
            title="段型过滤(段型构成环图点入 · 已加载条目内过滤)· 点击清除"
            className="flex-none rounded-full border border-accent bg-accent-soft px-2 py-1 text-[10px] text-accent transition-colors hover:border-accent-hover"
          >
            {segmentTypeMeta(typeFilter).label} ✕
          </button>
        ) : null}
      </div>
    </div>
  );
}

/* ============ 段级条目行(demo 一行一条;卡面 6 条与全量弹窗共用):
   缩略图 + 段型徽 + 时间戳 + 描述截断 + 焦段/拍法首标签 + views + 原帖 ↗ ============ */
export function SegmentRowLine({
  item,
  index,
  onOpen,
}: {
  item: CreativeSegmentItem;
  index: number;
  onOpen: (i: number) => void;
}) {
  const meta = segmentTypeMeta(item.segment_type);
  const firstStyle = item.styles[0] || item.video_styles[0] || null;
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-1.5 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={(ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onOpen(index);
        }
      }}
    >
      <SegmentThumb video={item.video} className="h-[26px] w-[46px] rounded-[6px]" />
      <span className={`flex-none rounded-[5px] border px-1.5 py-px text-[8.5px] font-bold ${meta.cls}`}>{meta.label}</span>
      {item.timestamp ? <span className="flex-none font-mono text-[9px] text-muted">{item.timestamp}</span> : null}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent" title={item.description}>
        {item.description || "—"}
      </span>
      {item.focals.length > 0 ? (
        <span className={`hidden flex-none rounded-full border px-1.5 py-px text-[8.5px] md:inline ${TAG_TONE.focal}`}>{item.focals[0]}</span>
      ) : null}
      {firstStyle ? (
        <span className={`hidden flex-none rounded-full border px-1.5 py-px text-[8.5px] lg:inline ${TAG_TONE.style}`}>{firstStyle.label}</span>
      ) : null}
      <span className="flex-none font-mono text-[9.5px] text-muted" title={item.video.view_count == null ? "所属视频未实测(≠ 0 播放)" : "所属视频播放(点时实测)"}>
        ▶ {item.video.view_count != null ? fmtNum(item.video.view_count) : "未实测"}
      </span>
      {item.video.content_url ? (
        <a
          className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none"
          href={item.video.content_url}
          target="_blank"
          rel="noopener noreferrer"
          title={`${item.video.platform || "原帖"} · 直跳来源视频`}
          onClick={(ev) => ev.stopPropagation()}
        >
          ↗
        </a>
      ) : null}
    </div>
  );
}
