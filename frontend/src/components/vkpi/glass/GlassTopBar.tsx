import { GlassButton } from './GlassButton';
import type { GlassTopAction } from './tokens';

const defaultActions: GlassTopAction[] = [
  { label: new Date().toISOString().slice(0, 10) },
  { label: '示例 · 待接入真实状态', variant: 'sync' },
  { label: '导出' },
  { label: '生成周报', variant: 'primary' },
];

interface GlassTopBarProps {
  placeholder?: string;
  actions?: GlassTopAction[];
}

export function GlassTopBar({
  placeholder = '问 V-KPI：找德国适合 35mm LAB 的红人 / 今天哪些项目有风险…',
  actions = defaultActions,
}: GlassTopBarProps) {
  return (
    <div className="top"><div className="command"><span>⌕</span><input placeholder={placeholder} readOnly /><span className="kbd">⌘ K</span></div><div className="actions">{actions.map((action, index) => <GlassButton key={index} variant={action.variant} onClick={action.onClick}>{action.label}</GlassButton>)}</div></div>
  );
}
