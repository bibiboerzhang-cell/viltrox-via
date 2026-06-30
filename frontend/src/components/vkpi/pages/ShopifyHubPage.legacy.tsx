// Archived legacy JSX block extracted verbatim from ShopifyHubPage.tsx (Region ①).
// This was ALREADY dead code — a commented-out /* ... */ rollback copy of the old
// GOAFFPRO config card (now living in settings/GoaffproConnectCard.tsx). Moved here
// purely to shrink the container file; it renders nothing and is import-free.
// Kept intact for rollback parity. Do not import — reference only.
export {};

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
