// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { ArrowLeft, ChevronRight } from "lucide-react";

export function Breadcrumb({ levels, onGoBack }: any) {
  if (levels.length === 0) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 backdrop-blur-xl">
      <button
        onClick={onGoBack}
        aria-label="Go back"
        className="flex items-center gap-1 rounded-md text-[10px] text-muted hover:text-ink"
      >
        <ArrowLeft size={12} />
        <span>Back</span>
      </button>
      <div className="h-3 w-px bg-line" />
      <div className="flex items-center gap-1 text-[11px]">
        <span className="text-muted">World</span>
        {levels.map((lv: any, i: any) => (
          <React.Fragment key={i}>
            <ChevronRight size={10} className="text-muted" />
            <span className={i === levels.length - 1 ? "text-ink font-medium" : "text-muted"}>{lv}</span>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
