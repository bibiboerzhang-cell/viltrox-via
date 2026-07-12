import React from "react";
import { EmptyLine, ErrorCard, KpiCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import { BarRow } from "./MarketVoicePage.charts";
import { Drow } from "./MarketVoicePage.dialogs";
import { CONFIDENCE_META, fmtNum, fmtUsd, type LaunchMember } from "../../../../services/vkpi/launchBoard-api";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

// 发射台 · 板块页范式辅助件(LaunchPadBoardPage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage.modules / MyKolBoardPage.charts 同构:模块卡骨架、KPI 卡、
//   条形行、空态三轨(EmptyLine/PendingCard/ErrorCard)全部复用零重写;本文件只放
//   ① MODULE_SOURCES 溯源注册表(真实表名/端点,禁编造) ② 全案六段的行式 body
//   (长文收弹窗,卡面一行一条) ③ KPI 带四卡。
//   段状态分流 blockGate = 旧 LaunchPadPage.BlockBody 原口径搬家:
//   empty(带 reason)/ module_pending(兄弟件未收口)/ error|unavailable 各给诚实态。
// 红线:本文件零直连网络(数据/回调全由 page 层注入);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 类零写死色;禁 opacity 修饰类;卡面零内部术语(口径进 SrcChip/溯源)。

/* ============ 溯源注册表(label=真实来源;rows=口径行,行数核实 2026-07-12 本地库) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiL: {
    label: "publish/pending 等 4 端点",
    rows: [
      ["发布计划", "vkpi_product_launches 未删行 · status=active 计数"],
      ["内容候选", "vkpi_project_content_posts · status=candidate 计数"],
      ["已发内容", "vkpi_project_kol_assignments · 归一阶段 content_posted 指派行"],
      ["审批中", "vkpi_publish_approvals(迁移173)· status=pending 计数 · 0 也如实 0"],
      ["时序", "四源均无按日快照端点 → 趋势线诚实虚线,不编序列"],
    ],
  },
  product: {
    label: "vkpi_products · launch/assemble",
    rows: [
      ["主表", "vkpi_products(SKU 目录)"],
      ["SKU 焦段", "型号名解析(assemble sku_focal)"],
      ["方法", "纯聚合已有数据 · 零 LLM 零采集"],
    ],
  },
  roster: {
    label: "memory 证据 · 候选打分",
    rows: [
      ["候选池", "memory product_family 证据 → 打分排序"],
      ["招牌拍法", "vkpi_analysis_cache(final_v1 深析)命中 top1"],
      ["焦段覆盖", "深析焦段 × SKU 焦段对照"],
    ],
  },
  budgets: {
    label: "报价历史分位",
    rows: [
      ["方法", "个人真实报价历史优先 · 无则同档分位回退"],
      ["总额", "只合计可估成员 · 缺口如实列出,不用均值硬补"],
      ["置信", "high/medium/low 按报价源数量"],
    ],
  },
  schedule: {
    label: "履约历史中位倒排",
    rows: [
      ["周期", "签收→发布个人历史中位(有样本);无履约史用默认周期"],
      ["去重", "同周 >2 人顺延错峰,顺延天数如实标"],
      ["时间", "存 UTC · 显示按浏览器时区"],
    ],
  },
  playbooks: {
    label: "vkpi_analysis_cache · final_v1",
    rows: [
      ["口径", "深析命中招牌拍法 top1 · 均播放为实测样本均值"],
      ["缺深析", "无 final_v1 样本 = 如实标注,不编打法"],
    ],
  },
  synergy: {
    label: "官号帖扫描",
    rows: [
      ["口径", "官号历史帖 × SKU 焦段/同品类命中"],
      ["降级", "数据不足 generic=true 如实标,建议仅通用款"],
    ],
  },
  coverage: {
    label: "roster_optimizer",
    rows: [
      ["方法", "组合受众重叠去重 · 被淘汰候选如实列出"],
    ],
  },
  forecast: {
    label: "播放分位预测",
    rows: [
      ["口径", "个人历史播放分位 p10/p50/p90 · 互动率实测"],
      ["不可测", "样本不足 = 如实标注,不给硬编数"],
    ],
  },
  contentSched: {
    label: "vkpi_project_content_posts",
    rows: [
      ["主表", "vkpi_project_content_posts(23 行 · 核实 2026-07-12)"],
      ["复核", "PATCH content-posts/{id} · matched 回填观察窗口"],
      ["审批/重排", "POST /publish/* → vkpi_publish_approvals upsert"],
      ["时间", "存 UTC · 显示按浏览器时区"],
    ],
  },
  approvals: {
    label: "vkpi_publish_approvals · 迁移173",
    rows: [
      ["主表", "vkpi_publish_approvals(0 行 = 已建未用,如实)"],
      ["入队方", "本板「内容排期」审批/重排动作 upsert 落行"],
      ["动作", "approve / reschedule / remind 三端点真调用"],
    ],
  },
  launches: {
    label: "vkpi_product_launches",
    rows: [
      ["口径", "未删行 · 更新时间倒序"],
      ["窗口", "launch_window_start/end · 存 UTC 按浏览器时区显示"],
    ],
  },
  stages: {
    label: "vkpi_project_kol_assignments",
    rows: [
      ["计数", "指派行(2,189 行 · 核实 2026-07-12)"],
      ["归一", "读侧词表归一(shipped→device_sent 等),不改写库"],
    ],
  },
  materials: {
    label: "三表静态盘点",
    rows: [
      ["官方物料", "vkpi_legacy_official_materials_staging · 241 行(无读端点待接)"],
      ["活动物料", "vkpi_event_materials · 0 行(已建未用)"],
      ["内容资产", "vkpi_content_assets · 0 行(已建未用)"],
      ["口径", "静态盘点 2026-07-12 · 非实时"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiL: "发布指标带",
  product: "产品概要",
  roster: "KOL 名单",
  budgets: "每人预算",
  schedule: "发布排期",
  playbooks: "每人打法",
  synergy: "官号协同",
  coverage: "覆盖最大化",
  forecast: "预测战绩",
  contentSched: "内容排期",
  approvals: "发布审批",
  launches: "发布计划",
  stages: "履约阶段",
  materials: "物料覆盖",
};

/* ============ 段状态分流(旧 BlockBody 原口径):null = 放行渲染 ============ */
export function blockGate(block: Row | null | undefined): React.ReactNode | null {
  const status = String(block?.status || "");
  if (!block || status === "empty") return <EmptyLine text={String(block?.reason || "暂无数据。")} />;
  if (status === "module_pending") {
    return (
      <PendingCard>
        <b>模块施工中</b> —— {String(block.module || "兄弟模块")} 未收口,收口后自动变活,不摆占位数字。
      </PendingCard>
    );
  }
  if (status === "error" || status === "unavailable") {
    return <ErrorCard title="该段聚合失败" text={String(block.reason || "unknown")} />;
  }
  return null;
}

/* ============ 小件:置信徽 / 可点行 / 备注行 ============ */

export function ConfidenceChip({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-[10px] text-muted">—</span>;
  const meta = CONFIDENCE_META[String(value)] || { label: String(value), cls: "border-line text-muted" };
  return <span className={`flex-none rounded-[5px] border px-1 py-px text-[8.5px] font-semibold ${meta.cls}`}>{meta.label}</span>;
}

export function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    fn();
  }
};

