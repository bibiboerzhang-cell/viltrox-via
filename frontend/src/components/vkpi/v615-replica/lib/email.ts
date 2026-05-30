// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Camera as Instagram } from "lucide-react";

const e = React.createElement;

export function genEmailSubject(product, item) {
  const handle = (item.handle || "").replace("@", "");
  if (!product) return `Viltrox × ${handle} · Collaboration`;
  if (/Cine|Cinema/i.test(product))     return `Viltrox Cine Series · ${handle} Invitation`;
  if (/LAB$/i.test(product))            return `Viltrox × ${handle} · ${product} Partnership`;
  if (/Pro$/i.test(product))            return `${product} · Partnership with Viltrox`;
  if (Array.isArray(product))           return `Viltrox Lens Collaboration · ${product.length} Product Lines`;
  return `Viltrox × ${handle} · ${product} Collaboration`;
}

export function genEmailBody(product, item) {
  const firstName = item.display_name?.split(" ")[0] || "there";
  const recentWork = item.representative_videos?.[0]?.title || "your recent work";
  const platform = item.platform === "youtube" ? "YouTube channel"
                 : item.platform === "instagram" ? "Instagram"
                 : item.platform === "tiktok" ? "TikTok"
                 : "channel";
  // 信号侦测
  const usedViltroxBefore = item.devices?.has_viltrox || item.brand_collaborations?.some(b => /viltrox/i.test(b.brand));
  const isHighGeo         = (item.geo_match || 0) > 0.9;
  const hasTrend          = item.trend_hits?.length > 0;
  const hasCompetitor     = item.devices?.competitor_brands?.length > 0;
  const isHighLoyalty     = (item.loyalty_score || 0) > 0.85;
  // ── Opener ──
  const opener = usedViltroxBefore
    ? `Hi ${firstName},\n\nGreat to reconnect. We loved seeing "${recentWork}" — your way of working with our glass keeps raising the bar for us.`
    : `Hi ${firstName},\n\nI'm Jianbo from Viltrox. I've been following your ${platform} for a while — "${recentWork}" especially stuck with me.`;
  // ── Why you ──
  const reasons = [];
  if (isHighLoyalty) reasons.push("your audience's depth and consistency");
  if (isHighGeo)     reasons.push("your reach in the markets that matter to us");
  if (hasTrend)      reasons.push(`how naturally your work taps into ${item.trend_hits[0]}`);
  if (reasons.length === 0) reasons.push("how your style aligns with where we're heading");
  const middle = `What stands out is ${reasons.slice(0, 2).join(" and ")}.`;
  // ── Product line ──
  const productName = product || "our latest glass";
  let productLine;
  if (hasCompetitor && !usedViltroxBefore) {
    productLine = `We'd love to put ${productName} in your hands — not to replace what you're using, but to give you another option for the kind of shots you're already chasing.`;
  } else if (usedViltroxBefore) {
    productLine = `We'd like to talk about ${productName} — early access, full creative freedom, and the same partnership terms you're used to.`;
  } else {
    productLine = `We'd like to explore a partnership around ${productName} — sending you a unit to play with, no strings attached, and seeing where it goes from there.`;
  }
  const closer = `Open to a 20-min call this week to see if there's something here?\n\nBest,\nJianbo\nViltrox`;
  return `${opener}\n\n${middle}\n\n${productLine}\n\n${closer}`;
}
