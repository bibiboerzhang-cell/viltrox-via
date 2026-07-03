import { useEffect, useMemo, useState } from 'react';
import { Bell, CheckCircle2, Heart } from 'lucide-react';
import type { OfficialChannelAccount } from '../channels/channelTypes';
import { useOfficialChannelMatrix } from '../channels/useOfficialChannelMatrix';
import type { VkpiDashboardData, VkpiPageKey, VkpiProjectRow } from '../../vkpiTypes';
import { ContributionRollupPanel } from './ContributionRollupPanel';
import { RiskIndexPanel } from './RiskIndexPanel';
import type { StaffCard } from './MyKolPage.helpers';
import {
  isGenericStaffShell,
  knownStaffDisplay,
  matchesKnownStaff,
  staffDisplayRole,
} from './MyKolPage.helpers';
import { EmployeeKolLibrary, OfficialMatrix, TeamMatrix } from './MyKolPage.Sections';
import './myKolPage.css';
import './myKolTeamMatrix.css';

interface MyKolPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
  data: VkpiDashboardData;
  userName?: string;
  userRole?: string;
  onRefreshData?: () => void;
  onSelectPage?: (page: VkpiPageKey) => void;
}

export function MyKolPage({ apiToken, viewMode, data, userName, onRefreshData }: MyKolPageProps) {
  const matrix = useOfficialChannelMatrix(apiToken);
  const [selectedPlatformKey, setSelectedPlatformKey] = useState('');
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  // D:团队卡选中态——点卡过滤下方 KOL 库,再点同卡取消。
  const [selectedStaff, setSelectedStaff] = useState<{ id: string; name: string } | null>(null);
  const selectedPlatform = matrix.platforms.find((platform) => platform.platform === selectedPlatformKey) || matrix.platforms[0];

  useEffect(() => {
    if (!selectedPlatformKey && matrix.platforms[0]) {
      setSelectedPlatformKey(matrix.platforms[0].platform);
    }
  }, [matrix.platforms, selectedPlatformKey]);

  useEffect(() => {
    if (!selectedPlatform) {
      setSelectedAccountId(null);
      return;
    }
    if (!selectedPlatform.accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(selectedPlatform.accounts[0]?.id ?? null);
    }
  }, [selectedAccountId, selectedPlatform]);

  const staffCards = useMemo<StaffCard[]>(() => {
    const accounts = matrix.platforms.flatMap((platform) => platform.accounts);
    const projectsByOwner = new Map<string, VkpiProjectRow[]>();
    data.projects.forEach((project) => {
      if (!project.ownerId) return;
      projectsByOwner.set(project.ownerId, [...(projectsByOwner.get(project.ownerId) || []), project]);
    });
    // A1:staff_managed 按 staff.id 桥接(后端 int → 前端 string,全仓惯例)
    const managedByStaff = new Map(matrix.staffManaged.map((entry) => [String(entry.staffId), entry]));
    const staff = data.staffMembers.length ? data.staffMembers : accounts.map((account) => ({
      id: String(account.staffId || account.staffEmail || account.staffName),
      name: account.staffName || '未分配',
      email: account.staffEmail || '',
      role: account.staffRole || '',
      active: account.staffActive,
      avatarUrl: account.staffAvatarUrl,
      vkpiPermission: 'read' as const,
    }));
    const seen = new Set<string>();
    const baseCards = staff.filter((member) => {
      if (seen.has(member.id)) return false;
      seen.add(member.id);
      return true;
    }).map((member) => ({
      id: member.id,
      name: member.name,
      role: member.role || 'KOL Manager',
      avatar: member.avatarUrl,
      accounts: accounts.filter((account) => String(account.staffId) === member.id || account.staffEmail === member.email),
      projects: projectsByOwner.get(member.id) || [],
      managed: managedByStaff.get(member.id),
    }));
    const consumedBaseIds = new Set<string>();
    const orderedKnownCards = knownStaffDisplay.map((known) => {
      const matched = baseCards.find((card) => !consumedBaseIds.has(card.id) && matchesKnownStaff(card, known));
      if (matched) {
        consumedBaseIds.add(matched.id);
        return {
          ...matched,
          name: matched.name === 'Jianbo' ? 'Jianbo Z' : matched.name,
          role: staffDisplayRole(matched.role, known.role),
          focus: known.focus,
          accent: known.accent,
        };
      }
      return {
        ...known,
        accounts: [] as OfficialChannelAccount[],
        projects: [] as VkpiProjectRow[],
      };
    });
    const remainingRealCards = baseCards.filter((card) => !consumedBaseIds.has(card.id) && !isGenericStaffShell(card));
    return [...orderedKnownCards, ...remainingRealCards];
  }, [data.projects, data.staffMembers, matrix.platforms, matrix.staffManaged]);

  const pendingCount = matrix.platforms.flatMap((platform) => platform.accounts).filter((account) => (
    account.syncStatus !== 'synced' && account.syncStatus !== 'official_readonly'
  )).length;

  return (
    <main className="mykol-page">
      <header className="mykol-hero">
        <div>
          <h1><Heart size={18} fill="currentColor" /> MY KOL <span>/ {viewMode === 'manager' ? '团队矩阵 / 账号管理' : '我的 KOL'}</span></h1>
          <p>{viewMode === 'manager' ? `管理层视角 · ${staffCards.length || '暂无'} 名负责人 · ${data.kolOptions.length || '暂无'} 个 KOL` : `${userName || '成员'} · 只看自己负责的数据`}</p>
        </div>
        <div className="mykol-hero-actions">
          <span className={pendingCount ? 'is-pending' : ''}><Bell size={14} /> {pendingCount ? `${pendingCount} 个待定` : '无待定'}</span>
          <button type="button" onClick={onRefreshData}><CheckCircle2 size={14} /> 刷新数据</button>
        </div>
      </header>
      {viewMode === 'manager' ? (
        <TeamMatrix
          cards={staffCards}
          pendingCount={pendingCount}
          selectedStaffId={selectedStaff?.id ?? null}
          onSelectStaff={(card) => setSelectedStaff((current) => (
            current?.id === card.id ? null : { id: card.id, name: card.name }
          ))}
        />
      ) : null}
      {viewMode === 'employee' ? <EmployeeKolLibrary apiToken={apiToken} data={data} viewMode={viewMode} onRefreshData={onRefreshData} /> : null}
      <OfficialMatrix
        apiToken={apiToken}
        matrix={matrix}
        selectedPlatform={selectedPlatform}
        selectedAccountId={selectedAccountId}
        onSelectPlatform={setSelectedPlatformKey}
        onSelectAccount={(account) => setSelectedAccountId(account.id)}
      />
      {viewMode === 'manager' ? (
        <EmployeeKolLibrary
          apiToken={apiToken}
          data={data}
          viewMode={viewMode}
          staffFilter={selectedStaff}
          onClearStaffFilter={() => setSelectedStaff(null)}
          onRefreshData={onRefreshData}
        />
      ) : null}
      {viewMode === 'manager' ? <ContributionRollupPanel apiToken={apiToken || ''} viewMode={viewMode} /> : null}
      {viewMode === 'manager' ? <RiskIndexPanel apiToken={apiToken || ''} /> : null}
    </main>
  );
}