/** 成员行壳:名字 + 右侧内容,点开成员详情(连续翻);全模块共用同一行语言。 */
export function MemberRow({
  member,
  onOpen,
  children,
  title,
}: {
  member: LaunchMember;
  onOpen?: () => void;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <div
      className={`flex min-w-0 items-center gap-2 border-b border-line py-[7px] text-[11.5px] last:border-0${
        onOpen ? " group cursor-pointer" : ""
      }`}
      title={title}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onClick={onOpen}
      onKeyDown={onOpen ? keyActivate(onOpen) : undefined}
    >
      <span className="min-w-0 flex-1 truncate text-ink-2 transition-colors group-hover:text-accent">
        {member.displayName}
        {member.handle ? <span className="ml-1 text-[9.5px] text-muted">@{member.handle.replace(/^@/, "")}</span> : null}
      </span>
      {children}
    </div>
  );
}

/* ============ ① 产品概要 ============ */
export function ProductBody({ product, skuFocal }: { product: Row; skuFocal: string }) {
  return (
    <div>
      <Drow k="型号" v={String(product.model_name || product.marketing_name || product.sku || "—")} />
      <Drow k="类目" v={[product.category_main, product.category_detail].filter(Boolean).join(" / ") || "—"} />
      <Drow k="系列 / 卡口" v={[product.series, product.mount].filter(Boolean).join(" / ") || "—"} />
      <Drow k="售价" v={product.price_usd != null ? `$${product.price_usd}` : "—"} />
      <Drow k="SKU 焦段" v={skuFocal || "—"} />
    </div>
  );
}

