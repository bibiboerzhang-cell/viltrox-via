import React from "react";
import { ArrowUpRight, type LucideIcon } from "lucide-react";

export function DashboardBoardLinkCard({
  label,
  summary,
  metric,
  Icon,
  onOpen,
}: {
  label: string;
  summary: string;
  metric?: string | number | null;
  Icon: LucideIcon;
  onOpen?: () => void;
}) {
  return (
    <button type="button" className="vkpi-dashboard-board-link" onClick={onOpen} disabled={!onOpen}>
      <span className="vkpi-dashboard-board-link__icon"><Icon size={17} /></span>
      <span className="vkpi-dashboard-board-link__copy">
        <strong>{metric ?? "打开"}</strong>
        <small>{summary}</small>
      </span>
      <span className="vkpi-dashboard-board-link__name">{label}</span>
      <ArrowUpRight size={15} />
    </button>
  );
}

