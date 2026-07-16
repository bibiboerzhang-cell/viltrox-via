import React from "react";
import { ArrowUpRight } from "lucide-react";
import { SrcChip } from "../components/provenance";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard, type Row } from "./MarketVoicePage.modules";

// Dashboard 跨板块拉卡(task #76)· 共享卡壳与取数件。
//   本文件只被各 crossBoardModules.<board>.tsx 卡文件 import(全部 React.lazy 进
//   Dashboard),绝不被注册表 crossBoardModules.tsx 静态引用 —— 否则 SrcChip /
//   MarketVoicePage.modules 会被拖进 Dashboard 首屏 chunk。
//   XbCard = 金样板 ModuleCard(MarketVoicePage.modules)同构卡壳 + 卡头「来源板块」
//   小徽(点击跳源板块,onNavigate 管道);SrcChip 沿用各源板块 MODULE_SOURCES 口径
//   (hover 口径卡;溯源弹窗留在源板块,不冒充)。
// 红线:纯展示零写端点;颜色全 token 零写死色;诚实空态/错误卡沿源板块闸口径;
//   动效只用既有 ds-* 类(自带 reduced-motion 降级)。

export interface XbCardProps {
  title: string;
  /** demo .cnt:accent-soft 短徽,只放短计数;拿不到真数就不渲染(诚实口径同源板块) */
  cnt?: React.ReactNode;
  srcLabel: string;
  srcRows: Array<[string, string]>;
  /** 来源板块名(卡头小徽文案,NAV_ITEMS label 同文;点击跳源板块) */
  boardLabel: string;
  onOpenBoard: () => void;
  statusLabel?: string;
  children: React.ReactNode;
}

/** ModuleCard 同构卡壳 + 来源板块徽(徽在 SrcChip 左侧,点击 = 跳源板块)。 */
export function XbCard({ title, cnt, srcLabel, srcRows, boardLabel, onOpenBoard, statusLabel = "实时", children }: XbCardProps) {
  return (
    <section className="ds-mod ds-rise flex h-full min-h-0 flex-col">
      <header className="flex flex-none items-center justify-between gap-2.5 px-4 pb-2 pt-[13px]">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
          {cnt != null && (
            <span className="flex-none rounded-md bg-accent-soft px-[6px] py-px text-[9.5px] font-semibold text-accent">{cnt}</span>
          )}
        </div>
        <span className="flex flex-none items-center gap-2">
          <button
            type="button"
            onClick={onOpenBoard}
            title={`打开 ${boardLabel} 板块`}
            aria-label={`打开 ${boardLabel} 板块`}
            className="flex flex-none items-center gap-0.5 rounded-[7px] border border-line bg-card px-1.5 py-px text-[9.5px] font-semibold text-muted transition-colors hover:border-accent hover:text-accent"
          >
            {boardLabel}
            <ArrowUpRight size={9} />
          </button>
          <SrcChip label={srcLabel} rows={srcRows} />
          {/* 数据为挂载时实取 → 诚实「实时」eyebrow(ModuleCard 同款固定件) */}
          <span className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-muted">{statusLabel}</span>
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">{children}</div>
    </section>
  );
}

/** 未登录卡(各源板块 noTokenCard 同款口径)。 */
export function xbNoToken(boardLabel: string): React.ReactNode {
  return (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载{boardLabel}数据。
    </PendingCard>
  );
}

/**
 * 通用自带取数 hook(源板块页级 effect 同构:alive 旗防晚到响应;挂载即取一次)。
 * fetcher 必须引用稳定(模块级函数或 useCallback),否则会循环重取。
 */
export function useXbFetch<T>(apiToken: string, fetcher: (token: string) => Promise<T>) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    setData(null);
    fetcher(apiToken)
      .then((res) => {
        if (alive) setData((res ?? null) as T | null);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setError(String(detail.detail || detail.message || "读取失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, fetcher]);
  return { data, error, loading };
}

/**
 * 逐组闸(金样板 extGate 同构:端点错 → ErrorCard,组缺席 → PendingCard,
 * 组 status error/empty → 逐组诚实降级;null = 放行画图)。
 */
export function xbGroupGate(opts: {
  apiToken: string;
  boardLabel: string;
  /** 端点失败错误卡标题(如实标端点名,源板块同口径) */
  errorTitle: string;
  error: string;
  /** 响应是否已到手(data != null) */
  loaded: boolean;
  loadingText: string;
  group?: Row | null;
}): React.ReactNode | null {
  if (!opts.apiToken) return xbNoToken(opts.boardLabel);
  if (opts.error) return <ErrorCard title={opts.errorTitle} text={opts.error} />;
  if (!opts.loaded) return <LoadingLine text={opts.loadingText} />;
  if (!opts.group) {
    return (
      <PendingCard>
        <b>该组字段缺席</b> —— 端点未返回本组,接通后自动点亮(不摆假图)。
      </PendingCard>
    );
  }
  if (String(opts.group.status) === "error") return <ErrorCard title="该组聚合失败" text={String(opts.group.reason || "未知原因")} />;
  if (String(opts.group.status) === "empty") return <EmptyLine text={String(opts.group.reason || "窗口内无本组数据。")} />;
  return null;
}

export type { Row };
