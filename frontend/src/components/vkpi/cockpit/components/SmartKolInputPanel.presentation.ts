import {
  type VkpiKolRecallItem,
  type VkpiKolSearchHistoryItem,
} from "../../../../domains/kol";
import {
  asRecord,
  cleanText,
  display,
  numberLabel,
  type Row,
} from "./SmartKolInputPanel.helpers";
import { isSearchSessionTerminal } from "./SmartKolInputPanel.progress-derivers";

// 全网发现状态码 → 人话(面向营销人,不暴露 queued/running 等内部状态码)。
export function advanceStatusLabel(value: unknown): string {
  const status = cleanText(value).toLowerCase();
  if (["ready", "done"].includes(status)) return "已完成";
  if (status === "partial") return "部分完成";
  if (["failed", "blocked"].includes(status)) return "未完成";
  if (status === "running") return "查找中";
  return "排队中";
}

// 异步会话状态横幅(诚实显示 排队/查找中/已完成/部分完成/未完成):只读后端真有的字段——
// 会话级 status(planned/running/ready/partial/failed/cancelled)+ result_summary
// .smart_search_profile_advance_job 的 {status, advance_status, advance_counts, error}。
// failed 时把后端 error 原文(异常串)收进 note,而非吞掉看似空白卡死。后端未单独返回「AI 规划
// 失败已退基础检索」原因字段(planner 失败被静默兜底),故只能据真实 status/error 说明,不编造。
type SessionBanner = { tone: "info" | "ok" | "warn" | "error"; label: string; note: string } | null;

// 会话完成态摘要原料(只读后端真有字段):全网新发现 / 库内已有 / 库内召回三组人数。
// 优先 result_summary.new_discovery.counts(发现真账),缺则按会话项类型数;都缺 → 0(不编数)。
export function sessionDiscoveryTally(session: VkpiKolSearchHistoryItem | null): { newFaces: number; existing: number; recall: number } {
  if (!session) return { newFaces: 0, existing: 0, recall: 0 };
  const items = (Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.active_items) && session.active_items.length
      ? session.active_items
      : Array.isArray(session.items_preview) ? session.items_preview : []).map((item) => asRecord(item));
  const countType = (type: string) => items.filter((item) => cleanText(item.item_type) === type).length;
  const counts = asRecord(asRecord(asRecord(session.result_summary).new_discovery).counts);
  const fromCounts = (key: string) => (typeof counts[key] === "number" ? Math.max(0, Number(counts[key])) : null);
  return {
    newFaces: fromCounts("new_creators") ?? countType("new_creator"),
    existing: fromCounts("existing_matches") ?? countType("existing_kol"),
    recall: countType("recall_candidate"),
  };
}

function sessionTallyText(session: VkpiKolSearchHistoryItem | null): string {
  const tally = sessionDiscoveryTally(session);
  return [
    tally.newFaces > 0 ? `本次全网新发现 ${tally.newFaces} 人` : "",
    tally.existing > 0 ? `库内已有 ${tally.existing} 人` : "",
    tally.recall > 0 ? `库内召回 ${tally.recall} 人` : "",
  ].filter(Boolean).join(" · ");
}

export function sessionStatusBanner(
  session: VkpiKolSearchHistoryItem | null,
  advanceStatus: string,
  counts: Row,
  polling: boolean,
): SessionBanner {
  if (!session && !advanceStatus && !polling) return null;
  const summary = asRecord(session?.result_summary);
  const job = asRecord(summary.smart_search_profile_advance_job);
  const raw = cleanText(advanceStatus || job.advance_status || job.status || session?.status).toLowerCase();
  const jobError = cleanText(job.error);
  const ready = Number(counts.ready ?? 0);
  const failed = Number(counts.failed ?? 0) + Number(counts.errors ?? 0);
  // 完成态以会话本身为准(会话 1106 案):会话已 ready/complete 而任务状态字段仍是旧的
  // running、或展示端仍挂着轮询 id,都不得再显示「正在查找」——改成完成态摘要。
  const terminal = session ? isSearchSessionTerminal(session) : false;
  if (["failed", "blocked"].includes(raw) && ready <= 0) {
    return { tone: "error", label: "这次没找到结果", note: jobError ? `失败原因:${jobError}` : "查找未能完成,可调整描述或换个区域重试。" };
  }
  if (["partial"].includes(raw) || (failed > 0 && ready > 0)) {
    return {
      tone: "warn",
      label: "已找到部分结果",
      note: `${sessionTallyText(session) || "下方结果可直接查看"};${failed > 0
        ? `另有 ${failed} 个没跑完,可稍后重试补齐。`
        : "部分人选还在补全,完成后会自动更新。"}`,
    };
  }
  if (["ready", "done"].includes(raw) || (terminal && !["queued", "planned"].includes(raw))) {
    const tally = sessionTallyText(session);
    return { tone: "ok", label: "已找完", note: tally ? `${tally},见下方。` : "这次没有新的人选,可换个描述再试。" };
  }
  if (raw === "running" || (polling && !terminal)) {
    return { tone: "info", label: "正在查找", note: "后台正从所选平台找人,找到的会随时显示,无需等待。" };
  }
  return { tone: "info", label: "已排队", note: "已进入后台查找队列,稍候会自动开始。" };
}

