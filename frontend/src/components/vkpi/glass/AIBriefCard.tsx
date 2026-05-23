import type { ReactNode } from 'react';

interface AIBriefCardProps {
  label?: ReactNode;
  title?: ReactNode;
  body?: ReactNode;
  primaryAction?: ReactNode;
  secondaryAction?: ReactNode;
  onPrimaryAction?: () => void;
  onSecondaryAction?: () => void;
}

export function AIBriefCard({
  label = 'AI BRIEF',
  title = '项目补人 / 竞品风险',
  body = '项目 · KOL · 品牌信号',
  primaryAction = '证据链',
  secondaryAction = '生成任务',
  onPrimaryAction,
  onSecondaryAction,
}: AIBriefCardProps) {
  return (
    <div className="brief"><div className="label">{label}</div><h2>{title}</h2><p>{body}</p><div className="brief-actions"><button className="dark-btn white" type="button" onClick={onPrimaryAction}>{primaryAction}</button><button className="dark-btn" type="button" onClick={onSecondaryAction}>{secondaryAction}</button></div></div>
  );
}
