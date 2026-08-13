import React from "react";
import { kolHumanDisplayName } from "../lib/kolIdentity";
import { KpiCard } from "./MarketVoicePage.modules";
import { ModalShell } from "./MarketVoicePage.dialogs";

// KOL 池 · 板块页辅助件(KolPoolBoardPage 专用,页内拆件不入公共桶;
//   金样板 = KolProfileBoardPage.modules / MyKolBoardPage.modules 同构)。
//   MODULE_SOURCES 溯源注册表(label=真实端点/表名,rows=2026-07-12 本地实测口径,禁编造)/
//   PoolKpiBand KPI 带四卡(全部点时快照,无历史时序端点 → 四卡诚实 spempty 虚线,
//   永不编 series/环比)/ NeedsBody 待深析清单(批量入队动作在 page 层回调)/
//   QuickProjectModal 快捷入项目小窗(纯展示件:项目下拉 + 确认;取数/写入住
//   actions.useQuickAddToProject,ModalShell 复用金样板弹窗骨架)。
// 红线:本文件零直连网络(取数住 page 层/actions hooks);fit 分只读展示绝不写回;
//   颜色全 token 类零写死色;零 opacity 修饰类;诚实空态;卡面零技术术语
//   (表名/端点/闸口径只进 SrcChip rows / tooltip)。

