// Presentational sub-components extracted from ShopifyHubPage.tsx.
// Behavior-preserving move: JSX is verbatim. These only consume props (no
// container state) — CopyField keeps its own local copied-state, as in the
// original inline implementation.
//
// Inline visual helpers (do NOT import from ShopifyConnectPage) — kept here to
// avoid cross-track coupling, same as the original file.

import React, { useCallback, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy } from "lucide-react";

const e = React.createElement;

export interface StatusPillProps {
  ok: boolean;
  okLabel: string;
  badLabel: string;
}

export function StatusPill({ ok, okLabel, badLabel }: StatusPillProps) {
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

export interface EnvRowProps {
  name: string;
  configured: boolean;
  hint: string;
}

export function EnvRow({ name, configured, hint }: EnvRowProps) {
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

export interface CopyFieldProps {
  label: string;
  value: string;
}

export function CopyField({ label, value }: CopyFieldProps) {
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

// Card shell shared by all three regions.
export interface CardProps {
  children?: React.ReactNode;
  className?: string;
}

export function Card(props: CardProps) {
  // 红线(崩溃修):函数组件被 React 以 (props, legacyContext={}) 调用,绝不能用 ...children
  // rest 参收子节点——那会把空的 legacyContext {} 当 child 渲染 → "Objects are not valid as a
  // React child (object with keys {})" 整页崩。子节点一律走 props.children。
  return e(
    "section",
    {
      className: `rounded-2xl border border-white/[0.06] bg-white/[0.015] p-5 mb-4 ${props.className || ""}`,
    },
    props.children,
  );
}

// Labeled input row used in the connect + generate forms.
export interface FieldInputProps {
  label: string;
  type?: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
  hint?: string;
}

export function FieldInput({ label, type, value, placeholder, onChange, hint }: FieldInputProps) {
  return e(
    "label",
    { className: "block" },
    e("div", { className: "text-[11px] text-slate-400 mb-1" }, label),
    e("input", {
      type: type || "text",
      value,
      placeholder,
      onChange: (ev: React.ChangeEvent<HTMLInputElement>) => onChange(ev.target.value),
      className:
        "w-full rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-blue-500/40 focus:outline-none",
    }),
    hint ? e("div", { className: "text-[11px] text-slate-600 mt-1" }, hint) : null,
  );
}

export interface FieldSelectProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children?: React.ReactNode;
}

export function FieldSelect({ label, value, onChange, children }: FieldSelectProps) {
  return e(
    "label",
    { className: "block" },
    e("div", { className: "text-[11px] text-slate-400 mb-1" }, label),
    e(
      "select",
      {
        value,
        onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => onChange(ev.target.value),
        className:
          "w-full rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] text-slate-200 focus:border-blue-500/40 focus:outline-none",
      },
      children,
    ),
  );
}
