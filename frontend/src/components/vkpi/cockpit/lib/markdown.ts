// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";

const e = React.createElement;

export function markdownToHtml(md: any) {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  // Headers
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // Bold + italic
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/_(.+?)_/g, "<em>$1</em>");
  // Blockquote
  html = html.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");
  // HR
  html = html.replace(/^---$/gm, "<hr>");
  // Tables
  html = html.replace(/((?:\|[^\n]+\|\n)+)/g, (match: any) => {
    const rows = match.trim().split("\n").filter((r: any) => r.includes("|"));
    if (rows.length < 2) return match;
    const isSeparator = (r: any) => /^\|[\s\-:|]+\|$/.test(r.trim());
    let result = "<table>";
    rows.forEach((row: any, i: any) => {
      if (isSeparator(row)) return;
      const cells = row.split("|").slice(1, -1).map((c: any) => c.trim());
      const tag = i === 0 ? "th" : "td";
      result += "<tr>" + cells.map((c: any) => `<${tag}>${c}</${tag}>`).join("") + "</tr>";
    });
    result += "</table>";
    return result;
  });
  // Lists
  html = html.replace(/((?:^- .+\n?)+)/gm, (match: any) => {
    const items = match.trim().split("\n").map((l: any) => l.replace(/^- /, ""));
    return "<ul>" + items.map((i: any) => `<li>${i}</li>`).join("") + "</ul>";
  });
  // Paragraphs (lines not already tagged)
  html = html.split("\n\n").map((block: any) => {
    block = block.trim();
    if (!block) return "";
    if (block.startsWith("<")) return block;
    return `<p>${block.replace(/\n/g, "<br>")}</p>`;
  }).join("\n");
  return html;
}
