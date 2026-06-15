// 地基A 产品 persona 展示面板(只读)。
// 渲染 vkpi_product_persona 的 LLM 生成「产品是什么 / 理想创作者人群 / 推广方向 / 规避类型」。
// 纯展示组件:不发评分请求、不写 fit;数据由上层(产品详情/搜索详情抽屉)以 persona prop 注入。
// 全新文件,沿用 v615-replica 的 React.createElement 约定,不改 KOLDetailDrawer / planner(避撞 w7fress05)。

import React from "react";
import { Sparkles, Target, Compass, Ban, Layers } from "lucide-react";

const e = React.createElement;

function asArray(value: any) {
  if (Array.isArray(value)) return value.filter((item: any) => String(item || "").trim());
  return [];
}

function ChipRow({ icon, label, items, tone }: any) {
  const list = asArray(items);
  if (!list.length) return null;
  const toneClass =
    tone === "avoid"
      ? "bg-rose-500/[0.08] text-rose-200 border-rose-500/20"
      : tone === "angle"
      ? "bg-amber-500/[0.08] text-amber-200 border-amber-500/20"
      : "bg-white/[0.04] text-slate-200 border-white/10";
  return e(
    "div",
    { className: "space-y-1.5" },
    e(
      "div",
      { className: "flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-400" },
      icon,
      e("span", null, label)
    ),
    e(
      "div",
      { className: "flex flex-wrap gap-1.5" },
      list.map((item: any, idx: any) =>
        e(
          "span",
          {
            key: idx,
            className: "px-2 py-0.5 rounded-full border text-xs " + toneClass,
          },
          String(item)
        )
      )
    )
  );
}

function SpecsGrid({ specs }: any) {
  const entries = specs && typeof specs === "object" && !Array.isArray(specs) ? Object.entries(specs) : [];
  const cleaned = entries.filter(([, value]: any) => String(value ?? "").trim());
  if (!cleaned.length) return null;
  return e(
    "div",
    { className: "space-y-1.5" },
    e(
      "div",
      { className: "flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-400" },
      e(Layers, { size: 13 }),
      e("span", null, "真实参数")
    ),
    e(
      "div",
      { className: "grid grid-cols-2 gap-1.5" },
      cleaned.map(([key, value]: any, idx: any) =>
        e(
          "div",
          { key: idx, className: "flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/[0.03] text-xs" },
          e("span", { className: "text-slate-400" }, String(key)),
          e("span", { className: "text-slate-200 text-right" }, String(value))
        )
      )
    )
  );
}

// persona: get_product_persona(sku) 的返回(JSON 列已为原生 dict/list)。
export function ProductPersonaPanel({ persona, sku }: any) {
  if (!persona) {
    return e(
      "div",
      { className: "p-4 rounded-xl border border-white/10 bg-white/[0.02] text-sm text-slate-400" },
      e("div", { className: "flex items-center gap-2 mb-1 text-slate-300" }, e(Sparkles, { size: 14 }), e("span", null, "产品 persona")),
      e("p", null, sku ? `「${sku}」尚未生成 persona。运行 build_product_personas.py --execute 后可见。` : "暂无产品 persona。")
    );
  }
  return e(
    "div",
    { className: "p-4 rounded-xl border border-white/10 bg-white/[0.02] space-y-3" },
    e(
      "div",
      { className: "flex items-center gap-2 text-slate-200" },
      e(Sparkles, { size: 15, className: "text-violet-300" }),
      e("span", { className: "text-sm font-medium" }, "产品 persona / 推广方向"),
      persona.category
        ? e("span", { className: "ml-auto px-2 py-0.5 rounded-full bg-white/[0.04] text-[11px] text-slate-400" }, String(persona.category))
        : null
    ),
    persona.what_is
      ? e("p", { className: "text-sm text-slate-300 leading-relaxed" }, String(persona.what_is))
      : null,
    persona.ideal_persona
      ? e(
          "div",
          { className: "space-y-1.5" },
          e(
            "div",
            { className: "flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-400" },
            e(Target, { size: 13 }),
            e("span", null, "理想创作者人群")
          ),
          e("p", { className: "text-sm text-slate-200 leading-relaxed" }, String(persona.ideal_persona))
        )
      : null,
    e(SpecsGrid, { specs: persona.key_specs_json }),
    e(ChipRow, { icon: e(Target, { size: 13 }), label: "创作者类型", items: persona.ideal_creator_types_json, tone: "neutral" }),
    e(ChipRow, { icon: e(Layers, { size: 13 }), label: "垂类", items: persona.verticals_json, tone: "neutral" }),
    e(ChipRow, { icon: e(Compass, { size: 13 }), label: "推广方向", items: persona.promotion_angles_json, tone: "angle" }),
    e(ChipRow, { icon: e(Ban, { size: 13 }), label: "规避类型", items: persona.avoid_types_json, tone: "avoid" }),
    persona.model
      ? e("div", { className: "pt-1 text-[10px] text-slate-500" }, `LLM ${String(persona.model)} · ${String(persona.source || "llm_persona_v1")}`)
      : null
  );
}

export default ProductPersonaPanel;
