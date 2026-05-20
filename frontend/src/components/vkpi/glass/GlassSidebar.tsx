import type { ReactNode } from 'react';
import { GlassNav, GLASS_DEFAULT_NAV_ITEMS } from './GlassNav';
import { GlassProfile } from './GlassProfile';
import type { GlassNavItem } from './tokens';

interface GlassSidebarProps {
  activeKey?: string;
  navItems?: GlassNavItem[];
  onSelectNav?: (key: string) => void;
  profileInitial?: string;
  profileName?: string;
  profileRole?: string;
  mark?: ReactNode;
  subtitle?: ReactNode;
}

export function GlassSidebar({
  activeKey = 'Dashboard',
  navItems = GLASS_DEFAULT_NAV_ITEMS,
  onSelectNav,
  profileInitial,
  profileName,
  profileRole,
  mark = '5 Brains Online',
  subtitle = 'Global AI Operating System',
}: GlassSidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand"><div className="v">VILTROX</div><div className="m">Marketing<br />Intelligence</div><div className="sub">{subtitle}</div><div className="mark">{mark}</div></div>
      <GlassNav activeKey={activeKey} items={navItems} onSelect={onSelectNav} />
      <GlassProfile initial={profileInitial} name={profileName} role={profileRole} />
    </aside>
  );
}
