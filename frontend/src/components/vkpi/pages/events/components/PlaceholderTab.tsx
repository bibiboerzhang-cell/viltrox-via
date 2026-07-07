import type { LucideIcon } from "lucide-react";

interface PlaceholderTabProps {
  icon: LucideIcon;
  title: string;
  message: string;
}

export default function PlaceholderTab({ icon: Icon, title, message }: PlaceholderTabProps) {
  return (
    <div className="p-12 text-center">
      <Icon size={32} className="text-slate-600 mx-auto mb-3" />
      <div className="text-[12px] text-slate-300 font-medium mb-1">{title}</div>
      <div className="text-[10.5px] text-slate-500">{message}</div>
    </div>
  );
}
