import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../../services/http';
import { rows, type Row } from '../../../domains/dashboard';
import { Avatar } from '../shared/Avatar';
import { Icon } from '../shared/Icon';
import type { VkpiPageKey } from '../vkpiTypes';
import './v2-shell.css';

interface V2ShellTopbarProps {
  apiToken?: string;
  pageTitle: string;
  subtitle?: string;
  reportLabel?: string;
  userName: string;
  userRole: string;
  userAvatar?: string;
  tasks?: Row[];
  alerts?: Row[];
  onReportClick?: () => void;
  onSelectPage?: (page: VkpiPageKey) => void;
  onSignOut?: () => Promise<void> | void;
}

function text(value: unknown, fallback = '无信号'): string {
  const clean = String(value ?? '').replace(/\s+/g, ' ').trim();
  return clean || fallback;
}

function rowTitle(row: Row): string {
  return text(row.title || row.label || row.task_title || row.name || row.alert_key, '未命名信号');
}

export function V2ShellTopbar({
  apiToken,
  pageTitle,
  subtitle,
  reportLabel = 'Report',
  userName,
  userRole,
  userAvatar,
  tasks,
  alerts,
  onReportClick,
  onSelectPage,
  onSignOut,
}: V2ShellTopbarProps) {
  const shellRef = useRef<HTMLElement | null>(null);
  const [openMenu, setOpenMenu] = useState<'help' | 'messages' | 'alerts' | 'user' | null>(null);
  const [taskRows, setTaskRows] = useState<Row[]>(tasks || []);
  const [alertRows, setAlertRows] = useState<Row[]>(alerts || []);

  useEffect(() => {
    if (tasks) setTaskRows(tasks);
  }, [tasks]);

  useEffect(() => {
    if (alerts) setAlertRows(alerts);
  }, [alerts]);

  useEffect(() => {
    if (!apiToken || (tasks && alerts)) return undefined;
    let cancelled = false;
    Promise.allSettled([
      tasks ? Promise.resolve(null) : apiFetch<Row>('/api/admin/vkpi/dashboard/tasks?limit=6', {}, apiToken),
      alerts ? Promise.resolve(null) : apiFetch<Row>('/api/admin/vkpi/alerts?status=open&limit=8', {}, apiToken),
    ]).then(([taskResult, alertResult]) => {
      if (cancelled) return;
      if (!tasks && taskResult.status === 'fulfilled' && taskResult.value) {
        setTaskRows(rows(taskResult.value.tasks).length ? rows(taskResult.value.tasks) : rows(taskResult.value.items));
      }
      if (!alerts && alertResult.status === 'fulfilled' && alertResult.value) {
        setAlertRows(rows(alertResult.value.alerts).length ? rows(alertResult.value.alerts) : rows(alertResult.value.items));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [alerts, apiToken, tasks]);

  useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnPointer = (event: PointerEvent) => {
      if (shellRef.current?.contains(event.target as Node)) return;
      setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenu(null);
    };
    window.addEventListener('pointerdown', closeOnPointer);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('pointerdown', closeOnPointer);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [openMenu]);

  const toggle = (menu: typeof openMenu) => setOpenMenu(openMenu === menu ? null : menu);
  const selectPage = (page: VkpiPageKey) => {
    setOpenMenu(null);
    onSelectPage?.(page);
  };
  const openReport = () => {
    setOpenMenu(null);
    if (onReportClick) onReportClick();
    else onSelectPage?.('reports');
  };

  return (
    <header className="v2-shell-topbar" ref={shellRef}>
      <div className="v2-shell-topbar__title">
        <strong>{pageTitle}</strong>
        {subtitle ? <span>{subtitle}</span> : null}
      </div>
      <div className="v2-shell-topbar__actions">
        <button className="v2-shell-icon-button" type="button" onClick={() => toggle('help')} aria-label="帮助" aria-expanded={openMenu === 'help'}>
          <Icon name="help" />
        </button>
        <button className="v2-shell-icon-button" type="button" onClick={() => toggle('messages')} aria-label="消息" aria-expanded={openMenu === 'messages'}>
          <Icon name="message" />{taskRows.length ? <i /> : null}
        </button>
        <button className="v2-shell-report-button" type="button" onClick={openReport}>
          <Icon name="report" />{reportLabel}
        </button>
        <button className="v2-shell-icon-button" type="button" onClick={() => toggle('alerts')} aria-label="通知" aria-expanded={openMenu === 'alerts'}>
          <Icon name="bell" />{alertRows.length ? <i /> : null}
        </button>
        <button className="v2-shell-user-button" type="button" onClick={() => toggle('user')} aria-expanded={openMenu === 'user'}>
          <Avatar name={userName} src={userAvatar} size="sm" />
          <span><strong>{userName}</strong><em>{userRole}</em></span>
          <Icon name="chevronDown" />
        </button>
      </div>

      <div className={`v2-shell-popover v2-shell-popover--help${openMenu === 'help' ? ' is-open' : ''}`}>
        <strong>诊断入口</strong>
        <button type="button" onClick={() => selectPage('dataQuality')}><Icon name="analytics" />数据质量</button>
        <button type="button" onClick={() => selectPage('audit')}><Icon name="file" />审计</button>
        <button type="button" onClick={() => selectPage('settings')}><Icon name="settings" />系统设置</button>
      </div>
      <div className={`v2-shell-popover v2-shell-popover--messages${openMenu === 'messages' ? ' is-open' : ''}`}>
        <strong>消息</strong>
        {taskRows.length ? taskRows.slice(0, 5).map((row, index) => <p key={`${rowTitle(row)}-${index}`}>{rowTitle(row)}</p>) : <p>暂无真实工作提醒</p>}
      </div>
      <div className={`v2-shell-popover v2-shell-popover--alerts${openMenu === 'alerts' ? ' is-open' : ''}`}>
        <strong>通知</strong>
        {alertRows.length ? alertRows.slice(0, 6).map((row, index) => <p key={`${rowTitle(row)}-${index}`}>{rowTitle(row)}</p>) : <p>无信号</p>}
      </div>
      <div className={`v2-shell-popover v2-shell-popover--user${openMenu === 'user' ? ' is-open' : ''}`}>
        <div className="v2-shell-popover__profile">
          <Avatar name={userName} src={userAvatar} size="md" />
          <span><strong>{userName}</strong><em>{userRole}</em></span>
        </div>
        <button type="button" onClick={() => selectPage('settings')}><Icon name="user" />个人资料</button>
        <button type="button" onClick={() => selectPage('settings')}><Icon name="settings" />系统设置</button>
        <div className="v2-shell-popover__row"><span><Icon name="language" />语言</span><b>中文</b></div>
        {onSignOut ? <button className="is-danger" type="button" onClick={() => void onSignOut()}><Icon name="logout" />退出登录</button> : null}
      </div>
    </header>
  );
}