export type Row = Record<string, any>;

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构;只写动态口径,不固化历史快照) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiK: {
    label: "vkpi_kol_pool · kol-pool/summary",
    rows: [
      ["在池总数", "vkpi_kol_pool 非重复行(duplicate_of_id IS NULL)· 当前值由列表实时取尽"],
      ["本周新发现", "created_at ≥ 近 7 天(UTC 存)· 当前值由页面实时计算"],
      ["已深析", "vkpi_kol_llm_deep_analysis_results · 当前覆盖与结果数由列表 payload 实时聚合"],
      ["暂不推荐", "raw_platform_data.low_reach 标 · 当前值由 kol-pool/summary 返回;接口缺失则显示不可确认"],
      ["趋势线", "四指标全点时快照,无历史时序端点 → 诚实虚线零环比,绝不编 series"],
      ["红线", "fit 分只读展示 · 本页零写库零打分"],
    ],
  },
  smart: {
    label: "smart-search · vkpi_kol_search_sessions",
    rows: [
      ["会话", "vkpi_kol_search_sessions + items · 当前数量以实时搜索历史接口为准"],
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
      ["播放量汇总", "Σ avg_views(仅统计真实有值行)· 非去重触达,如实标注"],
      ["待补全", "分类列缺失 → 诚实灰卡不可点,接真后恢复"],
      ["总数卡", "点开 = 全量池表大窗(搜索 + 平台分组)"],
      ["去向", "2026-07-12 默认布局撤出 → palette 备选;分类点击筛选在「推荐 · 卡片流」筛选条保留,总数大窗入口移至卡片流工具行,均 Fit 由「Fit 分布」直方替代"],
    ],
  },
  fitDist: {
    label: "vkpi_kol_pool.viltrox_fit_score · 前端分桶",
    rows: [
      ["口径", "全池行按 V6 Fit 十分位分 10 桶 + 未评分诚实桶(列表 payload 前端只读分桶)"],
      ["未评分", "fit 为空的行独立灰桶,绝不当 0 分"],
      ["红线", "既有分数只读展示 · 本页零打分零写回(rule_v0 既有产物)"],
    ],
  },
  platDist: {
    label: "vkpi_kol_pool.platform · 前端聚合",
    rows: [
      ["口径", "全池行按 platform 计数(与 kol-pool/summary.by_platform 同径)"],
      ["未标平台", "platform 空值归「未标平台」灰行,如实入总数"],
      ["联动", "平台维度筛选待接 → 纯展示行零假按钮"],
    ],
  },
  funnel: {
    label: "kol-pool/summary.discovery_funnel_30d",
    rows: [
      ["发现", "vkpi_kol_search_session_items 近 30 天条目(找达人产出,含在库命中)"],
      ["自动入库", "vkpi_kol_pool 近 30 天新建非重复行(搜到自动落池)"],
      ["已深析", "vkpi_kol_llm_deep_analysis_results 近 30 天 ready 覆盖 KOL 数"],
      ["已收藏", "vkpi_kol_pool_favorites 近 30 天收藏覆盖 KOL 数(收藏=归我)"],
      ["口径", "四段同窗各自计数(非严格同批追踪)· 段算不出=键缺席 → 灰行诚实缺席"],
    ],
  },
  needs: {
    label: "kol-pool/needs-analysis",
    rows: [
      ["口径", "库内有视频证据但无 ready 深析产物的 KOL(上限 50 一批)"],
      ["证据表", "vkpi_kol_video_evidence · 当前值由 needs-analysis 端点实时返回"],
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
            <span className="min-w-0 flex-1 truncate font-medium">{kolHumanDisplayName(it)}</span>
            <span className="flex-none text-muted">{it.platform}</span>
            <span className="w-12 flex-none text-right font-mono text-muted">{it.evidence_count || 0} 视频</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============ 快捷入项目小窗(卡片流行内「入项目」→ 不开抽屉直达;纯展示件,
   项目列表/写入回执全由 page 层 useQuickAddToProject 注入) ============ */
export function QuickProjectModal({
  item,
  projects,
  projectsError,
  busy,
  msg,
  onConfirm,
  onClose,
}: {
  item: Row;
  /** null = 读取中;[] = 真空列表或读取失败(以 projectsError 区分) */
  projects: Row[] | null;
  projectsError: string;
  busy: boolean;
  msg: { text: string; tone: "ok" | "error" | "info" } | null;
  onConfirm: (projectId: string) => void;
  onClose: () => void;
}) {
  const [projId, setProjId] = React.useState("");
  const options = (Array.isArray(projects) ? projects : [])
    .map((p: any) => {
      const raw = p?.id ?? p?.project_id;
      if (raw === undefined || raw === null || raw === "") return null;
      return { id: String(raw), name: String(p?.project_name || p?.name || p?.title || `项目 ${raw}`) };
    })
    .filter(Boolean) as Array<{ id: string; name: string }>;
  const toneCls = msg?.tone === "ok" ? "text-good" : msg?.tone === "error" ? "text-crit" : "text-muted";
  return (
    <ModalShell
      title="入项目"
      sub={`${kolHumanDisplayName(item)} · 加入现有项目(动作幂等,可重复确认)`}
      onClose={onClose}
      maxWidth="max-w-[420px]"
    >
      {projects === null && !projectsError ? (
        <div className="py-4 text-center text-[12px] text-muted">项目列表读取中…</div>
      ) : projectsError ? (
        <div className="py-4 text-center text-[12px] text-crit">{projectsError}</div>
      ) : options.length === 0 ? (
        <div className="rounded-[9px] border border-dashed border-line px-3 py-2.5 text-[10.5px] leading-[1.7] text-muted">
          <b className="font-semibold text-ink-2">暂无可选项目</b> —— 先在「项目」板块建项目,再回这里一键加入。
        </div>
      ) : (
        <div>
          <label className="mb-1.5 block text-[10.5px] text-muted" htmlFor="kol-pool-quick-project">目标项目</label>
          <select
            id="kol-pool-quick-project"
            value={projId}
            onChange={(ev) => setProjId(ev.target.value)}
            className="w-full rounded-[10px] border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none focus:border-accent"
          >
            <option value="">选择项目…</option>
            {options.map((opt) => (
              <option key={opt.id} value={opt.id}>{opt.name}</option>
            ))}
          </select>
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-line bg-card px-2.5 py-1.5 text-[10.5px] text-muted transition-colors hover:text-ink"
            >
              取消
            </button>
            <button
              type="button"
              disabled={busy || !projId}
              onClick={() => onConfirm(projId)}
              className="rounded-lg border border-accent bg-accent-soft px-2.5 py-1.5 text-[10.5px] font-medium text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
            >
              {busy ? "入项目中…" : "确认入项目"}
            </button>
          </div>
        </div>
      )}
      {msg ? <div className={`mt-2.5 text-[10.5px] ${toneCls}`}>{msg.text}</div> : null}
    </ModalShell>
  );
}
