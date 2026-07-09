// 数据可视化套件 · 阈值刻度尺(gtm_viz 撤退/观察/加码 移植)
// 三段带 + 脉冲 marker,token 配色,遵守 reduced-motion。
export function ThresholdScale({
  markerPct,
  retreatLabel = "撤退",
  watchLabel = "观察带",
  escalateLabel = "加码",
  cols = [1, 1.4, 1],
}: {
  markerPct: number; // 0-100
  retreatLabel?: string;
  watchLabel?: string;
  escalateLabel?: string;
  cols?: [number, number, number] | number[];
}) {
  const pct = Math.max(0, Math.min(100, markerPct));
  return (
    <div style={{ position: "relative", height: 34, borderRadius: 10, overflow: "hidden", display: "grid", gridTemplateColumns: cols.join("fr ") + "fr", border: "1px solid var(--ds-line)" }}>
      <Zone bg="var(--ds-crit-soft)" color="var(--ds-crit)" label={retreatLabel} />
      <Zone bg="rgba(128,128,128,.08)" color="var(--ds-muted)" label={watchLabel} />
      <Zone bg="var(--ds-good-soft)" color="var(--ds-good)" label={escalateLabel} />
      <span
        className="ds-scale-marker"
        style={{ position: "absolute", top: -4, bottom: -4, left: `${pct}%`, width: 3, borderRadius: 3, background: "var(--ds-text)", boxShadow: "0 0 0 3px var(--ds-card), 0 0 12px rgba(0,0,0,.3)" }}
      />
    </div>
  );
}

function Zone({ bg, color, label }: { bg: string; color: string; label: string }) {
  return (
    <div style={{ display: "grid", placeItems: "center", background: bg, color, fontSize: 9.5, letterSpacing: ".06em", textTransform: "uppercase", fontWeight: 600 }}>
      {label}
    </div>
  );
}
