import type { GlassNavItem } from './tokens';

const homeIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 10.5 12 3l9 7.5v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /></svg>
);

const missionIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3v18M3 12h18" /><circle cx="12" cy="12" r="8" /></svg>
);

const kolIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" /><circle cx="10" cy="7" r="4" /><path d="M21 21v-2a4 4 0 0 0-3-3.87" /></svg>
);

const campaignIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h16v13H4z" /><path d="M8 7V4h8v3" /></svg>
);

const productIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2 3 7l9 5 9-5-9-5Z" /><path d="m3 7 9 5v10l-9-5V7Z" /><path d="m21 7-9 5v10l9-5V7Z" /></svg>
);

const marketIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20" /></svg>
);

const dataIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 3v18h18" /><path d="m7 14 3-3 3 2 5-7" /></svg>
);

const settingsIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 19.4 9c.1.4.5.8 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></svg>
);

export const GLASS_DEFAULT_NAV_ITEMS: GlassNavItem[] = [
  { key: 'Dashboard', label: 'Dashboard', icon: homeIcon },
  { key: 'Mission', label: '今日作战', badge: '7', icon: missionIcon },
  { key: 'KOL', label: 'KOL Intelligence', icon: kolIcon },
  { key: 'Campaign', label: '项目 Autopilot', icon: campaignIcon },
  { key: 'Product', label: '产品作战', icon: productIcon },
  { key: 'Market', label: '市场地图', icon: marketIcon },
  { key: 'Data', label: '数据分析', icon: dataIcon },
  { key: 'Settings', label: '设置中心', icon: settingsIcon },
];

interface GlassNavProps {
  activeKey?: string;
  items?: GlassNavItem[];
  onSelect?: (key: string) => void;
}

export function GlassNav({ activeKey = 'Dashboard', items = GLASS_DEFAULT_NAV_ITEMS, onSelect }: GlassNavProps) {
  return (
    <nav className="nav">
      {items.map((item) => (
        <div
          key={item.key}
          className={`nav-item${activeKey === item.key ? ' active' : ''}`}
          data-nav={item.key}
          onClick={() => onSelect?.(item.key)}
        >
          {item.icon}
          {item.label}
          {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
        </div>
      ))}
    </nav>
  );
}
