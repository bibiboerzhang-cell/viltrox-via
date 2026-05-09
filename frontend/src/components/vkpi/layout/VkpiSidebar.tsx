import type { ChangeEvent } from 'react';
import { Avatar } from '../shared/Avatar';
import { Icon } from '../shared/Icon';
import type { VkpiPageKey } from '../vkpiTypes';
import type { VkpiNavItem } from './vkpiLayoutConstants';

interface VkpiSidebarProps {
  navItems: VkpiNavItem[];
  activePage: VkpiPageKey;
  userName: string;
  userRole: string;
  userAvatar?: string;
  avatarRequired?: boolean;
  onSelectPage: (page: VkpiPageKey) => void;
  onUploadAvatar?: (file: File) => void;
  onSignOut?: () => Promise<void> | void;
}

export function VkpiSidebar({
  navItems,
  activePage,
  userName,
  userRole,
  userAvatar,
  avatarRequired,
  onSelectPage,
  onUploadAvatar,
  onSignOut,
}: VkpiSidebarProps) {
  const handleAvatarChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (file) onUploadAvatar?.(file);
    event.currentTarget.value = '';
  };

  return (
    <aside className="vkpi-sidebar">
      <div className="vkpi-brand">
        <div className="vkpi-brand__wordmark" aria-label="Viltrox">VILTROX</div>
        <div className="vkpi-brand__product">Marketing</div>
      </div>

      <nav className="vkpi-nav" aria-label="Viltrox Marketing 导航">
        {navItems.map((item) => (
          <button
            key={item.key}
            className={`vkpi-nav__item ${activePage === item.key ? 'is-active' : ''}`}
            type="button"
            onClick={() => onSelectPage(item.key)}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="vkpi-user-chip">
        <Avatar name={userName} src={userAvatar} size="sm" />
        <div>
          <strong>{userName}</strong>
          <span>{userRole}</span>
          {avatarRequired ? <em>请上传真人头像</em> : null}
        </div>
        {onUploadAvatar ? (
          <label className="vkpi-avatar-upload">
            上传
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={handleAvatarChange} />
          </label>
        ) : null}
        {onSignOut ? (
          <button
            type="button"
            className="vkpi-user-chip__logout"
            onClick={() => void onSignOut()}
          >
            退出
          </button>
        ) : null}
      </div>
    </aside>
  );
}
