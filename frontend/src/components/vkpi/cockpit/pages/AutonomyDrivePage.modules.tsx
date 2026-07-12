import React from "react";
import { KpiCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

// 自治驾照 · 板块页范式辅助件(AutonomyDrivePage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage 四件套:卡头 cnt 短徽 + SrcChip 口径注册表 + KPI 卡
//   (spempty 诚实虚线)+ 空态双轨。旧页 AutonomyBoardPage 的驾照卡/五维 chips/
//   人工调级/评估结果全部在此重铸为 token 皮肤(功能语义零丢失,写死色清零)。
//   数据源真行数(2026-07-12 .venv 只读 PG 54329 实测):
//     vkpi_autonomy_licenses 5 行(全 L1)· vkpi_action_inbox 291 行(suggested 196 /
//     dismissed 75 / executed 13 / approved 6 / snoozed 1;requires_approval 289/291)·
//     vkpi_action_execution_ledger 35 行(success 33 / skipped 2)·
//     vkpi_agent_actions 3 行 · vkpi_agent_outcome_evaluations 107 行。
// 红线:本文件零直连网络(动作走 page 层回调);绝不渲染/触碰 viltrox_fit_score 与
//   rule_v0;「影响评分」维度永久禁止 + 自我提权永久禁止语义原样保留;颜色全 token
//   零写死色;零 token色+opacity 修饰类;时间戳一律 formatLocal 绝对时间。

export type Row = Record<string, any>;

export const ACTION_LABEL: Record<string, string> = {
  kol_recommend: "KOL 推荐",
  outreach_draft: "外联草稿",
  comment_reply_draft: "评论回复草稿",
  pool_enrich: "Pool 补全",
  report_generate: "报告生成",
};

export const LEVEL_LABEL: Record<number, string> = {
  0: "观察",
  1: "建议",
  2: "内部执行",
  3: "半自主",
  4: "全自主",
};

// L 级徽 token 色阶:低级中性 → 高级越亮(L4 用 crit 提示「权力最大,仅人工可授」)
const LEVEL_TONE: Record<number, string> = {
  0: "border-line bg-card text-ink-2",
  1: "border-accent bg-accent-soft text-accent",
  2: "border-good bg-good-soft text-good",
  3: "border-warn bg-warn-soft text-warn",
  4: "border-crit bg-crit-soft text-crit",
};

// 五维能力(卡面去术语:调LLM → 调用AI;key 与后端 dimensions 契约一致)
const DIMENSION_LABEL: Array<[string, string]> = [
  ["write_db", "写库"],
  ["call_llm", "调用AI"],
  ["spend_money", "花钱"],
  ["contact_external", "联系外部"],
  ["change_project_status", "改项目状态"],
];

const DECISION_TONE: Record<string, string> = {
  promote: "border-good bg-good-soft text-good",
  demote: "border-crit bg-crit-soft text-crit",
  hold: "border-line bg-card text-ink-2",
};

const DECISION_LABEL: Record<string, string> = { promote: "升1级", demote: "降1级", hold: "不动" };

export function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(0)}%`;
}

/* ============ 模块 SrcChip 口径注册表(label=真实端点/表名;长口径句全部住这里,
   卡面零介绍文案;动态口径(rules/generated_at)由调用点 extraRows 拼接) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiA: {
    label: "autonomy/licenses · prediction-ledger",
    rows: [
      ["制度", "挣来的自治 L0-L4:级别靠台账命中率挣、连续失手降;判定纯规则,不用 AI"],
      ["驾照", "vkpi_autonomy_licenses(每类动作一张)"],
      ["已对 / 待对答案", "prediction-ledger 聚合(推荐结果+反馈 / 押注复盘 / 告警闭环 / 执行台账)"],
      ["待人审建议", "vkpi_action_inbox status=suggested 近 200 条窗口计数(窗口满 = ≥200)"],
      ["时序", "该域无按日时序端点 → 四卡诚实虚线,不编序列"],
    ],
  },
  lic: {
    label: "vkpi_autonomy_licenses",
    rows: [
      ["红线", "「影响评分」维度永久禁止:任何级别、任何调级路径都不授予改评分能力(后端硬编码)"],
      ["红线", "自我提权永久禁止:升降只有规则评估与人工调级两条路,智能体不能改自己的驾照"],
      ["执行边界", "升降只改驾照级别,本页不执行任何外部动作"],
      ["历史", "表只存最近一次变更,非全量历史(如实)"],
      ["台账读数", "prediction-ledger 实时读取;缺席 / 样本不足 → 诚实不动并说明"],
    ],
  },
  gates: {
    label: "闸门登记表 · 与后端同版代码同源",
    rows: [
      ["口径", "登记表按当前版本后端代码如实登记;环境开关(默认关)以服务端运行时为准"],
      ["建议生成", "POST /actions/generate-daily 恒演练:只产建议,不执行、不写业务表"],
      ["执行双闸", "仅已批准可执行 + 执行前校验(评分红线 / 预算 / 关联实体),未过如实跳过"],
      ["闭环真跑", "白名单仅 3 类零副作用动作(活动收尾 / 库存预警 / 项目共享),名单外拒跑"],
      ["技能自动跑", "服务端 VKPI_SKILLS_AUTORUN 环境开关,缺省关闭"],
    ],
  },
  approvals: {
    label: "vkpi_action_inbox · 审批流",
    rows: [
      ["状态机", "建议 → 通过 / 稍后 / 忽略;已批准 → 执行(仍过执行双闸)"],
      ["范围", "管理层看全局;成员只见自己负责的"],
      ["台账", "执行留痕落 vkpi_action_execution_ledger(卡底「执行台账」可回读)"],
      ["需人审", "写库 / 用AI / 花钱动作一律先人工批准,卡内如实标注"],
    ],
  },
  ledger: {
    label: "prediction-ledger · 纯读聚合",
    rows: [
      ["口径", "预测→结果对齐真实表(推荐结果+反馈 / 押注复盘 / 告警闭环 / 品牌信号 / 执行台账)"],
      ["样本<5", "样本不足,驾照不得据此升级"],
      ["缺席", "接口失败 = 整卡安静缺席(如实留白,不甩报错装数据)"],
      ["评分", "台账永不影响任何评分"],
    ],
  },
  scorecard: {
    label: "learning/weekly-scorecard",
    rows: [
      ["口径", "台账同款裁决按 ISO 周分桶(纯聚合已有数据);每周判定<5 = 诚实样本荒"],
      ["积压", "待对答案不计分母;顶部红条点名最老欠账"],
      ["缺席", "接口失败 = 整卡安静缺席(如实留白)"],
    ],
  },
  miss: {
    label: "learning/miss-review",
    rows: [
      ["口径", "未命中 / 失败条目按动作类分组,词表聚类失败原因(不用 AI)"],
      ["低命中", "命中率<0.5 且样本≥5 标记「需复盘」"],
      ["写入", "「入记忆」只落学习留痕表;同组同日幂等;不改线上规则、不碰评分"],
    ],
  },
  shadow: {
    label: "learning/shadow-evals",
    rows: [
      ["口径", "挑战者 vs 旧版同指标离线回测(带内率 / 中位误差),纯读零写"],
      ["上线闸", "只有双赢才「建议上线」;结论只建议,绝不自动切换线上规则"],
    ],
  },
  loop: {
    label: "agents/loop · 六步链",
    rows: [
      ["六步", "取建议 → 验驾照 → 批准 → 执行留痕 → 结果登记 → 入记忆,每步真实表 + 行 id"],
      ["落点", "vkpi_action_inbox / vkpi_autonomy_licenses / vkpi_agent_actions / vkpi_action_execution_ledger / vkpi_agent_outcome_evaluations"],
      ["演练", "卡内按钮只发演练串跑:零执行、零业务写"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiA: "自治总览",
  lic: "驾照与调级",
  gates: "权限闸门",
  approvals: "审批流 · 今日建议",
  ledger: "预测台账",
  scorecard: "周度记分卡",
  miss: "低命中复盘",
  shadow: "影子评测",
  loop: "闭环串跑",
};

/* ============ KPI 带四卡(现值全真;趋势线 = board-series?board=autonomy 按日真序列:
   待人审建议←inbox_suggested 新建议/日、已对答案←inbox_executed 建议执行/日
   (关联指标,卡面大数是当前存量 → 不挂环比药丸);驾照无时序 → spempty 虚线;
   端点失败 boardSeriesVals=null → 虚线让位) ============ */
export function AutonomyKpiBand({
  lic,
  ledger,
  inbox,
  boardSeries,
}: {
  lic: { ready: boolean; count: number; note: string };
  ledger: { ready: boolean; judged: number; pending: number; note: string };
  inbox: { ready: boolean; suggested: number; note: string };
  /** board-series?board=autonomy 响应(null=未就绪/失败 → 趋势位 spempty 诚实虚线) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  const bs = boardSeries ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard label="驾照" value={lic.count} unit="张" pending={!lic.ready} pendingNote={lic.note} seriesColor="var(--ds-accent)" />
      <KpiCard
        label="已对答案"
        value={ledger.judged}
        unit="条"
        pending={!ledger.ready}
        pendingNote={ledger.note}
        series={boardSeriesVals(bs, "inbox_executed")}
        seriesColor="var(--ds-good)"
      />
      <KpiCard
        label="待对答案"
        value={ledger.pending}
        unit="条"
        tone="warn"
        pending={!ledger.ready}
        pendingNote={ledger.note}
        seriesColor="var(--ds-warn)"
      />
      <KpiCard
        label="待人审建议"
        value={inbox.suggested}
        unit="条"
        tone="warn"
        pending={!inbox.ready}
        pendingNote={inbox.note}
        series={boardSeriesVals(bs, "inbox_suggested")}
        seriesColor="var(--ds-warn)"
      />
    </div>
  );
}

/* ============ 权限闸门登记表(gate 开关如实:默认 OFF 的如实标「默认关」;
   永久禁止两条 = auto 层禁 AI 写 prod / 自我提权,语义原样) ============ */
const GATE_TONE: Record<string, string> = {
  crit: "border-crit bg-crit-soft text-crit",
  warn: "border-warn bg-warn-soft text-warn",
  good: "border-good bg-good-soft text-good",
  accent: "border-accent bg-accent-soft text-accent",
  line: "border-line bg-card text-muted",
};

export type GateItem = { name: string; state: string; tone: keyof typeof GATE_TONE; detail: string };

export const GATE_ITEMS: GateItem[] = [
  {
    name: "影响评分",
    state: "永久禁止",
    tone: "crit",
    detail: "任何级别、任何调级路径都不授予改评分能力;后端硬编码,不可翻真。",
  },
  {
    name: "自我提权",
    state: "永久禁止",
    tone: "crit",
    detail: "智能体不能改自己的驾照级别或能力维度;升级只有规则评估与人工调级两条路。",
  },
  {
    name: "L4 全自主",
    state: "仅人工",
    tone: "warn",
    detail: "自动晋升封顶 L3;到 L4 只有人工调级一条路,绝不自动授予。",
  },
  {
    name: "人审线",
    state: "常开",
    tone: "good",
    detail: "L0 / L1 只许建议;写库、用AI、花钱动作执行前必须人工批准。",
  },
  {
    name: "执行双闸",
    state: "常开",
    tone: "good",
    detail: "仅已批准的建议可执行;执行前再校验评分红线 / 预算 / 关联实体,未过如实跳过。",
  },
  {
    name: "建议生成",
    state: "恒演练",
    tone: "accent",
    detail: "每日建议只产草稿:不执行、不写业务表。",
  },
  {
    name: "闭环真跑白名单",
    state: "默认演练",
    tone: "accent",
    detail: "串跑缺省演练;真跑只放行 3 类零副作用动作(活动收尾 / 库存预警 / 项目共享),名单外拒跑。",
  },
  {
    name: "执行后自动跑技能",
    state: "默认关",
    tone: "line",
    detail: "服务端环境开关,缺省关闭;开启后也只在执行成功后顺带跑。",
  },
];

export function GatesBody() {
  return (
    <div>
      {GATE_ITEMS.map((g) => (
        <div key={g.name} className="border-b border-line py-1.5 last:border-0">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-ink-2">{g.name}</span>
            <span className={`flex-none rounded-md border px-1.5 py-px text-[9.5px] font-semibold ${GATE_TONE[g.tone]}`}>{g.state}</span>
          </div>
          <div className="mt-0.5 text-[10px] leading-relaxed text-muted">{g.detail}</div>
        </div>
      ))}
    </div>
  );
}

/* ============ 评估结果块(演练 / 已执行;逐驾照 decision + 落库回执,失败如实) ============ */
export function EvalResultBlock({ result }: { result: Row }) {
  const items: Row[] = Array.isArray(result.items) ? result.items : [];
  const errored = result.status === "error" || result.status === "empty";
  return (
    <div className="rounded-xl border border-line bg-card px-3 py-2.5">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[11.5px] font-semibold text-ink">
          评估结果{result.dry_run ? "(演练,未落库)" : "(已执行)"}
        </span>
        <span className="text-[9.5px] text-muted">{result.evaluated_at ? formatLocal(String(result.evaluated_at)) : ""}</span>
      </div>
      {errored ? (
        <div className="px-1 py-2 text-[11px] text-muted">{String(result.reason || "评估无结果")}</div>
      ) : items.length === 0 ? (
        <div className="px-1 py-2 text-[11px] text-muted">没有驾照可评估。</div>
      ) : (
        <div className="space-y-1.5">
          {items.map((it) => (
            <div key={String(it.action_type)} className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-2.5 py-1.5">
              <span className="text-[11.5px] text-ink-2">{ACTION_LABEL[String(it.action_type)] || it.action_type}</span>
              <span className={`rounded-md border px-1.5 py-px text-[9.5px] ${DECISION_TONE[String(it.decision)] || DECISION_TONE.hold}`}>
                {DECISION_LABEL[String(it.decision)] || it.decision}
                {it.decision !== "hold" ? ` L${it.current_level}→L${it.proposed_level}` : ""}
              </span>
              {it.applied ? (
                <span className="rounded-md border border-good bg-good-soft px-1.5 py-px text-[9.5px] text-good">已落库</span>
              ) : null}
              {it.apply_error ? <span className="text-[10px] text-crit">落库失败:{String(it.apply_error)}</span> : null}
              <span className="min-w-0 flex-1 truncate text-[10px] text-muted" title={String(it.reason || "")}>
                {String(it.reason || "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============ 驾照卡(旧页功能零丢失:L 徽 + 五维 chips + 永久禁止 chip +
   四读数 + 最近升降 + 人工调级;调级动作回调走 page 层) ============ */
function DimensionChip({ label, allowed }: { label: string; allowed: boolean }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[9.5px] ${
        allowed ? "border-good bg-good-soft text-good" : "border-line bg-card text-muted"
      }`}
    >
      {label} {allowed ? "许" : "禁"}
    </span>
  );
}

