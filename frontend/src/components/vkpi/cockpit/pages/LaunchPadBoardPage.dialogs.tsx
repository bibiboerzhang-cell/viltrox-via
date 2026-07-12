import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { Drow, ModalShell, SectionLabel, platformBadge } from "./MarketVoicePage.dialogs";
import { statusPill } from "./LaunchPadBoardPage.ops";
import { ConfidenceChip } from "./LaunchPadBoardPage.modules";
import {
  APPROVAL_STATUS,
  CONTENT_POST_STATUS,
  fmtNum,
  fmtUsd,
  type LaunchMember,
  type Row,
} from "../../../../services/vkpi/launchBoard-api";
import type { useContentReview, usePublishActions } from "./LaunchPadBoardPage.actions";

// 发射台 · 弹窗族(金样板 = MarketVoicePage.dialogs 的 FeedDetailModal 连续翻体验,
//   ModalShell/SectionLabel/Drow 复用零重写;依赖单向:page/modules/ops → 本文件禁反向)。
//   MemberDetailModal  成员全案一档(①名单+④打法+②预算+③排期+预测合并)+ ‹#n/N›
//                      连续翻 + ↑↓ 方向键;「打开 KOL 档案 →」= sessionStorage 传
//                      kol_pool_id + vkpi:open-kol-profile 事件(CockpitApp 既有管道)。
//   PostDetailModal    内容排期单条详情 + 连续翻 + 闭环动作:✓确认/✕剔除/需复核
//                      (PATCH 真端点)+ 审批通过/重排/提醒(POST /publish/* upsert)。
//   ApprovalDetailModal 审批条目详情 + 同三动作。
//   动作纪律:回执一律以端点真实返回为准(hooks 状态),绝不点击即置绿;失败原因不吞。
// 红线:颜色全 token 零写死色;禁 opacity 修饰类;时间绝对时间戳;不触 fit 分/rule_v0。

const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:text-muted";
const ACT_BTN =
  "rounded-lg border border-line px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";
const DONE_BTN = "rounded-lg border border-good bg-good-soft px-2.5 py-1.5 text-[11px] text-good";
const FIELD = "rounded-lg border border-line bg-card px-2 py-1.5 text-[10.5px] text-ink-2 outline-none focus:border-accent";

/** ‹ #n/N › + ↑↓ 方向键连续翻(范式要素⑤;弹窗在场时监听,Esc 由 ModalShell 处理)。 */
function useArrowNav(index: number, total: number, onNav: (i: number) => void) {
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        if (index < total - 1) onNav(index + 1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        if (index > 0) onNav(index - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, total, onNav]);
}

function NavBar({ index, total, onNav }: { index: number; total: number; onNav: (i: number) => void }) {
  return (
    <span className="flex items-center gap-2">
      <button type="button" className={NAV_BTN} disabled={index <= 0} onClick={() => onNav(index - 1)}>
        ‹ 上一条
      </button>
      <span className="font-mono text-[10.5px] text-muted">
        #{index + 1} / {total}
      </span>
      <button type="button" className={NAV_BTN} disabled={index >= total - 1} onClick={() => onNav(index + 1)}>
        下一条 ›
      </button>
    </span>
  );
}