/* ============ ② KOL 名单(招牌拍法 + 焦段覆盖;点行 → 成员详情) ============ */
export function RosterBody({ members, onOpenMember }: { members: LaunchMember[]; onOpenMember: (i: number) => void }) {
  return (
    <div>
      {members.map((member, i) => {
        const sig: Row = member.roster.signature || {};
        const top: Row = sig.top_mode || {};
        const fc: Row = member.roster.focal_coverage || {};
        return (
          <MemberRow key={member.kolPoolId} member={member} onOpen={() => onOpenMember(i)}>
            <span className="flex-none font-mono text-[9.5px] text-muted">
              {member.platform || "—"}
              {member.country ? ` · ${member.country}` : ""}
            </span>
            {member.score != null && (
              <span className="flex-none font-mono text-[10px] text-ink" title="匹配分">
                {member.score}
              </span>
            )}
            {sig.status === "ready" && top.label ? (
              <span className="flex-none rounded-full border border-accent bg-accent-soft px-2 py-0.5 text-[9.5px] text-accent">
                {String(top.label)}
                {top.avg_views != null ? ` · 均播 ${fmtNum(Number(top.avg_views))}` : ""}
              </span>
            ) : (
              <span className="flex-none text-[9.5px] text-muted" title={String(sig.reason || "")}>
                无深析样本
              </span>
            )}
            {fc.status === "ready" && fc.sku_focal ? (
              <span
                className={`flex-none rounded-[5px] border px-1.5 py-px text-[9.5px] ${
                  fc.sku_focal_covered ? "border-good bg-good-soft text-good" : "border-line text-muted"
                }`}
                title={`共覆盖 ${fc.covered_focal_count ?? 0} 个焦段`}
              >
                {String(fc.sku_focal)} {fc.sku_focal_covered ? `拍过 ${fc.sku_focal_video_count} 条` : "空白可切入"}
              </span>
            ) : null}
          </MemberRow>
        );
      })}
    </div>
  );
}

/* ============ ③ 每人预算(条形 = p50 相对;不可估 = 空槽 + 原因) ============ */
export function BudgetsBody({
  members,
  total,
  onOpenMember,
}: {
  members: LaunchMember[];
  total: Row;
  onOpenMember: (i: number) => void;
}) {
  const p50s = members.map((m) => Number(m.budget?.estimate?.estimated_usd_p50));
  const max = Math.max(0, ...p50s.filter((v) => Number.isFinite(v)));
  return (
    <div>
      {members.map((member, i) => {
        const est: Row = member.budget?.estimate || {};
        const ok = est.status === "ok" || est.status === "ready";
        const p50 = Number(est.estimated_usd_p50);
        return (
          <BarRow
            key={member.kolPoolId}
            name={member.displayName}
            widthPct={ok && max > 0 && Number.isFinite(p50) ? (p50 / max) * 100 : 0}
            dashed={!ok}
            value={
              <span className="inline-flex items-center gap-1.5">
                {ok ? fmtUsd(p50) : "不可估"}
                <ConfidenceChip value={ok ? est.confidence : null} />
              </span>
            }
            title={
              ok
                ? `区间 ${fmtUsd(est.estimated_usd_low)}–${fmtUsd(est.estimated_usd_high)} · ${String(est.method || "—")} · 报价源 ${est.source_count ?? "—"}`
                : String(est.reason || est.status || "不可估")
            }
            onClick={() => onOpenMember(i)}
          />
        );
      })}
      {Number(total.unpriced_members) > 0 && (
        <ProvNote>
          {total.unpriced_members} 人暂无法估价 · 总额只含可估 {total.priced_members} 人,不用均值硬补
        </ProvNote>
      )}
    </div>
  );
}

