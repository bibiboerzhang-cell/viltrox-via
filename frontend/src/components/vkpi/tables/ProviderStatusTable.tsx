export function ProviderStatusTable({ rows, busyProvider, onProbe }: { rows: Array<Record<string, unknown>>; busyProvider: string; onProbe: (provider: string) => void }) {
  return (
    <div className="vkpi-table-wrap">
      <table className="vkpi-table">
        <thead><tr><th>API</th><th>配置</th><th>工作状态</th><th>最近检测</th><th>错误</th><th>操作</th></tr></thead>
        <tbody>
          {rows.length ? rows.map((row) => {
            const provider = String(row.provider || '');
            const status = String(row.latest_status || row.status || 'unknown');
            const configured = Boolean(row.configured);
            const displayStatus = configured ? status : 'not_configured';
            return (
              <tr key={provider || String(row.id)}>
                <td><strong>{providerLabel(provider)}</strong></td>
                <td>{configured ? '已配置' : '未配置'}</td>
                <td><span className={`vkpi-status-pill ${providerTone(displayStatus)}`}>{providerStatusLabel(displayStatus)}</span></td>
                <td>{String(row.last_ok_at || row.updated_at || '-')}</td>
                <td>{String(row.last_error || '-')}</td>
                <td><button className="vkpi-link-button" type="button" disabled={busyProvider === provider} onClick={() => onProbe(provider)}>{busyProvider === provider ? '检测中' : '检测'}</button></td>
              </tr>
            );
          }) : <tr><td className="vkpi-table-empty" colSpan={6}>未读取到 API 状态。</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function providerLabel(provider: string): string {
  const key = provider.toLowerCase();
  if (key === 'anthropic') return '对话引擎';
  if (key === 'google') return '多模态引擎';
  if (key === 'openai') return '通用引擎';
  if (key === 'apify') return 'Apify';
  if (key === 'resend') return 'Resend';
  return provider || '-';
}

function providerStatusLabel(status: string): string {
  const key = status.toLowerCase();
  if (key === 'not_configured') return '未配置';
  if (key === 'healthy') return '正常';
  if (key === 'degraded') return '异常';
  if (key === 'down') return '不可用';
  return '未检测';
}

function providerTone(status: string): string {
  const key = status.toLowerCase();
  if (key === 'not_configured') return 'is-muted';
  if (key === 'healthy') return 'is-good';
  if (key === 'degraded') return 'is-warn';
  if (key === 'down') return 'is-danger';
  return 'is-muted';
}
