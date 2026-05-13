import React, { useMemo, useState } from 'react';
import { submitTeamFeedback } from '../../../services/vkpi.ui-api';

type FeedbackType = 'bug' | 'button_issue' | 'missing_data' | 'suggestion' | 'question';
type Severity = 'low' | 'medium' | 'high' | 'critical';

interface FeedbackWidgetProps {
  apiToken?: string;
  activePage: string;
  userName?: string;
}

const typeOptions: Array<{ value: FeedbackType; label: string }> = [
  { value: 'button_issue', label: '按钮没反应' },
  { value: 'missing_data', label: '数据不对/缺失' },
  { value: 'bug', label: '页面错误' },
  { value: 'suggestion', label: '优化建议' },
  { value: 'question', label: '使用问题' },
];

const severityOptions: Array<{ value: Severity; label: string }> = [
  { value: 'medium', label: '普通' },
  { value: 'high', label: '影响使用' },
  { value: 'critical', label: '阻塞工作' },
  { value: 'low', label: '低优先级' },
];

export function FeedbackWidget({ apiToken, activePage, userName }: FeedbackWidgetProps) {
  const [open, setOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState<FeedbackType>('button_issue');
  const [severity, setSeverity] = useState<Severity>('medium');
  const [title, setTitle] = useState('');
  const [detail, setDetail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const pagePath = useMemo(() => {
    if (typeof window === 'undefined') return activePage;
    return `${window.location.pathname}${window.location.search}${window.location.hash || `#${activePage}`}`;
  }, [activePage]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setMessage('');
    setError('');
    if (!apiToken) {
      setError('当前没有登录 token，不能提交反馈。');
      return;
    }
    const cleanTitle = title.trim();
    if (!cleanTitle) {
      setError('请写一句标题，方便团队定位。');
      return;
    }
    setSubmitting(true);
    try {
      const result = await submitTeamFeedback(apiToken, {
        feedbackType,
        severity,
        pagePath,
        title: cleanTitle,
        detail: detail.trim(),
        metadata: {
          activePage,
          userName,
          userAgent: typeof navigator === 'undefined' ? '' : navigator.userAgent,
          viewport: typeof window === 'undefined' ? '' : `${window.innerWidth}x${window.innerHeight}`,
        },
      });
      const uid = String((result.feedback || {}).uid || '');
      setMessage(uid ? `已提交: ${uid}` : '已提交反馈。');
      setTitle('');
      setDetail('');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '反馈提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={`vkpi-feedback-widget${open ? ' is-open' : ''}`} data-testid="vkpi-feedback-widget">
      <button className="vkpi-feedback-widget__trigger" type="button" onClick={() => setOpen((value) => !value)}>
        {open ? '收起反馈' : '反馈 / 报错'}
      </button>
      {open ? (
        <form className="vkpi-feedback-widget__panel" onSubmit={handleSubmit}>
          <header>
            <strong>内测反馈</strong>
            <span>当前页面: {activePage}</span>
          </header>
          <div className="vkpi-feedback-widget__row">
            <label>
              类型
              <select value={feedbackType} onChange={(event) => setFeedbackType(event.target.value as FeedbackType)}>
                {typeOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label>
              影响
              <select value={severity} onChange={(event) => setSeverity(event.target.value as Severity)}>
                {severityOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            标题
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如: 数据分析页查看全部无反应" />
          </label>
          <label>
            说明
            <textarea value={detail} onChange={(event) => setDetail(event.target.value)} placeholder="写清楚你点了哪里、看到什么、期望是什么。" />
          </label>
          {error ? <p className="vkpi-feedback-widget__error">{error}</p> : null}
          {message ? <p className="vkpi-feedback-widget__success">{message}</p> : null}
          <button className="vkpi-button vkpi-button--primary" type="submit" disabled={submitting || !apiToken}>
            {submitting ? '提交中...' : '提交反馈'}
          </button>
          {!apiToken ? <small>登录后才能提交到后端。</small> : null}
        </form>
      ) : null}
    </div>
  );
}

export default FeedbackWidget;