/* ============ 成员全案详情(六段合并一档 + 连续翻 + 身份跳) ============ */
export function MemberDetailModal({
  member,
  index,
  total,
  onNav,
  onClose,
  onNavigate,
}: {
  member: LaunchMember;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  onNavigate?: (navKey: string) => void;
}) {
  useArrowNav(index, total, onNav);
  const sig: Row = member.roster.signature || {};
  const top: Row = sig.top_mode || {};
  const fc: Row = member.roster.focal_coverage || {};
  const est: Row = member.budget?.estimate || {};
  const estOk = est.status === "ok" || est.status === "ready";
  const sch: Row = member.schedule || {};
  const basis: Row = sch.leadtime_basis || {};
  const pb: Row = member.playbook || {};
  const fcast: Row = member.forecast?.forecast || {};
  const fcOk = fcast.status === "ok" || fcast.status === "ready";
  const openProfile = () => {
    try {
      window.sessionStorage.setItem("vkpi:kol-profile-id", String(member.kolPoolId));
    } catch {
      /* sessionStorage 不可用忽略,事件管道仍会切页 */
    }
    if (onNavigate) onNavigate("kolProfile");
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
  };
  return (
    <ModalShell
      title={
        <span className="flex items-center gap-2">
          <span className="min-w-0 truncate">{member.displayName}</span>
          <span className="flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[9px] font-semibold text-ink-2">
            {platformBadge(member.platform)}
          </span>
        </span>
      }
      sub={
        <span className="flex flex-wrap items-center gap-2">
          <span>
            @{member.handle.replace(/^@/, "") || "—"}
            {member.country ? ` · ${member.country}` : ""}
            {member.score != null ? ` · 匹配分 ${member.score}` : ""}
          </span>
          <NavBar index={index} total={total} onNav={onNav} />
        </span>
      }
      onClose={onClose}
    >
      <div className="mb-[22px]">
        <SectionLabel>招牌拍法 · 焦段覆盖</SectionLabel>
        {sig.status === "ready" && top.label ? (
          <>
            <Drow k="招牌拍法" v={String(top.label)} />
            {top.avg_views != null && <Drow k="均播放" v={fmtNum(Number(top.avg_views))} />}
          </>
        ) : (
          <Drow k="招牌拍法" v={String(sig.reason || "无深析样本")} tone="text-muted" />
        )}
        {fc.status === "ready" ? (
          <Drow
            k="SKU 焦段"
            v={
              fc.sku_focal
                ? `${fc.sku_focal} · ${fc.sku_focal_covered ? `拍过 ${fc.sku_focal_video_count} 条` : "空白可切入"} · 共覆盖 ${fc.covered_focal_count ?? 0} 个焦段`
                : `覆盖 ${fc.covered_focal_count ?? 0} 个焦段`
            }
          />
        ) : (
          <Drow k="SKU 焦段" v="—" tone="text-muted" />
        )}
      </div>
      <div className="mb-[22px]">
        <SectionLabel>预算</SectionLabel>
        {estOk ? (
          <>
            <Drow k="报价 p50" v={fmtUsd(est.estimated_usd_p50)} />
            <Drow k="区间" v={est.estimated_usd_low != null ? `${fmtUsd(est.estimated_usd_low)} – ${fmtUsd(est.estimated_usd_high)}` : "—"} />
            <Drow k="方法" v={String(est.method || "—")} />
            <Drow k="真实报价源" v={String(est.source_count ?? "—")} />
            <Drow k="置信" v={<ConfidenceChip value={est.confidence} />} />
          </>
        ) : (
          <Drow k="预算" v={`不可估:${String(est.reason || est.status || "—")}`} tone="text-muted" />
        )}
      </div>
      <div className="mb-[22px]">
        <SectionLabel>排期</SectionLabel>
        {member.schedule ? (
          <>
            <Drow k="发布周" v={`${String(sch.publish_week || "—")}${Number(sch.shifted_days) > 0 ? ` · 顺延 ${sch.shifted_days} 天(同周去重)` : ""}`} />
            <Drow k="目标日期" v={String(sch.publish_date || "—")} />
            <Drow k="制作周期" v={sch.leadtime_days != null ? `${sch.leadtime_days} 天` : "—"} />
            <Drow
              k="周期依据"
              v={String(basis.method || "").startsWith("personal") ? `个人历史中位(${basis.sample_count} 样本)` : "默认周期(无履约历史)"}
            />
            {basis.detail ? <Drow k="明细" v={String(basis.detail)} /> : null}
            <Drow k="置信" v={<ConfidenceChip value={basis.confidence} />} />
          </>
        ) : (
          <Drow k="排期" v="该成员无排期行" tone="text-muted" />
        )}
      </div>
      <div className="mb-[22px]">
        <SectionLabel>打法</SectionLabel>
        {member.playbook ? (
          <>
            <div className={`text-[12.5px] leading-relaxed ${pb.status === "ready" ? "text-ink" : "text-muted"}`}>{String(pb.line || "—")}</div>
            {pb.status === "ready" && pb.basis ? (
              <div className="mt-1.5 font-mono text-[9.5px] text-muted">
                依据:{pb.basis.video_count} 条深析命中该拍法
                {pb.basis.avg_views != null ? ` · 均播放 ${fmtNum(Number(pb.basis.avg_views))}(${pb.basis.views_sample} 条有播放数)` : ""}
              </div>
            ) : null}
          </>
        ) : (
          <Drow k="打法" v="该成员无打法行" tone="text-muted" />
        )}
      </div>
      <div className="mb-[22px]">
        <SectionLabel>预测战绩</SectionLabel>
        {fcOk ? (
          <>
            <Drow k="预计播放 p50" v={fmtNum(fcast.expected_views_p50)} />
            <Drow k="p10 – p90" v={fcast.expected_views_p10 != null ? `${fmtNum(fcast.expected_views_p10)} – ${fmtNum(fcast.expected_views_p90)}` : "—"} />
            <Drow k="互动率" v={fcast.engagement_rate != null ? `${(Number(fcast.engagement_rate) * 100).toFixed(2)}%` : "—"} />
            <Drow k="置信" v={<ConfidenceChip value={fcast.confidence} />} />
          </>
        ) : (
          <Drow k="预测" v={`不可测:${String(fcast.reason || "样本不足")}`} tone="text-muted" />
        )}
      </div>
      <div className="border-t border-line pt-3.5">
        <button type="button" className={ACT_BTN} onClick={openProfile}>
          打开 KOL 档案 →
        </button>
      </div>
    </ModalShell>
  );
}

