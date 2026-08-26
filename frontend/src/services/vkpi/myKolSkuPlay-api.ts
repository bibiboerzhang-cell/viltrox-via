import { apiFetch } from "../http";
import { normalizeTaskState, type VkpiTaskState } from "./myKolVideoTasks";

// MY KOL 单品播放数据(波 D·C 车道前端 / B 车道后端 my_kol_sku_play_overview_v1):
//   GET  /api/admin/vkpi/my-kol/sku-play-overview
//        —— 被追踪视频按单品(SKU)聚合的播放总览(纯读;收藏 ∪ 授权共享口径,
//           员工恒看本人,管理层全团队,后端 scope 裁剪)。
// data-watch 的 POST 客户端与应答类型只有一份真源:myKolBoard-api.ts 的
// dataWatchMyKolVideo / VkpiDataWatchResponse(墙+详情两个入口都走它,别在本文件复刻)。
// 逐行「重新实测」同理:唯一写口是 myKolBoard-api.ts 的 refreshMyKolVideoMetrics
// (POST …/videos/{evidence_id}/refresh)——它只排队,不同步出数,门面必须如实说。
// 本文件只多带三样只读真值:refresh(任务态,契约同 my_kol_video_recovery_v1)、
// can_refresh(付费动作围栏投影:同事共享给你的红人只能看不能测)、
// refresh_cadence_hours(既有采样档位,用来判断「刚测过」)。
// 诚实口径:null = 未实测(绝不当 0 播放);total_views 只汇总实测过的视频;
// delta 需要窗口内 ≥2 次实测,不够 = null 如实待积累。全部字段按「缺席不渲染」读。

/** 播放增量窗口(d1/d7/d30 = 最近实测 − 窗口基线;null = 样本不足不编数)。 */
export interface SkuPlayDelta {
  d1?: number | null;
  d7?: number | null;
  d30?: number | null;
}

export interface SkuPlayItem {
  evidence_id: number;
  kol_pool_id: number;
  kol_name?: string;
  platform?: string;
  title?: string;
  content_url?: string;
  view_count?: number | null;
  like_count?: number | null;
  measured_at?: string | null;
  delta?: SkuPlayDelta;
  tracking_status?: "active" | "paused" | string;
  /** SKU 归属真值：manual=员工手选，detected=系统检测待确认，confirmed=已审核确认。 */
  link_relation_type?: "manual" | "detected" | "confirmed" | string;
  /** 这一行最近一次重新实测的任务态(排队/进行中/已完成/失败…);缺席 = 旧服务端。 */
  refresh?: VkpiTaskState;
  /** 能不能从这里发起重新实测(false = 只读,如同事共享给你的红人)。 */
  can_refresh?: boolean;
  /** can_refresh=false 时的机器码原因,门面另做人话映射。 */
  refresh_forbidden_reason?: string | null;
  /** 该视频的既有采样间隔(小时);实测时刻在这个窗口内即视为「刚测过」。 */
  refresh_cadence_hours?: number;
  recently_measured?: boolean;
}

export interface SkuPlayGroup {
  sku_code: string;
  sku_name?: string;
  videos?: number;
  kols?: number;
  latest_measured_at?: string | null;
  total_views?: number | null;
  delta?: SkuPlayDelta;
  items?: SkuPlayItem[];
  /** 本单品下当前账号可发起重新实测的视频条数(服务端按围栏实算,前端不再猜)。 */
  refreshable_videos?: number;
  /** 本单品下还在路上(排队/进行中/重试中)的条数。 */
  in_flight_videos?: number;
}

export interface VkpiMyKolSkuPlayOverviewResponse {
  contract?: string;
  generated_at?: string | null;
  summary?: {
    skus?: number;
    videos?: number;
    kols?: number;
    measured_videos?: number;
  };
  groups?: SkuPlayGroup[];
  truncated?: boolean;
  empty_reason?: "no_tracked_sku_videos" | string | null;
}

export async function fetchSkuPlayOverview(token: string) {
  return apiFetch<VkpiMyKolSkuPlayOverviewResponse>("/api/admin/vkpi/my-kol/sku-play-overview", {}, token);
}

/** 逐行任务态归一:缺席一律落「未发起」,绝不把缺席当完成。 */
export function skuPlayRefreshState(item: SkuPlayItem): VkpiTaskState {
  return normalizeTaskState(item.refresh);
}

/** can_refresh=false 的人话原因(门面禁内部词;未知码只说「不可发起」不编故事)。 */
export function skuPlayRefreshBlockText(reason: string | null | undefined): string {
  const key = String(reason || "").trim();
  if (key === "my_kol_paid_action_write_forbidden") return "这是同事分享给你的红人,只能查看,不能从这里重新实测";
  if (key === "vkpi_write_permission_required") return "你的账号没有发起实测的权限";
  if (key === "staff_identity_required") return "当前登录身份无法发起实测";
  if (key === "my_kol_target_read_forbidden" || key === "kol_pool_not_found") return "这个红人已不在你的可见范围内";
  return "当前账号不能从这里重新实测";
}

/** 实测读数文案:有值 → 「1,234」;null → 「未实测」(未实测 ≠ 0 播放)。 */
export function skuPlayCountText(value: number | null | undefined): string {
  if (value != null && Number.isFinite(Number(value))) return Number(value).toLocaleString();
  return "未实测";
}

/* ============ 逐条/逐单品「重新实测」的报价 + 派活(2026-08-25 补服务端硬闸)============
   GET  /api/admin/vkpi/my-kol/sku-play-refresh/plan  报价:这次要去平台取几次数(纯读,零花费)
   POST /api/admin/vkpi/my-kol/sku-play-refresh       派活:唯一花钱的一步,必须带报价指纹
   三道闸(单次上限 / 每日上限 / 冷却)全部由**服务端**判定,本文件只负责把服务端算出来的
   数字与原因如实显示;绕开界面也拿不到无上限的批量取数。计量单位 = 真实取数次数
   (一条视频一次,后端已核实),不写「各取一次」再让人自己猜。 */

