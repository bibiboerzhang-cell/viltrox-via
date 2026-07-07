// Verbatim from vkpi_v6.15.7_integrated.html


import { Settings, X } from "lucide-react";
import { CenterModal } from "./CenterModal";

export function SettingsModal({ onClose, t }: any) {
  return (
    <CenterModal onClose={onClose} maxWidth="lg">
      <div className="px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">{t("系统设置")}</h2>
        <button onClick={onClose} className="rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white"><X size={14} /></button>
      </div>
      <div className="p-8 text-center">
        <Settings size={36} className="mx-auto text-slate-600 mb-3" />
        <div className="text-[12px] text-slate-300 mb-1">{t("系统设置稍后研究")}</div>
        <div className="text-[10px] text-slate-500">{t("API Keys / Platform Crawl / Feature Flags / Budgets / 成员访问 / Audit · 占位")}</div>
      </div>
    </CenterModal>
  );
}
