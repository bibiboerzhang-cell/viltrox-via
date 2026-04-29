import { motion } from "framer-motion";

export function CatBubble({
  title,
  text,
  scrollable = false,
  className = "",
}: {
  title?: string;
  text: string;
  scrollable?: boolean;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={`relative rounded-[28px] border border-slate-200 bg-white px-6 py-5 text-slate-700 shadow-[0_24px_40px_rgba(15,23,42,0.08)] ${className}`}
    >
      {title ? <div className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-slate-400">{title}</div> : null}
      <div className={scrollable ? "max-h-48 overflow-y-auto pr-2 text-[15px] leading-8" : "text-[15px] leading-8"}>{text}</div>
      <div className="absolute -bottom-3 left-8 h-6 w-6 rotate-45 rounded-[8px] border-b border-r border-slate-200 bg-white" />
    </motion.div>
  );
}
