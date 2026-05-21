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
  label = 'AI BRIEF · 示例',
  title = '今日优先：项目补人和竞品风险。',
  body = '基于项目、KOL 和品牌信号生成。',
  primaryAction = '查看证据链',
  secondaryAction = '生成任务',
  onPrimaryAction,
  onSecondaryAction,
}: AIBriefCardProps) {
  return (
    <div className="brief"><div className="label">{label}</div><h2>{title}</h2><p>{body}</p><div className="brief-actions"><button className="dark-btn white" type="button" onClick={onPrimaryAction}>{primaryAction}</button><button className="dark-btn" type="button" onClick={onSecondaryAction}>{secondaryAction}</button></div></div>
  );
}
