// Render parts extracted from ShopifyHubPage.tsx to keep the container ≤800 lines.
// Behavior-preserving move: the JSX (React.createElement trees) is verbatim from
// the original inline blocks; only the surrounding container state/handlers are now
// passed in as typed props. No business logic changed.
//
//   LegacyShopifyConnect — Region ① 的「自建 Shopify(旧·待退役)」可折叠二级块。
//   TrackRegion          — Region ③「数据追踪」(GOAFFPRO 归因汇总表 + 汇总条)。

import React from "react";
import { Link2, Loader2, PackageCheck, Plug, RefreshCw, Search, Table2 } from "lucide-react";
import type { ShopifyProviderStatus } from "../../../domains/attribution";
import type { PromoAttributionRow } from "../../../services/vkpi/shopifyHub-api";
import { Card, EnvRow, FieldInput, StatusPill } from "./ShopifyHubPage.Sections";
import { fmtMoney, pickNum, pickStr, type Row } from "./ShopifyHubPage.helpers";

const e = React.createElement;

// ---------------------------------------------------------------------------
// Region ① — 自建 Shopify(旧·待退役)可折叠块。
// ---------------------------------------------------------------------------

export interface LegacyShopifyConnectProps {
  legacyShopifyOpen: boolean;
  setLegacyShopifyOpen: React.Dispatch<React.SetStateAction<boolean>>;
  configured: boolean;
  storeDomain: string;
  setStoreDomain: (v: string) => void;
  accessToken: string;
  setAccessToken: (v: string) => void;
  webhookSecret: string;
  setWebhookSecret: (v: string) => void;
  savingCreds: boolean;
  credsErr: string;
  credsMsg: string;
  onSaveCreds: () => void | Promise<void>;
  statusLoading: boolean;
  loadStatus: () => void | Promise<void>;
  statusErr: string;
  status: ShopifyProviderStatus | null;
  env: Record<string, unknown>;
}

export function LegacyShopifyConnect(props: LegacyShopifyConnectProps): React.ReactElement {
  const {
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
  } = props;
  const envRec = env;
  return e(
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
                configured: Boolean(envRec.SHOPIFY_SHOP_DOMAIN ?? status?.shop_domain_configured),
                hint: "店铺域名",
              }),
              e(EnvRow, {
                name: "SHOPIFY_ADMIN_ACCESS_TOKEN",
                configured: Boolean(envRec.SHOPIFY_ADMIN_ACCESS_TOKEN ?? status?.access_token_configured),
                hint: "Admin API access token",
              }),
              e(EnvRow, {
                name: "SHOPIFY_WEBHOOK_SECRET",
                configured: Boolean(envRec.SHOPIFY_WEBHOOK_SECRET ?? status?.webhook_secret_configured),
                hint: "Webhook HMAC secret（可选）",
              }),
            ),
          ),
        ) // /legacy 展开内容 div
      : null, // legacyShopifyOpen 折叠时不渲染
  ); // /自建 Shopify(旧·待退役)可折叠 wrapper
}

// ---------------------------------------------------------------------------
// Region ③ — 数据追踪(GOAFFPRO 归因汇总)。
// ---------------------------------------------------------------------------

export interface TrackTotals {
  clicks?: number;
  orders?: number;
  gmv_usd?: number;
  commission_usd?: number;
}

export interface TrackRegionProps {
  tab: string;
  lastSynced: string;
  refreshTrack: () => void | Promise<void>;
  trackLoading: boolean;
  trackSyncing: boolean;
  trackSearch: string;
  setTrackSearch: (v: string) => void;
  loadTrack: (searchArg?: string) => void | Promise<void>;
  trackErr: string;
  rows: PromoAttributionRow[];
  trackTotals: TrackTotals | null;
}

export function TrackRegion(props: TrackRegionProps): React.ReactElement {
  const {
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
  } = props;
  return e(
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
        { className: "text-[11px] text-slate-500 mb-2" },
        "按 KOL 汇总点击 / 订单 / GMV / 佣金,按 GMV 降序(头部在前)。GOAFFPRO 实时归因,读缓存秒出。",
      ),
      // 查询框 + 查询按钮(KOL 多时按名/handle/ref/优惠码筛选)。
      e(
        "div",
        { className: "flex items-center gap-2 mb-3" },
        e("input", {
          type: "text",
          value: trackSearch,
          onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setTrackSearch(ev.target.value),
          onKeyDown: (ev: React.KeyboardEvent<HTMLInputElement>) => { if (ev.key === "Enter") void loadTrack(trackSearch); },
          placeholder: "搜 KOL 名 / handle / ref 码 / 优惠码",
          className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-black/30 px-3 py-1.5 text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-blue-500/40 focus:outline-none",
        }),
        e("button", {
          type: "button",
          onClick: () => void loadTrack(trackSearch),
          disabled: trackLoading,
          className: "inline-flex items-center gap-1 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-[12px] text-blue-200 hover:bg-blue-500/15 disabled:opacity-50",
        }, e(Search, { size: 13 }), "查询"),
        trackSearch
          ? e("button", {
              type: "button",
              onClick: () => { setTrackSearch(""); void loadTrack(""); },
              className: "text-[11px] text-slate-500 hover:text-slate-300",
            }, "清除")
          : null,
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
                  ([
                    ["总点击", String(trackTotals.clicks ?? 0)],
                    ["总订单", String(trackTotals.orders ?? 0)],
                    ["总 GMV", fmtMoney(trackTotals.gmv_usd ?? null)],
                    ["应付佣金", fmtMoney(trackTotals.commission_usd ?? null)],
                  ] as Array<[string, string]>).map(([label, val], i) =>
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
                    const isPartial = Boolean(row.partial);
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
                            (handle || platform) ? e("div", { className: "text-[9px] text-slate-500 truncate max-w-[150px]" }, [platform, handle].filter(Boolean).join(" · ")) : null,
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
}
