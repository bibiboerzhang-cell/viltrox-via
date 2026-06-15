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
  getPromoAttributionSummary,
  listPromoKols,
  listPromoProducts,
  listSources,
  saveShopifyCreds,
  type GeneratePromoLinkResult,
  type PromoAttributionRow,
} from "../../../services/vkpi/shopifyHub-api";

const e = React.createElement;

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
function Card(props: { children?: unknown; className?: string }, ...children) {
  return e(
    "section",
    {
      className: `rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4 ${props.className || ""}`,
    },
    ...children,
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
  const [trackLoading, setTrackLoading] = useState(false);
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
      const res = await getPromoAttributionSummary(apiToken, { limit: 50 });
      setRows(res.items);
    } catch (err) {
      // Honest: do NOT fabricate rows. Keep the table empty + show the reason.
      setRows([]);
      setTrackErr(err instanceof Error ? err.message : "归因汇总尚未接入");
    } finally {
      setTrackLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    void loadStatus();
    void loadOptions();
    void loadTrack();
  }, [loadStatus, loadOptions, loadTrack]);

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
    e(
      Card,
      { className: "" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e("h2", { className: "text-sm font-semibold text-white" }, "① 连接 Shopify"),
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

  const regionGenerate = e(
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
        e("h2", { className: "text-sm font-semibold text-white" }, "③ 数据追踪"),
        e(
          "button",
          {
            type: "button",
            onClick: () => void loadTrack(),
            disabled: trackLoading,
            className:
              "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white disabled:opacity-50",
          },
          trackLoading ? e(Loader2, { size: 12, className: "animate-spin" }) : e(RefreshCw, { size: 12 }),
          "刷新",
        ),
      ),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-3" },
        "按 KOL / 来源 / 产品 汇总点击、订单、GMV 与 ROI。无数据时诚实显示「待接入」，绝不编造数字。",
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
                  e("th", { className: "py-2 pr-3 font-medium" }, "来源"),
                  e("th", { className: "py-2 pr-3 font-medium" }, "产品"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "点击"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "订单"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "GMV"),
                  e("th", { className: "py-2 font-medium text-right" }, "ROI"),
                ),
              ),
              e(
                "tbody",
                null,
                rows.map((r, i) => {
                  const sourceLabel =
                    pickStr(r as Row, ["source_label"]) ||
                    pickStr(r as Row, ["source_type"], "—");
                  return e(
                    "tr",
                    { key: i, className: "border-b border-white/[0.04] text-slate-300" },
                    e("td", { className: "py-2 pr-3" }, pickStr(r as Row, ["kol_name"], "—")),
                    e("td", { className: "py-2 pr-3" }, sourceLabel),
                    e(
                      "td",
                      { className: "py-2 pr-3 font-mono text-slate-400" },
                      pickStr(r as Row, ["product_sku"], "—"),
                    ),
                    e(
                      "td",
                      { className: "py-2 pr-3 text-right" },
                      String(pickNum(r as Row, ["clicks"]) ?? 0),
                    ),
                    e(
                      "td",
                      { className: "py-2 pr-3 text-right" },
                      String(pickNum(r as Row, ["orders"]) ?? 0),
                    ),
                    e(
                      "td",
                      { className: "py-2 pr-3 text-right" },
                      fmtMoney(pickNum(r as Row, ["gmv_usd"])),
                    ),
                    e(
                      "td",
                      { className: "py-2 text-right" },
                      fmtRoi(pickNum(r as Row, ["roi"])),
                    ),
                  );
                }),
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
          "三区合一：连接 Shopify、生成 KOL 推广链接、追踪点击与销售归因。",
        ),
      ),
    ),

    // Tab switcher
    e(
      "div",
      { className: "flex items-center gap-2 mb-5" },
      TabBtn("connect", "连接", Plug),
      TabBtn("generate", "生成推广链接", Link2),
      TabBtn("track", "数据追踪", Table2),
    ),

    regionConnect,
    regionGenerate,
    regionTrack,
  );
}

export default ShopifyHubPage;
