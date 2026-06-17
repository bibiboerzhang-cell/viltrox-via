// GOAFFPRO 联盟营销连接配置卡（独立组件）—— 从 Shopify Hub「连接」页迁来。
//
// 用户拍板:token/密钥这类应由管理员在 Settings 配置区管理,故把原 ShopifyHubPage
// Region ① 的「连接 GOAFFPRO 联盟营销」配置卡整块抽成这个可复用组件,挂到
// SettingsPage 的「GOAFFPRO 联盟营销」模块下。保存端点是 vkpi:admin 权限,放管理员区正合适。
//
// 行为与原卡 1:1 保留:两个 token 密码框(保存后清空不回显)+ 保存并连接 + 刷新状态
// + 拉取 affiliate 预览(显 total + 字段名/_raw_keys 便于校准)+ 连接状态徽标 + *_configured 明细。
// 复用 goaffpro-api 的 getGoaffproStatus / saveGoaffproCredentials / listGoaffproAffiliates。
//
// Secrets policy: 绝不 log token;后端加密落库不回显明文;状态只展示 masked + *_configured 布尔。

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Plug,
  RefreshCw,
  Table2,
} from "lucide-react";
import {
  getGoaffproStatus,
  saveGoaffproCredentials,
  listGoaffproAffiliates,
  type GoaffproStatus,
  type ListGoaffproAffiliatesResult,
} from "../../../../services/vkpi/goaffpro-api";

const e = React.createElement;

type Row = Record<string, unknown>;

// ---------------------------------------------------------------------------
// 内联视觉辅件(与 ShopifyHubPage 同款,独立实现避免跨页耦合)。
// ---------------------------------------------------------------------------

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

function FieldInput({
  label,
  type,
  value,
  placeholder,
  onChange,
  hint,
}: {
  label: string;
  type?: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  return e(
    "label",
    { className: "block" },
    e("div", { className: "text-[11px] text-slate-400 mb-1" }, label),
    e("input", {
      type: type || "text",
      value,
      placeholder,
      autoComplete: "new-password",
      onChange: (ev: React.ChangeEvent<HTMLInputElement>) => onChange(ev.target.value),
      className:
        "w-full rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-blue-500/40 focus:outline-none",
    }),
    hint ? e("div", { className: "text-[11px] text-slate-600 mt-1" }, hint) : null,
  );
}

function pickStr(row: Row, keys: string[], fallback = ""): string {
  for (const k of keys) {
    const v = row[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v);
  }
  return fallback;
}

// ---------------------------------------------------------------------------
// 卡片。props: apiToken（管理员登录态 token，鉴权用）。
// ---------------------------------------------------------------------------

export function GoaffproConnectCard({ apiToken = "" }: { apiToken?: string } = {}) {
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

  useEffect(() => {
    void loadGoaffStatus();
  }, [loadGoaffStatus]);

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

  // GOAFFPRO 连接判定:status==='connected' 或 access_token_configured 为真即视为已连接。
  const goaffConnected = Boolean(
    goaffStatus &&
      (goaffStatus.status === "connected" || goaffStatus.access_token_configured),
  );

  return e(
    "section",
    {
      className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5",
    },
    e(
      "div",
      { className: "flex items-center justify-between mb-3" },
      e("h2", { className: "text-sm font-semibold text-white" }, "连接 GOAFFPRO 联盟营销"),
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
      "每个 KOL = 一个 GOAFFPRO affiliate，注册后自动获得专属追踪链与优惠码；点击 / 销售 / 佣金归因由 GOAFFPRO 自动接入，无需手动生成短链。token / 密钥仅管理员可在此配置。",
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
    // affiliate 预览 —— 校准用:显 total + 字段名/_raw_keys 便于后续 key 对接。
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
                      pickStr(aff as Row, ["name", "email", "id"], `affiliate #${i + 1}`),
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
  );
}

export default GoaffproConnectCard;