/* ============ 重排控件(datetime-local → ISO;端点真实返回才置绿) ============ */
function RescheduleRow({
  done,
  busy,
  onSubmit,
}: {
  done: string | undefined;
  busy: boolean;
  onSubmit: (whenIso: string) => void;
}) {
  const [when, setWhen] = React.useState("");
  const submit = () => {
    if (!when) return;
    const d = new Date(when);
    if (!Number.isNaN(d.getTime())) onSubmit(d.toISOString());
  };
  return (
    <span className="flex items-center gap-1.5">
      <input type="datetime-local" aria-label="新发布时间" className={FIELD} value={when} onChange={(ev) => setWhen(ev.target.value)} />
      {done ? (
        <span className={DONE_BTN}>✓ 已重排 {formatLocal(done)}</span>
      ) : (
        <button type="button" className={ACT_BTN} disabled={busy || !when} onClick={submit}>
          {busy ? "更新中…" : "重排"}
        </button>
      )}
    </span>
  );
}

/** 审批/重排/提醒三动作条(内容排期详情与审批详情共用;状态=hooks 端点真实回执)。 */
function PublishActionsBar({
  publish,
  sourceTable,
  sourceId,
  meta,
  approvedAlready,
}: {
  publish: ReturnType<typeof usePublishActions>;
  sourceTable: string;
  sourceId: string;
  meta: { platform?: string; account_handle?: string; title?: string };
  approvedAlready?: boolean;
}) {
  const key = `${sourceTable}:${sourceId}`;
  const state = publish.states[key] || {};
  const busy = publish.busyKey === key;
  const approved = approvedAlready || state.approved;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {approved ? (
        <span className={DONE_BTN}>✓ 已通过</span>
      ) : (
        <button type="button" className={ACT_BTN} disabled={busy} onClick={() => publish.approve(sourceTable, sourceId, meta)}>
          {busy ? "提交中…" : "审批通过"}
        </button>
      )}
      <RescheduleRow done={state.scheduledAt} busy={busy} onSubmit={(iso) => publish.reschedule(sourceTable, sourceId, iso, meta)} />
      {state.reminded ? (
        <span className={DONE_BTN}>✓ 已提醒</span>
      ) : (
        <button type="button" className={ACT_BTN} disabled={busy} onClick={() => publish.remind(sourceTable, sourceId, meta)}>
          提醒 KOL
        </button>
      )}
      {publish.error ? <span className="text-[10.5px] text-crit">{publish.error}</span> : null}
    </div>
  );
}

