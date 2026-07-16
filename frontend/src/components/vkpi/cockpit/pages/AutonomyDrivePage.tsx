import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { apiFetch, jsonBody } from "../../../../services/http";
import { getBoardSeries, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";
import { EmptyLine, ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import {
  AutonomyKpiBand,
  EvalResultBlock,
  GatesBody,
  LicenseCard,
  MODULE_SOURCES,
  PROV_TITLES,
  type OverrideAdapter,
  type Row,
} from "./AutonomyDrivePage.modules";
import { ApprovalsEmbed, LedgerEmbed, LoopEmbed, MissEmbed, ScorecardEmbed, ShadowEmbed } from "./AutonomyDrivePage.embeds";

// 自治驾照 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage
//   embeds 手法 + GtmCommandBoardPage 纯读纪律 1:1 同构)。旧 AutonomyBoardPage
//   保留不删(回滚垫);切换点只在 CockpitApp.tsx 的 lazy import 一行(报告注明,
//   本刀不改 CockpitApp)。
//   页壳:pagehead(自治驾照 L0-L4 + 实时辉光点 + 刷新 + 编辑布局)+
//   EditableDashboardBoard(9 模块注册表 + 默认四行 + palette 备选两件)。
//   数据源(全真,零编造;行数 2026-07-12 .venv 只读 PG 实测,见 modules 头注):
//     GET  /api/admin/vkpi/autonomy/licenses            —— 驾照 + 台账实时读数(K1 + lic)
//     POST /api/admin/vkpi/autonomy/evaluate?dry_run=   —— 升降评估(演练缺省;写库须确认)
//     POST /api/admin/vkpi/autonomy/licenses/{a}/override —— 人工调级(reason 必填,唯一 L4 路径)
//     GET  /api/admin/vkpi/prediction-ledger/summary    —— K2 已对答案 / K3 待对答案
//     GET  /api/admin/vkpi/actions/inbox?status=suggested&limit=200 —— K4 待人审建议
//     审批流 / 台账 / 记分卡 / 复盘 / 影子 / 闭环 六件 embeds 内自取(旧组件零改动)。
//   【验收纪律】卡面零介绍性文案:旧页副题 / 升降规则说明盒 / 页脚口径句全部收进
//   MODULE_SOURCES + rules 动态口径行(SrcChip + 溯源弹窗),按钮动词直说。
// 红线:「影响评分」维度永久禁止 + 自我提权永久禁止语义原样(gates 模块 + 驾照卡
//   双处如实展示);升降只动驾照级别,不执行任何外部动作;绝不渲染/触碰
//   viltrox_fit_score 与 rule_v0;颜色全 token 零写死色;零 token色+opacity 修饰类;
//   时间戳 formatLocal 绝对时间;布局只走本机 storageKey,不传 apiToken 给
//   EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1 键,金样板同注释)。

const STORAGE_KEY = "vkpi-autonomy-layout-v1";

// 默认布局(12 列 · 四行):kpiA(12) → lic(8)+gates(4) → approvals(8)+ledger(4)
// → scorecard(8)+loop(4);miss / shadow 进 palette 备选。
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiA", span: 12 },
  { moduleKey: "lic", span: 8 },
  { moduleKey: "gates", span: 4 },
  { moduleKey: "approvals", span: 8 },
  { moduleKey: "ledger", span: 4 },
  { moduleKey: "scorecard", span: 8 },
  { moduleKey: "loop", span: 4 },
];

export function AutonomyDrivePage({ apiToken = "", embeddedModuleKey }: { apiToken?: string; onNavigate?: (navKey: string) => void; embeddedModuleKey?: string }) {
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 驾照(items + rules + 诚实空态 reason)
  const [licData, setLicData] = React.useState<Row | null>(null);
  const [licLoading, setLicLoading] = React.useState(false);
  const [licError, setLicError] = React.useState("");

  // 升降评估(演练 / 写库)
  const [evalResult, setEvalResult] = React.useState<Row | null>(null);
  const [evalBusy, setEvalBusy] = React.useState(false);

  // 人工调级(逐驾照独立态)
  const [ovLevel, setOvLevel] = React.useState<Record<string, string>>({});
  const [ovReason, setOvReason] = React.useState<Record<string, string>>({});
  const [ovBusy, setOvBusy] = React.useState<Record<string, boolean>>({});
  const [ovMsg, setOvMsg] = React.useState<Record<string, string>>({});

  // KPI 带专属:台账聚合(K2/K3)+ 待人审建议窗口计数(K4)
  const [ledgerSummary, setLedgerSummary] = React.useState<Row | null>(null);
  const [ledgerError, setLedgerError] = React.useState("");
  const [inboxCount, setInboxCount] = React.useState<number | null>(null);
  const [inboxError, setInboxError] = React.useState("");
  // KPI 卡趋势线(挂账迸发①):board-series 按日真序列;失败 = KPI 卡照旧 spempty 诚实虚线
  const [kpiSeries, setKpiSeries] = React.useState<VkpiBoardSeriesResponse | null>(null);
  const [kpiSeriesError, setKpiSeriesError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setKpiSeriesError("");
    getBoardSeries(apiToken, { board: "autonomy" })
      .then((res) => {
        if (alive) setKpiSeries(res ?? null);
      })
      .catch((err: any) => {
        if (alive) {
          setKpiSeries(null);
          setKpiSeriesError(String(err?.detail || err?.message || "加载失败"));
        }
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLicLoading(true);
    setLicError("");
    apiFetch<Row>("/api/admin/vkpi/autonomy/licenses", { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setLicData(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setLicError(String(err?.detail || err?.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setLicLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLedgerError("");
    apiFetch<Row>("/api/admin/vkpi/prediction-ledger/summary", { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setLedgerSummary(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setLedgerError(String(err?.detail || err?.message || "加载失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setInboxError("");
    apiFetch<Row>("/api/admin/vkpi/actions/inbox?status=suggested&limit=200", { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (!alive) return;
        if (res && res.available === false) {
          setInboxCount(null);
          setInboxError("建议系统未启用");
          return;
        }
        const n = Number(res?.count);
        setInboxCount(Number.isFinite(n) ? n : Array.isArray(res?.items) ? res.items.length : 0);
      })
      .catch((err: any) => {
        if (alive) setInboxError(String(err?.detail || err?.message || "加载失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  // 升降评估:演练缺省;写库前二次确认(升降只动驾照级别,不执行外部动作)
  const runEvaluate = React.useCallback(
    (dryRun: boolean) => {
      if (!apiToken || evalBusy) return;
      if (!dryRun && !window.confirm("确认执行升降并写库(dry_run=false)。升降只动驾照级别,不执行任何外部动作。")) return;
      setEvalBusy(true);
      apiFetch<Row>(`/api/admin/vkpi/autonomy/evaluate?dry_run=${dryRun ? "true" : "false"}`, { method: "POST" }, apiToken)
        .then((res) => {
          setEvalResult(res);
          if (!dryRun) setReloadTick((t) => t + 1);
        })
        .catch((err: any) => setEvalResult({ status: "error", reason: String(err?.detail || err?.message || "评估失败") }))
        .finally(() => setEvalBusy(false));
    },
    [apiToken, evalBusy],
  );

  // 人工调级:级别 + reason 双必填;端点真实返回才落回执,绝不点击即置绿
  const runOverride = React.useCallback(
    (actionType: string) => {
      if (!apiToken || ovBusy[actionType]) return;
      const level = ovLevel[actionType];
      const reason = (ovReason[actionType] || "").trim();
      if (level === undefined || level === "") {
        setOvMsg((m) => ({ ...m, [actionType]: "先选目标级别" }));
        return;
      }
      if (!reason) {
        setOvMsg((m) => ({ ...m, [actionType]: "人工调级必须写 reason" }));
        return;
      }
      setOvBusy((m) => ({ ...m, [actionType]: true }));
      setOvMsg((m) => ({ ...m, [actionType]: "" }));
      apiFetch<Row>(
        `/api/admin/vkpi/autonomy/licenses/${encodeURIComponent(actionType)}/override`,
        { method: "POST", body: jsonBody({ level: Number(level), reason }) },
        apiToken,
      )
        .then((res) => {
          if (res.status === "error") {
            setOvMsg((m) => ({ ...m, [actionType]: String(res.reason || "调级失败") }));
            return;
          }
          setOvMsg((m) => ({ ...m, [actionType]: `已调至 L${res.level}(原 L${res.previous_level ?? "?"})` }));
          setOvReason((m) => ({ ...m, [actionType]: "" }));
          setReloadTick((t) => t + 1);
        })
        .catch((err: any) => setOvMsg((m) => ({ ...m, [actionType]: String(err?.detail || err?.message || "调级失败") })))
        .finally(() => setOvBusy((m) => ({ ...m, [actionType]: false })));
    },
    [apiToken, ovBusy, ovLevel, ovReason],
  );

  const items: Row[] = Array.isArray(licData?.items) ? licData!.items : [];
  const rules: Row = licData?.rules && typeof licData.rules === "object" ? licData.rules : {};
  const licReady = Boolean(licData) && String(licData!.status) === "ready" && !licError;
  const licEmptyReason = licData && String(licData.status) !== "ready" ? String(licData.reason || "") : "";

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载自治驾照数据。
    </PendingCard>
  );

  /* ---------- SrcChip 口径(静态注册表 + 动态口径行;点击开溯源弹窗) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 模块 body ---------- */

  // KPI 带:四卡现值全真;无按日时序端点 → 全 spempty 虚线零环比药丸(诚实)
  const renderKpiBand = () => {
    const totals: Row = ledgerSummary?.totals && typeof ledgerSummary.totals === "object" ? ledgerSummary.totals : {};
    const ledgerReady = Boolean(ledgerSummary) && String(ledgerSummary!.status) === "ok" && !ledgerError;
    const kpiExtra: Array<[string, string]> = [
      ...(licData?.note ? ([["驾照口径", String(licData.note)]] as Array<[string, string]>) : []),
      ...(ledgerError ? ([["台账端点", `prediction-ledger/summary → ${ledgerError}`]] as Array<[string, string]>) : []),
      ...(inboxError ? ([["建议端点", `actions/inbox → ${inboxError}`]] as Array<[string, string]>) : []),
      [
        "趋势线",
        "board-series?board=autonomy 按日真序列(30 天窗,vkpi_action_inbox):待人审建议←新建议/日、已对答案←建议执行/日(executed 行按最后状态变更日);两条为关联指标,卡面大数是当前存量 → 不挂环比药丸",
      ],
      ...(kpiSeriesError
        ? ([["趋势线源", `读取失败:${kpiSeriesError}(卡面虚线如实,不编时序)`]] as Array<[string, string]>)
        : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiA", "自治总览", licReady ? `${items.length} 张` : undefined, kpiExtra)}>
        {!apiToken ? (
          noTokenCard
        ) : (
          <AutonomyKpiBand
            lic={{ ready: licReady, count: items.length, note: licError ? "驾照读取失败" : licEmptyReason || "驾照读取中…" }}
            ledger={{
              ready: ledgerReady,
              judged: Number(totals.judged_total) || 0,
              pending: Number(totals.pending_total) || 0,
              note: ledgerError ? "台账读取失败" : ledgerSummary ? "台账聚合失败" : "台账读取中…",
            }}
            inbox={{
              ready: inboxCount != null && !inboxError,
              suggested: inboxCount ?? 0,
              note: inboxError || "建议队列读取中…",
            }}
            boardSeries={kpiSeries}
          />
        )}
      </ModuleCard>
    );
  };

  // 驾照与调级:评估两键(演练 / 写库)+ 评估结果 + 驾照卡族(五维 / 读数 / 调级)
  const renderLic = () => {
    // 升降规则 + 生成口径 = 动态诚实口径行(旧页说明盒的新家,SrcChip 可查)
    const licExtra: Array<[string, string]> = licData
      ? [
          ...(rules.promote ? ([["升", String(rules.promote)]] as Array<[string, string]>) : []),
          ...(rules.demote ? ([["降", String(rules.demote)]] as Array<[string, string]>) : []),
          ...(rules.hold ? ([["不动", String(rules.hold)]] as Array<[string, string]>) : []),
          ...(rules.forbidden_dimension ? ([["禁维", String(rules.forbidden_dimension)]] as Array<[string, string]>) : []),
        ]
      : [];
    let body: React.ReactNode;
    if (!apiToken) body = noTokenCard;
    else if (licLoading && !licData) body = <LoadingLine text="驾照读取中…" />;
    else if (licError) body = <ErrorCard title="autonomy/licenses 读取失败" text={licError} />;
    else if (!licData) body = <EmptyLine text="暂无数据。" />;
    else if (items.length === 0) body = <EmptyLine text={licEmptyReason || "暂无驾照记录(初始种子未落库)。"} />;
    else
      body = (
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => runEvaluate(true)}
              disabled={evalBusy}
              className="rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[11.5px] text-accent transition-colors hover:border-accent-hover disabled:opacity-50"
            >
              {evalBusy ? "评估中…" : "评估升降(演练)"}
            </button>
            <button
              type="button"
              onClick={() => runEvaluate(false)}
              disabled={evalBusy}
              className="rounded-lg border border-warn bg-warn-soft px-3 py-1.5 text-[11.5px] text-warn transition-colors disabled:opacity-50"
            >
              执行升降(写库)
            </button>
          </div>
          {evalResult ? <EvalResultBlock result={evalResult} /> : null}
          {items.map((it) => {
            const action = String(it.action_type || "");
            const ov: OverrideAdapter = {
              level: ovLevel[action] ?? "",
              reason: ovReason[action] ?? "",
              busy: Boolean(ovBusy[action]),
              msg: ovMsg[action] ?? "",
              onLevel: (v) => setOvLevel((m) => ({ ...m, [action]: v })),
              onReason: (v) => setOvReason((m) => ({ ...m, [action]: v })),
              onSubmit: () => runOverride(action),
            };
            return <LicenseCard key={action} item={it} ov={ov} />;
          })}
        </div>
      );
    return <ModuleCard {...cardProps("lic", "驾照与调级", items.length ? `${items.length} 张` : undefined, licExtra)}>{body}</ModuleCard>;
  };

  // 权限闸门:代码同源登记表(默认 OFF 如实标;两条永久禁止红线语义原样)
  const renderGates = () => (
    <ModuleCard {...cardProps("gates", "权限闸门", "8 道")}>
      <GatesBody />
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认高度贴内容 · 1 格 = 22px + 14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    // kpiA:卡头 + 单行 4 KPI 卡 ≈ 200px → 6 格
    { key: "kpiA", label: "自治总览带", description: "驾照 / 已对答案 / 待对答案 / 待人审建议", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    // lic:两键 + 5 驾照卡(每卡 ≈ 230px)→ 默认 20 格卡内滚
    { key: "lic", label: "驾照与调级", description: "每类动作一张驾照 · 升降评估 + 人工调级", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 20, minHeight: 8, maxHeight: 40, render: renderLic },
    // gates:8 行登记表 ≈ 450px → 13 格
    { key: "gates", label: "权限闸门", description: "闸门一览 · 默认关的如实标", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 6, maxHeight: 24, render: renderGates },
    // approvals:今日建议 3 条收敛 + 台账折叠 ≈ 500px → 15 格
    { key: "approvals", label: "审批流 · 今日建议", description: "通过 / 稍后 / 忽略 / 执行 · 执行台账回读", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 15, minHeight: 8, maxHeight: 32, render: () => <ApprovalsEmbed apiToken={apiToken} noToken={noTokenCard} /> },
    // ledger:5 组命中率条 + 口径注 ≈ 430px → 13 格
    { key: "ledger", label: "预测台账", description: "预测命中率读数 · 升降的证据底座", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 6, maxHeight: 28, render: () => <LedgerEmbed apiToken={apiToken} noToken={noTokenCard} /> },
    // scorecard:积压红条 + 组周曲线 ≈ 500px → 15 格
    { key: "scorecard", label: "周度记分卡", description: "周命中率曲线 + 待对答案催办", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 15, minHeight: 6, maxHeight: 30, render: () => <ScorecardEmbed apiToken={apiToken} noToken={noTokenCard} /> },
    // loop:串跑钮 + 链路图 ≈ 360px → 11 格
    { key: "loop", label: "闭环串跑", description: "六步链路演练串跑 · 每步真实落点", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 6, maxHeight: 24, render: () => <LoopEmbed apiToken={apiToken} noToken={noTokenCard} /> },
    // ↓ palette 备选(不进默认布局,注册表保留全量可选)
    { key: "miss", label: "低命中复盘", description: "失败原因聚类 · 一键入记忆", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 30, render: () => <MissEmbed apiToken={apiToken} noToken={noTokenCard} /> },
    { key: "shadow", label: "影子评测", description: "挑战者赢旧版才上线", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 30, render: () => <ShadowEmbed apiToken={apiToken} noToken={noTokenCard} /> },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="自治驾照" />;
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 实时辉光点 + 刷新 + 编辑布局;旧页副题 / 规则
          说明盒 / 页脚口径句全部收进 SrcChip 口径行(卡面去介绍文案,诚实信息不丢) */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">自治驾照 L0-L4</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={() => setReloadTick((t) => t + 1)}
            disabled={!apiToken || licLoading}
            className="flex items-center gap-1.5 rounded-xl border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink disabled:opacity-50"
          >
            <RefreshCw size={13} className={licLoading ? "animate-spin" : ""} />
            <span>刷新</span>
          </button>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 模块溯源说明弹窗(SrcChip 点击;口径 = 静态注册表 + 动态 rules 行合并) */}
      {provKey && (
        <ModuleProvModal
          title={PROV_TITLES[provKey] || provKey}
          caliber={mergedRowsRef.current[provKey] || srcOf(provKey).rows}
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}
