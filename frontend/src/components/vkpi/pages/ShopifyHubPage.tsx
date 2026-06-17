// @ts-nocheck
// Shopify Hub (三区合一) — T2-shopify-hub-fe.
//
// One page, three regions stacked vertically, a tab switcher just toggles which
// region is visible:
//   ① 连接 (Connect)   — fill store_domain / access_token / webhook_secret →
//                        POST saveShopifyCreds; read live status badge via the
//                        EXISTING getShopifyStatus.
//   ② 生成推广链接 (Generate) — 来源(项目/活动) × KOL × 产品 × 折扣 →
//                        POST generatePromoLink → 折扣码 + 追踪短链 + 折扣直达链.
//   ③ 数据追踪 (Track) — KOL/来源/产品/点击/订单/GMV/ROI table from the
//                        attribution summary; empty data honestly shows 待接入.
//
// Visual helpers (StatusPill / EnvRow / CopyField) are inline-reimplemented here
// (NOT imported from ShopifyConnectPage) to avoid cross-track coupling.

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Link2,
  Loader2,
  PackageCheck,
  Plug,
  RefreshCw,
  Table2,
} from "lucide-react";
import { getShopifyStatus, type ShopifyProviderStatus } from "../../../domains/attribution";
import {
  generatePromoLink,
  listPromoKols,
  listPromoProducts,
  listSources,
  saveShopifyCreds,
  type GeneratePromoLinkResult,
  type PromoAttributionRow,
} from "../../../services/vkpi/shopifyHub-api";
import {
  getGoaffproStatus,
  saveGoaffproCredentials,
  listGoaffproAffiliates,
  getGoaffproSummary,
  syncGoaffproMetrics,
  type GoaffproStatus,
  type ListGoaffproAffiliatesResult,
} from "../../../services/vkpi/goaffpro-api";

const e = React.createElement;

// 自建短链生成器退役开关 —— true=Region ② 整块不渲染(代码留着,可回滚)。
// 短链生成已迁移到 GOAFFPRO:每个 KOL 注册为 affiliate 后自动获得追踪链 + 优惠码。
const RETIRE_SELF_LINK = true;

type Row = Record<string, unknown>;
type TabKey = "connect" | "generate" | "track";

// ---------------------------------------------------------------------------
// Inline visual helpers (do NOT import from ShopifyConnectPage).
// ---------------------------------------------------------------------------

function StatusPill({ ok, okLabel, badLabel }) {
  return e(
    "span",
    {
      className: `inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${
        ok
          ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20"
          : "bg-amber-500/10 text-amber-300 border border-amber-500/20"
      }`,
    },
    ok ? e(CheckCircle2, { size: 12 }) : e(AlertTriangle, { size: 12 }),
    ok ? okLabel : badLabel,
  );
}

function EnvRow({ name, configured, hint }) {
  return e(
    "div",
    {
      className:
        "flex items-start justify-between gap-3 rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2",
    },
    e(
      "div",
      { className: "min-w-0" },
      e("code", { className: "text-[12px] font-mono text-slate-200" }, name),
      e("div", { className: "text-[11px] text-slate-500 mt-0.5" }, hint),
    ),
    e(StatusPill, { ok: configured, okLabel: "已配置", badLabel: "未配置" }),
  );
}