/* ============ 内容排期单条详情(复核 + 审批三动作 + 连续翻) ============ */
export function PostDetailModal({
  item,
  index,
  total,
  onNav,
  onClose,
  review,
  publish,
}: {
  item: Row;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  review: ReturnType<typeof useContentReview>;
  publish: ReturnType<typeof usePublishActions>;
}) {
  useArrowNav(index, total, onNav);
  const id = Number(item.id) || 0;
  const status = String(review.reviewed[id] ?? item.status ?? "");
  const busy = review.busyId === id;
  const meta = { platform: String(item.platform || ""), account_handle: String(item.project_name || ""), title: String(item.title || "") };
  return (
    <ModalShell
      title={
        <span className="flex items-center gap-2">
          <span className="min-w-0 truncate">{String(item.title || item.content_url || `内容 #${id}`)}</span>
          {statusPill(CONTENT_POST_STATUS, status)}
        </span>
      }
      sub={
        <span className="flex flex-wrap items-center gap-2">
          <span>
            {String(item.platform || "—")} · {String(item.project_name || "—")}
          </span>
          <NavBar index={index} total={total} onNav={onNav} />
        </span>
      }
      onClose={onClose}
    >
      <div className="mb-[22px]">
        <SectionLabel>条目</SectionLabel>
        <Drow k="项目" v={`${String(item.project_name || "—")} · ${String(item.product_name || "—")}`} />
        <Drow
          k="发布时间"
          v={<span title="存 UTC · 按浏览器时区显示">{formatLocal(String(item.published_at || ""))}</span>}
        />
        <Drow k="播放 / 赞 / 评" v={`${fmtNum(item.view_count)} / ${fmtNum(item.like_count)} / ${fmtNum(item.comment_count)}`} />
        {item.matched_terms ? <Drow k="命中词" v={String(item.matched_terms)} /> : null}
        {item.match_confidence != null ? <Drow k="匹配置信" v={String(item.match_confidence)} /> : null}
        {item.match_reason ? <Drow k="匹配理由" v={String(item.match_reason)} /> : null}
        {item.content_url ? (
          <Drow
            k="原帖"
            v={
              <a className="text-accent hover:text-accent-hover" href={String(item.content_url)} target="_blank" rel="noopener noreferrer">
                {String(item.content_url)} ↗
              </a>
            }
          />
        ) : null}
      </div>
      <div className="mb-[22px]">
        <SectionLabel>人工复核</SectionLabel>
        <div className="flex flex-wrap items-center gap-2">
          {status === "matched" ? (
            <span className={DONE_BTN}>✓ 已确认</span>
          ) : (
            <button type="button" className={ACT_BTN} disabled={busy} onClick={() => review.review(id, "matched")}>
              {busy ? "提交中…" : "✓ 确认"}
            </button>
          )}
          {status === "rejected" ? (
            <span className={DONE_BTN}>已剔除</span>
          ) : (
            <button type="button" className={ACT_BTN} disabled={busy} onClick={() => review.review(id, "rejected")}>
              ✕ 剔除
            </button>
          )}
          {status !== "needs_review" && (
            <button type="button" className={ACT_BTN} disabled={busy} onClick={() => review.review(id, "needs_review")}>
              需复核
            </button>
          )}
          {review.error ? <span className="text-[10.5px] text-crit">{review.error}</span> : null}
        </div>
      </div>
      <div className="border-t border-line pt-3.5">
        <SectionLabel>发布审批</SectionLabel>
        <PublishActionsBar publish={publish} sourceTable="vkpi_project_content_posts" sourceId={String(id)} meta={meta} />
      </div>
    </ModalShell>
  );
}

