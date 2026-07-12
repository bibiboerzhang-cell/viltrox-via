import React from "react";
import { KpiCard } from "./MarketVoicePage.modules";

// KOL 池 · 板块页辅助件(KolPoolBoardPage 专用,页内拆件不入公共桶;
//   金样板 = KolProfileBoardPage.modules / MyKolBoardPage.modules 同构)。
//   MODULE_SOURCES 溯源注册表(label=真实端点/表名,rows=2026-07-12 本地实测口径,禁编造)/
//   PoolKpiBand KPI 带四卡(全部点时快照,无历史时序端点 → 四卡诚实 spempty 虚线,
//   永不编 series/环比)/ NeedsBody 待深析清单(批量入队动作在 page 层回调)。
// 红线:本文件零直连网络(取数住 page 层/actions hooks);fit 分只读展示绝不写回;
//   颜色全 token 类零写死色;零 opacity 修饰类;诚实空态;卡面零技术术语
//   (表名/端点/闸口径只进 SrcChip rows / tooltip)。

export type Row = Record<string, any>;

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构;计数=2026-07-12 本地实测) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiK: {
    label: "vkpi_kol_pool · kol-pool/summary",
    rows: [
      ["在池总数", "vkpi_kol_pool 非重复行(1,237 · duplicate_of_id IS NULL · 分页取尽)"],
      ["本周新发现", "created_at ≥ 近 7 天(UTC 存)· 2026-07-12 实测 15"],
      ["已深析", "vkpi_kol_llm_deep_analysis_results(830 行 · 覆盖 334 KOL · 2026-07-12 实测)"],
      ["暂不推荐", "raw_platform_data.low_reach 标(补全回填后重过闸,81 · kol-pool/summary 计数)· 已入库仅不推荐,行保留不删"],
      ["趋势线", "四指标全点时快照,无历史时序端点 → 诚实虚线零环比,绝不编 series"],
      ["红线", "fit 分只读展示 · 本页零写库零打分"],
    ],
  },
  smart: {
    label: "smart-search · vkpi_kol_search_sessions",
    rows: [
      ["会话", "vkpi_kol_search_sessions(813)+ _items(1,807)· 2026-07-12 实测"],
      ["一个框", "URL 看资料 / 建档 / 视频分析 / 语义召回 · 模式三档改真实请求参数"],
      ["展示闸", "低触达/分析中折叠计数由后端按池现值实时重判(分析后再推荐)· 行为原样"],
      ["入库", "全网新发现即时轻量入库(仅基础资料,不触评分)· 搜到≠归我,勾选才收藏"],
    ],
  },
  recs: {
    label: "vkpi_kol_pool · kol-pool/{id}/signature",
    rows: [
      ["卡片", "池行 + 招牌一行(signature 纯聚合,懒加载会话缓存,失败安静缺席)"],
      ["排序", "V6 Fit 只读展示(rule_v0 既有产物,绝不回写)· 可切实测互动率/粉丝等"],
      ["筛选", "卡片流与表格视图共用同一份筛选与排序"],
    ],
  },
  kinds: {
    label: "vkpi_kol_pool · 前端推导",
    rows: [
      ["新/已有", "按 linked_main_kol_id 推导(candidate_kind 列待后端)· 卡内如实标注"],
      ["播放量汇总", "Σ avg_views(611/1,237 有值)· 非去重触达,如实标注"],
      ["待补全", "分类列缺失 → 诚实灰卡不可点,接真后恢复"],
      ["总数卡", "点开 = 全量池表大窗(搜索 + 平台分组)"],
    ],
  },
  needs: {
    label: "kol-pool/needs-analysis",
    rows: [
      ["口径", "库内有视频证据但无 ready 深析产物的 KOL(上限 50 一批)"],
      ["证据表", "vkpi_kol_video_evidence(2,868 活跃行 · 2026-07-12 实测)"],
      ["入队", "批量进后台分析队列 · 单进程串行处理,预算闸在后端"],
    ],
  },
  lanes: {
    label: "workflow_runs · 任务队列",
    rows: [
      ["泳道", "搜索中 / 思考中 / 总结中 + 排队区(真实执行序 queue_position)"],
      ["刷新", "5s 轮询 compact 投影 · 页面切后台自动停"],
      ["失败", "失败任务可点重试 · 只看我的开关记本机"],
    ],
  },
  coverage: {
    label: "vkpi_kol_pool · 前端聚合",
    rows: [
      ["口径", "estimated_country_reach = 档案国家 × 均播放(前端推导,非去重触达)"],
      ["缺口", "重点市场(FR/KR/ES/IT)KOL < 3 → 建议补位,一键切发现模式"],
      ["GMV", "未接入 → 如实占位,绝不摆假数"],
    ],
  },
  table: {
    label: "vkpi_kol_pool · 列表",
    rows: [
      ["行", "与卡片流共用同一份筛选与排序 · 虚拟滚动全量"],
      ["详情", "点行打开详情抽屉(收藏/联系/补全/合作方案草案都在抽屉里)"],
    ],
  },
};

