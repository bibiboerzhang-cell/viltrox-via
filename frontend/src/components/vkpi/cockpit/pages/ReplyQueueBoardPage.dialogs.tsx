import React from "react";
import { ProvChain, RecordPreview, type ProvStep } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import type { ReplyQueueItem } from "../../../../services/vkpi/replyQueue-api";
import { Drow, ModalShell, SectionLabel, platformBadge } from "./MarketVoicePage.dialogs";
import { STATUS_META, intentMeta, langLabel } from "./ReplyQueueBoardPage.modules";
import type { DraftReceipt } from "./ReplyQueueBoardPage.actions";

// 回复队列 · 弹窗族(金样板 MarketVoicePage.dialogs 同构;弹窗骨架 ModalShell/
//   SectionLabel/Drow 全复用零自造样式;行数纪律 ≤700/文件)。
//   QueueListModal   全量列表(队列 ≤500 已一次拉齐,零分页;行由调用方 children 传入)。
//   QueueDetailModal 单条详情:‹#n/N› + ↑↓ 连续翻 + 原文/草稿 + 数值行 + 溯源链
//                    (链回 vkpi_comments 源评论,库节点开 RecordPreview)+ 闭环动作行
//                    (生成草稿/复制/标记已回/忽略,全真端点,由页层适配器注入)。
// 红线:零直连网络(动作走调用方回调);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 类零写死色;零 opacity 修饰类;绝对时间戳(UTC 存 · 浏览器时区显示);
//   provider/表名等术语只进溯源区,不上按钮门面。

/* ============ 全量列表弹窗 ============ */

export function QueueListModal({
  total,
  filterLabel,
  onClose,
  children,
}: {
  total: number;
  /** 当前过滤口径(如「待起草」「平台 IG」);空 = 全部 */
  filterLabel?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <ModalShell
      title="回复队列 · 全量"
      sub={`${filterLabel ? `${filterLabel} · ` : ""}共 ${total} 条 · 点单条看详情(详情内 ↑↓ 连续翻)`}
      onClose={onClose}
    >
      <SectionLabel>全量队列</SectionLabel>
      {children}
    </ModalShell>
  );
}

/* ============ 单条详情:数值行 / 溯源链 / 记录预览 ============ */

function recordRows(item: ReplyQueueItem, rec: string, draftReceipt?: DraftReceipt): Array<[string, string]> {
  if (rec === "comment") {
    return [
      ["表", "vkpi_comments"],
      ["回链键", "platform + external_comment_id(与队列幂等键同构)"],
      ["platform", item.platform || "—"],
      ["external_comment_id", item.comment_external_id || "—"],
      ["说明", "队列行由该幂等键链回源评论(入队即由源评论生成)"],
    ];
  }
  if (rec === "screen") {
    return [
      ["方法", "多语种购买意向词表 · 纯规则(零模型评分)"],
      ["intent_tag", item.intent_tag || "—"],
      ["入口", "全量扫描(screen)/ 市场之声单条转入(enqueue-comment)"],
      ["manual", "词表未命中的人工点名入队,如实标注"],
    ];
  }
  if (rec === "draft") {
    return [
      ["字段", "vkpi_reply_queue.draft_reply"],
      ["链路", "检索产品目录(vkpi_products)→ 预算闸先行 · 不足降级模板"],
      ["生成方", draftReceipt?.provider || "—(本会话外生成,方式未记录)"],
      ["检索 SKU", draftReceipt?.retrievedSkus?.length ? draftReceipt.retrievedSkus.join(", ") : "—"],
      ["铁律", "只到草稿,人工复制手动回,绝不自动发帖"],
    ];
  }
  const st = STATUS_META[String(item.status || "").toLowerCase()];
  return [
    ["表", "vkpi_reply_queue"],
    ["id", `#${item.id}`],
    ["status", `${item.status}${st ? ` · ${st.label}` : ""}`],
    ["claimed_by", item.claimed_by != null ? `#${item.claimed_by}` : "未认领"],
    ["claimed_at", item.claimed_at || "—"],
    ["created_at", item.created_at || "—"],
    ["updated_at", item.updated_at || "—"],
  ];
}

const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:border-line disabled:text-muted";

