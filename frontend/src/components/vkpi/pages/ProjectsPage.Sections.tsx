import React, { useMemo, useState } from 'react';
import type { VkpiProjectRow } from '../vkpiTypes';
import type { IntelligenceProjectFocusPayload } from '../intelligence/intelligenceProjectFocus';
import { type ImportKolRow, parseKolImportRows } from './ProjectsPage.helpers';

export function ProjectFocusBanner({
  focus,
  matched,
  onDismiss,
}: {
  focus: IntelligenceProjectFocusPayload;
  matched: boolean;
  onDismiss: () => void;
}) {
  return (
    <section className="vkpi-task-focus-banner is-medium" aria-live="polite">
      <div>
        <span>来自智能中心 / 红人搜索</span>
        <h2>{focus.projectName}</h2>
        <p>{focus.summary}</p>
      </div>
      <div className="vkpi-task-focus-banner__meta">
        <span><b>KOL</b>{focus.kolHandle || focus.kolId || '-'}</span>
        <span><b>产品</b>{focus.productSku || focus.productName || '-'}</span>
        <span><b>状态</b>{matched ? '已定位项目详情' : '等待数据刷新后定位'}</span>
      </div>
      <div className="vkpi-task-focus-banner__actions">
        <button className="vkpi-button vkpi-button--ghost" type="button" onClick={onDismiss}>关闭</button>
      </div>
    </section>
  );
}

export function ImportKolListModal({
  projects,
  selectedProject,
  busy,
  onClose,
  onSubmit,
}: {
  projects: VkpiProjectRow[];
  selectedProject?: VkpiProjectRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (project: VkpiProjectRow, rows: ImportKolRow[]) => Promise<void>;
}) {
  const [projectId, setProjectId] = useState(selectedProject?.id || projects[0]?.id || '');
  const [fallbackPlatform, setFallbackPlatform] = useState<string>(selectedProject?.platform || projects[0]?.platform || 'Instagram');
  const [rawText, setRawText] = useState('Instagram, @creator.handle, Creator Name, creator@email.com');
  const [error, setError] = useState('');
  const targetProject = projects.find((project) => project.id === projectId) || projects[0];
  const parsedRows = useMemo(() => parseKolImportRows(rawText, fallbackPlatform), [fallbackPlatform, rawText]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!targetProject) {
      setError('请先选择目标推广。');
      return;
    }
    if (!parsedRows.length) {
      setError('没有解析到可导入的 KOL 行。');
      return;
    }
    setError('');
    await onSubmit(targetProject, parsedRows);
  };

  return (
    <div className="vkpi-project-modal-backdrop" role="presentation">
      <form className="vkpi-project-import-modal" onSubmit={submit} role="dialog" aria-label="导入 KOL 名单">
        <header>
          <div>
            <h2>导入 KOL 名单</h2>
            <p>从 KOL Pool 匹配账号，批量追加到目标推广；最多 50 行，不再创建孤立项目。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-project-import-grid">
          <label>目标推广
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.campaign} · {project.kolHandle || project.kolName}</option>)}
            </select>
          </label>
          <label>默认平台
            <select value={fallbackPlatform} onChange={(event) => setFallbackPlatform(event.target.value)}>
              {['Instagram', 'YouTube', 'TikTok', 'Facebook', 'Reddit', 'X', 'Other'].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="is-full">名单内容
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder={`支持格式：\nInstagram, @handle, Name, email@example.com\n@handle, Name, email@example.com`}
            />
          </label>
        </div>
        <div className="vkpi-project-import-preview">
          <strong>预览 {parsedRows.length} 行</strong>
          <div>
            {parsedRows.slice(0, 6).map((row) => <span key={`${row.platform}-${row.handle}`}>{row.platform} · {row.handle}</span>)}
            {parsedRows.length > 6 ? <span>还有 {parsedRows.length - 6} 行</span> : null}
          </div>
        </div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button className="vkpi-project-modal-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="vkpi-project-modal-button is-primary" type="submit" disabled={busy || !parsedRows.length}>
            {busy ? '导入中' : `导入 ${parsedRows.length} 个 KOL`}
          </button>
        </footer>
      </form>
    </div>
  );
}

export function ProjectDetailSkeleton({ onBack }: { onBack: () => void }) {
  return (
    <section className="vkpi-campaign-detail" aria-label="项目详情加载中">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>
      <div className="vkpi-campaign-detail-hero vkpi-campaign-skeleton">
        <div>
          <span />
          <strong />
          <p />
          <p />
        </div>
        <div />
      </div>
      <div className="vkpi-campaign-kpis vkpi-campaign-skeleton-kpis">
        {Array.from({ length: 6 }).map((_, index) => <div key={index} />)}
      </div>
      <div className="vkpi-campaign-panel vkpi-campaign-skeleton-panel" />
    </section>
  );
}

export function ProjectDetailError({ message, onBack }: { message: string; onBack: () => void }) {
  return (
    <section className="vkpi-campaign-detail" aria-label="项目详情错误">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>
      <div className="vkpi-campaign-placeholder is-error">
        <h3>{message}</h3>
        <p>请返回项目列表后刷新数据，或确认该项目仍在当前账号权限范围内。</p>
      </div>
    </section>
  );
}
