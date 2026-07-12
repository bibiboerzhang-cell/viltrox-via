import React from "react";
import { Drow, ModalShell, SectionLabel, platformBadge } from "./MarketVoicePage.dialogs";
import { ContentRowLine, MATCH_FIELD_LABEL } from "./Sku360BoardPage.modules";
import { asRow, fmtZhCompact, num, str, type Row } from "./Sku360BoardPage.charts";

// SKU 360° · 弹窗族(Sku360BoardPage 专用;骨架 ModalShell/Drow/SectionLabel 复用金样板)。
//   ContentListModal:提及内容全量列表(范式要素⑤「全量 + 连续翻」;数据端点单次
//   最多回传 100 条,弹窗内一次给全,无分页假象)。
//   ContentDetailModal:单条详情 ‹ #n/N › + ↑↓ 方向键连续翻;数值行 + 命中依据 +
//   溯源行(证据表 id / 深析层 / 命中别名)+ 原帖 ↗;底部跨板块下钻 =「KOL 档案 →」
//   (复用既有 vkpi:open-kol-profile 事件管道,零重造)。
// 红线:纯展示零网络;颜色全 token;零 opacity 修饰类;发布日期=源数据日粒度,
//   原样绝对展示不装时分。

const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted";

export function ContentListModal({
  items,
  sku,
  onOpenDetail,
  onClose,
}: {
  items: Row[];
  sku: string;
  onOpenDetail: (i: number) => void;
  onClose: () => void;
}) {
  return (
    <ModalShell
      title="提及内容 · 全量"
      sub={`${sku} · 共 ${items.length} 条(端点单次上限 100)· 点单条看详情(详情内 ↑↓ 连续翻)· 每条「↗」直跳原内容`}
      onClose={onClose}
    >
      <SectionLabel>全量清单 · 按互动降序</SectionLabel>
      {items.map((it, i) => (
        <ContentRowLine key={i} item={it} index={i} onOpen={onOpenDetail} />
      ))}
    </ModalShell>
  );
}

export function ContentDetailModal({
  item,
  index,
  total,
  onNav,
  onClose,
  onOpenKol,
}: {
  item: Row;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  /** 跨板块下钻:打开 KOL 档案(page 层接既有事件管道);缺省 = 按钮不出现 */
  onOpenKol?: (kolPoolId: number) => void;
}) {
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

  const match = asRow(item.match);
  const kol = asRow(item.kol);
  const kolId = num(kol?.kol_pool_id);
  const kolName = str(kol?.display_name) || str(kol?.handle) || (kolId !== null ? `#${kolId}` : "—");
  const url = str(item.content_url);
  const deep = item.has_deep_analysis === true;
  const fieldLabel = MATCH_FIELD_LABEL[str(match?.field)] || str(match?.field) || "—";
  const marketingValue = num(item.marketing_value_score);

  return (
    <ModalShell
      title={`内容详情 · ${platformBadge(str(item.platform))}`}
      sub={`发布 ${str(item.posted_at) || "—"}(源数据日粒度)· 创作者 ${kolName}`}
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
        <SectionLabel>标题</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink-2">{str(item.title) || "—"}</div>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>数值行</SectionLabel>
        <Drow k="曝光(实测播放)" v={fmtZhCompact(num(item.view_count))} />
        <Drow k="点赞" v={fmtZhCompact(num(item.like_count))} />
        <Drow k="评论" v={fmtZhCompact(num(item.comment_count))} />
        <Drow k="互动(赞+评)" v={fmtZhCompact(num(item.engagement))} />
        {marketingValue !== null ? <Drow k="深析·营销价值读数" v={String(marketingValue)} tone="text-accent" /> : null}
        <Drow k="发布日期" v={str(item.posted_at) || "—"} />
      </div>

      <div className="mb-[22px]">
        <SectionLabel>命中与溯源</SectionLabel>
        <Drow k="命中依据" v={fieldLabel} />
        <Drow k="命中别名" v={str(match?.alias) || "—"} />
        {num(match?.confidence) !== null ? <Drow k="别名置信" v={`${Math.round((num(match?.confidence) ?? 0) * 100)}%`} /> : null}
        <Drow k="深析覆盖" v={deep ? "已深析(五层命中口径)" : "未深析(仅标题层命中)"} tone={deep ? "text-good" : "text-warn"} />
        <Drow k="证据记录" v={`vkpi_kol_video_evidence #${num(item.evidence_id) ?? "—"}`} />
        {deep ? <Drow k="深析产物" v="vkpi_analysis_cache · 视频深析" /> : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg border border-accent bg-accent-soft px-2.5 py-1.5 text-[11px] text-accent transition-colors hover:border-accent-hover"
          >
            原内容 ↗
          </a>
        ) : (
          <span className="text-[10.5px] text-muted">原帖 URL 缺失(该源无内容链接)</span>
        )}
        {onOpenKol && kolId !== null ? (
          <button
            type="button"
            onClick={() => onOpenKol(kolId)}
            className="rounded-lg border border-line px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
          >
            KOL 档案 →
          </button>
        ) : null}
      </div>
    </ModalShell>
  );
}