/* ============ ④ 排期(发布周 + 目标日 + 顺延徽) ============ */
export function ScheduleBody({ members, onOpenMember }: { members: LaunchMember[]; onOpenMember: (i: number) => void }) {
  const rows = members.filter((m) => m.schedule);
  if (rows.length === 0) return <EmptyLine text="排期段无成员行。" />;
  return (
    <div>
      {rows.map((member) => {
        const s: Row = member.schedule || {};
        const basis: Row = s.leadtime_basis || {};
        const personal = String(basis.method || "").startsWith("personal");
        const idx = members.indexOf(member);
        return (
          <MemberRow
            key={member.kolPoolId}
            member={member}
            onOpen={() => onOpenMember(idx)}
            title={personal ? `个人历史中位(${basis.sample_count} 样本)` : "默认周期(无履约历史)"}
          >
            <span className="flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[9.5px] font-semibold text-accent">
              {String(s.publish_week || "—")}
            </span>
            {Number(s.shifted_days) > 0 && (
              <span
                className="flex-none rounded-[5px] border border-warn bg-warn-soft px-1 py-px text-[8.5px] font-semibold text-warn"
                title="同周超过 2 人,顺延错峰"
              >
                顺延 {s.shifted_days} 天
              </span>
            )}
            <span className="flex-none font-mono text-[9.5px] text-muted">{String(s.publish_date || "—")}</span>
            <span className="flex-none font-mono text-[9.5px] text-muted" title="签收→发布制作周期">
              {s.leadtime_days != null ? `${s.leadtime_days}d` : "—"}
            </span>
            <ConfidenceChip value={basis.confidence} />
          </MemberRow>
        );
      })}
    </div>
  );
}

/* ============ ⑤ 每人打法(top1 一行一条,全文进成员详情) ============ */
export function PlaybooksBody({ members, onOpenMember }: { members: LaunchMember[]; onOpenMember: (i: number) => void }) {
  const rows = members.filter((m) => m.playbook);
  if (rows.length === 0) return <EmptyLine text="打法段无成员行。" />;
  return (
    <div>
      {rows.map((member) => {
        const p: Row = member.playbook || {};
        const idx = members.indexOf(member);
        return (
          <MemberRow key={member.kolPoolId} member={member} onOpen={() => onOpenMember(idx)} title={String(p.line || "")}>
            {p.status !== "ready" && (
              <span className="flex-none rounded-[5px] border border-line px-1 py-px text-[8.5px] text-muted">缺深析</span>
            )}
            <span className={`min-w-0 max-w-[55%] flex-none truncate text-[10.5px] ${p.status === "ready" ? "text-ink" : "text-muted"}`}>
              {String(p.line || "—")}
            </span>
          </MemberRow>
        );
      })}
    </div>
  );
}

/* ============ ⑥ 官号协同(generic 降级如实;行点开全文弹窗) ============ */
export function SynergyBody({ synergy, onOpenFull }: { synergy: Row; onOpenFull: () => void }) {
  const items: Row[] = Array.isArray(synergy.suggestions) ? synergy.suggestions : [];
  return (
    <div>
      {String(synergy.status) === "generic" && (
        <div className="mb-2 rounded-lg border border-warn bg-warn-soft px-3 py-1.5 text-[10.5px] text-warn">
          数据不足降级:{String(synergy.reason || "")}
        </div>
      )}
      {items.map((s, i) => (
        <div
          key={i}
          role="button"
          tabIndex={0}
          onClick={onOpenFull}
          onKeyDown={keyActivate(onOpenFull)}
          title={s.basis ? `依据:${String(s.basis.source || "")}${s.basis.sample != null ? ` · ${s.basis.sample} 条样本` : ""}` : undefined}
          className="group cursor-pointer border-b border-line py-[7px] text-[11.5px] last:border-0"
        >
          <span className={`block truncate transition-colors group-hover:text-accent ${String(synergy.status) === "generic" ? "text-muted" : "text-ink-2"}`}>
            {String(s.line || "—")}
          </span>
        </div>
      ))}
      {items.length > 0 && (
        <button
          type="button"
          onClick={onOpenFull}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全文 {items.length} 条
        </button>
      )}
    </div>
  );
}

/* ============ ⑦ 覆盖最大化(选中/淘汰/方法 + 淘汰名单) ============ */
export function CoverageBody({ coverage }: { coverage: Row }) {
  const dropped: any[] = Array.isArray(coverage.dropped_overlap) ? coverage.dropped_overlap : [];
  const droppedNames = dropped.slice(0, 8).map((d) => (typeof d === "object" && d ? d.handle || d.kol_pool_id : d)).join("、");
  return (
    <div>
      <Drow k="选中人数" v={String((coverage.selected || []).length || 0)} />
      <Drow k="受众重叠淘汰" v={String(dropped.length || 0)} />
      <Drow k="方法" v={String(coverage.method || "roster_optimizer")} />
      {dropped.length > 0 && (
        <ProvNote>
          被淘汰:{droppedNames}
          {dropped.length > 8 ? " …" : ""}
        </ProvNote>
      )}
      {coverage.note ? <ProvNote>{String(coverage.note)}</ProvNote> : null}
    </div>
  );
}

