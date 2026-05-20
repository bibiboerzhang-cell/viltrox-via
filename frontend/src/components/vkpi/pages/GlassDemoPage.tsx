import { useCallback, useState } from 'react';
import {
  GlassFAB,
  GlassSidebar,
  GlassToast,
  GlassTopBar,
  HeroSection,
  glassVarStyle,
} from '../glass';
import '../glass-future/tokens.css';
import '../glass-future/background.css';
import '../glass-future/components.css';
import '../glass-future/animations.css';
import '../glass-future/responsive.css';

interface GlassDemoPageProps {
  userName?: string;
  userRole?: string;
}

let glassToastTimer: number | undefined;

type MockRow<T> = T & {
  isMock: true;
  mockLabel: string;
};

const demoKpis: Array<MockRow<{
  icon: string;
  label: string;
  value: string;
  meta: string;
  trend: 'up' | 'down';
  ig: string;
  ic: string;
  sparkPath: string;
}>> = [
  { icon: '◉', label: '总曝光量', value: '86.37M', meta: '较上周 ↑ 12.5%', trend: 'up', ig: 'rgba(27,108,255,.12)', ic: '#1b6cff', sparkPath: 'M2 18 C12 20 18 12 27 16 S44 20 53 12 70 9 81 15 98 22 118 12', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▣', label: 'GMV', value: '$342.6K', meta: '较上周 ↑ 8.3%', trend: 'up', ig: 'rgba(24,199,132,.13)', ic: '#18c784', sparkPath: 'M2 18 C18 14 27 18 40 13 S60 11 74 16 94 20 118 12', isMock: true, mockLabel: '示例 KPI' },
  { icon: '♚', label: '新增 KOL', value: '12', meta: '较上周 ↑ 3', trend: 'up', ig: 'rgba(139,92,246,.13)', ic: '#8b5cf6', sparkPath: 'M2 17 C15 19 22 14 35 18 S55 8 69 12 86 20 118 15', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▥', label: '内容互动率', value: '3.24%', meta: '较上周 ↓ 0.4%', trend: 'down', ig: 'rgba(255,159,46,.14)', ic: '#ff9f2e', sparkPath: 'M2 12 C18 13 24 18 38 15 S58 9 70 17 92 20 118 13', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▤', label: '订单量', value: '1,287', meta: '较上周 ↑ 6.7%', trend: 'up', ig: 'rgba(27,108,255,.11)', ic: '#1b6cff', sparkPath: 'M2 18 C17 13 24 17 34 15 S54 14 66 11 86 19 118 10', isMock: true, mockLabel: '示例 KPI' },
  { icon: '¥', label: '平均 ROI', value: '5.21x', meta: '较上周 ↑ 0.7x', trend: 'up', ig: 'rgba(255,77,166,.13)', ic: '#ff4da6', sparkPath: 'M2 12 C20 10 29 14 44 13 S65 19 76 17 96 12 118 9', isMock: true, mockLabel: '示例 KPI' },
];

const regions = [
  { label: '北美', value: '67.2%', color: '#1b6cff' },
  { label: '欧洲', value: '21.8%', color: '#6aa6ff' },
  { label: '亚太', value: '8.6%', color: '#18d5ff' },
  { label: '南美', value: '1.6%', color: '#8b5cf6' },
  { label: '其他', value: '0.8%', color: '#cfe0ff' },
];

const productRows = [
  { rank: 1, name: 'AF 56mm F1.2 Pro', width: '96%', value: '8.21x' },
  { rank: 2, name: 'AF 35mm F1.2 LAB', width: '82%', value: '6.74x' },
  { rank: 3, name: 'AF 135mm F1.8 LAB', width: '68%', value: '5.31x' },
  { rank: 4, name: 'AF 16mm F1.8', width: '52%', value: '4.22x' },
];

const contentTypes = [
  { label: '视频', value: '56.7%', color: '#1b6cff' },
  { label: '图集', value: '24.3%', color: '#18d5ff' },
  { label: '图文', value: '13.6%', color: '#8b5cf6' },
];

const platforms = [
  { icon: 'IG', label: 'Instagram', width: '100%', value: '56.57M', background: '#e1306c' },
  { icon: 'YT', label: 'YouTube', width: '35%', value: '19.80M', background: '#ff0000' },
  { icon: 'TT', label: 'TikTok', width: '11%', value: '6.21M', background: '#111827' },
  { icon: 'FB', label: 'Facebook', width: '5%', value: '2.41M', background: '#1877f2' },
];

const alerts: Array<MockRow<{
  icon: string;
  title: string;
  body: string;
  time: string;
  bgc: string;
  col: string;
}>> = [
  { icon: '!', title: 'Sigma 35mm F1.4 EX 发布', body: '竞品发布导致相关流量下降 8%', time: '2h', bgc: '#fff1f0', col: '#f04438', isMock: true, mockLabel: '示例提醒' },
  { icon: '!', title: 'Z50II AF 问题讨论增加', body: '新增 18 条负面舆情', time: '5h', bgc: '#fff7ed', col: '#f79009', isMock: true, mockLabel: '示例提醒' },
  { icon: '✓', title: '35mm F1.2 LAB 互动创新高', body: '互动率较上周增长 23%', time: '1d', bgc: '#ecfdf3', col: '#12b76a', isMock: true, mockLabel: '示例提醒' },
];

const tasks: Array<MockRow<{
  title: string;
  priority: 'high' | 'mid' | 'low';
  priorityLabel: string;
  body: string;
  width: string;
}>> = [
  { title: 'DC-A1 Monitor 上市任务', priority: 'high', priorityLabel: '高', body: '52/60 KOL 已对接，剩余 8 个需本周确认。', width: '87%', isMock: true, mockLabel: '示例任务' },
  { title: '补寄任务（US 仓库）', priority: 'mid', priorityLabel: '中', body: '6 位 KOL 等待寄样，预计影响 56mm Pro 预热。', width: '0%', isMock: true, mockLabel: '示例任务' },
  { title: 'Cinegear 物料准备', priority: 'low', priorityLabel: '低', body: '剩余 4 天截止，目前进行中。', width: '55%', isMock: true, mockLabel: '示例任务' },
];

const quickActions = [
  { icon: '⌁', label: '内容发布' },
  { icon: '⌕', label: 'KOL 寻找' },
  { icon: '▣', label: '舆情监控' },
  { icon: '◎', label: '竞品监控' },
  { icon: '▦', label: '产品管理' },
  { icon: '▥', label: '数据报表' },
];

function DemoKpiCard({ item }: { item: typeof demoKpis[number] }) {
  return (
    <div className="glass-card kpi" style={glassVarStyle({ '--ig': item.ig, '--ic': item.ic })} title={item.mockLabel}>
      <div className="topline"><div className="icon">{item.icon}</div></div>
      <div className="label">{item.label}</div>
      <div className="value">{item.value}</div>
      <div className={`meta ${item.trend}`}>{item.meta}</div>
      <svg className="spark" viewBox="0 0 120 28"><path d={item.sparkPath} fill="none" stroke={item.ic} strokeWidth="3" strokeLinecap="round" /></svg>
    </div>
  );
}

export function GlassDemoPage({ userName = 'Jianbo', userRole = 'Marketing Director' }: GlassDemoPageProps) {
  const [toast, setToast] = useState('已触发');
  const [toastVisible, setToastVisible] = useState(false);
  const [activeNav, setActiveNav] = useState('Dashboard');
  const [activeSegment, setActiveSegment] = useState('曝光量');

  const showToast = useCallback((message: string) => {
    setToast(message);
    setToastVisible(true);
    window.clearTimeout(glassToastTimer);
    glassToastTimer = window.setTimeout(() => setToastVisible(false), 1600);
  }, []);

  const handleNavSelect = (key: string) => {
    setActiveNav(key);
    showToast(`${key} · 高级玻璃方向占位`);
  };

  const handleSegmentSelect = (key: string) => {
    setActiveSegment(key);
    showToast(`切换：${key}`);
  };

  return (
    <div className="vkpi-glass-shell" data-testid="vkpi-glass-demo">
      <div className="browser"><div className="traffic"><span className="t-dot red"></span><span className="t-dot yellow"></span><span className="t-dot green"></span></div><div className="browser-title">viltroxtest.com · V-KPI Glass Intelligence</div><div className="browser-icons">◉ ⇧</div></div>
      <div className="app">
        <GlassSidebar activeKey={activeNav} onSelectNav={handleNavSelect} profileInitial={userName.slice(0, 1).toUpperCase()} profileName={userName} profileRole={userRole} />
        <main className="main">
          <GlassTopBar
            actions={[
              { label: new Date().toISOString().slice(0, 10), onClick: () => showToast('今日日期 · 实时') },
              { label: '示例 · 待接入真实状态', variant: 'sync', onClick: () => showToast('同步状态 · 开发占位') },
              { label: '导出', onClick: () => showToast('原型交互 · 可接真实路由') },
              { label: '生成周报', variant: 'primary', onClick: () => showToast('原型交互 · 可接真实路由') },
            ]}
          />
          <HeroSection />
          <section className="kpis">
            {demoKpis.map((item) => <DemoKpiCard key={item.label} item={item} />)}
          </section>
          <div className="content-grid">
            <div>
              <div className="left-grid">
                <div className="glass-card panel">
                  <div className="panel-head"><h3>全球曝光分布</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>Market Map</span></div>
                  <div className="holo-map">
                    <div className="zoom"><span>+</span><span>−</span></div>
                    <svg viewBox="0 0 620 240" preserveAspectRatio="none"><defs><linearGradient id="mg" x1="0" x2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#18d5ff" /></linearGradient><filter id="gl"><feGaussianBlur stdDeviation="5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs><path d="M92 82c40-22 94-9 121 2 27 11 68 3 92 15 30 15 9 39-28 40-58 3-125 15-161-2-35-16-61-39-24-55z" fill="url(#mg)" opacity=".93" filter="url(#gl)" /><path d="M272 82c68-32 128-19 184-4 42 11 77 30 120 21 34-7 65 13 62 35-4 22-49 30-91 24-57-8-99-3-148 16-52 20-120 7-136-30-8-18-14-43 9-62z" fill="#9dc4ff" opacity=".66" /><path d="M184 154c50-14 87 1 119 13 44 17 86 9 121 23 27 11 25 33-5 41-42 12-88-2-126-10-47-10-91 6-124-14-30-17-34-48 15-53z" fill="#6aa6ff" opacity=".50" /><path d="M457 150c43-10 86 4 110 23 24 19 9 41-35 39-50-2-98-14-111-35-10-15 7-23 36-27z" fill="#cfe0ff" opacity=".70" /><circle cx="170" cy="106" r="8" fill="#1b6cff" /><circle cx="170" cy="106" r="28" fill="#1b6cff" opacity=".11" /><circle cx="332" cy="123" r="7" fill="#18d5ff" /><circle cx="332" cy="123" r="24" fill="#18d5ff" opacity=".13" /><circle cx="486" cy="166" r="6" fill="#8b5cf6" /><circle cx="486" cy="166" r="21" fill="#8b5cf6" opacity=".12" /></svg>
                  </div>
                  <div className="region-list">
                    {regions.map((region) => <div className="region" style={glassVarStyle({ '--c': region.color })} key={region.label}><span><i></i>{region.label}</span><b>{region.value}</b></div>)}
                  </div>
                </div>
                <div className="glass-card panel">
                  <div className="panel-head"><h3>曝光趋势（近 7 天）</h3><div className="segment">{['曝光量', '互动量', '销售额'].map((segment) => <button className={activeSegment === segment ? 'active' : ''} data-seg={segment} onClick={() => handleSegmentSelect(segment)} type="button" key={segment}>{segment}</button>)}</div></div>
                  <div className="linechart"><svg viewBox="0 0 520 250" preserveAspectRatio="none"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#1b6cff" stopOpacity="0" /></linearGradient></defs><g stroke="rgba(92,130,190,.16)"><line x1="26" y1="30" x2="500" y2="30" /><line x1="26" y1="80" x2="500" y2="80" /><line x1="26" y1="130" x2="500" y2="130" /><line x1="26" y1="180" x2="500" y2="180" /></g><path d="M34 196 C82 162 121 164 166 134 S235 102 285 101 363 76 411 51 458 25 500 32" fill="none" stroke="#1b6cff" strokeWidth="4" strokeLinecap="round" /><path d="M34 196 C82 162 121 164 166 134 S235 102 285 101 363 76 411 51 458 25 500 32 L500 222 L34 222 Z" fill="url(#area)" opacity=".25" /><circle cx="500" cy="32" r="8" fill="#1b6cff" stroke="#fff" strokeWidth="4" /><text x="34" y="238" fontSize="12" fill="#667085">05/11</text><text x="116" y="238" fontSize="12" fill="#667085">05/12</text><text x="198" y="238" fontSize="12" fill="#667085">05/13</text><text x="280" y="238" fontSize="12" fill="#667085">05/14</text><text x="362" y="238" fontSize="12" fill="#667085">05/15</text><text x="444" y="238" fontSize="12" fill="#667085">05/16</text></svg><div className="float-tip">05/17<b>86.37M</b></div></div>
                </div>
                <div className="lower">
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>产品 ROI 排行</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>
                    {productRows.map((row) => <div className="row" key={row.rank}><span className="rank">{row.rank}</span><div><b>{row.name}</b><div className="bar"><span style={glassVarStyle({ '--w': row.width })}></span></div></div><small>{row.value}</small></div>)}
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>内容类型分布</h3></div>
                    <div className="donut-wrap"><div className="donut"></div><div className="donut-label"><span>总内容</span><b>2,847</b></div></div>
                    <div className="region-list" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>{contentTypes.map((item) => <div className="region" style={glassVarStyle({ '--c': item.color })} key={item.label}><span><i></i>{item.label}</span><b>{item.value}</b></div>)}</div>
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>平台表现</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>
                    {platforms.map((platform) => <div className="platform" key={platform.label}><span className="picon" style={{ background: platform.background }}>{platform.icon}</span><div><b>{platform.label}</b><div className="bar"><span style={glassVarStyle({ '--w': platform.width })}></span></div></div><small>{platform.value}</small></div>)}
                  </div>
                </div>
              </div>
              <div className="glass-card latest"><div className="panel-head"><h3>最新内容表现</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>进入内容中心</span></div><table className="table"><thead><tr><th>内容</th><th>KOL / 平台</th><th>发布平台</th><th>发布于</th><th>曝光量</th><th>互动率</th><th>操作</th></tr></thead><tbody><tr><td><div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}><div className="thumb"></div><div><b>Viltrox 35mm F1.2 LAB Real World Review</b><br /><span className="tag">视频</span> <span className="tag">测评</span> <span className="tag">示例</span></div></div></td><td>@vor_ject<br /><span style={{ color: '#667085' }}>Instagram</span></td><td>Instagram</td><td>2 小时前</td><td><b>1.24M</b></td><td>4.62%</td><td><button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>⌁</button> <button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>↗</button> <button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>…</button></td></tr></tbody></table></div>
            </div>
            <aside className="rail">
              <div className="glass-card rail-card copilot"><div className="ai-kicker">V-KPI Copilot</div><h3>系统正在把推荐、风险、任务压缩成 7 张行动卡。</h3><p>今日重点：处理 4 条推荐反馈、补齐 35mm LAB 项目 KOL 缺口、检查 Sigma 竞品内容。</p><div className="insight">示例 · 置信度 91% · 证据 18 条 · 数据新鲜度 4h</div></div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>重要提醒</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>{alerts.map((alert) => <div className="alert" title={alert.mockLabel} key={alert.title}><div className="alert-ic" style={glassVarStyle({ '--bgc': alert.bgc, '--col': alert.col })}>{alert.icon}</div><div><b>{alert.title}</b><p>{alert.body}</p></div><span className="time">{alert.time}</span></div>)}</div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>本周关键任务</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>{tasks.map((task) => <div className="task" title={task.mockLabel} key={task.title}><div className="task-head"><b>{task.title}</b><span className={`priority ${task.priority}`}>{task.priorityLabel}</span></div><p>{task.body}</p><div className="progress"><span style={glassVarStyle({ '--w': task.width })}></span></div></div>)}</div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>快捷入口</h3></div><div className="quick">{quickActions.map((action) => <div key={action.label} onClick={() => showToast('原型交互 · 可接真实路由')}><b>{action.icon}</b><span>{action.label}</span></div>)}</div></div>
            </aside>
          </div>
        </main>
      </div>
      <GlassFAB onClick={() => showToast('原型交互 · 可接真实路由')} />
      <GlassToast show={toastVisible}>{toast}</GlassToast>
    </div>
  );
}

export default GlassDemoPage;