export function historyKindLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  if (type === "url_video") return "视频 URL";
  if (type === "url_profile") return "账号 URL";
  if (type === "text_recall") return "查找";
  return "历史";
}

// 历史标签可读化:文字搜索显查询语;URL 搜索把裸链接解析成「平台 @handle / 平台 帖id」,
// 让一堆长得一样的 instagram.com/p/... 能区分、看懂搜的是谁/什么(用户:历史要备注搜索信息)。
const _RESERVED_PATH = new Set(["p", "reel", "reels", "shorts", "video", "watch", "videos"]);

export function historyLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  // 数据里很多是 query_type='unknown' 且 query_text 形如「账号分析 · https://…」——
  // 去掉中文前缀、从文本里抠出 URL 再解析,而不是死认 query_type。
  const stripped = raw.replace(/^[一-龥A-Za-z]+\s*·\s*/, "").trim();
  const urlMatch = stripped.match(/https?:\/\/\S+/) || raw.match(/https?:\/\/\S+/);
  // 「账号分析」意图优先于路径启发式(如 /@x/shorts 是主页 Shorts 栏,不是单条视频)
  const isAccount = type === "url_profile" || /账号分析/.test(raw);
  const looksVideo = !isAccount && (type === "url_video" || /视频分析/.test(raw) || /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw));
  if (!urlMatch) return display(raw, "未命名"); // 纯文字搜索:原文
  try {
    const u = new URL(urlMatch[0]);
    const host = u.hostname.replace(/^www\./, "");
    const plat = host.includes("instagram")
      ? "IG"
      : host.includes("tiktok")
        ? "TikTok"
        : host.includes("youtu")
          ? "YouTube"
          : (host.split(".")[0] || "URL");
    const parts = u.pathname.split("/").filter(Boolean);
    const handleSeg = parts.find((p) => p.startsWith("@"));
    const firstNamed = parts[0] && !_RESERVED_PATH.has(parts[0]) ? parts[0] : "";
    // 账号/主页:有 @handle 或首段是用户名 → 「平台 @handle」
    if (!looksVideo && (handleSeg || firstNamed)) {
      return `${plat} @${(handleSeg || firstNamed).replace(/^@/, "")}`;
    }
    // 视频/帖:youtube 取 v 参数,其余取末段 id
    const vid = u.searchParams.get("v");
    const id = vid || parts[parts.length - 1] || firstNamed || "";
    return `${plat} ${looksVideo ? "视频" : "帖"} ${id}`.trim();
  } catch {
    return display(raw, "未命名");
  }
}

