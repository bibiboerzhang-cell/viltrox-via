import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine } from "./MarketVoicePage.modules";
import { platformBadge } from "./MarketVoicePage.dialogs";
import { DistBars, asArray, asRow, fmtZhCompact, num, str, type Row } from "./KolProfileBoardPage.charts";

// KOL 档案 · 板块页范式辅助件(KolProfileBoardPage 专用,页内拆件不入公共桶)。
//   通用骨架件(ModuleCard/PendingCard/EmptyLine/ErrorCard/LoadingLine/KpiCard)直接
//   复用 MarketVoicePage.modules(金样板放行「引组件」);本文件只放档案页专属件:
//   MODULE_SOURCES 溯源注册表(label=真实端点/表名,rows=2026-07-12 本地实测口径,禁编造)/
//   IdentityBody 身份档案卡 / DeepBody 深析摘要 / CommentsBody 评论声音 /
//   MiniRows 未知契约防御渲染(受众画像估算附注用)。
// 红线:本文件零直连网络(取数住 page 层/内嵌组件自带);fit 分只读展示绝不写回;
//   颜色全 token 类零写死色;零 opacity 修饰类;诚实空态;卡面零技术术语
//   (表名/端点/提供方只进 SrcChip rows / tooltip)。

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiP: {
    label: "kol-pool/{id}/detail-bundle · vkpi_kol_pool",
    rows: [
      ["粉丝/均播/互动率", "vkpi_kol_pool 档案点时快照(抓取时刻读数,非时序)"],
      ["已深析视频", "vkpi_analysis_cache final 产物计数 / vkpi_kol_video_evidence 证据总数"],
      ["趋势线", "无逐 KOL 历史时序端点 → 四卡诚实虚线,零环比药丸,绝不编 series"],
      ["红线", "fit 分只读展示 · 本页零写库零打分"],
    ],
  },
  identity: {
    label: "vkpi_kol_pool · detail-bundle",
    rows: [
      ["主表", "vkpi_kol_pool(1,237 非重复行 · bio 1,042 / 邮箱 317 · 2026-07-12 本地实测)"],
      ["三秒读懂", "他是谁=primary_topic/content_style/bio · 什么最灵=深析结论 · 值多少钱=fit+合作状态+粉丝"],
      ["Fit 徽", "viltrox_fit_score 只读展示(998/1,237 已评)· 绝不写回"],
      ["动作", "收藏/联系/补全 → KOL 池抽屉(vkpi:open-kol-pool-item 事件桥,复用既有动作零重造)"],
    ],
  },
  credit: {
    label: "competing-activity · vkpi_brand_signal",
    rows: [
      ["竞业主源", "近 90 天竞品露出:深析产物 competitor_mentions/presence(视频×品牌去重)+ vkpi_brand_signal 按 URL 对齐"],
      ["竞业兜底", "主源不可用时回落已落库品牌关系(/competitors · 前端如实标注兜底口径)"],
      ["实测互动率", "vkpi_kol_pool.real_er 评论样本回算(124/1,237 已实测 · 2026-07-12)"],
      ["虚高嫌疑", "vkpi_kol_pool.suspect_inflation 判定行(1,151/1,237 已检)· 未检=如实「未跑」"],
    ],
  },
  signature: {
    label: "kol-pool/{id}/signature 纯聚合",
    rows: [
      ["聚合源", "深析产物(final_v1)+ vkpi_kol_video_evidence + 已入库评论 · 零新采集零模型调用"],
      ["三块", "擅长拍法(词表分类)/ 最爆代表作 TOP3(实测播放排序)/ 最引反馈 TOP3(评论数)"],
      ["点评", "代表作一句话点评=深析产物 verdict · 未深析视频如实标「暂无点评」"],
      ["红线", "独立展示信号 · 不参与 fit 评分"],
    ],
  },
  deep: {
    label: "vkpi_kol_llm_deep_analysis_results",
    rows: [
      ["深析表", "vkpi_kol_llm_deep_analysis_results(817 行 · 覆盖 330/1,237 KOL · 2026-07-12 本地实测)"],
      ["来源标注", "行级 provider/analysis_kind 字段(模型深析或本地抽取,SrcChip 动态行如实透出)"],
      ["置信度", "行级 confidence 字段原样展示 · 无置信=不摆徽"],
      ["诚实", "未深析 KOL =「未分析」灰态 · 绝不当 0 分/0 风险"],
    ],
  },
  videos: {
    label: "vkpi_analysis_cache · vkpi_kol_video_evidence",
    rows: [
      ["证据表", "vkpi_kol_video_evidence(2,868 活跃行 · 2,202 有实测播放 · 2026-07-12 本地实测)"],
      ["深析缓存", "vkpi_analysis_cache(final + 关键帧 QA)· detail-bundle 直喂零重复请求"],
      ["展示", "逐视频深析卡(内嵌既有面板零改动)· 默认前 3 条,展开看全部"],
    ],
  },
  comments: {
    label: "intelligence-card · vkpi_comments",
    rows: [
      ["评论库", "vkpi_comments(本地 875 行 · 其中 KOL 帖 128 · 2026-07-12 实测)—— 覆盖有限,空态如实"],
      ["分布口径", "已入库评论的意向/情绪/品牌态度计数(词表+批注,非全网)"],
      ["样本", "cached_comment_count = 该 KOL 名下已入库评论数"],
    ],
  },
  coop: {
    label: "vkpi_kol_cooperation_events",
    rows: [
      ["事件表", "vkpi_kol_cooperation_events(0 行 · 2026-07-12 本地实测 → 诚实空,记一条即点亮)"],
      ["状态", "当前状态 = 最新事件 status_after · append-only 时间线"],
      ["动作", "续约/加大投入/评估/退出 → POST 同端点真落库(内嵌既有面板零改动)"],
    ],
  },
  leadtime: {
    label: "observation_windows · content_posts",
    rows: [
      ["主链", "vkpi_project_content_observation_windows(签收锚)× vkpi_project_content_posts(发布贴)配对"],
      ["副链", "vkpi_project_stage_events received→published(项目级粗粒度)"],
      ["口径", "中位天数 · 无完整「签收后发布」样本=诚实空(履约链现状,非故障)"],
    ],
  },
  audience: {
    label: "audience-geo 多信号 · vkpi_kol_pool",
    rows: [
      ["融合信号", "评论语言 / 评论者名字字系 / 评论者档案国别 / 创作者国别弱先验(逐信号独立状态)"],
      ["代理口径", "无评论侧实测时挂「代理口径」角标 · 绝不伪装成受众实测"],
      ["画像估算", "vkpi_kol_pool.audience_estimated_json(3/1,237 已填 · 2026-07-12)—— 几乎全空,如实空态"],
      ["语言分布", "audience_languages = 评论法真分布 · 无评论 sample_size=0 诚实空"],
    ],
  },
  gear: {
    label: "档案抽取 · detail-bundle",
    rows: [
      ["抽取源", "bio/raw 档案文本 + 深析产物聚合(device_primary / 镜头品牌)"],
      ["性质", "派生字段非采集字段 · 抽不到=「未提取」不猜"],
    ],
  },
};

