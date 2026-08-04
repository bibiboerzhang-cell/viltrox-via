// Verbatim from vkpi_v6.15.7_integrated.html


import { X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

// Only list shortcuts that are implemented and covered by interaction tests.
export function ShortcutsModal({ onClose }: { onClose?: () => void }) {
  const { t } = useT();
  return (
    <CenterModal onClose={onClose} maxWidth="md">
      <div className="px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">{t("键盘快捷键")}</h2>
        <button onClick={onClose} className="rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white"><X size={14} /></button>
      </div>
      <div className="space-y-2 p-5">
        <div className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-300">
          <span>{t("打开智能问答与全局搜索")}</span><kbd className="rounded border border-white/10 px-2 py-1 font-mono text-[10px] text-white">⌘/Ctrl K</kbd>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-300">
          <span>{t("在结果中选择")}</span><kbd className="rounded border border-white/10 px-2 py-1 font-mono text-[10px] text-white">↑ / ↓</kbd>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-300">
          <span>{t("打开所选搜索结果")}</span><kbd className="rounded border border-white/10 px-2 py-1 font-mono text-[10px] text-white">Enter</kbd>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-300">
          <span>{t("执行智能问答")}</span><kbd className="rounded border border-white/10 px-2 py-1 font-mono text-[10px] text-white">⌘/Ctrl Enter</kbd>
        </div>
      </div>
    </CenterModal>
  );
}
