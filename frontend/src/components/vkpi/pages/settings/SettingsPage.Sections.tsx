export function SettingsZoneHeader({ zone, title, hint }: { zone: string; title: string; hint: string }) {
  return (
    <div className="vkpi-settings-zone" data-zone={zone}>
      <header className="vkpi-settings-zone__head">
        <strong>{title}</strong>
        <em>{hint}</em>
      </header>
    </div>
  );
}

type ApiKeyDraft = {
  account_name: string;
  provider: string;
  key: string;
  daily_quota: string;
  enabled: boolean;
};

interface SettingsApiKeyPoolPanelProps {
  apiKeyPool: Array<Record<string, unknown>>;
  keyDraft: ApiKeyDraft;
  busy: boolean;
  onKeyDraftChange: (draft: ApiKeyDraft) => void;
  onToggleApiKey: (row: Record<string, unknown>) => void;
  onRemoveApiKey: (id: number) => void;
  onSaveApiKey: () => void;
}

export function SettingsApiKeyPoolPanel({
  apiKeyPool,
  keyDraft,
  busy,
  onKeyDraftChange,
  onToggleApiKey,
  onRemoveApiKey,
  onSaveApiKey,
}: SettingsApiKeyPoolPanelProps) {
  return (
    <div className="vkpi-settings-keypool">
      <p className="vkpi-settings-hint">
        本轮只预留输入位:7月由用户手动填入,系统不自动轮转(轮转 worker 未接线)。key 仅以密文入库,前端永不回显;留空表示保留已存密文不变。
      </p>
      <table className="vkpi-table vkpi-settings-keypool__table">
        <thead>
          <tr>
            <th>账号名</th>
            <th>Provider</th>
            <th>Key</th>
            <th>日额度</th>
            <th>启用</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {apiKeyPool.map((row) => (
            <tr key={String(row.id)}>
              <td>{String(row.account_name ?? '')}</td>
              <td>{String(row.provider ?? '')}</td>
              <td>{row.key_prefix ? `${String(row.key_prefix)}(已存,留空不改)` : '(未填)'}</td>
              <td>{Number(row.daily_quota ?? 0)}</td>
              <td>
                <input
                  type="checkbox"
                  checked={Boolean(row.enabled)}
                  disabled={busy}
                  onChange={() => void onToggleApiKey(row)}
                  aria-label="启用"
                />
              </td>
              <td>
                <button type="button" className="vkpi-btn vkpi-btn--danger" disabled={busy} onClick={() => void onRemoveApiKey(Number(row.id))}>
                  删除
                </button>
              </td>
            </tr>
          ))}
          <tr className="vkpi-settings-keypool__draft">
            <td>
              <input
                type="text"
                value={keyDraft.account_name}
                placeholder="账号名"
                disabled={busy}
                onChange={(event) => onKeyDraftChange({ ...keyDraft, account_name: event.target.value })}
              />
            </td>
            <td>
              <select
                value={keyDraft.provider}
                disabled={busy}
                onChange={(event) => onKeyDraftChange({ ...keyDraft, provider: event.target.value })}
              >
                {['gemini', 'openai', 'anthropic', 'apify', 'youtube', 'resend'].map((provider) => (
                  <option key={provider} value={provider}>{provider}</option>
                ))}
              </select>
            </td>
            <td>
              <input
                type="password"
                value={keyDraft.key}
                placeholder="7月填,留空=不改"
                autoComplete="new-password"
                disabled={busy}
                onChange={(event) => onKeyDraftChange({ ...keyDraft, key: event.target.value })}
              />
            </td>
            <td>
              <input
                type="number"
                min={0}
                value={keyDraft.daily_quota}
                placeholder="0"
                disabled={busy}
                onChange={(event) => onKeyDraftChange({ ...keyDraft, daily_quota: event.target.value })}
              />
            </td>
            <td>
              <input
                type="checkbox"
                checked={keyDraft.enabled}
                disabled={busy}
                onChange={(event) => onKeyDraftChange({ ...keyDraft, enabled: event.target.checked })}
                aria-label="启用"
              />
            </td>
            <td>
              <button type="button" className="vkpi-btn" disabled={busy || !keyDraft.account_name.trim()} onClick={() => void onSaveApiKey()}>
                新增 / 保存
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
