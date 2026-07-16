// Shopify connect page for the V-KPI v6.15 replica shell.
// Additive + honest: it surfaces the EXISTING attribution pipeline's Shopify
// connection state (env-based config, same pattern as the 17track token) and the
// real vkpi_shopify_sync_runs history. It never fabricates orders or sales.

import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, PackageCheck, RefreshCw, XCircle } from "lucide-react";
import {
  getShopifyStatus,
  runShopifySync,
  type ShopifyProviderStatus,
  type ShopifySyncRun,
} from "../../../domains/attribution";

const e = React.createElement;

// Webhook host base: same-origin in the browser; the backend mounts the webhook
// routes under /api/vkpi/webhooks/shopify (see vkpi.py webhook_router).
function originBase(): string {
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin;
  }
  return "";
}

function webhookUrl(path: string): string {
  return `${originBase()}${path}`;
}

function StatusPill({ ok, okLabel, badLabel }: { ok: boolean; okLabel: string; badLabel: string }) {
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

function EnvRow({ name, configured, hint }: { name: string; configured: boolean; hint: string }) {
  return e(
    "div",
    { className: "flex items-start justify-between gap-3 rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2" },
    e(
      "div",
      { className: "min-w-0" },
      e("code", { className: "text-[12px] font-mono text-slate-200" }, name),
      e("div", { className: "text-[11px] text-slate-500 mt-0.5" }, hint),
    ),
    e(StatusPill, { ok: configured, okLabel: "已配置", badLabel: "未配置" }),
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(value).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      });
    }
  }, [value]);
  return e(
    "div",
    { className: "rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2" },
    e("div", { className: "text-[11px] text-slate-500 mb-1" }, label),
    e(
      "div",
      { className: "flex items-center gap-2" },
      e(
        "code",
        { className: "flex-1 min-w-0 truncate text-[12px] font-mono text-blue-300" },
        value,
      ),
      e(
        "button",
        {
          type: "button",
          onClick: onCopy,
          className: "shrink-0 inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white",
        },
        e(Copy, { size: 11 }),
        copied ? "已复制" : "复制",
      ),
    ),
  );
}

function fmtTime(value?: string | null): string {
  if (!value) return "—";
  return String(value).replace("T", " ").replace("Z", "");
}

