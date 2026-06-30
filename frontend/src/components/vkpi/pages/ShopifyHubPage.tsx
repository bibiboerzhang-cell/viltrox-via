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
  Link2,
  Loader2,
  PackageCheck,
  Plug,
  RefreshCw,
  Table2,
  type LucideIcon,
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
import {
  RETIRE_SELF_LINK,
  pickStr,
  type Row,
  type TabKey,
} from "./ShopifyHubPage.helpers";
import {
  Card,
  CopyField,
  FieldInput,
  FieldSelect,
  StatusPill,
} from "./ShopifyHubPage.Sections";
import {
  LegacyShopifyConnect,
  TrackRegion,
  type TrackTotals,
} from "./ShopifyHubPage.Parts";

const e = React.createElement;

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
  const [trackTotals, setTrackTotals] = useState<TrackTotals | null>(null);
  const [trackLoading, setTrackLoading] = useState(false);
  const [trackSyncing, setTrackSyncing] = useState(false);
  const [lastSynced, setLastSynced] = useState<string>("");
  const [trackSearch, setTrackSearch] = useState("");
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

  const loadTrack = useCallback(async (searchArg: string = "") => {
    if (!apiToken) {
      setTrackErr("缺少 API token，无法读取归因汇总。");
      return;
    }
    setTrackLoading(true);
    setTrackErr("");
    try {
      // GOAFFPRO 归因汇总(读缓存秒出;按 GMV 降序;可搜 KOL名/handle/ref/优惠码)。
      const res = await getGoaffproSummary(apiToken, { limit: 200, search: searchArg });
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
      void loadTrack(trackSearch);
    }
  }, [apiToken, trackSyncing, loadTrack, trackSearch]);

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
  function TabBtn(key: TabKey, label: string, icon: LucideIcon) {
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

    // ===== 旧·GOAFFPRO 配置卡(已迁至 settings/GoaffproConnectCard.tsx)=====
    // 旧实现的注释回滚副本已抽到 ./ShopifyHubPage.legacy.tsx(原为 /* */ 死代码,
    // 移出仅为瘦身容器;此处不渲染任何东西,行为不变)。

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
    // 整块已抽到 ./ShopifyHubPage.Parts.tsx(LegacyShopifyConnect),渲染结果不变。
    // -----------------------------------------------------------------------
    e(LegacyShopifyConnect, {
      legacyShopifyOpen,
      setLegacyShopifyOpen,
      configured,
      storeDomain,
      setStoreDomain,
      accessToken,
      setAccessToken,
      webhookSecret,
      setWebhookSecret,
      savingCreds,
      credsErr,
      credsMsg,
      onSaveCreds,
      statusLoading,
      loadStatus,
      statusErr,
      status,
      env,
    }),
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
  const regionTrack = e(TrackRegion, {
    tab,
    lastSynced,
    refreshTrack,
    trackLoading,
    trackSyncing,
    trackSearch,
    setTrackSearch,
    loadTrack,
    trackErr,
    rows,
    trackTotals,
  });

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
