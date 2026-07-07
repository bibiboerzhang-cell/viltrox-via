// Verbatim from vkpi_v6.15.7_integrated.html


import { CANDIDATE_KIND_INFO } from "../data/candidateKindInfo";

export function CandidateKindChip({ kind, size = "normal", showDot = true }: { kind?: any; size?: string; showDot?: boolean }) {
  const info = (CANDIDATE_KIND_INFO as any)[kind];
  if (!info) return null;
  const isXs = size === "xs";
  const animating = kind === "new_discovered" || kind === "existing_low_confidence";
  return (
    <span
      className="inline-flex items-center gap-1 rounded font-medium whitespace-nowrap"
      style={{
        background: info.bg,
        border: "1px solid " + info.border,
        color: info.fg,
        padding: isXs ? "0px 5px" : "1px 6px",
        fontSize: isXs ? 9 : 10,
        lineHeight: isXs ? "13px" : "16px",
      }}
      title={info.label}
    >
      {showDot && (
        <span
          className={animating ? "animate-pulse" : ""}
          style={{ width: 5, height: 5, borderRadius: "50%", background: info.fg, display: "inline-block" }}
        />
      )}
      {isXs ? info.short : info.label}
    </span>
  );
}
