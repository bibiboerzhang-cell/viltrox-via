// 文件: frontend/src/components/vkpi/pages/DataAnalysisPage.tsx (新建, 30 行)

import { useState } from 'react';
import { PageShell } from './PageShell';
import { CrossPlatformPanel } from './data-analysis/CrossPlatformPanel';
import './data-analysis/styles/data-analysis.css';
import type { VkpiDashboardProps } from '../VkpiDashboard';

interface DataAnalysisPageProps {
  apiToken?: string;
  viewMode?: VkpiDashboardProps['viewMode'];
}

export function DataAnalysisPage({ apiToken, viewMode = 'manager' }: DataAnalysisPageProps) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  return (
    <PageShell
      title="数据分析"
      description="Viltrox Marketing 数据分析: 行业 / 竞品 / 自有账号矩阵, 跨平台 KPI, 帖子库, 账号详情。抓取默认关闭, 未配置时显示真实空态。"
    >
      <CrossPlatformPanel
        apiToken={apiToken}
        busy={busy}
        onBusyChange={setBusy}
        onMessage={setMessage}
        viewMode={viewMode}
      />
      {message ? (
        <div className="da-toast" role="status">
          <span>Viltrox</span>
          <p>{message}</p>
          <button type="button" onClick={() => setMessage('')} aria-label="关闭消息">×</button>
        </div>
      ) : null}
    </PageShell>
  );
}