/* ============ ＋ 预测战绩(条形 = 预计播放 p50) ============ */
export function ForecastBody({ members, onOpenMember }: { members: LaunchMember[]; onOpenMember: (i: number) => void }) {
  const rows = members.filter((m) => m.forecast);
  if (rows.length === 0) return <EmptyLine text="预测段无成员行。" />;
  const p50s = rows.map((m) => Number(m.forecast?.forecast?.expected_views_p50));
  const max = Math.max(0, ...p50s.filter((v) => Number.isFinite(v)));
  return (
    <div>
      {rows.map((member) => {
        const fc: Row = member.forecast?.forecast || {};
        const ok = fc.status === "ok" || fc.status === "ready";
        const p50 = Number(fc.expected_views_p50);
        const idx = members.indexOf(member);
        return (
          <BarRow
            key={member.kolPoolId}
            name={member.displayName}
            widthPct={ok && max > 0 && Number.isFinite(p50) ? (p50 / max) * 100 : 0}
            dashed={!ok}
            color="linear-gradient(90deg, var(--ds-accent-2), var(--ds-accent))"
            value={
              <span className="inline-flex items-center gap-1.5">
                {ok ? fmtNum(p50) : "不可测"}
                <ConfidenceChip value={ok ? fc.confidence : null} />
              </span>
            }
            title={
              ok
                ? `p10–p90:${fmtNum(fc.expected_views_p10)}–${fmtNum(fc.expected_views_p90)}${
                    fc.engagement_rate != null ? ` · 互动率 ${(Number(fc.engagement_rate) * 100).toFixed(2)}%` : ""
                  }`
                : String(fc.reason || "样本不足")
            }
            onClick={() => onOpenMember(idx)}
          />
        );
      })}
    </div>
  );
}

/* ============ KPI 带四卡(四源真计数;趋势线 = board-series?board=launchpad 按日
   真序列:内容候选待核←content_candidates 新候选帖/日、审批中←publish_approvals
   新审批行/日(关联指标,卡面大数是待办存量 → 不挂环比药丸;表未建/端点失败
   boardSeriesVals=null → spempty 诚实虚线让位);计划/已发无按日序列照旧虚线) ============ */
export function LaunchKpiBand({
  launchesActive,
  launchesNote,
  candidates,
  candidatesNote,
  posted,
  postedNote,
  approvalsPending,
  approvalsNote,
  boardSeries,
}: {
  launchesActive: number | null;
  launchesNote: string;
  candidates: number | null;
  candidatesNote: string;
  posted: number | null;
  postedNote: string;
  approvalsPending: number | null;
  approvalsNote: string;
  /** board-series?board=launchpad 响应(null=未就绪/失败 → 趋势位 spempty 诚实虚线) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  const bs = boardSeries ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {launchesActive != null ? (
        <KpiCard label="发布计划进行中" value={launchesActive.toLocaleString()} unit="个" />
      ) : (
        <KpiCard label="发布计划进行中" value="—" pending pendingNote={launchesNote} />
      )}
      {candidates != null ? (
        <KpiCard
          label="内容候选待核"
          value={candidates.toLocaleString()}
          unit="条"
          tone="warn"
          series={boardSeriesVals(bs, "content_candidates")}
          seriesColor="var(--ds-warn)"
        />
      ) : (
        <KpiCard label="内容候选待核" value="—" pending pendingNote={candidatesNote} />
      )}
      {posted != null ? (
        <KpiCard label="已发内容" value={posted.toLocaleString()} unit="人次" />
      ) : (
        <KpiCard label="已发内容" value="—" pending pendingNote={postedNote} />
      )}
      {approvalsPending != null ? (
        <KpiCard
          label="审批中"
          value={approvalsPending.toLocaleString()}
          unit="条"
          tone="warn"
          series={boardSeriesVals(bs, "publish_approvals")}
          seriesColor="var(--ds-warn)"
        />
      ) : (
        <KpiCard label="审批中" value="—" pending pendingNote={approvalsNote} />
      )}
    </div>
  );
}
