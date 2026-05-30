import React from 'react';

export function Icon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    grid: (<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>),
    discover: (<><circle cx="11" cy="11" r="6" /><path d="M16 16l5 5" /><path d="M11 8v6M8 11h6" /></>),
    folder: (<><path d="M3 7.5h7l2 2H21v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.5Z" /><path d="M3 7.5V6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1.5" /></>),
    link: (<><path d="M9.5 14.5l5-5" /><path d="M10.5 6.5l1.2-1.2a4 4 0 0 1 5.7 5.7l-1.9 1.9a4 4 0 0 1-5.2.4" /><path d="M13.5 17.5l-1.2 1.2a4 4 0 0 1-5.7-5.7l1.9-1.9a4 4 0 0 1 5.2-.4" /></>),
    nodes: (<><circle cx="6" cy="7" r="3" /><circle cx="18" cy="7" r="3" /><circle cx="12" cy="18" r="3" /><path d="M8.5 9.5l2 5M15.5 9.5l-2 5M9 7h6" /></>),
    report: (<><path d="M6 3.5h9l3 3V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 20V5a1.5 1.5 0 0 1 2-1.5Z" /><path d="M15 3.5V7h3" /><path d="M8 12h8M8 16h6M8 8h3" /></>),
    settings: (<><circle cx="12" cy="12" r="3" /><path d="M19 12a7.5 7.5 0 0 0-.1-1.1l2-1.5-2-3.4-2.4 1a8 8 0 0 0-1.9-1.1L14.3 3h-4.6l-.4 2.9A8 8 0 0 0 7.5 7l-2.4-1-2 3.4 2 1.5A7.5 7.5 0 0 0 5 12c0 .4 0 .8.1 1.1l-2 1.5 2 3.4 2.4-1c.6.5 1.2.8 1.9 1.1l.4 2.9h4.6l.4-2.9c.7-.3 1.3-.7 1.9-1.1l2.4 1 2-3.4-2-1.5c0-.3.1-.7.1-1.1Z" /></>),
    search: (<><circle cx="11" cy="11" r="6" /><path d="M16 16l5 5" /></>),
    calendar: (<><rect x="4" y="5" width="16" height="16" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></>),
    file: (<><path d="M6 3.5h8l4 4V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 20V5a1.5 1.5 0 0 1 2-1.5Z" /><path d="M14 3.5V8h4" /></>),
    table: (<><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 10h16M9 5v14M15 5v14" /></>),
    spark: (<><path d="M12 3l1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3Z" /><path d="M19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z" /></>),
    info: (<><circle cx="12" cy="12" r="8" /><path d="M12 11v5M12 8h.01" /></>),
    help: (<><circle cx="12" cy="12" r="9" /><path d="M9.8 9.2a2.4 2.4 0 0 1 4.5 1.2c0 1.6-1.4 2.1-2.1 2.9-.3.3-.4.7-.4 1.2" /><path d="M12 17.3h.01" /></>),
    heart: (<><path d="M20.8 4.6a5.4 5.4 0 0 0-7.7 0L12 5.8l-1.1-1.2a5.4 5.4 0 0 0-7.7 7.7L12 21l8.8-8.7a5.4 5.4 0 0 0 0-7.7Z" /></>),
    message: (<><path d="M5 5.5h14a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2H9l-5 3v-12.5a2 2 0 0 1 2-2Z" /><path d="M8 10h8M8 13h5" /></>),
    bell: (<><path d="M18 10a6 6 0 1 0-12 0c0 7-3 7-3 7h18s-3 0-3-7" /><path d="M10 20a2 2 0 0 0 4 0" /></>),
    user: (<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>),
    language: (<><path d="M4 5h9M8.5 3v2M10.5 5c-.7 4.2-2.7 7-6.2 9" /><path d="M5.5 8.5c1.1 2.1 2.8 3.8 5.2 5" /><path d="M14 20l4-9 4 9M15.3 17h5.4" /></>),
    logout: (<><path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4" /><path d="M15 7l5 5-5 5" /><path d="M20 12H9" /></>),
    chevronDown: (<path d="m7 10 5 5 5-5" />),
    columns: (<><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M10 5v14M15 5v14" /></>),
    filter: (<><path d="M4 6h16M7 12h10M10 18h4" /></>),
    download: (<><path d="M12 4v11" /><path d="M8 11l4 4 4-4" /><path d="M5 20h14" /></>),
    analytics: (<><path d="M3 3v18h18" /><path d="M7 14l3-3 4 4 5-6" /><circle cx="7" cy="14" r="1.2" fill="currentColor" /><circle cx="10" cy="11" r="1.2" fill="currentColor" /><circle cx="14" cy="15" r="1.2" fill="currentColor" /><circle cx="19" cy="9" r="1.2" fill="currentColor" /></>),
    moon: (<><path d="M21 14.8A8.5 8.5 0 0 1 9.2 3 7 7 0 1 0 21 14.8Z" /></>),
    package: (<><path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5v-7Z" /><path d="M3.4 8.8 12 14l8.6-5.2M12 21v-7" /></>),
    refresh: (<><path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M18 11a6.5 6.5 0 0 0-11.2-3.7L4 11M20 13l-2.8 3.7A6.5 6.5 0 0 1 6 13" /></>),
  };

  return (
    <svg className="vkpi-icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name] || paths.info}
    </svg>
  );
}
