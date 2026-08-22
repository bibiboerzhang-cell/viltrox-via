// 联系方式两档揭示徽标(全站共用):已核验(公开商务信息+证据)/ 观测到 · 未核验(扫描/声明/人工录入)。
// 门面只出这两档文案,绝不露 source / verification_status 这类内部码(raw_bio_scan / observed …)。
// 视觉与 modals/ContactModal.tsx 内的同名徽标 1:1(该文件归 L1 车道,待其切换到本共享件后删私有副本)。
import React from "react";
import type { KolContactTier } from "../lib/kolContacts";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function ContactTierBadge({ tier }: { tier?: KolContactTier }) {
  const { t } = useT();
  if (tier === "verified") {
    return e("span", {
      "data-contact-tier": "verified",
      title: t("来源已核验为公开商务联系方式"),
      className: "inline-flex shrink-0 items-center rounded border border-emerald-400/30 bg-emerald-500/[0.08] px-1 py-0.5 text-[9px] leading-none text-emerald-300",
    }, t("已核验"));
  }
  if (tier === "observed") {
    return e("span", {
      "data-contact-tier": "observed",
      title: t("由公开资料扫描或人工录入获得,尚未核验;联系前请自行确认"),
      className: "inline-flex shrink-0 items-center rounded border border-amber-400/30 bg-amber-500/[0.08] px-1 py-0.5 text-[9px] leading-none text-amber-200",
    }, t("观测到 · 未核验"));
  }
  return null;
}