/** 被闸挡下的稳定原因码(门面另做人话映射,未知码归统一兜底句)。 */
export type SkuPlayRefreshSkipReason =
  | "shared_readonly"
  | "already_in_flight"
  | "recently_measured"
  | "daily_cap"
  | "per_click_cap"
  | string;

export interface SkuPlayRefreshTarget {
  evidence_id: number;
  kol_pool_id: number;
  kol_name?: string;
  platform?: string;
  title?: string;
}

export interface SkuPlayRefreshSkipItem extends SkuPlayRefreshTarget {
  reason: SkuPlayRefreshSkipReason;
}

export interface SkuPlayRefreshLimits {
  per_click: number;
  daily: number;
  daily_used: number;
  daily_left: number;
  cooldown_hours: number;
}

export interface SkuPlayRefreshPlan {
  status?: string;
  sku_code?: string;
  evidence_id?: number | null;
  planned_count?: number;
  planned?: SkuPlayRefreshTarget[];
  /** 一条视频一次取数(后端硬承诺)。 */
  fetch_per_video?: number;
  /** 这次真正会发生的取数次数 = planned_count × fetch_per_video。 */
  fetch_calls_total?: number;
  requires_confirmation?: boolean;
  skipped?: Partial<Record<SkuPlayRefreshSkipReason, SkuPlayRefreshSkipItem[]>>;
  skipped_counts?: Partial<Record<SkuPlayRefreshSkipReason, number>>;
  candidates_total?: number;
  candidates_truncated?: boolean;
  limits?: SkuPlayRefreshLimits;
  plan_hash?: string;
}

export interface SkuPlayRefreshResult {
  status?: "dispatched" | "nothing_to_fetch" | string;
  plan?: SkuPlayRefreshPlan;
  queued?: SkuPlayRefreshTarget[];
  already_queued?: SkuPlayRefreshTarget[];
  failed?: (SkuPlayRefreshTarget & { reason?: string })[];
  counts?: { planned?: number; queued?: number; already_queued?: number; failed?: number };
  provider_calls_performed?: boolean;
}

/** 报价(纯读,零花费):可安全反复调用,确认框里的每个数字都来自这里。 */
export async function fetchSkuPlayRefreshPlan(token: string, skuCode: string, evidenceId?: number) {
  const params = new URLSearchParams({ sku_code: String(skuCode) });
  if (evidenceId && evidenceId > 0) params.set("evidence_id", String(evidenceId));
  return apiFetch<SkuPlayRefreshPlan>(
    `/api/admin/vkpi/my-kol/sku-play-refresh/plan?${params.toString()}`,
    {},
    token,
  );
}

/** 派活(唯一花钱的一步):报价指纹 + 条数一起回传,服务端重算比对,对不上一条都不派。 */
export async function runSkuPlayRefresh(
  token: string,
  body: { sku_code: string; evidence_id?: number; plan_hash: string; expected_count: number },
) {
  return apiFetch<SkuPlayRefreshResult>(
    "/api/admin/vkpi/my-kol/sku-play-refresh",
    { method: "POST", body: JSON.stringify(body) },
    token,
  );
}

/** 被闸挡下的人话(数字一律来自服务端 limits,前端不写死上限)。 */
export function skuPlayRefreshSkipText(
  reason: SkuPlayRefreshSkipReason,
  limits?: SkuPlayRefreshLimits,
): string {
  const key = String(reason || "").trim();
  if (key === "already_in_flight") return "已经在队列里,不会重复排";
  if (key === "recently_measured") {
    const hours = Number(limits?.cooldown_hours) || 0;
    return hours > 0 ? `刚测过不久,${hours} 小时内不重复取数` : "刚测过不久,这次跳过";
  }
  if (key === "daily_cap") {
    const daily = Number(limits?.daily) || 0;
    return daily > 0 ? `今天的取数额度用完了(每天最多 ${daily} 条)` : "今天的取数额度用完了";
  }
  if (key === "per_click_cap") {
    const cap = Number(limits?.per_click) || 0;
    return cap > 0 ? `超过单次上限(一次最多 ${cap} 条)` : "超过单次上限";
  }
  if (key === "shared_readonly" || key === "my_kol_paid_action_write_forbidden") {
    return "同事分享给你的,只能查看";
  }
  return "这次不去取";
}

/** 派活失败 / 请求失败的人话;未知码一律归统一兜底句,绝不把机器码打到门面上。 */
export function skuPlayRefreshFailText(reason: string | null | undefined): string {
  const key = String(reason || "").trim();
  if (key === "my_kol_paid_action_write_forbidden" || key === "my_kol_video_write_forbidden") {
    return "没有权限";
  }
  if (key === "vkpi_write_permission_required" || key === "staff_identity_required") {
    return "当前账号没有发起实测的权限";
  }
  if (key === "video_evidence_not_found" || key === "video_evidence_id_invalid") {
    return "这条视频已经不在库里";
  }
  if (key === "video_metric_platform_unsupported" || key === "video_url_unsupported") {
    return "这个平台的视频暂时取不到播放数据";
  }
  if (key === "video_evidence_identity_invalid" || key === "video_evidence_target_mismatch") {
    return "这条视频的地址对不上,先核对一下链接";
  }
  if (key === "sku_play_refresh_plan_drifted" || key === "sku_play_refresh_plan_required") {
    return "这次要取的条数刚刚变了,请重新点一次看最新数字";
  }
  return "原因暂时说不清,可以稍后再试";
}