/* ============ 小工具(金样板同构) ============ */

export function fmtZhCompact(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

/* ============ KPI 带四卡:在池总数 / 本周新发现 / 已深析 / 暂不推荐(低触达隐藏)。
   四指标全部 = 请求时点读数(池行聚合 + summary 计数),无历史时序端点 →
   四卡 series 缺席 = KpiCard 自动 spempty 诚实虚线,永不编趋势/环比。 ============ */
export function PoolKpiBand({
  total,
  weekNew,
  deepKols,
  deepRows,
  lowReachHidden,
  loading,
}: {
  total: number;
  /** null = 池行还没带 created_at 字段(本机旧缓存投影)→ 诚实 pending */
  weekNew: number | null;
  deepKols: number;
  /** 深析结果总条数(行级计数,tooltip 用) */
  deepRows: number;
  /** null = summary 未返回该键(旧后端/读取失败)→ 诚实 pending */
  lowReachHidden: number | null;
  /** 池行仍在读取(首屏)→ 四卡诚实 pending */
  loading: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="在池总数"
        value={total.toLocaleString()}
        unit="KOL"
        pending={loading || total === 0}
        pendingNote={loading ? "池数据读取中" : "暂无入池 KOL"}
      />
      <KpiCard
        label="本周新发现"
        value={weekNew === null ? "—" : weekNew.toLocaleString()}
        unit="KOL"
        pending={loading || weekNew === null}
        pendingNote={loading ? "池数据读取中" : "刷新后可用"}
      />
      <KpiCard
        label="已深析"
        value={deepKols.toLocaleString()}
        unit={deepRows > 0 ? `KOL · ${deepRows.toLocaleString()} 份结果` : "KOL"}
        pending={loading}
        pendingNote="池数据读取中"
      />
      <KpiCard
        label="低触达 · 暂不推荐"
        value={lowReachHidden === null ? "—" : lowReachHidden.toLocaleString()}
        unit="KOL"
        tone="warn"
        pending={lowReachHidden === null}
        pendingNote="计数暂不可用"
      />
    </div>
  );
}

/* ============ 待深析清单(旧页「待分析」折叠区 → 模块真身;动作在 page 层回调) ============ */
export function NeedsBody({
  items,
  loading,
  busy,
  msg,
  onRunBatch,
}: {
  items: Row[];
  loading: boolean;
  busy: boolean;
  msg: string;
  onRunBatch: () => void;
}) {
  if (loading && items.length === 0) {
    return <div className="py-6 text-center text-[12px] text-muted">清单读取中…</div>;
  }
  if (items.length === 0) {
    return (
      <div className="rounded-[9px] border border-dashed border-line px-3 py-2.5 text-[10.5px] leading-[1.7] text-muted">
        <b className="font-semibold text-ink-2">全部分析完</b> —— 库内有视频的 KOL 都已有分析结果;新视频入库后自动出现在这里。
      </div>
    );
  }
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[10.5px] text-muted">{items.length}{items.length >= 50 ? "+" : ""} 位 KOL 有视频还没分析</span>
        <button
          type="button"
          onClick={onRunBatch}
          disabled={busy || !items.length}
          className="rounded-lg border border-accent bg-accent-soft px-2.5 py-1 text-[10.5px] font-medium text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
        >
          {busy ? "入队中…" : `全部分析 (${items.length})`}
        </button>
      </div>
      {msg ? <div className="mb-2 text-[10px] text-good">{msg}</div> : null}
      <div className="space-y-0.5">
        {items.slice(0, 50).map((it: any) => (
          <div key={it.kol_pool_id} className="flex items-center gap-2 rounded-[7px] px-1.5 py-1 text-[10.5px] text-ink-2 hover:bg-card">
            <span className="min-w-0 flex-1 truncate font-medium">{it.handle || `#${it.kol_pool_id}`}</span>
            <span className="flex-none text-muted">{it.platform}</span>
            <span className="w-12 flex-none text-right font-mono text-muted">{it.evidence_count || 0} 视频</span>
          </div>
        ))}
      </div>
    </div>
  );
}