function CopyField({ label, value }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    if (typeof navigator !== "undefined" && navigator.clipboard && value) {
      void navigator.clipboard.writeText(value).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      });
    }
  }, [value]);
  return e(
    "div",
    { className: "rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2" },
    label ? e("div", { className: "text-[11px] text-slate-500 mb-1" }, label) : null,
    e(
      "div",
      { className: "flex items-center gap-2" },
      e(
        "code",
        { className: "flex-1 min-w-0 truncate text-[12px] font-mono text-blue-300" },
        value || "—",
      ),
      e(
        "button",
        {
          type: "button",
          onClick: onCopy,
          className:
            "shrink-0 inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white",
        },
        e(Copy, { size: 11 }),
        copied ? "已复制" : "复制",
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Small field helpers (defensive reads — list shapes vary across endpoints).
// ---------------------------------------------------------------------------

function pickStr(row: Row, keys: string[], fallback = ""): string {
  for (const k of keys) {
    const v = row[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v);
  }
  return fallback;
}

function pickNum(row: Row, keys: string[]): number | null {
  for (const k of keys) {
    const v = row[k];
    if (v !== undefined && v !== null && v !== "" && !Number.isNaN(Number(v))) return Number(v);
  }
  return null;
}

function fmtMoney(v: number | null): string {
  if (v === null) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtRoi(v: number | null): string {
  if (v === null) return "—";
  return `${v.toFixed(2)}x`;
}

// Card shell shared by all three regions.
function Card(props: { children?: unknown; className?: string }) {
  // 红线(崩溃修):函数组件被 React 以 (props, legacyContext={}) 调用,绝不能用 ...children
  // rest 参收子节点——那会把空的 legacyContext {} 当 child 渲染 → "Objects are not valid as a
  // React child (object with keys {})" 整页崩。子节点一律走 props.children。
  return e(
    "section",
    {
      className: `rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4 ${props.className || ""}`,
    },
    (props as { children?: unknown }).children,
  );
}

// Labeled input row used in the connect + generate forms.
function FieldInput({ label, type, value, placeholder, onChange, hint }) {
  return e(
    "label",
    { className: "block" },
    e("div", { className: "text-[11px] text-slate-400 mb-1" }, label),
    e("input", {
      type: type || "text",
      value,
      placeholder,
      onChange: (ev) => onChange(ev.target.value),
      className:
        "w-full rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-blue-500/40 focus:outline-none",
    }),
    hint ? e("div", { className: "text-[11px] text-slate-600 mt-1" }, hint) : null,
  );
}

function FieldSelect({ label, value, onChange, children }) {
  return e(
    "label",
    { className: "block" },
    e("div", { className: "text-[11px] text-slate-400 mb-1" }, label),
    e(
      "select",
      {
        value,
        onChange: (ev) => onChange(ev.target.value),
        className:
          "w-full rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] text-slate-200 focus:border-blue-500/40 focus:outline-none",
      },
      children,
    ),
  );
}

// ---------------------------------------------------------------------------
// Page.
// ---------------------------------------------------------------------------

export function ShopifyHubPage({ apiToken = "" }: { apiToken?: string } = {}) {
  const [tab, setTab] = useState<TabKey>("connect");

  // --- Region ① Connect state ---
  const [storeDomain, setStoreDomain] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [savingCreds, setSavingCreds] = useState(false);
  const [credsMsg, setCredsMsg] = useState("");
  const [credsErr, setCredsErr] = useState("");
  const [status, setStatus] = useState<ShopifyProviderStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusErr, setStatusErr] = useState("");
  // 自建 Shopify 连接(旧·待退役)折叠开关 —— 默认折叠,展开仍可用,不删不破坏。
  const [legacyShopifyOpen, setLegacyShopifyOpen] = useState(false);

  // --- GOAFFPRO 联盟营销接入 state(替代自建短链) ---
  const [goaffAccessToken, setGoaffAccessToken] = useState("");
  const [goaffPublicToken, setGoaffPublicToken] = useState("");
  const [goaffSaving, setGoaffSaving] = useState(false);
  const [goaffSaveMsg, setGoaffSaveMsg] = useState("");
  const [goaffSaveErr, setGoaffSaveErr] = useState("");
  const [goaffStatus, setGoaffStatus] = useState<GoaffproStatus | null>(null);
  const [goaffStatusLoading, setGoaffStatusLoading] = useState(false);
  const [goaffStatusErr, setGoaffStatusErr] = useState("");
  const [goaffPreview, setGoaffPreview] = useState<ListGoaffproAffiliatesResult | null>(null);
  const [goaffPreviewLoading, setGoaffPreviewLoading] = useState(false);
  const [goaffPreviewErr, setGoaffPreviewErr] = useState("");

  // --- Region ② Generate state ---
  const [projects, setProjects] = useState<Row[]>([]);
  const [events, setEvents] = useState<Row[]>([]);
  const [kols, setKols] = useState<Row[]>([]);
  const [products, setProducts] = useState<Row[]>([]);
  const [source, setSource] = useState(""); // encoded "project:<id>" / "event:<id>"
  const [kolId, setKolId] = useState("");
  const [productSku, setProductSku] = useState("");
  const [discount, setDiscount] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genErr, setGenErr] = useState("");
  const [genResult, setGenResult] = useState<GeneratePromoLinkResult | null>(null);

  // --- Region ③ Track state ---
  const [rows, setRows] = useState<PromoAttributionRow[]>([]);
  const [trackTotals, setTrackTotals] = useState<any>(null);
  const [trackLoading, setTrackLoading] = useState(false);
  const [trackSyncing, setTrackSyncing] = useState(false);
  const [lastSynced, setLastSynced] = useState<string>("");
  const [trackErr, setTrackErr] = useState("");

  // ---- Loaders ----

  const loadStatus = useCallback(async () => {
    if (!apiToken) {
      setStatusErr("缺少 API token，无法读取 Shopify 连接状态。");
      return;
    }
    setStatusLoading(true);
    setStatusErr("");
    try {
      const res = await getShopifyStatus(apiToken, 10);
      setStatus(res);
    } catch (err) {
      setStatusErr(err instanceof Error ? err.message : "读取 Shopify 状态失败");
    } finally {
      setStatusLoading(false);
    }
  }, [apiToken]);

  const loadGoaffStatus = useCallback(async () => {
    if (!apiToken) {
      setGoaffStatusErr("缺少 API token，无法读取 GOAFFPRO 连接状态。");
      return;
    }
    setGoaffStatusLoading(true);
    setGoaffStatusErr("");
    try {
      const res = await getGoaffproStatus(apiToken);
      setGoaffStatus(res);
    } catch (err) {
      setGoaffStatusErr(err instanceof Error ? err.message : "读取 GOAFFPRO 状态失败");
    } finally {
      setGoaffStatusLoading(false);
    }
  }, [apiToken]);

  const loadOptions = useCallback(async () => {
    if (!apiToken) return;
    try {
      const [src, kolRes, prodRes] = await Promise.all([
        listSources(apiToken),
        listPromoKols(apiToken),
        listPromoProducts(apiToken),
      ]);
      setProjects(src.projects);
      setEvents(src.events);
      setKols(Array.isArray(kolRes?.kols) ? kolRes.kols : []);
      setProducts(Array.isArray(prodRes?.products) ? prodRes.products : []);
    } catch (err) {
      setGenErr(err instanceof Error ? err.message : "加载下拉选项失败");
    }
  }, [apiToken]);

  const loadTrack = useCallback(async () => {
    if (!apiToken) {
      setTrackErr("缺少 API token，无法读取归因汇总。");
      return;
    }
    setTrackLoading(true);
    setTrackErr("");
    try {
      // GOAFFPRO 归因汇总(取代已退役的自建 promo attribution 端点 → 修「Not Found」)。
      const res = await getGoaffproSummary(apiToken, { limit: 50 });
      setRows(res.items as unknown as PromoAttributionRow[]);
      setTrackTotals(res.totals || null);
      setLastSynced(res.last_synced_at || "");
      if (res.note) setTrackErr(res.note);
    } catch (err) {
      // Honest: do NOT fabricate rows. Keep the table empty + show the reason.
      setRows([]);
      setTrackErr(err instanceof Error ? err.message : "归因汇总尚未接入");
    } finally {
      setTrackLoading(false);
    }
  }, [apiToken]);

  // 刷新 = 先同步 GOAFFPRO 指标落库,再读缓存(秒出)。
  const refreshTrack = useCallback(async () => {
    if (!apiToken || trackSyncing) return;
    setTrackSyncing(true);
    setTrackErr("");
    try {
      await syncGoaffproMetrics(apiToken);
    } catch (err) {
      setTrackErr(err instanceof Error ? err.message : "同步 GOAFFPRO 指标失败");
    } finally {
      setTrackSyncing(false);
      void loadTrack();
    }
  }, [apiToken, trackSyncing, loadTrack]);

  useEffect(() => {
    void loadStatus();
    void loadGoaffStatus();
    void loadOptions();
    void loadTrack();
  }, [loadStatus, loadGoaffStatus, loadOptions, loadTrack]);

  // ---- Actions ----

  const onSaveCreds = useCallback(async () => {
    if (!apiToken) {
      setCredsErr("缺少 API token，无法保存凭据。");
      return;
    }
    setSavingCreds(true);
    setCredsErr("");
    setCredsMsg("");
    try {
      const res = await saveShopifyCreds(apiToken, { storeDomain, accessToken, webhookSecret });
      setCredsMsg(res?.message || "凭据已加密保存。");
      // Clear secret fields from memory after a successful save.
      setAccessToken("");
      setWebhookSecret("");
      await loadStatus();
    } catch (err) {
      setCredsErr(err instanceof Error ? err.message : "保存凭据失败");
    } finally {
      setSavingCreds(false);
    }
  }, [apiToken, storeDomain, accessToken, webhookSecret, loadStatus]);

  const onSaveGoaff = useCallback(async () => {
    if (!apiToken) {
      setGoaffSaveErr("缺少 API token，无法保存 GOAFFPRO 凭据。");
      return;
    }
    if (!goaffAccessToken.trim()) {
      setGoaffSaveErr("请填写 access_token。");
      return;
    }
    setGoaffSaving(true);
    setGoaffSaveErr("");
    setGoaffSaveMsg("");
    try {
      // 绝不 log token —— 携带 GOAFFPRO 密钥。
      const res = await saveGoaffproCredentials(apiToken, {
        access_token: goaffAccessToken.trim(),
        public_token: goaffPublicToken.trim() || undefined,
      });
      setGoaffSaveMsg(res?.ok === false ? "保存返回未成功，请检查凭据。" : "GOAFFPRO 凭据已加密保存。");
      // 保存后清空密码框,绝不回显明文。
      setGoaffAccessToken("");
      setGoaffPublicToken("");
      await loadGoaffStatus();
    } catch (err) {
      setGoaffSaveErr(err instanceof Error ? err.message : "保存 GOAFFPRO 凭据失败");
    } finally {
      setGoaffSaving(false);
    }
  }, [apiToken, goaffAccessToken, goaffPublicToken, loadGoaffStatus]);

  const onPreviewAffiliates = useCallback(async () => {
    if (!apiToken) {
      setGoaffPreviewErr("缺少 API token，无法拉取 affiliate 预览。");
      return;
    }
    setGoaffPreviewLoading(true);
    setGoaffPreviewErr("");
    setGoaffPreview(null);
    try {
      const res = await listGoaffproAffiliates(apiToken, { limit: 5 });
      setGoaffPreview(res);
      if (res?.ok === false) {
        setGoaffPreviewErr(
          res.reason === "not_configured"
            ? "尚未配置 GOAFFPRO 凭据。"
            : res.reason || res.error || "拉取失败。",
        );
      }
    } catch (err) {
      setGoaffPreviewErr(err instanceof Error ? err.message : "拉取 affiliate 预览失败");
    } finally {
      setGoaffPreviewLoading(false);
    }
  }, [apiToken]);

  const onGenerate = useCallback(async () => {
    if (!apiToken) {
      setGenErr("缺少 API token，无法生成推广链接。");
      return;
    }
    if (!source || !kolId || !productSku) {
      setGenErr("请先选择来源、KOL 与产品。");
      return;
    }
    const [sourceType, sourceId] = source.split(":");
    const pct = discount.trim() === "" ? undefined : Number(discount);
    setGenerating(true);
    setGenErr("");
    setGenResult(null);
    try {
      const res = await generatePromoLink(apiToken, {
        sourceType: sourceType as "project" | "event",
        sourceId,
        kolId,
        productSku,
        discountPercent: pct,
      });
      setGenResult(res);
    } catch (err) {
      setGenErr(err instanceof Error ? err.message : "生成推广链接失败（后端可能尚未接入）");
    } finally {
      setGenerating(false);
    }
  }, [apiToken, source, kolId, productSku, discount]);

  // ---- Derived ----

  const configured = status?.provider_status === "configured";
  const env = status?.env_vars || {};

  // GOAFFPRO 连接判定:status==='connected' 或 access_token_configured 为真即视为已连接。
  const goaffConnected = Boolean(
    goaffStatus &&
      (goaffStatus.status === "connected" || goaffStatus.access_token_configured),
  );

  // ---- Tab button ----
  function TabBtn(key: TabKey, label: string, icon) {
    const active = tab === key;
    return e(
      "button",
      {
        type: "button",
        onClick: () => setTab(key),
        className: `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
          active
            ? "bg-white/[0.06] text-white border border-white/[0.1]"
            : "text-slate-400 border border-transparent hover:text-slate-200 hover:bg-white/[0.03]"
        }`,
      },
      e(icon, { size: 13 }),
      label,
    );
  }

  // =========================================================================
  // Region ① Connect
  // =========================================================================
  const regionConnect = e(
    "div",
    { className: tab === "connect" ? "" : "hidden" },

    // -----------------------------------------------------------------------
    // GOAFFPRO 联盟营销「连接配置」已迁至 设置 → GOAFFPRO 联盟营销（管理员配置区）。
    // 用户拍板:token/密钥这类应在 Settings 由管理员管。这里只留一行迁移提示卡 +
    // 只读连接状态徽标(loadGoaffStatus 仍在跑,纯只读不含配置/密码框)。
    // 原配置卡(两个密码框 + 保存并连接 + 刷新状态 + 拉取 affiliate 预览 + *_configured 明细)
    // 已整块抽成 settings/GoaffproConnectCard.tsx,旧实现注释保留在本段下方,可回滚。
    // -----------------------------------------------------------------------
    e(
      Card,
      { className: "border-blue-500/15 bg-blue-500/[0.03]" },
      e(
        "div",
        { className: "flex items-start justify-between gap-3" },
        e(
          "div",
          { className: "flex items-start gap-3" },
          e(Plug, { size: 18, className: "text-blue-300 mt-0.5 shrink-0" }),
          e(
            "div",
            null,
            e(
              "div",
              { className: "text-[13px] font-medium text-blue-200 mb-1" },
              "GOAFFPRO 连接配置已移至设置",
            ),
            e(
              "p",
              { className: "text-[12px] text-slate-400" },
              "填 token / 连接状态 / 拉取 affiliate 预览已迁到 设置 → GOAFFPRO 联盟营销（管理员配置）。token / 密钥由管理员在设置区统一管理。",
            ),
          ),
        ),
        // 只读连接状态徽标(纯展示,不含任何配置入口)。
        goaffStatusLoading
          ? e(
              "span",
              { className: "inline-flex items-center gap-1.5 text-[11px] text-slate-400 shrink-0" },
              e(Loader2, { size: 12, className: "animate-spin" }),
              "读取状态…",
            )
          : e(StatusPill, {
              ok: goaffConnected,
              okLabel: goaffStatus?.status === "connected" ? "已连接" : "已配置",
              badLabel: "未连接",
            }),
      ),
    ),

    // ===== 旧·GOAFFPRO 配置卡(已迁至 settings/GoaffproConnectCard.tsx,注释保留可回滚)=====
    /*
    e(
      Card,
      { className: "" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e("h2", { className: "text-sm font-semibold text-white" }, "① 连接 GOAFFPRO 联盟营销"),
        goaffStatusLoading
          ? e(
              "span",
              { className: "inline-flex items-center gap-1.5 text-[11px] text-slate-400" },
              e(Loader2, { size: 12, className: "animate-spin" }),
              "读取状态…",
            )
          : e(StatusPill, {
              ok: goaffConnected,
              okLabel: goaffStatus?.status === "connected" ? "已连接" : "已配置",
              badLabel: "未连接",
            }),
      ),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-4" },
        "每个 KOL = 一个 GOAFFPRO affiliate，注册后自动获得专属追踪链与优惠码；点击 / 销售 / 佣金归因由 GOAFFPRO 自动接入，无需手动生成短链。",
      ),
      e(
        "div",
        { className: "space-y-3" },
        e(FieldInput, {
          label: "access_token",
          type: "password",
          value: goaffAccessToken,
          placeholder: "X-GOAFFPRO-ACCESS-TOKEN（管理私钥）",
          onChange: setGoaffAccessToken,
          hint: "GOAFFPRO 管理私钥（保存后清空，不回显）",
        }),
        e(FieldInput, {
          label: "public_token",
          type: "password",
          value: goaffPublicToken,
          placeholder: "可选 · X-GOAFFPRO-PUBLIC-TOKEN（公钥）",
          onChange: setGoaffPublicToken,
          hint: "可选公钥（保存后清空，不回显）",
        }),
      ),
      e(
        "div",
        { className: "mt-4 flex items-center gap-3 flex-wrap" },
        e(
          "button",
          {
            type: "button",
            onClick: () => void onSaveGoaff(),
            disabled: goaffSaving || !goaffAccessToken.trim(),
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5 text-[12px] font-medium text-emerald-300 hover:bg-emerald-500/15 disabled:opacity-50",
          },
          goaffSaving ? e(Loader2, { size: 13, className: "animate-spin" }) : e(Plug, { size: 13 }),
          "保存并连接",
        ),
        e(
          "button",
          {
            type: "button",
            onClick: () => void loadGoaffStatus(),
            disabled: goaffStatusLoading,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white disabled:opacity-50",
          },
          goaffStatusLoading ? e(Loader2, { size: 12, className: "animate-spin" }) : e(RefreshCw, { size: 12 }),
          "刷新状态",
        ),
        e(
          "button",
          {
            type: "button",
            onClick: () => void onPreviewAffiliates(),
            disabled: goaffPreviewLoading,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-[11px] text-blue-300 hover:bg-blue-500/15 disabled:opacity-50",
          },
          goaffPreviewLoading ? e(Loader2, { size: 12, className: "animate-spin" }) : e(Table2, { size: 12 }),
          "拉取 affiliate 预览",
        ),
      ),
      goaffSaveErr
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
            goaffSaveErr,
          )
        : null,
      goaffSaveMsg
        ? e(
            "div",
            {
              className:
                "mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-[12px] text-emerald-300",
            },
            goaffSaveMsg,
          )
        : null,
      goaffStatusErr
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300" },
            goaffStatusErr,
          )
        : null,
      // GOAFFPRO 状态明细 + api_base（masked-only,绝不回显明文 token）。
      goaffStatus
        ? e(
            "div",
            { className: "mt-3 space-y-2" },
            goaffStatus.api_base
              ? e(
                  "div",
                  { className: "text-[12px] text-slate-300" },
                  "api_base:",
                  e("code", { className: "ml-2 font-mono text-blue-300" }, goaffStatus.api_base),
                )
              : null,
            e(EnvRow, {
              name: "GOAFFPRO_ACCESS_TOKEN",
              configured: Boolean(goaffStatus.access_token_configured),
              hint: "管理私钥（masked）",
            }),
            e(EnvRow, {
              name: "GOAFFPRO_PUBLIC_TOKEN",
              configured: Boolean(goaffStatus.public_token_configured),
              hint: "公钥（masked · 可选）",
            }),
          )
        : null,
      goaffPreviewErr
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300" },
            goaffPreviewErr,
          )
        : null,
      goaffPreview && goaffPreview.ok !== false
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-3" },
            e(
              "div",
              { className: "text-[11px] text-slate-400 mb-2" },
              "affiliate 预览(前 ",
              String(goaffPreview.affiliates?.length ?? 0),
              " 条 · total = ",
              goaffPreview.total === null || goaffPreview.total === undefined
                ? "未知"
                : String(goaffPreview.total),
              ")",
            ),
            (goaffPreview.affiliates && goaffPreview.affiliates.length)
              ? e(
                  "div",
                  { className: "space-y-2" },
                  goaffPreview.affiliates.map((aff, i) => {
                    const rawKeys = Array.isArray(aff?._raw_keys)
                      ? aff._raw_keys
                      : Object.keys(aff || {}).filter((k) => k !== "_raw_keys");
                    return e(
                      "div",
                      {
                        key: i,
                        className: "rounded-md border border-white/[0.06] bg-black/20 px-2.5 py-2",
                      },
                      e(
                        "div",
                        { className: "text-[11px] text-slate-300" },
                        pickStr(aff as Row, ["name", "email", "id"], "affiliate #" + (i + 1)),
                      ),
                      e(
                        "div",
                        { className: "text-[10px] font-mono text-slate-500 mt-1 break-all" },
                        "字段: ",
                        rawKeys.join(", ") || "—",
                      ),
                    );
                  }),
                )
              : e(
                  "div",
                  { className: "text-[11px] text-slate-600" },
                  "暂无 affiliate（GOAFFPRO 端尚未注册 KOL，或字段映射待校准）。",
                ),
          )
        : null,
    ),
    */

    // 短链生成已迁移说明卡(原 Region ② 短链生成器退役后的原位说明)。
    RETIRE_SELF_LINK
      ? e(
          Card,
          { className: "border-blue-500/15 bg-blue-500/[0.03]" },
          e(
            "div",
            { className: "flex items-start gap-3" },
            e(Link2, { size: 18, className: "text-blue-300 mt-0.5 shrink-0" }),
            e(
              "div",
              null,
              e(
                "div",
                { className: "text-[13px] font-medium text-blue-200 mb-1" },
                "短链生成已迁移到 GOAFFPRO",
              ),
              e(
                "p",
                { className: "text-[12px] text-slate-400" },
                "每个 KOL 注册为 affiliate 后自动获得专属追踪链与优惠码，无需手动生成短链。原「生成推广链接」面板已退役。",
              ),
            ),
          ),
        )
      : null,

    // -----------------------------------------------------------------------
    // 自建 Shopify(旧·待退役)—— 可折叠二级块,默认折叠,展开仍可用,不删不破坏。
    // -----------------------------------------------------------------------
    e(
      "div",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.01] mb-4" },
      e(
        "button",
        {
          type: "button",
          onClick: () => setLegacyShopifyOpen((v) => !v),
          className:
            "w-full flex items-center justify-between gap-3 px-5 py-3 text-left",
        },
        e(
          "span",
          { className: "inline-flex items-center gap-2 text-[12px] font-medium text-slate-400" },
          e(PackageCheck, { size: 14, className: "text-slate-500" }),
          "自建 Shopify（旧 · 待退役）",
          e(
            "span",
            { className: "rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300" },
            "Legacy",
          ),
        ),
        e(
          "span",
          { className: "text-[11px] text-slate-500" },
          legacyShopifyOpen ? "收起 ▲" : "展开 ▼",
        ),
      ),
      legacyShopifyOpen
        ? e(
            "div",
            { className: "px-5 pb-5 pt-1 space-y-4" },
            e(
              "p",
              { className: "text-[11px] text-slate-500" },
              "自建 Shopify 直连归因已被 GOAFFPRO 取代,此处保留仅供回滚/排障,展开后仍可正常保存与查看状态。",
            ),
    e(
      Card,
      { className: "" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e("h2", { className: "text-sm font-semibold text-white" }, "自建 Shopify 连接（旧）"),
        e(StatusPill, {
          ok: configured,
          okLabel: "已配置",
          badLabel: "未配置",
        }),
      ),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-4" },
        "填写店铺域名与 Admin API 凭据，提交后由后端加密存储（绝不回显、绝不入日志）。",
      ),
      e(
        "div",
        { className: "space-y-3" },
        e(FieldInput, {
          label: "store_domain",
          type: "text",
          value: storeDomain,
          placeholder: "your-store.myshopify.com",
          onChange: setStoreDomain,
          hint: "店铺域名",
        }),
        e(FieldInput, {
          label: "access_token",
          type: "password",
          value: accessToken,
          placeholder: "shpat_…",
          onChange: setAccessToken,
          hint: "Shopify Admin API access token（保存后清空，不回显）",
        }),
        e(FieldInput, {
          label: "webhook_secret",
          type: "password",
          value: webhookSecret,
          placeholder: "可选 · 用于校验 webhook HMAC",
          onChange: setWebhookSecret,
          hint: "可选",
        }),
      ),
      e(
        "div",
        { className: "mt-4 flex items-center gap-3" },
        e(
          "button",
          {
            type: "button",
            onClick: () => void onSaveCreds(),
            disabled: savingCreds || !storeDomain || !accessToken,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5 text-[12px] font-medium text-emerald-300 hover:bg-emerald-500/15 disabled:opacity-50",
          },
          savingCreds ? e(Loader2, { size: 13, className: "animate-spin" }) : e(Plug, { size: 13 }),
          "保存并连接",
        ),
        e(
          "button",
          {
            type: "button",
            onClick: () => void loadStatus(),
            disabled: statusLoading,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white disabled:opacity-50",
          },
          statusLoading ? e(Loader2, { size: 12, className: "animate-spin" }) : e(RefreshCw, { size: 12 }),
          "刷新状态",
        ),
      ),
      credsErr
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
            credsErr,
          )
        : null,
      credsMsg
        ? e(
            "div",
            {
              className:
                "mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-[12px] text-emerald-300",
            },
            credsMsg,
          )
        : null,
    ),

    // Live status detail card.
    e(
      Card,
      { className: "" },
      e("h3", { className: "text-sm font-semibold text-white mb-3" }, "连接状态明细"),
      statusErr
        ? e(
            "div",
            { className: "mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
            statusErr,
          )
        : null,
      e(
        "div",
        { className: "text-[12px] text-slate-400 mb-3" },
        status?.message || (statusLoading ? "加载中…" : "等待读取状态…"),
      ),
      status?.shop_domain
        ? e(
            "div",
            { className: "text-[12px] text-slate-300 mb-3" },
            "店铺域名:",
            e("code", { className: "ml-2 font-mono text-blue-300" }, status.shop_domain),
          )
        : null,
      e(
        "div",
        { className: "space-y-2" },
        e(EnvRow, {
          name: "SHOPIFY_SHOP_DOMAIN",
          configured: Boolean(env.SHOPIFY_SHOP_DOMAIN ?? status?.shop_domain_configured),
          hint: "店铺域名",
        }),
        e(EnvRow, {
          name: "SHOPIFY_ADMIN_ACCESS_TOKEN",
          configured: Boolean(env.SHOPIFY_ADMIN_ACCESS_TOKEN ?? status?.access_token_configured),
          hint: "Admin API access token",
        }),
        e(EnvRow, {
          name: "SHOPIFY_WEBHOOK_SECRET",
          configured: Boolean(env.SHOPIFY_WEBHOOK_SECRET ?? status?.webhook_secret_configured),
          hint: "Webhook HMAC secret（可选）",
        }),
      ),
    ),
          ) // /legacy 展开内容 div
        : null, // legacyShopifyOpen 折叠时不渲染
    ), // /自建 Shopify(旧·待退役)可折叠 wrapper
  );

  // =========================================================================
  // Region ② Generate
  // =========================================================================
  const sourceOptions = [
    e("option", { key: "_none", value: "" }, "— 选择来源 —"),
    projects.length
      ? e(
          "optgroup",
          { key: "g_projects", label: "项目" },
          projects.map((p) => {
            const id = pickStr(p, ["id", "project_id", "projectId"]);
            const label = pickStr(p, ["name", "title", "project_name"], `项目 ${id}`);
            return e("option", { key: `project:${id}`, value: `project:${id}` }, label);
          }),
        )
      : null,
    events.length
      ? e(
          "optgroup",
          { key: "g_events", label: "活动" },
          events.map((ev) => {
            const id = pickStr(ev, ["id", "event_id", "eventId"]);
            const label = pickStr(ev, ["title", "name"], `活动 ${id}`);
            return e("option", { key: `event:${id}`, value: `event:${id}` }, label);
          }),
        )
      : null,
  ];

  const kolOptions = [
    e("option", { key: "_none", value: "" }, "— 选择 KOL —"),
    ...kols.map((k) => {
      const id = pickStr(k, ["kol_id", "id", "kolId"]);
      const label = pickStr(k, ["display_name", "name", "handle", "username"], `KOL ${id}`);
      return e("option", { key: id || label, value: id }, label);
    }),
  ];

  const productOptions = [
    e("option", { key: "_none", value: "" }, "— 选择产品 —"),
    ...products.map((p) => {
      const sku = pickStr(p, ["sku", "product_sku"]);
      const name = pickStr(p, ["marketing_name", "model_name", "product_name", "name"], sku);
      return e("option", { key: sku || name, value: sku }, sku ? `${sku} · ${name}` : name);
    }),
  ];

  // 退役:RETIRE_SELF_LINK=true 时整块短链生成器不渲染(代码留着,可回滚)。
  // 原位说明卡已在 Region ① 内通过 RETIRE_SELF_LINK 渲染。
  const regionGenerate = RETIRE_SELF_LINK ? null : e(
    "div",
    { className: tab === "generate" ? "" : "hidden" },
    e(
      Card,
      { className: "" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e("h2", { className: "text-sm font-semibold text-white" }, "② 生成推广链接"),
        e(
          "button",
          {
            type: "button",
            onClick: () => void loadOptions(),
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white",
          },
          e(RefreshCw, { size: 12 }),
          "刷新选项",
        ),
      ),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-4" },
        "选择 来源(项目/活动) × KOL × 产品 × 折扣，生成专属折扣码与追踪短链。",
      ),
      e(
        "div",
        { className: "grid grid-cols-1 md:grid-cols-2 gap-3" },
        e(FieldSelect, { label: "来源（项目 / 活动）", value: source, onChange: setSource }, sourceOptions),
        e(FieldSelect, { label: "KOL", value: kolId, onChange: setKolId }, kolOptions),
        e(FieldSelect, { label: "产品", value: productSku, onChange: setProductSku }, productOptions),
        e(FieldInput, {
          label: "折扣（%）",
          type: "number",
          value: discount,
          placeholder: "例如 10",
          onChange: setDiscount,
          hint: "可选",
        }),
      ),
      e(
        "div",
        { className: "mt-4" },
        e(
          "button",
          {
            type: "button",
            onClick: () => void onGenerate(),
            disabled: generating || !source || !kolId || !productSku,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-blue-500/30 bg-blue-500/10 px-4 py-1.5 text-[12px] font-medium text-blue-300 hover:bg-blue-500/15 disabled:opacity-50",
          },
          generating ? e(Loader2, { size: 13, className: "animate-spin" }) : e(Link2, { size: 13 }),
          "生成推广链接",
        ),
      ),
      genErr
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
            genErr,
          )
        : null,
      genResult
        ? e(
            "div",
            { className: "mt-4 space-y-3" },
            e(
              "div",
              { className: "text-[11px] font-medium text-emerald-300 mb-1" },
              "生成结果",
            ),
            e(CopyField, { label: "折扣码", value: genResult.discount_code || "" }),
            e(CopyField, { label: "追踪短链", value: genResult.tracking_url || "" }),
            e(CopyField, { label: "折扣直达链", value: genResult.discount_url || "" }),
            genResult.slug
              ? e("div", { className: "text-[11px] text-slate-600" }, "slug: ", genResult.slug)
              : null,
          )
        : null,
    ),
  );

  // =========================================================================
  // Region ③ Track
  // =========================================================================
  const regionTrack = e(
    "div",
    { className: tab === "track" ? "" : "hidden" },
    e(
      Card,
      { className: "" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e(
          "div",
          { className: "flex items-center gap-2" },
          e("h2", { className: "text-sm font-semibold text-white" }, "③ 数据追踪"),
          lastSynced ? e("span", { className: "text-[10px] text-slate-500" }, "更新于 " + new Date(lastSynced).toLocaleString("zh-CN", { hour12: false })) : null,
        ),
        e(
          "button",
          {
            type: "button",
            onClick: () => void refreshTrack(),
            disabled: trackLoading || trackSyncing,
            title: "从 GOAFFPRO 拉取最新点击/订单/GMV/佣金并落库",
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white disabled:opacity-50",
          },
          (trackLoading || trackSyncing) ? e(Loader2, { size: 12, className: "animate-spin" }) : e(RefreshCw, { size: 12 }),
          trackSyncing ? "同步中…" : "刷新",
        ),
      ),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-3" },
        "按 KOL / 来源 / 产品 汇总点击、订单、GMV 与 ROI。无数据时诚实显示「待接入」，绝不编造数字。",
      ),
      // GOAFFPRO 接入 note —— 归因将由 affiliate↔KOL + 销售/佣金自动接入,字段对接中。
      e(
        "div",
        { className: "mb-3 rounded-lg border border-blue-500/15 bg-blue-500/[0.04] px-3 py-2 text-[12px] text-blue-200" },
        "归因数据将由 GOAFFPRO 自动接入（affiliate ↔ KOL + 销售 / 佣金），字段对接中。",
      ),
      trackErr
        ? e(
            "div",
            { className: "mb-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300" },
            trackErr,
          )
        : null,
      rows.length === 0
        ? e(
            "div",
            { className: "py-10 text-center" },
            e(Table2, { size: 24, className: "mx-auto text-slate-600 mb-2" }),
            e("div", { className: "text-[13px] text-slate-400" }, "待接入"),
            e(
              "div",
              { className: "text-[11px] text-slate-600 mt-1" },
              trackLoading ? "加载中…" : "暂无归因数据。生成推广链接并产生点击/订单后将出现在此。",
            ),
          )
        : e(
            "div",
            null,
            // 汇总条:总点击 / 总订单 / 总 GMV / 应付佣金(GOAFFPRO 实时合计)
            trackTotals
              ? e(
                  "div",
                  { className: "mb-3 grid grid-cols-4 gap-2" },
                  [
                    ["总点击", String(trackTotals.clicks ?? 0)],
                    ["总订单", String(trackTotals.orders ?? 0)],
                    ["总 GMV", fmtMoney(trackTotals.gmv_usd)],
                    ["应付佣金", fmtMoney(trackTotals.commission_usd)],
                  ].map(([label, val], i) =>
                    e(
                      "div",
                      { key: i, className: "rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
                      e("div", { className: "text-[9px] text-slate-500" }, label),
                      e("div", { className: "text-[15px] font-semibold text-white tabular-nums mt-0.5" }, val),
                    ),
                  ),
                )
              : null,
            e(
              "div",
              { className: "overflow-x-auto" },
              e(
                "table",
                { className: "w-full text-[11px]" },
                e(
                  "thead",
                  null,
                  e(
                    "tr",
                    { className: "text-left text-slate-500 border-b border-white/[0.06]" },
                    e("th", { className: "py-2 pr-3 font-medium" }, "KOL"),
                    e("th", { className: "py-2 pr-3 font-medium" }, "ref 码"),
                    e("th", { className: "py-2 pr-3 font-medium" }, "优惠码"),
                    e("th", { className: "py-2 pr-3 font-medium" }, "佣金比例"),
                    e("th", { className: "py-2 pr-3 font-medium text-right" }, "点击"),
                    e("th", { className: "py-2 pr-3 font-medium text-right" }, "订单"),
                    e("th", { className: "py-2 pr-3 font-medium text-right" }, "GMV"),
                    e("th", { className: "py-2 font-medium text-right" }, "佣金"),
                  ),
                ),
                e(
                  "tbody",
                  null,
                  rows.map((r, i) => {
                    const row = r as Row;
                    const name = pickStr(row, ["kol_name"], "—");
                    const handle = pickStr(row, ["kol_handle"], "");
                    const platform = pickStr(row, ["kol_platform"], "");
                    const avatar = pickStr(row, ["kol_avatar"], "");
                    const isPartial = !!(row as any).partial;
                    return e(
                      "tr",
                      { key: i, className: "border-b border-white/[0.04] text-slate-300" },
                      e(
                        "td",
                        { className: "py-2 pr-3" },
                        e(
                          "div",
                          { className: "flex items-center gap-2" },
                          avatar
                            ? e("img", { src: avatar, className: "w-6 h-6 rounded-full object-cover shrink-0", referrerPolicy: "no-referrer" })
                            : e("div", { className: "w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0", style: { background: "linear-gradient(135deg,#a855f7,#06b6d4)" } }, (name || "K").charAt(0)),
                          e(
                            "div",
                            { className: "min-w-0" },
                            e("div", { className: "text-slate-200 truncate max-w-[150px]" }, name, isPartial ? e("span", { title: "该 KOL 数据查询失败,数值可能偏低", className: "ml-1 text-amber-300" }, "⚠") : null),
                            (handle || platform) && e("div", { className: "text-[9px] text-slate-500 truncate max-w-[150px]" }, [platform, handle].filter(Boolean).join(" · ")),
                          ),
                        ),
                      ),
                      e("td", { className: "py-2 pr-3 font-mono text-slate-400" }, pickStr(row, ["ref_code"], "—")),
                      e("td", { className: "py-2 pr-3 font-mono text-emerald-300" }, pickStr(row, ["coupon"], "—")),
                      e("td", { className: "py-2 pr-3 text-amber-200" }, pickStr(row, ["commission_rate"], "—")),
                      e("td", { className: "py-2 pr-3 text-right tabular-nums" }, String(pickNum(row, ["clicks"]) ?? 0)),
                      e("td", { className: "py-2 pr-3 text-right tabular-nums" }, String(pickNum(row, ["orders"]) ?? 0)),
                      e("td", { className: "py-2 pr-3 text-right tabular-nums" }, fmtMoney(pickNum(row, ["gmv_usd"]))),
                      e("td", { className: "py-2 text-right tabular-nums" }, fmtMoney(pickNum(row, ["commission_usd"]))),
                    );
                  }),
                ),
              ),
            ),
          ),
    ),
  );

  // =========================================================================
  // Shell
  // =========================================================================
  return e(
    "div",
    { className: "p-6 md:p-8 max-w-4xl mx-auto" },

    // Header
    e(
      "div",
      { className: "flex items-start gap-3 mb-5" },
      e(PackageCheck, { size: 28, className: "text-emerald-400 mt-0.5" }),
      e(
        "div",
        null,
        e("h1", { className: "text-lg font-semibold text-white" }, "Shopify Hub"),
        e(
          "p",
          { className: "text-[12px] text-slate-400 mt-0.5 max-w-xl" },
          "连接 GOAFFPRO 联盟营销：每个 KOL 自动获得追踪链与优惠码，点击 / 销售 / 归因自动接入。",
        ),
      ),
    ),

    // Tab switcher —— 短链生成器退役后隐藏「生成推广链接」tab(RETIRE_SELF_LINK)。
    e(
      "div",
      { className: "flex items-center gap-2 mb-5" },
      TabBtn("connect", "连接", Plug),
      RETIRE_SELF_LINK ? null : TabBtn("generate", "生成推广链接", Link2),
      TabBtn("track", "数据追踪", Table2),
    ),

    regionConnect,
    regionGenerate,
    regionTrack,
  );
}

export default ShopifyHubPage;
