import { GlassButton } from './GlassButton';
import type { GlassTopAction } from './tokens';

function localDateISO(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

const defaultActions: GlassTopAction[] = [
  { label: localDateISO() },
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