export function relativeTime(value: unknown): string {
  const s = cleanText(value);
  if (!s) return "";
  const t = Date.parse(s);
  if (!Number.isFinite(t)) return "";
  const diff = Date.now() - t;
  if (diff < 60000) return "刚刚";
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

export function historyKindMeta(session: VkpiKolSearchHistoryItem): { label: string; cls: string } {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  const video = { label: "视频", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100/90" };
  const account = { label: "账号", cls: "border-violet-300/30 bg-violet-400/[0.10] text-violet-100/90" };
  if (type === "url_video" || /视频分析/.test(raw)) return video;
  if (type === "url_profile" || /账号分析/.test(raw)) return account;
  if (type === "text_recall") return { label: "找人", cls: "border-cyan-300/30 bg-cyan-400/[0.10] text-cyan-100/90" };
  // query_type='unknown' 但文本含 URL:按视频路径/否则账号 兜底归类
  if (/https?:\/\//.test(raw)) return /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw) ? video : account;
  return { label: "历史", cls: "border-white/[0.1] bg-white/[0.03] text-slate-300" };
}

export function historyStatusMeta(value: unknown): { label: string; cls: string; dot: string } {
  const label = advanceStatusLabel(value);
  if (label === "已完成") return { label, cls: "text-emerald-300/85", dot: "#34d399" };
  if (label === "部分完成") return { label, cls: "text-amber-300/85", dot: "#fbbf24" };
  if (label === "查找中") return { label, cls: "text-amber-300/85", dot: "#fbbf24" };
  if (label === "未完成") return { label, cls: "text-rose-300/85", dot: "#fb7185" };
  return { label, cls: "text-slate-500", dot: "#64748b" };
}

// 历史卡状态:优先读会话级诚实终态。不支持的平台链接标「不支持」,
// 不再显示「部分完成/未完成」诱导用户等待或重试。
// (bilibili/抖音/小红书视频链接自 2026-07-20 起走「仅内容分析」通道,不再落 unsupported。)
export function historySessionStatusMeta(session: VkpiKolSearchHistoryItem): { label: string; cls: string; dot: string } {
  if (cleanText(asRecord(session.result_summary).result_state) === "unsupported") {
    return { label: "不支持", cls: "text-slate-500", dot: "#64748b" };
  }
  return historyStatusMeta(session.status || "ready");
}

// 问题5 UI:相关度按名次分档(列表已按分排序,名次=相关度强弱),裸 score 进 title 供细看,
// 避免向量分都 <0.5 时全显「相关」无区分。
// demote:后端 relevance_tier_hint==="demote"(如视频向产品×纯平面摄影候选)时,封顶为「中相关」,
// 绝不显「高相关」——纯展示分档,不动召回侧排序与任何评分字段。
export function relevanceTier(score: number, demote = false): { label: string; cls: string; dot: string } {
  // 按真实相关度分值(0-1)分档:高≥0.6 / 中≥0.3 / 相关。demote 封顶「中相关」绝不显「高相关」。纯展示。
  if (score >= 0.6 && !demote) return { label: "高相关", cls: "border-emerald-300/35 bg-emerald-400/[0.10] text-emerald-100", dot: "#34d399" };
  if (score >= 0.3) return { label: "中相关", cls: "border-cyan-300/30 bg-cyan-400/[0.07] text-cyan-100", dot: "#22d3ee" };
  return { label: "相关", cls: "border-white/[0.08] bg-white/[0.02] text-slate-400", dot: "#64748b" };
}

// 内容契合判定 → 徽章(纯展示信号,读会话项透传的 content_fit;绝不并入/改写 viltrox_fit_score）。
// 已深析才显;verdict ∈ {fit/partial_fit/not_fit} 映射 适合/一般/不适合,不可识别则不渲染。
export function contentFitBadge(value: unknown): { label: string; cls: string } | null {
  const verdict = cleanText(value).toLowerCase();
  if (verdict === "fit") return { label: "适合", cls: "border-emerald-300/35 bg-emerald-400/[0.12] text-emerald-100" };
  if (verdict === "not_fit") return { label: "不适合", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100" };
  if (verdict === "partial_fit") return { label: "一般", cls: "border-amber-300/30 bg-amber-400/[0.10] text-amber-100" };
  return null;
}

// 预估曝光(说人话):读会话项 exposure_potential / avg_views / views,折成 K/M 量级。
// 纯展示触达潜力(终极=提升曝光/市场),不参与任何评分。
export function exposureLabel(item: VkpiKolRecallItem): string {
  const self = item as unknown as Row;
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const raw = Number(
    self.exposure_potential ?? src.exposure_potential ?? src.avg_views ?? src.views ?? 0,
  );
  return numberLabel(raw);
}

// 新人/新鲜标(纯展示,优先新人裁令):
//  - newcomer:全网发现 new_creator(无 kol_pool_id 未入库)= 优先新人主源。
//  - fresh:近 90 天有新作(published 时间戳)。
//  - lowCollab:历史无合作记录(无 historical_match / cooperation 计数为 0)。
export function freshnessMarks(item: VkpiKolRecallItem): { newcomer: boolean; fresh: boolean; lowCollab: boolean } {
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const newcomer = !Number(item.kol_pool_id) && cleanText(item.type_label) === "全网发现";
  let fresh = false;
  const published = cleanText(src.published);
  if (published) {
    const ts = Date.parse(published);
    if (Number.isFinite(ts)) fresh = Date.now() - ts <= 90 * 24 * 3600 * 1000;
  }
  const coop = Number(src.history_cooperation_count ?? src.cooperation_count ?? 0);
  const lowCollab = newcomer || (!src.historical_match && coop <= 0);
  return { newcomer, fresh, lowCollab };
}

// YouTube search.list 无 @handle → 后端用 UC... channel_id 占 handle(保证非空 + 当 identity:
// profile_url 走 /channel/{id})。但 UC... 不是可读名,卡片应显示真频道名(channel_name→display_name)。
const YT_CHANNEL_ID_RE = /^UC[A-Za-z0-9_-]{20,}$/;
export function readableCreatorName(item: VkpiKolRecallItem): string {
  const handle = cleanText(item.handle);
  const displayName = cleanText(item.display_name);
  // 优先非「频道ID」的 handle(IG/TikTok 的 @用户名)→ 再非「频道ID」的真频道名 → 兜底
  if (handle && !YT_CHANNEL_ID_RE.test(handle)) return handle;
  if (displayName && !YT_CHANNEL_ID_RE.test(displayName)) return displayName;
  // 身份修(2026-07-21):UC 开头的裸频道 ID 永远不当显示名。富化前的旧入库行再探一层
  // 会话 payload 里的真频道名;全无 → 诚实显示「YouTube 频道」(真名由回填脚本补)。
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const channelName = cleanText(src.channel_name);
  if (channelName && !YT_CHANNEL_ID_RE.test(channelName) && channelName.toLowerCase() !== "unknown creator") {
    return channelName;
  }
  return handle || displayName ? "YouTube 频道" : "";
}

// 契合命中 tags 中文化:海外创作者发现是英文搜索词命中,这里把常见摄影/创作术语映射成中文(生僻保留原文)。
const RELEVANCE_ZH: Record<string, string> = {
  wedding: "婚礼", portrait: "人像", landscape: "风光", wildlife: "野生动物", travel: "旅行",
  street: "街拍", fashion: "时尚", product: "产品", food: "美食", sports: "运动", event: "活动",
  macro: "微距", astro: "星空", documentary: "纪实", lifestyle: "生活方式", newborn: "新生儿",
  family: "家庭", commercial: "商业", editorial: "大片", "fine art": "艺术", boudoir: "闺房写真",
  flash: "闪光灯", strobe: "影室灯", lighting: "灯光", "studio lighting": "影室布光", studio: "影棚",
  softbox: "柔光箱", lens: "镜头", camera: "相机", tripod: "三脚架", gimbal: "稳定器", drone: "无人机",
  educator: "教学", reviewer: "测评", vlogger: "Vlog", filmmaker: "影片制作", cinematographer: "摄影指导",
  photographer: "摄影师", creator: "创作者", influencer: "达人", tutorial: "教程", "how-to": "教程",
  bts: "幕后", videography: "视频", cinematic: "电影感", color: "调色", "color grading": "调色",
};
export function zhTag(s: string): string {
  const k = String(s || "").trim().toLowerCase();
  return RELEVANCE_ZH[k] || s;
}

// A·下框:时间戳分镜 + 内容概述。读 final_v1 cache 的 layer1_visual_content.scene_timeline / content_summary。
// 复用 KOLVideoAnalysisPanel 的 sceneTimeline 行结构;缺则静默不渲染(降级,绝不报错占位)。
export function sceneTimelineRowsLocal(value: unknown, max = 8): { key: string; timestamp: string; what: string }[] {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    return {
      key: `${cleanText(record.timestamp) || "scene"}-${index}`,
      timestamp: cleanText(record.timestamp),
      what: cleanText(record.what ?? record.scene ?? record.content),
    };
  }).filter((row) => row.timestamp || row.what).slice(0, max);
}