/* ============ 审批条目详情(三动作 + 连续翻) ============ */
export function ApprovalDetailModal({
  item,
  index,
  total,
  onNav,
  onClose,
  publish,
}: {
  item: Row;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  publish: ReturnType<typeof usePublishActions>;
}) {
  useArrowNav(index, total, onNav);
  const key = `${item.source_table}:${item.source_id}`;
  const state = publish.states[key] || {};
  const status = state.approved ? "approved" : state.scheduledAt ? "scheduled" : String(item.status || "pending");
  return (
    <ModalShell
      title={
        <span className="flex items-center gap-2">
          <span className="min-w-0 truncate">{String(item.title || item.account_handle || `审批 #${item.id}`)}</span>
          {statusPill(APPROVAL_STATUS, status)}
        </span>
      }
      sub={
        <span className="flex flex-wrap items-center gap-2">
          <span>
            {String(item.platform || "—")}
            {item.account_handle ? ` · ${item.account_handle}` : ""}
          </span>
          <NavBar index={index} total={total} onNav={onNav} />
        </span>
      }
      onClose={onClose}
    >
      <div className="mb-[22px]">
        <SectionLabel>条目</SectionLabel>
        <Drow k="来源" v={`${String(item.source_table || "—")} #${String(item.source_id || "—")}`} />
        <Drow
          k="计划发布"
          v={<span title="存 UTC · 按浏览器时区显示">{formatLocal(String(state.scheduledAt || item.scheduled_publish_at || ""))}</span>}
        />
        {item.approved_at ? <Drow k="通过于" v={formatLocal(String(item.approved_at))} /> : null}
        {item.reminder_sent_at ? <Drow k="上次提醒" v={formatLocal(String(item.reminder_sent_at))} /> : null}
        <Drow k="创建" v={formatLocal(String(item.created_at || ""))} />
      </div>
      <div className="border-t border-line pt-3.5">
        <PublishActionsBar
          publish={publish}
          sourceTable={String(item.source_table || "")}
          sourceId={String(item.source_id || "")}
          meta={{ platform: String(item.platform || ""), account_handle: String(item.account_handle || ""), title: String(item.title || "") }}
          approvedAlready={String(item.status) === "approved"}
        />
      </div>
    </ModalShell>
  );
}

/* ============ 官号协同全文(卡面一行一条 → 全文进弹窗) ============ */
export function SynergyFullModal({ synergy, onClose }: { synergy: Row; onClose: () => void }) {
  const items: Row[] = Array.isArray(synergy.suggestions) ? synergy.suggestions : [];
  return (
    <ModalShell
      title="官号协同 · 全文"
      sub={
        synergy.status === "ready"
          ? `官号 ${synergy.scanned_posts} 贴命中 ${synergy.matched_posts} 条(${synergy.scope === "sku_focal" ? "SKU 焦段" : "同品类"}口径)`
          : `数据不足降级:${String(synergy.reason || "")}`
      }
      onClose={onClose}
    >
      {items.map((s, i) => (
        <div key={i} className="mb-2.5 rounded-lg border border-line px-3 py-2">
          <div className="text-[12.5px] leading-relaxed text-ink">{String(s.line || "—")}</div>
          {s.basis ? (
            <div className="mt-1 font-mono text-[9.5px] text-muted">
              依据:{String(s.basis.source || "—")}
              {s.basis.sample != null ? ` · ${s.basis.sample} 条样本` : ""}
              {s.basis.metric ? ` · 口径 ${s.basis.metric}` : ""}
            </div>
          ) : null}
        </div>
      ))}
    </ModalShell>
  );
}

/* ============ 内容排期全量列表弹窗(卡面 6 条之外在这里滚) ============ */
export function PostListModal({
  total,
  children,
  onClose,
}: {
  total: number;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <ModalShell title="内容排期 · 全量" sub={`${total} 条 · 点单条连续翻`} onClose={onClose}>
      {children}
    </ModalShell>
  );
}
