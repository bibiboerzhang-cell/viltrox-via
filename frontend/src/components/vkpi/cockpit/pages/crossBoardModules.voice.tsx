import React from "react";
import { fetchVoiceReportExt } from "../../../../services/vkpi/marketVoice-api";
import { AlertsBody, SentiTrendBody } from "./MarketVoicePage.charts";
import { MODULE_SOURCES } from "./MarketVoicePage.modules";
import { XbCard, useXbFetch, xbGroupGate, type Row } from "./crossBoardModules.shell";

// Dashboard 跨板块拉卡 · 市场之声两件(声量告警 / 情绪趋势)。
//   数据 = GET /api/admin/vkpi/market/voice-report-ext(卡内自取,月份缺省 = 近 30 天,
//   与源板块默认窗口同口径);渲染件 = 源板块导出的 AlertsBody / SentiTrendBody 原件。
//   源板块内点行为「数据点下钻弹窗」;跨板块视图无页级弹窗 → 点行 = 跳市场之声板块
//   (板块页范式:跨板块下钻才出现「切到完整板块」跳转,口径行如实注明)。
// 红线:纯读展示;SrcChip 口径 = 源 MODULE_SOURCES 唯一注册表 + 跨板块差异行,零第二份。

const BOARD_LABEL = "市场之声";
const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };
const XB_ROWS: Array<[string, string]> = [
  ["窗口", "近 30 天固定(月份切换在市场之声板块内)"],
  ["跨板块", "点行 / 点来源徽 → 市场之声板块"],
];

const fetchVoiceExt = (token: string) => fetchVoiceReportExt(token);

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
}

const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export function VoiceAlertsXbCard({ apiToken, onOpenBoard }: XbProps) {
  const ext = useXbFetch(apiToken, fetchVoiceExt);
  const g = (ext.data as Row | null)?.alerts_state as Row | undefined;
  const gate = xbGroupGate({
    apiToken,
    boardLabel: BOARD_LABEL,
    errorTitle: "voice-report-ext 读取失败",
    error: ext.error,
    loaded: ext.data != null,
    loadingText: "图形化字段聚合中…",
    group: g,
  });
  const triggeredN = arr(g?.categories).filter((c) => c.triggered).length;
  return (
    <XbCard
      title="声量告警"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={g ? `${triggeredN} 触发` : undefined}
      srcLabel={src("alerts").label}
      srcRows={[...src("alerts").rows, ...XB_ROWS]}
    >
      {gate ?? <AlertsBody alerts={g || {}} onSelect={() => onOpenBoard()} />}
    </XbCard>
  );
}

export function VoiceSentiXbCard({ apiToken, onOpenBoard }: XbProps) {
  const ext = useXbFetch(apiToken, fetchVoiceExt);
  const g = (ext.data as Row | null)?.sentiment_summary as Row | undefined;
  const gate = xbGroupGate({
    apiToken,
    boardLabel: BOARD_LABEL,
    errorTitle: "voice-report-ext 读取失败",
    error: ext.error,
    loaded: ext.data != null,
    loadingText: "图形化字段聚合中…",
    group: g,
  });
  const cnt = `${String(g?.granularity) === "week" ? "周" : "日"} × ${arr(g?.trend).length}`;
  return (
    <XbCard
      title="情绪趋势"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={g ? cnt : undefined}
      srcLabel={src("senti").label}
      srcRows={[...src("senti").rows, ...XB_ROWS]}
    >
      {gate ?? <SentiTrendBody senti={g || {}} onPointClick={() => onOpenBoard()} />}
    </XbCard>
  );
}
