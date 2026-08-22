import type { PropsWithChildren } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nContext } from '../cockpit/lib/i18n';
import { ThemeProvider } from '../../../app/providers/ThemeProvider';
import { MANAGER_NAV_ITEMS } from './vkpiLayoutConstants';
import { VkpiSidebar } from './VkpiSidebar';
import { VkpiTopbar } from './VkpiTopbar';

function EnglishShell({ children }: PropsWithChildren) {
  return (
    <ThemeProvider>
      <I18nContext.Provider
        value={{
          lang: 'en',
          setLang: vi.fn(),
          t: (source) => `EN:${source}`,
        }}
      >
        {children}
      </I18nContext.Provider>
    </ThemeProvider>
  );
}

describe('V-KPI main shell i18n', () => {
  afterEach(() => cleanup());

  it('translates sidebar labels when consuming static navigation constants', () => {
    render(
      <EnglishShell>
        <VkpiSidebar
          navItems={MANAGER_NAV_ITEMS.slice(0, 1)}
          activePage="cockpit"
          userName="Ada"
          userRole="Manager"
          avatarRequired
          onSelectPage={vi.fn()}
          onUploadAvatar={vi.fn()}
          onSignOut={vi.fn()}
        />
      </EnglishShell>,
    );

    expect(screen.getByRole('navigation', { name: 'EN:Viltrox Marketing 导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'EN:管理主控' })).toBeInTheDocument();
    expect(screen.getByText('EN:请上传真人头像')).toBeInTheDocument();
    expect(screen.getByText('EN:上传')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'EN:退出' })).toBeInTheDocument();
  });

  it('translates topbar controls, range constants, and report status', () => {
    render(
      <EnglishShell>
        <VkpiTopbar
          query=""
          range="7d"
          dataStatus="live"
          dataNotice=""
          viewMode="manager"
          canSwitchView
          onQueryChange={vi.fn()}
          onRangeChange={vi.fn()}
          onToggleView={vi.fn()}
          onExportPDF={vi.fn()}
          onExportCSV={vi.fn()}
          onGenerateWeeklyReport={vi.fn()}
          weeklyReportStatus={{ state: 'success', message: '周报已生成并下载。', href: '/report.pdf' }}
        />
      </EnglishShell>,
    );

    expect(screen.getByPlaceholderText('EN:搜索红人 / 项目 / 短链 / 消息')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'EN:选择数据范围' })).toHaveDisplayValue('EN:近 7 天');
    expect(screen.getByRole('button', { name: 'EN:切换我的视角' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'EN:导出 PDF' })).toBeInTheDocument();
    expect(screen.getByText('EN:周报已生成并下载。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'EN:打开' })).toBeInTheDocument();
  });
});
