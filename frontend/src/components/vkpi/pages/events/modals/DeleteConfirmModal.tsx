import React from "react";
import { AlertCircle } from "lucide-react";

interface DeleteConfirmModalProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
  onConfirm: () => void;
}

export default function DeleteConfirmModal({ title, subtitle, onClose, onConfirm }: DeleteConfirmModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="rounded-2xl border border-red-500/30 bg-[#0b1220] w-full max-w-sm p-5" onClick={(ev: React.MouseEvent) => ev.stopPropagation()}>
        <div className="flex items-start gap-3 mb-4">
          <div className="w-9 h-9 rounded-lg bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0">
            <AlertCircle size={16} className="text-red-300" />
          </div>
          <div>
            <h3 className="text-[14px] font-semibold text-white">确认删除</h3>
            <p className="text-[11px] text-slate-400 mt-1">{title}</p>
            {subtitle && <p className="text-[10px] text-slate-500 mt-1">{subtitle}</p>}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]">取消</button>
          <button onClick={onConfirm} className="px-3.5 py-1.5 rounded-md bg-red-500 hover:bg-red-400 text-white text-[11px] font-medium">删除</button>
        </div>
      </div>
    </div>
  );
}
