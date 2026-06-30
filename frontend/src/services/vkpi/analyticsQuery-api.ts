import { apiFetch, jsonBody } from "../http";

// N6 问数 / 数据导出 · 只读分析问答客户端。
// 后端:backend/app/api/routers/vkpi_analytics_export.py(前缀 /api/admin/vkpi)。
//   GET  /analytics/intents  -> { intents: AnalyticsIntent[] }
//   POST /analytics/ask      -> AnalyticsAskResult
// 全只读、白名单意图;前端绝不拼 SQL,只传 intent / range / source。

// 单元格值:后端 SELECT 原值回传(数值 / 字符串 / 布尔读回为 int 1/0 / NULL)。
export type AnalyticsCell = string | number | boolean | null;
export type AnalyticsRow = Record<string, AnalyticsCell>;

export interface AnalyticsIntent {
  intent: string;
  title: string;
  description: string;
  examples: string[];
  columns: string[];
  uses_range: boolean;
  uses_source: boolean;
}

export interface AnalyticsIntentsResponse {
  intents: AnalyticsIntent[];
}

// /analytics/ask 的回包:命中意图时 intent 为 key;未命中时 intent=null + available_intents。
export interface AnalyticsAskResult {
  intent: string | null;
  title?: string;
  columns: string[];
  rows: AnalyticsRow[];
  sql_explain: string;
  message?: string;
  available_intents?: string[];
}

export interface AnalyticsAskRequest {
  intent: string;
  range?: number;
  source?: string;
}

export async function fetchAnalyticsIntents(token: string): Promise<AnalyticsIntent[]> {
  const res = await apiFetch<AnalyticsIntentsResponse>(
    "/api/admin/vkpi/analytics/intents",
    { cache: "no-store" },
    token,
  );
  return Array.isArray(res.intents) ? res.intents : [];
}

export async function askAnalytics(
  token: string,
  req: AnalyticsAskRequest,
): Promise<AnalyticsAskResult> {
  const body: Record<string, unknown> = { intent: req.intent };
  if (typeof req.range === "number") body.range = req.range;
  if (req.source) body.source = req.source;
  return apiFetch<AnalyticsAskResult>(
    "/api/admin/vkpi/analytics/ask",
    { method: "POST", body: jsonBody(body), timeoutMs: 20000 },
    token,
  );
}

// 把结构化结果转成 CSV(RFC4180 转义),仅前端,不触后端。
export function rowsToCsv(columns: string[], rows: AnalyticsRow[]): string {
  const escape = (value: AnalyticsCell): string => {
    if (value === null || value === undefined) return "";
    const s = String(value);
    if (/[",\r\n]/.test(s)) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines: string[] = [];
  lines.push(columns.map((c) => escape(c)).join(","));
  for (const row of rows) {
    lines.push(columns.map((c) => escape(row[c] ?? null)).join(","));
  }
  return lines.join("\r\n");
}

// 触发浏览器下载(BOM 前缀让 Excel 正确识别 UTF-8 中文)。
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