const ACT_BTN =
  "flex-1 rounded-lg border px-3 py-2 text-center text-[11.5px] transition-colors disabled:cursor-default";

/** 详情闭环动作适配器(页层注入;端点真实返回才落状态,gone 不写 ✓)。 */
export interface QueueDetailActions {
  draftBusy: boolean;
  draftError: string;
  /** 缺省 = 终态不回炉,按钮 disabled */
  onDraft?: () => void;
  copied: boolean;
  onCopy: () => void;
  markBusy: boolean;
  markError: string;
  onMarkReplied?: () => void;
  onMarkDismissed?: () => void;
  /** 本会话草稿回执(provider/检索 SKU 真值);缺省 = 「本会话外生成」诚实占位 */
  draftReceipt?: DraftReceipt;
  /** kol_pool_id 命中 KOL 池 → 溯源链身份节点可跳档案;缺省 = 不渲染身份节点 */
  onIdentityJump?: () => void;
}

export function QueueDetailModal({
  item,
  index,
  total,
  onNav,
  onClose,
  actions,
}: {
  item: ReplyQueueItem;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  actions: QueueDetailActions;
}) {
  const [rec, setRec] = React.useState<string | null>(null);

  // 切条时收起记录预览(每条用自己的记录链)
  React.useEffect(() => {
    setRec(null);
  }, [item?.id]);

  // ↑↓(以及 ←→)方向键连续翻(金样板同款);Escape 交给 ModalShell。
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

  const st = STATUS_META[String(item.status || "").toLowerCase()] || STATUS_META.pending;
  const intent = intentMeta(item.intent_tag);
  const terminal = ["replied", "dismissed"].includes(String(item.status || "").toLowerCase());

  const steps: ProvStep[] = [
    // 源评论无帖级 URL(队列表不带 post 链接,诚实不摆假外链);库节点开记录预览
    { label: `源评论 vkpi_comments · ${platformBadge(item.platform)}`, rec: "comment" },
    ...(item.kol_pool_id != null && actions.onIdentityJump
      ? [{ label: `身份 KOL #${item.kol_pool_id} →跳档案`, rec: "identity" } as ProvStep]
      : item.kol_pool_id != null
        ? [{ label: `身份 KOL #${item.kol_pool_id}` } as ProvStep]
        : []),
    { label: "意向筛选 · 规则词表", rec: "screen" },
    { label: `队列行 vkpi_reply_queue #${item.id}`, rec: "queue", rid: String(item.id) },
    ...(item.draft_reply ? [{ label: "草稿 draft_reply", rec: "draft" } as ProvStep] : []),
  ];

  return (
    <ModalShell
      title={`队列详情 · ${platformBadge(item.platform)}`}
      sub={
        <>
          建队 {formatLocal(item.created_at, { year: "numeric" })}(按浏览器时区)· 状态{" "}
          <span className={st.cls.split(" ")[1] || "text-ink"}>{st.label}</span>
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

      <div className="mb-[22px]">
        <SectionLabel>评论原文</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink-2">{item.comment_text || "—"}</div>
      </div>

      {item.draft_reply ? (
        <div className="mb-[22px]">
          <SectionLabel>回复草稿(人工复制手动回 · 不自动发帖)</SectionLabel>
          <div className="rounded-[11px] border border-accent bg-accent-soft px-3.5 py-2.5 text-[12.5px] leading-relaxed text-ink-2">
            {item.draft_reply}
          </div>
        </div>
      ) : null}

      <div className="mb-[22px]">
        <SectionLabel>数值行</SectionLabel>
        <Drow k="意向" v={intent.label} tone={intent.cls.split(" ")[1] || "text-ink"} />
        <Drow k="状态" v={st.label} tone={st.cls.split(" ")[1] || "text-ink"} />
        <Drow k="平台" v={item.platform || "—"} />
        <Drow k="语言" v={langLabel(item.lang)} />
        <Drow k="认领" v={item.claimed_by != null ? `#${item.claimed_by} · ${formatLocal(item.claimed_at, { year: "numeric" })}` : "未认领"} />
        <Drow k="建队(本地)" v={formatLocal(item.created_at, { year: "numeric" })} />
        <Drow k="created_at(UTC)" v={item.created_at || "—"} />
        <Drow k="最近更新(本地)" v={formatLocal(item.updated_at, { year: "numeric" })} />
      </div>

      <div>
        <SectionLabel>溯源链 · 每一跳可点(本条专属记录)</SectionLabel>
        <ProvChain
          steps={steps}
          onRecord={(key) => {
            if (key === "identity") actions.onIdentityJump?.();
            else setRec(key);
          }}
        />
        {rec ? (
          <RecordPreview
            title="库记录预览 · 点其他节点切换"
            rows={recordRows(item, rec, actions.draftReceipt)}
          />
        ) : null}
      </div>

      {/* 闭环动作行(金样板 .aacts 同构):全真端点,端点真实返回才落状态 */}
      <div className="mt-[22px] flex flex-wrap gap-2 border-t border-line pt-3.5">
        <button
          type="button"
          disabled={actions.draftBusy || !actions.onDraft}
          onClick={actions.onDraft}
          title={
            !actions.onDraft
              ? "终态(已回复/已忽略)不回炉,不重复起草"
              : item.draft_reply
                ? "重新生成回复草稿(POST /reply-queue/{id}/draft,后端原子认领防并发)"
                : "生成品牌口吻回复草稿(POST /reply-queue/{id}/draft)"
          }
          className={`${ACT_BTN} border-line text-ink-2 hover:border-accent hover:bg-accent-soft hover:text-accent disabled:border-line disabled:text-muted`}
        >
          {actions.draftBusy ? "生成中…" : item.draft_reply ? "✎ 重新起草" : "✎ 生成草稿"}
        </button>
        <button
          type="button"
          disabled={!item.draft_reply}
          onClick={actions.onCopy}
          title={item.draft_reply ? "复制草稿到剪贴板,去平台人工回复" : "暂无草稿可复制"}
          className={`${ACT_BTN} ${
            actions.copied
              ? "border-good bg-good-soft text-good"
              : "border-line text-ink-2 hover:border-accent hover:bg-accent-soft hover:text-accent disabled:border-line disabled:text-muted"
          }`}
        >
          {actions.copied ? "✓ 已复制" : "⧉ 复制草稿"}
        </button>
        <button
          type="button"
          disabled={actions.markBusy || !actions.onMarkReplied}
          onClick={actions.onMarkReplied}
          title={
            !actions.onMarkReplied
              ? "该条已是「已回复」"
              : "人工回复完成后标记(POST /reply-queue/{id}/mark · 带乐观锁)"
          }
          className={`${ACT_BTN} ${
            String(item.status).toLowerCase() === "replied"
              ? "border-good bg-good-soft text-good"
              : "border-line text-ink-2 hover:border-good hover:bg-good-soft hover:text-good disabled:border-line disabled:text-muted"
          }`}
        >
          {String(item.status).toLowerCase() === "replied" ? "✓ 已回复" : actions.markBusy ? "标记中…" : "标记已回"}
        </button>
        <button
          type="button"
          disabled={actions.markBusy || !actions.onMarkDismissed}
          onClick={actions.onMarkDismissed}
          title={!actions.onMarkDismissed ? "该条已是「已忽略」" : "不值得回的队列项,标记忽略(可追溯,不删行)"}
          className={`${ACT_BTN} border-line text-muted hover:border-line-strong hover:text-ink disabled:border-line disabled:text-muted`}
        >
          {String(item.status).toLowerCase() === "dismissed" ? "已忽略" : "忽略"}
        </button>
      </div>
      {actions.draftError ? (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">
          生成草稿失败:{actions.draftError}
        </div>
      ) : null}
      {actions.markError ? (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">
          标记失败:{actions.markError}
        </div>
      ) : null}
      {terminal ? (
        <div className="mt-1.5 text-right text-[9.5px] text-muted">
          终态不回炉 · 历史行保留可追溯(vkpi_reply_queue 不删行)
        </div>
      ) : (
        <div className="mt-1.5 text-right text-[9.5px] text-muted">
          链路:起草 → 复制 → 人工回 → 标记 · 端点真实返回才落状态,绝不自动发帖
        </div>
      )}
    </ModalShell>
  );
}
