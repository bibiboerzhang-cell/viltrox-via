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
  title = '今天优先处理 35mm LAB 项目补人和 Sigma 风险。',
  body = '系统判断当前不是数据缺失，而是项目转化瓶颈：3→4 阶段偏低，同时德国 / 北美中腰部 KOL 有补人机会。',
  primaryAction = '查看证据链',
  secondaryAction = '生成任务',
  onPrimaryAction,
  onSecondaryAction,
}: AIBriefCardProps) {
  return (
    <div className="brief"><div className="label">{label}</div><h2>{title}</h2><p>{body}</p><div className="brief-actions"><button className="dark-btn white" type="button" onClick={onPrimaryAction}>{primaryAction}</button><button className="dark-btn" type="button" onClick={onSecondaryAction}>{secondaryAction}</button></div></div>
  );
}