/* ============ 展示原子 ============ */

export function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line py-1.5 text-[11.5px] last:border-0">
      <span className="flex-none text-muted">{label}</span>
      <span className="min-w-0 break-words text-right text-ink-2">{value}</span>
    </div>
  );
}

function fitTone(fit: number | null): string {
  if (fit === null) return "border-line text-muted";
  if (fit >= 80) return "border-good bg-good-soft text-good";
  if (fit >= 60) return "border-accent bg-accent-soft text-accent";
  if (fit >= 40) return "border-warn bg-warn-soft text-warn";
  return "border-crit bg-crit-soft text-crit";
}

const CHIP = "flex-none rounded-[5px] border px-1.5 py-px text-[9px] font-bold";
const ACT_BTN =
  "rounded-lg border border-line px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent";

/* ============ 身份档案卡(头像/名号/徽 + 三秒读懂 + KV + 动作行) ============ */
export function IdentityBody({
  item,
  signatureLine,
  coopLabel,
  onOpenDrawer,
}: {
  item: Row;
  /** 什么最灵(深析结论一句话;缺=如实占位) */
  signatureLine: string;
  /** 当前合作状态标签(cooperation 端点;缺=「未记录」) */
  coopLabel: string;
  onOpenDrawer: () => void;
}) {
  const [copied, setCopied] = React.useState(false);
  const displayName = str(item.display_name) || str(item.handle) || "—";
  const handle = str(item.handle);
  const platform = str(item.platform);
  const country = str(item.country);
  const avatarUrl = str(item.avatar_url);
  const profileUrl = str(item.profile_url);
  const email = str(item.email);
  const bio = str(item.bio);
  const followers = num(item.followers);
  const fit = num(item.viltrox_fit_score); // 只读展示,红线:绝不写回
  const verified = item.is_verified === true || item.is_verified === 1;
  const whoLine =
    [str(item.primary_topic), str(item.content_style), str(item.production_quality)].filter(Boolean).join(" · ") ||
    (bio ? (bio.length > 70 ? `${bio.slice(0, 70)}…` : bio) : "画像待补全");
  const worthLine = [
    fit !== null ? `Fit ${Math.round(fit)}` : "Fit 未评",
    `合作 ${coopLabel || "未记录"}`,
    followers !== null ? `${fmtZhCompact(followers)} 粉` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const copyEmail = () => {
    if (!email) return;
    try {
      void navigator.clipboard.writeText(email).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      });
    } catch {
      /* clipboard 不可用忽略 */
    }
  };
  return (
    <div>
      <div className="flex flex-wrap items-start gap-3">
        {avatarUrl ? (
          <img src={avatarUrl} alt={displayName} className="h-12 w-12 flex-none rounded-full border border-line object-cover" />
        ) : (
          <span className="grid h-12 w-12 flex-none place-items-center rounded-full border border-line bg-panel text-[16px] text-muted">
            {(displayName || "?").slice(0, 1).toUpperCase()}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">{displayName}</span>
            {handle && handle !== displayName ? <span className="text-[11px] text-muted">@{handle.replace(/^@/, "")}</span> : null}
            {platform ? <span className="flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[8.5px] font-semibold text-ink-2">{platformBadge(platform)}</span> : null}
            {country ? <span className={`${CHIP} border-line text-muted`}>{country}</span> : null}
            {verified ? <span className={`${CHIP} border-info bg-info-soft text-info`}>认证</span> : null}
            <span className={`${CHIP} ${fitTone(fit)}`} title="只读展示 · 本页零写分">
              {fit !== null ? `Fit ${Math.round(fit)}` : "Fit 未评"}
            </span>
          </div>
          {/* 三秒读懂条 */}
          <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
            {[
              ["他是谁", whoLine],
              ["什么最灵", signatureLine],
              ["值多少钱", worthLine],
            ].map(([k, v]) => (
              <div key={k} className="min-w-0 rounded-[9px] border border-line bg-[var(--ds-card-2,var(--ds-card))] px-2.5 py-1.5 text-[10.5px]">
                <span className="text-muted">{k} </span>
                <span className="text-ink-2">{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2.5">
        {bio ? <p className="mb-1.5 text-[11.5px] leading-relaxed text-ink-2">{bio.length > 220 ? `${bio.slice(0, 220)}…` : bio}</p> : null}
        {profileUrl ? (
          <KV
            label="主页"
            value={
              <a className="text-accent transition-colors hover:text-accent-hover" href={profileUrl} target="_blank" rel="noopener noreferrer">
                {profileUrl.length > 44 ? `${profileUrl.slice(0, 44)}…` : profileUrl} ↗
              </a>
            }
          />
        ) : null}
        {email ? <KV label="邮箱" value={email} /> : <KV label="邮箱" value={<span className="text-muted">未收录</span>} />}
        <KV label="语言" value={str(item.language) || "—"} />
        <KV label="来源" value={str(item.source_type) || "—"} />
        <KV label="内容量" value={num(item.posts_count) !== null ? fmtZhCompact(num(item.posts_count)) : "—"} />
        <KV label="入池" value={<span title="UTC 存 · 按浏览器时区显示">{formatLocal(str(item.created_at) || null)}</span>} />
        <KV label="最近可见" value={<span title="UTC 存 · 按浏览器时区显示">{formatLocal(str(item.last_seen_at) || null)}</span>} />
      </div>
      {/* 动作行:直链既有抽屉动作,不重造 */}
      <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
        <button type="button" onClick={onOpenDrawer} className={`${ACT_BTN} border-accent bg-accent-soft text-accent`}>
          收藏 / 联系 / 补全 →
        </button>
        {profileUrl ? (
          <a href={profileUrl} target="_blank" rel="noopener noreferrer" className={ACT_BTN}>
            主页 ↗
          </a>
        ) : null}
        {email ? (
          <button type="button" onClick={copyEmail} className={ACT_BTN}>
            {copied ? "已复制 ✓" : "复制邮箱"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/* ============ 深析摘要(结论优先:强项/短板/建议/研判;未深析=「未分析」) ============ */

const KIND_LABEL: Record<string, string> = { profile_llm: "档案研判", video_deep: "视频深析", final_v1: "视频深析" };

function chipsLine(label: string, values: string[], cls: string) {
  if (values.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {values.slice(0, 6).map((v, i) => (
          <span key={i} className={`rounded-full border px-2 py-0.5 text-[10.5px] ${cls}`} title={v}>
            {v.length > 42 ? `${v.slice(0, 42)}…` : v}
          </span>
        ))}
        {values.length > 6 ? <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-muted">+{values.length - 6}</span> : null}
      </div>
    </div>
  );
}

export function DeepBody({ deep }: { deep: Row }) {
  const primary = asRow(deep.primary_result);
  const summary = asRow(deep.summary);
  const count = num(deep.count) ?? 0;
  if (!primary && !summary && count === 0) {
    return <EmptyLine text="未分析 —— 无深析记录。" />;
  }
  const dims = asRow(primary?.llm_dimensions_11);
  const v6 = num(primary?.llm_v6_fit);
  const confidence = num(primary?.confidence);
  const kind = str(primary?.analysis_kind);
  const createdAt = str(primary?.created_at);
  const strLIst = (v: unknown) => asArray(v).map((x) => (typeof x === "string" ? x : str(asRow(x)?.text) || str(asRow(x)?.label))).filter(Boolean);
  const strengths = strLIst(dims?.strengths);
  const weaknesses = strLIst(dims?.weaknesses);
  const recommendations = strLIst(dims?.recommendations);
  const judgment = str(dims?.account_judgment) || str(asRow(dims?.account_judgment)?.summary);
  const risk = str(dims?.risk) || strLIst(dims?.risk).join(" · ");
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap gap-1.5">
        {v6 !== null ? (
          <span className="rounded-full border border-accent-2 px-2 py-0.5 text-[10.5px] text-accent-2" title="深析契合读数 · 独立于 Fit 分,只读">
            深析契合 {Math.round(v6 * 100) / 100}
          </span>
        ) : null}
        {confidence !== null ? (
          <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-ink-2" title="行级置信度字段原样展示">
            置信 {Math.round(confidence * 100) / 100}
          </span>
        ) : null}
        {count > 0 ? <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-muted">{count} 条记录</span> : null}
        {KIND_LABEL[kind] ? <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-muted">{KIND_LABEL[kind]}</span> : null}
      </div>
      {judgment ? <p className="text-[11.5px] leading-relaxed text-ink-2">{judgment.length > 240 ? `${judgment.slice(0, 240)}…` : judgment}</p> : null}
      {chipsLine("强项", strengths, "border-good bg-good-soft text-good")}
      {chipsLine("短板", weaknesses, "border-warn bg-warn-soft text-warn")}
      {risk ? <KV label="风险" value={risk.length > 90 ? `${risk.slice(0, 90)}…` : risk} /> : null}
      {recommendations.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">建议</div>
          {recommendations.slice(0, 3).map((r, i) => (
            <div key={i} className="rounded-[9px] border border-line px-2.5 py-1.5 text-[11px] leading-relaxed text-ink-2">
              {r.length > 120 ? `${r.slice(0, 120)}…` : r}
            </div>
          ))}
        </div>
      ) : null}
      {!judgment && strengths.length === 0 && weaknesses.length === 0 && recommendations.length === 0 ? (
        summary ? <MiniRows data={summary} maxKeys={6} /> : <EmptyLine text="有记录但无结论字段。" />
      ) : null}
      {createdAt ? (
        <div className="font-mono text-[9px] text-muted" title="最近一条深析产物时间(UTC 存 · 按浏览器时区显示)">
          最近分析 {formatLocal(createdAt)}
        </div>
      ) : null}
    </div>
  );
}

/* ============ 评论声音 · 意向分布 ============ */
export function CommentsBody({ intel }: { intel: Row | null }) {
  if (!intel || str(intel.status) === "empty" || str(intel.status) === "not_configured") {
    return <EmptyLine text="评论未入库。" />;
  }
  const counts = asRow(intel.counts);
  const cached = num(intel.cached_comment_count) ?? 0;
  const questions = num(counts?.questions) ?? 0;
  const opportunities = num(counts?.opportunities) ?? 0;
  const issues = num(counts?.issues) ?? 0;
  const sentiment = asRow(counts?.sentiment);
  const attitude = asRow(counts?.brand_attitude);
  const SENTI_TONE: Record<string, string> = { positive: "var(--ds-good)", negative: "var(--ds-crit)", neutral: "var(--ds-muted)" };
  if (cached === 0 && !sentiment && !attitude) return <EmptyLine text="评论已配置但样本 0 条。" />;
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap gap-1.5">
        <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-ink-2">样本 {cached}</span>
        {questions > 0 ? <span className="rounded-full border border-info bg-info-soft px-2 py-0.5 text-[10.5px] text-info">提问 {questions}</span> : null}
        {opportunities > 0 ? <span className="rounded-full border border-good bg-good-soft px-2 py-0.5 text-[10.5px] text-good">机会 {opportunities}</span> : null}
        {issues > 0 ? <span className="rounded-full border border-warn bg-warn-soft px-2 py-0.5 text-[10.5px] text-warn">吐槽 {issues}</span> : null}
      </div>
      {sentiment ? <DistBars dist={sentiment} title="情绪" tone={(l) => SENTI_TONE[l]} /> : null}
      {attitude ? <DistBars dist={attitude} title="品牌态度" /> : null}
    </div>
  );
}

/* ============ MiniRows:未知契约对象的防御渲染(标量→KV / 字符串数组→chips) ============ */
export function MiniRows({ data, maxKeys = 8 }: { data: Row; maxKeys?: number }) {
  const META = new Set(["status", "method", "mode", "note", "reason", "generated_at", "kol_pool_id", "source", "source_table", "cache", "contract", "diagnostics"]);
  const entries = Object.entries(data)
    .filter(([k, v]) => !META.has(k) && v !== null && v !== undefined && v !== "")
    .slice(0, maxKeys);
  if (entries.length === 0) return <EmptyLine text="有响应但无实质字段。" />;
  return (
    <div className="space-y-1">
      {entries.map(([k, v]) => {
        const scalars = asArray(v).filter((x) => typeof x === "string" || typeof x === "number");
        if (Array.isArray(v) && scalars.length === v.length && scalars.length > 0) {
          return (
            <div key={k} className="space-y-1">
              <div className="text-[10px] text-muted">{k}</div>
              <div className="flex flex-wrap gap-1">
                {scalars.slice(0, 8).map((s, i) => (
                  <span key={i} className="rounded-full border border-line px-2 py-0.5 text-[10px] text-ink-2">
                    {String(s)}
                  </span>
                ))}
                {scalars.length > 8 ? <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">+{scalars.length - 8}</span> : null}
              </div>
            </div>
          );
        }
        const row = asRow(v);
        if (row) {
          const inner = Object.entries(row)
            .filter(([, iv]) => typeof iv === "string" || typeof iv === "number")
            .slice(0, 3)
            .map(([ik, iv]) => `${ik} ${String(iv).slice(0, 30)}`)
            .join(" · ");
          return <KV key={k} label={k} value={inner || "…"} />;
        }
        const s = str(v);
        if (!s) return null;
        return <KV key={k} label={k} value={s.length > 90 ? `${s.slice(0, 90)}…` : s} />;
      })}
    </div>
  );
}

/* ============ 受众画像估算附注(page 侧;AudienceGeoPanel 内嵌之外的档案字段) ============ */
export function AudienceEstimateExtra({ item }: { item: Row }) {
  const estimated = asRow(item.audience_estimated);
  const languages = asRow(item.audience_languages);
  const langItems = asArray(languages?.languages ?? languages?.items)
    .map((l) => asRow(l))
    .filter((l): l is Row => Boolean(l));
  const sampleSize = num(languages?.sample_size);
  return (
    <div className="mt-2 space-y-2 border-t border-line pt-2">
      {estimated ? (
        <MiniRows data={estimated} maxKeys={6} />
      ) : (
        <div className="text-[10.5px] text-muted" title="KOL 池抽屉「补全」动作生成(评论抽样推断)">
          画像未估算。
        </div>
      )}
      {langItems.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {langItems.slice(0, 6).map((l, i) => {
            // 后端 pct 已是 0-100 整数(comments_language 口径),原样展示不再归一
            const share = num(l.pct) ?? num(l.share);
            return (
              <span key={i} className="rounded-full border border-line px-2 py-0.5 text-[10px] text-ink-2">
                {str(l.lang) || str(l.language) || "—"}
                {share !== null ? ` ${share}%` : ""}
              </span>
            );
          })}
          {sampleSize !== null ? <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">评论样本 {sampleSize}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