function Readout({ k, v, title }: { k: string; v: React.ReactNode; title?: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-2.5 py-1.5" title={title}>
      <div className="text-[9.5px] text-muted">{k}</div>
      <div className="mt-0.5 truncate text-[13px] font-semibold text-ink">{v}</div>
    </div>
  );
}

export type OverrideAdapter = {
  level: string;
  reason: string;
  busy: boolean;
  msg: string;
  onLevel: (value: string) => void;
  onReason: (value: string) => void;
  onSubmit: () => void;
};

export function LicenseCard({ item, ov }: { item: Row; ov: OverrideAdapter }) {
  const action = String(item.action_type || "");
  const level = Number(item.level ?? 0);
  const dims: Row = item.dimensions && typeof item.dimensions === "object" ? item.dimensions : {};
  const ledger: Row = item.ledger && typeof item.ledger === "object" ? item.ledger : {};
  const liveRate = typeof ledger.hit_rate === "number" ? ledger.hit_rate : null;
  const ledgerNotReady = ledger.status !== "ready" && ledger.status !== "ok" && ledger.reason;
  return (
    <div className="rounded-xl border border-line bg-card px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[12.5px] font-semibold text-ink">{ACTION_LABEL[action] || action}</span>
        <span className={`rounded-md border px-2 py-0.5 text-[10.5px] ${LEVEL_TONE[level] || LEVEL_TONE[0]}`}>
          L{level} {item.level_label || LEVEL_LABEL[level] || ""}
        </span>
      </div>

      {/* 五维能力 + 永久禁止维度(红线语义原样) */}
      <div className="flex flex-wrap items-center gap-1.5">
        {DIMENSION_LABEL.map(([key, label]) => (
          <DimensionChip key={key} label={label} allowed={Boolean(dims[key])} />
        ))}
        <span
          className="rounded-full border border-crit bg-crit-soft px-2 py-0.5 text-[9.5px] text-crit"
          title="红线:任何级别、任何调级路径都不授予改评分能力(后端硬编码永久禁止)"
        >
          影响评分 永久禁止
        </span>
      </div>

      {/* 台账读数四格 */}
      <div className="mt-2 grid grid-cols-2 gap-2 xl:grid-cols-4">
        <Readout k="台账命中率(近20次)" v={fmtRate(liveRate)} />
        <Readout k="台账样本数" v={typeof ledger.sample_count === "number" ? ledger.sample_count : "—"} />
        <Readout
          k="上次变更时快照"
          v={
            <>
              {fmtRate(item.hit_rate_snapshot)}
              <span className="ml-1 text-[9.5px] font-normal text-muted">样本 {item.sample_count ?? 0}</span>
            </>
          }
        />
        <Readout k="台账状态" v={<span className="text-[11.5px] text-ink-2">{String(ledger.status || "—")}</span>} title={String(ledger.reason || "")} />
      </div>
      {ledgerNotReady ? <div className="mt-1.5 text-[10px] text-warn">{String(ledger.reason)}</div> : null}

      {/* 最近一次升降(表只存最近一条,如实) */}
      <div className="mt-2 rounded-lg border border-line px-2.5 py-1.5">
        <div className="text-[9.5px] text-muted">最近一次升降(表只存最近一条,非全量历史)</div>
        <div className="mt-0.5 text-[11px] text-ink-2">{item.last_change_reason || "—"}</div>
        <div className="mt-0.5 text-[9.5px] text-muted">{item.changed_at ? formatLocal(String(item.changed_at)) : ""}</div>
      </div>

      {/* 人工调级(唯一能到 L4 的路径;reason 必填) */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-[10.5px] text-muted">人工调级:</span>
        <select
          value={ov.level}
          onChange={(ev) => ov.onLevel(ev.target.value)}
          className="rounded-lg border border-line bg-card px-2 py-1.5 text-[11.5px] text-ink outline-none focus:border-accent"
        >
          <option value="">目标级别…</option>
          {[0, 1, 2, 3, 4].map((lv) => (
            <option key={lv} value={String(lv)}>
              L{lv} {LEVEL_LABEL[lv]}
              {lv === 4 ? "(仅人工可授)" : ""}
            </option>
          ))}
        </select>
        <input
          value={ov.reason}
          onChange={(ev) => ov.onReason(ev.target.value)}
          placeholder="调级理由(必填)"
          className="min-w-[180px] flex-1 rounded-lg border border-line bg-card px-2 py-1.5 text-[11.5px] text-ink outline-none placeholder:text-muted focus:border-accent"
        />
        <button
          type="button"
          onClick={ov.onSubmit}
          disabled={ov.busy}
          className="rounded-lg border border-line px-3 py-1.5 text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
        >
          {ov.busy ? "调级中…" : "确认调级"}
        </button>
        {ov.msg ? <span className="text-[10.5px] text-warn">{ov.msg}</span> : null}
      </div>
    </div>
  );
}