export function ShopifyConnectPage({ apiToken = "" }: { apiToken?: string } = {}) {
  const [status, setStatus] = useState<ShopifyProviderStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<Record<string, unknown> | null>(null);
  const [syncError, setSyncError] = useState("");

  const loadStatus = useCallback(async () => {
    if (!apiToken) {
      setError("缺少 API token，无法读取 Shopify 连接状态。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await getShopifyStatus(apiToken, 10);
      setStatus(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取 Shopify 状态失败");
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const onTestSync = useCallback(async () => {
    if (!apiToken) {
      setSyncError("缺少 API token，无法触发测试同步。");
      return;
    }
    setSyncing(true);
    setSyncError("");
    setSyncResult(null);
    try {
      const res = await runShopifySync(apiToken, {});
      setSyncResult(res as Record<string, unknown>);
      // Refresh the history table so the new run row shows up.
      await loadStatus();
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "测试同步失败");
    } finally {
      setSyncing(false);
    }
  }, [apiToken, loadStatus]);

  const configured = ["configured", "connected"].includes(String(status?.provider_status || ""));
  const env = {
    SHOPIFY_SHOP_DOMAIN: Boolean(status?.credential_fields?.shop_domain),
    SHOPIFY_ADMIN_ACCESS_TOKEN: Boolean(status?.credential_fields?.access_token),
    SHOPIFY_WEBHOOK_SECRET: Boolean(status?.credential_fields?.webhook_secret),
  };
  const runs: ShopifySyncRun[] = status?.sync_runs || [];
  const ordersWebhook = webhookUrl(status?.webhooks?.orders_create || "/api/vkpi/webhooks/shopify/orders");
  const refundsWebhook = webhookUrl(status?.webhooks?.orders_refund_create || "/api/vkpi/webhooks/shopify/refunds");

  return e(
    "div",
    { className: "p-6 md:p-8 max-w-4xl mx-auto" },

    // Header
    e(
      "div",
      { className: "flex items-start justify-between gap-4 mb-6" },
      e(
        "div",
        { className: "flex items-start gap-3" },
        e(PackageCheck, { size: 28, className: "text-emerald-400 mt-0.5" }),
        e(
          "div",
          null,
          e("h1", { className: "text-lg font-semibold text-white" }, "Shopify 连接"),
          e(
            "p",
            { className: "text-[12px] text-slate-400 mt-0.5 max-w-xl" },
            "把 Shopify 订单接入既有归因管线(vkpi_sales_attributions)。配置后台环境变量并在 Shopify Admin 注册 webhook 即可。未配置时不会生成任何假订单或假销售额。",
          ),
        ),
      ),
      e(
        "button",
        {
          type: "button",
          onClick: () => void loadStatus(),
          disabled: loading,
          className: "shrink-0 inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white disabled:opacity-50",
        },
        loading ? e(Loader2, { size: 12, className: "animate-spin" }) : e(RefreshCw, { size: 12 }),
        "刷新",
      ),
    ),

    error
      ? e(
          "div",
          { className: "mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
          error,
        )
      : null,

    // (a) Provider status
    e(
      "section",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4" },
      e(
        "div",
        { className: "flex items-center justify-between mb-3" },
        e("h2", { className: "text-sm font-semibold text-white" }, "连接状态"),
        e(StatusPill, {
          ok: configured,
          okLabel: "Shopify Admin API 已配置",
          badLabel: "未配置",
        }),
      ),
      e(
        "div",
        { className: "text-[12px] text-slate-400 mb-3" },
        status?.message || (loading ? "加载中…" : "等待读取状态…"),
      ),
      status?.shop_domain
        ? e(
            "div",
            { className: "text-[12px] text-slate-300 mb-3" },
            "店铺域名:",
            e("code", { className: "ml-2 font-mono text-blue-300" }, status.shop_domain),
          )
        : null,
    ),

    // (b) Env var instructions
    e(
      "section",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4" },
      e("h2", { className: "text-sm font-semibold text-white mb-1" }, "第 1 步 · 配置环境变量"),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-3" },
        "在项目根目录的 .gitignore 保护的 ",
        e("code", { className: "font-mono text-slate-200" }, ".env"),
        " 文件中添加以下变量(与现有 ",
        e("code", { className: "font-mono text-slate-200" }, "VKPI_17TRACK_TOKEN"),
        " 同一套路),保存后重启后端进程使其生效。Token 只放 .env,绝不入库或入码。",
      ),
      e(
        "div",
        { className: "space-y-2" },
        e(EnvRow, {
          name: "SHOPIFY_SHOP_DOMAIN",
          configured: Boolean(env.SHOPIFY_SHOP_DOMAIN),
          hint: "例如 your-store.myshopify.com",
        }),
        e(EnvRow, {
          name: "SHOPIFY_ADMIN_ACCESS_TOKEN",
          configured: Boolean(env.SHOPIFY_ADMIN_ACCESS_TOKEN),
          hint: "Shopify Admin API access token（shpat_…）",
        }),
        e(EnvRow, {
          name: "SHOPIFY_WEBHOOK_SECRET",
          configured: Boolean(env.SHOPIFY_WEBHOOK_SECRET),
          hint: "可选:用于校验 webhook HMAC 签名",
        }),
      ),
    ),

    // (c) Webhook URLs
    e(
      "section",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4" },
      e("h2", { className: "text-sm font-semibold text-white mb-1" }, "第 2 步 · 在 Shopify Admin 注册 Webhook"),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-3" },
        "Settings → Notifications → Webhooks,新增两个 webhook(格式选 JSON),指向以下地址:",
      ),
      e(
        "div",
        { className: "space-y-3" },
        e(
          "div",
          null,
          e("div", { className: "text-[11px] text-slate-400 mb-1" }, "事件 orders/create →"),
          e(CopyField, { label: ordersWebhook ? "" : "Webhook URL", value: ordersWebhook }),
        ),
        e(
          "div",
          null,
          e("div", { className: "text-[11px] text-slate-400 mb-1" }, "事件 orders/refund/create →"),
          e(CopyField, { label: refundsWebhook ? "" : "Webhook URL", value: refundsWebhook }),
        ),
      ),
    ),

    // (d) Test sync
    e(
      "section",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4" },
      e("h2", { className: "text-sm font-semibold text-white mb-1" }, "第 3 步 · 测试同步"),
      e(
        "p",
        { className: "text-[12px] text-slate-400 mb-3" },
        "记录一次同步请求(写入 vkpi_shopify_sync_runs)。未配置 Admin API 时返回 not_configured,不生成任何订单。",
      ),
      e(
        "button",
        {
          type: "button",
          onClick: () => void onTestSync(),
          disabled: syncing,
          className: "inline-flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5 text-[12px] font-medium text-emerald-300 hover:bg-emerald-500/15 disabled:opacity-50",
        },
        syncing ? e(Loader2, { size: 13, className: "animate-spin" }) : e(RefreshCw, { size: 13 }),
        "测试同步",
      ),
      syncError
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[12px] text-red-300" },
            syncError,
          )
        : null,
      syncResult
        ? e(
            "div",
            { className: "mt-3 rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2.5 text-[12px] text-slate-300 font-mono space-y-1" },
            e("div", null, "sync_uid: ", String(syncResult.sync_uid ?? "—")),
            e("div", null, "status: ", String(syncResult.status ?? "—")),
            e("div", null, "provider_status: ", String(syncResult.provider_status ?? "—")),
            e("div", null, "orders_received: ", String(syncResult.orders_received ?? 0)),
            syncResult.message ? e("div", { className: "text-slate-400" }, String(syncResult.message)) : null,
          )
        : null,
    ),

    // (e) Sync history table
    e(
      "section",
      { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5" },
      e("h2", { className: "text-sm font-semibold text-white mb-3" }, "同步历史 (vkpi_shopify_sync_runs)"),
      runs.length === 0
        ? e(
            "div",
            { className: "text-[12px] text-slate-500 py-6 text-center" },
            loading ? "加载中…" : "暂无同步记录。点击上方「测试同步」生成第一条记录。",
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
                  e("th", { className: "py-2 pr-3 font-medium" }, "开始时间"),
                  e("th", { className: "py-2 pr-3 font-medium" }, "来源"),
                  e("th", { className: "py-2 pr-3 font-medium" }, "状态"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "收到"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "已匹配"),
                  e("th", { className: "py-2 pr-3 font-medium text-right" }, "未匹配"),
                  e("th", { className: "py-2 font-medium" }, "说明"),
                ),
              ),
              e(
                "tbody",
                null,
                runs.map((run) =>
                  e(
                    "tr",
                    { key: run.sync_uid || run.id, className: "border-b border-white/[0.04] text-slate-300" },
                    e("td", { className: "py-2 pr-3 whitespace-nowrap font-mono text-slate-400" }, fmtTime(run.started_at)),
                    e("td", { className: "py-2 pr-3" }, run.source || "—"),
                    e(
                      "td",
                      { className: "py-2 pr-3" },
                      e(
                        "span",
                        {
                          className: `inline-flex items-center gap-1 ${
                            run.status === "not_configured" ? "text-amber-300" : "text-emerald-300"
                          }`,
                        },
                        run.status === "not_configured" ? e(XCircle, { size: 11 }) : e(CheckCircle2, { size: 11 }),
                        run.status || "—",
                      ),
                    ),
                    e("td", { className: "py-2 pr-3 text-right" }, String(run.orders_received ?? 0)),
                    e("td", { className: "py-2 pr-3 text-right" }, String(run.orders_matched ?? 0)),
                    e("td", { className: "py-2 pr-3 text-right" }, String(run.orders_unmatched ?? 0)),
                    e("td", { className: "py-2 text-slate-500 max-w-[200px] truncate" }, run.error_message || "—"),
                  ),
                ),
              ),
            ),
          ),
    ),
  );
}

export default ShopifyConnectPage;
