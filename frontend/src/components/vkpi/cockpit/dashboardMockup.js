// @ts-nocheck
/* 1:1 搬版:V-KPI-Dashboard mockup 的原始 <body> 与 <script>,一字不改地复用,
   保证 board / 北极星流动环 / sparkline 发光 / LLM 队列 / 可编辑看板 / Ask / 下钻弹窗 等
   视觉与动效 100% 与 mockup 一致(零漂移)。由 MockupDashboard.tsx 注入容器后调用 mount()。
   真数据(KPI 等)后续通过替换 KPI_DATA 等注入;当前为 mockup 样例值。 */
export const MOCKUP_BODY_HTML = '<div class="aura"><b></b><b></b></div>\n<div class="shell">\n  <aside class="rail">\n    <button class="railtoggle" id="railToggle" title="收起 / 展开侧栏"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 6l-6 6 6 6"/></svg></button>\n    <div class="brand"><div class="mk"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/></svg></div><div class="brand-txt"><div class="nm">V-KPI</div><div class="sb mono">GROWTH · OS</div></div></div>\n    <div class="nav" id="nav"></div>\n    <div class="raillm" id="raillm" title="LLM 任务队列">\n      <div class="rl-h"><span class="rl-t">LLM 队列</span><span class="rl-c">3 处理中</span></div>\n      <div class="rl-lanes">\n        <div class="rl-lane"><span class="rl-n">交互</span><span class="rl-bar"><i class="c-acc" style="width:78%"></i></span><span class="rl-p">78</span></div>\n        <div class="rl-lane"><span class="rl-n">批 A</span><span class="rl-bar"><i class="c-good" style="width:62%"></i></span><span class="rl-p">62</span></div>\n        <div class="rl-lane"><span class="rl-n">批 B</span><span class="rl-bar"><i class="c-good" style="width:94%"></i></span><span class="rl-p">94</span></div>\n        <div class="rl-lane idle"><span class="rl-n">批 C</span><span class="rl-bar idle"><i></i></span><span class="rl-p">空闲</span></div>\n      </div>\n      <div class="rl-f">今日 1,240 次 · $0.86 · 预算 29%</div>\n    </div>\n    <div class="railfoot"><span class="d"></span>系统在线 · build e424d9</div>\n  </aside>\n\n  <main class="main">\n    <div class="top">\n      <div class="top-title"><h1>Dashboard</h1><span class="top-sub"><i></i>增长总览 · 实时</span></div>\n      <div class="search" id="askOpen" title="问一问 AI / 全局搜索"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5 10.1 10.9 5.5 9l4.6-1.4z"/></svg>问一问 · AI 或搜索 KOL / 项目…<kbd class="kbd">⌘K</kbd></div>\n      <div class="top-actions">\n        <button class="editbtn" id="editBtn"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16 4l4 4L8 20l-4 1 1-4z"/></svg>编辑布局</button>\n        <button class="iconbtn" id="apprBtn" title="外观 / 主题"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3a9 9 0 000 18c1.5 0 1.9-1.1 1.1-1.9-.8-.9-.3-2.1 1-2.1H17a4 4 0 004-4c0-5-4-10-9-10z"/><circle cx="8" cy="8.5" r="1.1"/><circle cx="12.5" cy="6.5" r="1.1"/><circle cx="16.5" cy="9.5" r="1.1"/><circle cx="7" cy="13" r="1.1"/></svg></button>\n        <button class="iconbtn" id="bellBtn" title="通知"><span class="dotbadge"></span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9a6 6 0 1112 0c0 4.5 1.8 5.5 1.8 5.5H4.2S6 13.5 6 9z"/><path d="M10 20a2 2 0 004 0"/></svg></button>\n        <button class="acct" id="acctBtn" title="Admin · Owner · 在线"><span class="avatar">A</span><span class="acct-nm"><b>Admin</b><span>Owner</span></span></button>\n      </div>\n      <div class="apprPop" id="apprPop">\n        <div class="pop-l">外观风格</div>\n        <div class="seg" id="styleSeg"><button data-s="glass" class="on">玻璃</button><button data-s="instrument">仪器</button><button data-s="commandos">单色</button></div>\n        <div class="pop-l">主题</div>\n        <div class="seg" id="themeSeg"><button data-t="light">浅</button><button data-t="dark" class="on">深</button></div>\n      </div>\n    </div>\n\n    <div class="canvas">\n      <div class="edithint"><b style="color:var(--acc)">编辑模式</b> · 拖动卡片排序 · 右上角 ◧ 改大小 / ✕ 移除 · 底部「+ 添加模块」加自定义/备忘录 · 布局自动保存<button class="editbtn" id="resetLayout" style="margin-left:auto">↺ 恢复默认布局</button></div>\n      <div id="board"></div>\n    </div>\n  </main>\n</div>\n\n<div class="palette" id="palette"><div class="box">\n  <h3>添加模块</h3><p>选一个加到看板;之后可拖动、改大小、移除。</p>\n  <div class="opts" id="paletteOpts"></div>\n  <div style="text-align:right;margin-top:14px"><button class="editbtn" id="palClose">取消</button></div>\n</div></div>\n\n<div class="scrim" id="scrim"></div>\n<aside class="drawer" id="drawer"><div class="dh"><div><div class="t" id="drTitle"></div><div class="s" id="drSub"></div></div><button class="dx" id="drClose">✕</button></div><div class="db" id="drBody"></div></aside>\n\n<div class="askov" id="askov"><div class="askbox">\n  <div class="askhead"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5 10.1 10.9 5.5 9l4.6-1.4z"/></svg><input id="askin" class="askin" placeholder="问点什么…例如「本周哪些 KOL 值得加码?」" autocomplete="off"/><button class="askx" id="askClose">ESC</button></div>\n  <div class="asksugs" id="asksugs"></div>\n  <div class="askans" id="askans"></div>\n</div></div>';
export function mountMockDashboard() {
var root=document.documentElement, body=document.body;
  if(!root.getAttribute('data-style'))root.setAttribute('data-style','glass');
  if(!root.getAttribute('data-theme'))root.setAttribute('data-theme','dark');
  var reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  function cur(v){return getComputedStyle(root).getPropertyValue(v).trim();}
  var LS={get:function(k,d){try{return JSON.parse(localStorage.getItem(k))??d;}catch(e){return d;}},set:function(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}}};

  var ICONS={
    dash:'<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    kol:'<circle cx="12" cy="8" r="3.4"/><path d="M5.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/>',
    pool:'<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v6c0 1.6 3.4 3 7.5 3s7.5-1.4 7.5-3V6"/><path d="M4.5 12v6c0 1.6 3.4 3 7.5 3s7.5-1.4 7.5-3v-6"/>',
    profile:'<rect x="4" y="4" width="16" height="16" rx="2.5"/><circle cx="9.5" cy="10" r="2.1"/><path d="M14 9h4M14 13h4M6 16.5c.7-1.7 2-2.6 3.5-2.6s2.8.9 3.5 2.6"/>',
    proj:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M9 3.5h6v3H9z"/><path d="M8.5 12.5l2.3 2.3 4.4-4.8"/>',
    event:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 9.5h16M9 3v4M15 3v4"/>',
    shop:'<path d="M6 8h12l-1 11.4a1.5 1.5 0 01-1.5 1.4h-9A1.5 1.5 0 015 19.4z"/><path d="M9 8V6.5a3 3 0 016 0V8"/>',
    deal:'<path d="M12 21c4.5-4.2 7-7.4 7-10.5a7 7 0 10-14 0C5 13.6 7.5 16.8 12 21z"/><circle cx="12" cy="10.5" r="2.3"/>',
    chat:'<path d="M20 11.5a7.5 7.5 0 01-10.8 6.7L4 20l1.9-4.9A7.5 7.5 0 1120 11.5z"/><path d="M9 11h6M9 8.5h4"/>',
    voice:'<circle cx="12" cy="12" r="2.1"/><path d="M8 8a5.5 5.5 0 000 8M16 8a5.5 5.5 0 010 8M5.5 5.5a9 9 0 000 13M18.5 5.5a9 9 0 010 13"/>',
    sku:'<path d="M12 3l7.5 4.2v9L12 20.4 4.5 16.2v-9z"/><path d="M12 3v17.4M4.5 7.2L12 11.5l7.5-4.3"/>',
    asset:'<rect x="4" y="4" width="16" height="16" rx="2.5"/><circle cx="9" cy="9.5" r="1.7"/><path d="M4.5 17l4.5-3.8 3.5 2.6 3-2.2 4 3.4"/>',
    queue:'<path d="M4 13l2.2 5h11.6L20 13"/><path d="M4 13V5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5V13"/><path d="M9 13a3 3 0 006 0"/>',
    launch:'<path d="M14.5 4.5C10 6 7 10 5.6 13.4l5 5C14 17 18 14 19.4 9.5c.6-2 .6-4 .6-5 0 0-3-.6-5.6 0z"/><path d="M9 15l-3.4 3.4M6 14c-1.3.4-2.3 1.6-2.5 3.5C5.4 17.3 6.6 16.3 7 15"/><circle cx="14.5" cy="9.5" r="1.5"/>',
    auto:'<rect x="5" y="7" width="14" height="12" rx="2.5"/><path d="M9 3.5v3.5M15 3.5v3.5M3 11h2M3 15h2M19 11h2M19 15h2"/><circle cx="9.5" cy="13" r="1"/><circle cx="14.5" cy="13" r="1"/>',
    strat:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4.2"/><circle cx="12" cy="12" r="1"/>',
    gtm:'<circle cx="12" cy="12" r="8.5"/><path d="M15.6 8.4l-2.2 5.2L8 16l2.2-5.2z"/>'};
  var NAVG=[
    ['总览',[['dash','Dashboard',1]]],
    ['达人运营',[['kol','MY KOL'],['pool','KOL Pool',0,'35'],['profile','KOL 档案']]],
    ['增长渠道',[['proj','Projects'],['event','Events'],['shop','Shopify'],['deal','Dealers']]],
    ['智能中枢',[['chat','Intelligent 问答'],['voice','市场之声'],['sku','SKU 360°'],['asset','创意资产库']]],
    ['自动化',[['queue','回复队列',0,'3'],['launch','发射台'],['auto','自治驾照'],['strat','战略台'],['gtm','GTM Command']]]
  ];
  document.getElementById('nav').innerHTML=NAVG.map(function(g){return '<div class="navcap">'+g[0]+'</div>'+g[1].map(function(n){return '<a class="navi'+(n[2]?' on':'')+'" title="'+n[1]+'"><span class="ni"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+(ICONS[n[0]]||'')+'</svg></span><span class="nl">'+n[1]+'</span>'+(n[3]?'<span class="nb">'+n[3]+'</span>':'')+'</a>';}).join('');}).join('');
  // 外观 / 主题 弹层
  document.getElementById('apprBtn').addEventListener('click',function(e){e.stopPropagation();document.getElementById('apprPop').classList.toggle('on');});
  document.addEventListener('click',function(e){var p=document.getElementById('apprPop');if(p&&p.classList.contains('on')&&!e.target.closest('#apprPop')&&!e.target.closest('#apprBtn'))p.classList.remove('on');});
  // 侧栏收起 / 展开(记忆)
  if(LS.get('rail-min',false))document.body.classList.add('rail-min');
  document.getElementById('railToggle').addEventListener('click',function(){var m=document.body.classList.toggle('rail-min');LS.set('rail-min',m);});

  // 生成式海报缩略图
  function poster(hue,kind,dur,cls){return '<div class="poster '+(cls||'')+'" style="--h:'+hue+'">'+(kind==='video'?'<div class="pl"><span>▶</span></div>'+(dur?'<div class="dur">'+dur+'</div>':''):'')+'</div>';}

  // ==== 详情抽屉内容(点模块下钻)====
  var DETAIL={
    aitoday:{title:'AI Today · 今日简报',sub:'6/30 · 电影感 Vlog 拍摄方案',html:function(){return ''
      +'<div class="dsec"><div class="dl">今日重点决策</div><div style="font-size:13px;line-height:1.75;color:var(--tx2)"><b style="color:var(--tx)">电影感 Vlog</b> 是海外七月最强上升热点(复古街拍 + 真实人像)。近 7 天相关标签曝光 <b style="color:var(--good)">+38%</b>,但承接页转化偏弱 —— 建议官号先出教育向样片验证,再放量。</div></div>'
      +'<div class="dsec"><div class="dl">拍摄方案 · 六步</div><div class="steps">'
        +'<div class="step"><b>黄金时刻自然光</b> —— 日出后 / 日落前 1h,侧逆光拍人像轮廓光,避开正午硬光。</div>'
        +'<div class="step"><b>复古街拍机位</b> —— 低机位 + 长焦压缩(85mm F1.4),背景城市霓虹虚化成光斑。</div>'
        +'<div class="step"><b>真实人像互动</b> —— 抓拍回眸 / 走动,不摆拍;手持微动带呼吸感。</div>'
        +'<div class="step"><b>电影级调色</b> —— 青橙色调、降饱和、加颗粒,LUT 出胶片质感。</div>'
        +'<div class="step"><b>节奏与配乐</b> —— 卡点转场 + 低 BPM 氛围乐,前 3 秒定情绪。</div>'
        +'<div class="step"><b>承接引导</b> —— 片尾挂独立站「电影感套装」短链,评论区置顶器材清单。</div>'
      +'</div></div>'
      +'<div class="dsec"><div class="dl">真实视频案例 · 可参考</div><div class="vcases">'+[
        [208,'黄昏街头人像 · 85mm 光斑','@nightcitylens','2.4M','48s','长焦光斑+侧逆光,完播 71%'],
        [30,'复古胶片调色教程','@filmlookdaily','880K','6:12','手把手 LUT,收藏率高'],
        [275,'手持运镜一镜到底','@wanderframe','1.1M','34s','呼吸感运镜,评论问器材'],
        [150,'日落人像 Reels','@goldenhourgirl','3.0M','22s','黄金时刻标杆,转化强']
      ].map(function(v){return '<div class="vcase">'+poster(v[0],'video',v[4])+'<div class="vi"><div class="vt">'+v[1]+'</div><div class="vm"><span>'+v[2]+'</span><span>'+v[3]+' 播放</span></div><div class="vw">✦ '+v[5]+'</div></div></div>';}).join('')+'</div></div>'
      +'<div class="dsec"><div class="dl">简报指标</div><div class="drow"><span class="k">发布率</span><span class="v g">49.2%</span></div><div class="drow"><span class="k">起草</span><span class="v">758</span></div><div class="drow"><span class="k">AI 花费(今日)</span><span class="v">$0.001</span></div><div class="drow"><span class="k">热点上升</span><span class="v g">+38%</span></div></div>'
      +'<button class="dbtn">一键生成官号脚本 →</button>';}},
    signals:{title:'市场信号 · 竞品雷达',sub:'4 条 · 21:00 EDT',html:function(){return ''
      +'<div class="dsec"><div class="dl">重点竞品 · Tamron 17-70 F2.8</div>'+poster(208,'product')
      +'<div style="font-size:12.5px;line-height:1.75;color:var(--tx2);margin-top:11px">腾龙发布适用于 <b style="color:var(--tx)">佳能 RF / 尼康 Z</b> 卡口的 17–70mm F2.8 恒定光圈标变。覆盖 APS-C 常用焦段,与 Viltrox <b>27 / 33 / 56</b> 三定焦形成「一镜走天下 vs 大光圈定焦」的直接竞争。</div></div>'
      +'<div class="dsec"><div class="dl">影响评估</div><div class="drow"><span class="k">提及量(7d)</span><span class="v">3</span></div><div class="drow"><span class="k">受影响 SKU</span><span class="v">VL-27 / 33 / 56</span></div><div class="drow"><span class="k">威胁等级</span><span class="v c">中高</span></div><div class="drow"><span class="k">建议动作</span><span class="v g">出对比测评</span></div></div>'
      +'<div class="dsec"><div class="dl">相关素材</div><div class="imgrow">'+poster(208,'product')+poster(24,'product')+poster(150,'product')+'</div></div>'
      +'<button class="dbtn">加入竞品监视清单 →</button>';}},
    generic:{title:'模块详情',sub:'完整数据',html:function(name){return '<div class="dsec"><div class="dl">'+(name||'明细')+'</div><div style="font-size:12.5px;color:var(--tx2);line-height:1.75">点进来这里会展开该模块的<b style="color:var(--tx)">全部明细</b>:完整列表 / 趋势 / 来源 / 可下钻子项 / 导出。真实平台按各模块接后端明细端点。</div></div><div class="dsec"><div class="dl">示例明细</div><div class="drow"><span class="k">数据源</span><span class="v">真实 API</span></div><div class="drow"><span class="k">更新</span><span class="v">实时</span></div><div class="drow"><span class="k">可下钻</span><span class="v g">是</span></div></div>';}}
  };

  // ==== 6 个 KPI 指标的下钻详情(全部 / 个人KOL / 公司账号 三视角)====
  var MKEYS=['roster','active30','exposure','engagement','gmv','roi'];
  var SRC_ROSTER=['accounts · vkpi_employee_channels(官方在线)','metrics · vkpi_channel_metrics','reviews · vkpi_channel_post_metrics'];
  var SRC_POST=['posts · vkpi_kol_posts / channel_post_metrics','identity · kol_pool + staff_assignment','evidence · video_evidence'];
  var SRC_EXP=['metrics · vkpi_channel_metrics(日快照)','fill · vkpi_channel_metrics_filled','kol · kol_daily_snapshot'];
  var PLAT_OFF=[['Instagram',8,43.3],['TikTok',5,27.8],['Facebook',4,22.2],['Reddit',1,5.6],['X',1,5.6],['YouTube',1,5.6]];
  var PLAT_KOL=[['TikTok',214,64.6],['Instagram',68,20.5],['YouTube',49,14.8]];
  var RANK_OFF=[['Viltrox','Meet the all-new AF 28mm F4','4.50M'],['Viltrox','AF 75mm F1.8 EVO & 80mm F2','3.50M'],['Viltrox','AF 35mm F1.2 LAB N FE','1.50M'],['Viltrox','AF 20mm F1.8 · dropped','1.30M'],['Viltrox','AF 35/56 LAB · screenless','1.20M'],['Viltrox','AF 35mm F1.2 · OUT NOW','1.10M'],['Viltrox.Flash','21 Pro · 18000s HSS','990.8K'],['Viltrox.Flash','22 sample · mini light','896.9K']];
  var RANK_KOL=[['josiahlebante14','TikTok · 516K','Fit 95'],['frank_of_all_trades','TikTok · 1.0M','Fit 95'],['zahidangeless','TikTok · 2.2M','Fit 85'],['swetih','TikTok · 162K','Fit 83'],['kai_hussin','TikTok · 117K','Fit 81'],['eliinfante','TikTok · 88K','Fit 79']];
  var METRIC={
    roster:{label:'Active Roster',src:SRC_ROSTER,groupTitle:'账号分组',rankTitle:{all:'合作 / 官方 Top',personal:'KOL 榜单',company:'官方账号榜单'},scopes:{
      all:{count:'525',big:'525',unit:'',s1:['合作 KOL','507'],s2:['官方账号','18'],trend:[480,492,488,500,506,512,519,525],color:'good',groups:[['KOL 合作',507,'88%'],['官方账号',18,'12%'],['本月新增',34,'6%']]},
      personal:{count:'507',big:'507',unit:'',s1:['进行中','142'],s2:['待跟进','23'],trend:[440,452,455,462,468,472,476,480],color:'good',groups:[['头部 >1M',46,'9%'],['腰部 100K-1M',213,'42%'],['新锐 <100K',248,'49%']]},
      company:{count:'18',big:'18',unit:'',s1:['粉丝总数','1.24M'],s2:['总播放','370.70M'],trend:[12,13,14,15,16,17,17,18],color:'acc',groups:[['主品牌',6,'33.3%'],['产品线',6,'33.3%'],['区域',6,'33.3%']]}}},
    active30:{label:'Active 30D',src:SRC_POST,groupTitle:'内容类型',rankTitle:{all:'高热内容',personal:'KOL 高热帖',company:'官方高热帖'},scopes:{
      all:{count:'146',big:'146',unit:'',s1:['KOL 发布','130'],s2:['官方发布','16'],trend:[98,110,118,124,130,138,142,146],color:'good',groups:[['视频',112,'77%'],['图文',24,'16%'],['直播',10,'7%']]},
      personal:{count:'130',big:'130',unit:'',s1:['视频','104'],s2:['图文','26'],trend:[100,104,107,110,112,120,126,130],color:'good',groups:[['TikTok',96,'74%'],['Reels',22,'17%'],['Shorts',12,'9%']]},
      company:{count:'16',big:'16',unit:'',s1:['官方视频','14'],s2:['图文','2'],trend:[8,9,10,12,13,14,15,16],color:'acc',groups:[['主品牌',9,'56%'],['产品线',5,'31%'],['区域',2,'13%']]}}},
    exposure:{label:'Exposure',src:SRC_EXP,groupTitle:'曝光构成',rankTitle:{all:'高曝光内容',personal:'KOL 高曝光',company:'官方高曝光'},scopes:{
      all:{count:'2.05B',big:'2.05',unit:'B',s1:['自然','1.78B'],s2:['付费','0.27B'],trend:[1.4,1.5,1.6,1.72,1.8,1.9,1.98,2.05],color:'acc',groups:[['自然曝光','1.78B','87%'],['付费曝光','0.27B','13%'],['官号占比','430M','21%']]},
      personal:{count:'1.62B',big:'1.62',unit:'B',s1:['视频','1.40B'],s2:['图文','220M'],trend:[1.1,1.2,1.3,1.4,1.48,1.54,1.58,1.62],color:'acc',groups:[['头部 KOL','780M','48%'],['腰部','610M','38%'],['新锐','230M','14%']]},
      company:{count:'430M',big:'430',unit:'M',s1:['视频','370.7M'],s2:['图文','59M'],trend:[280,300,330,360,380,400,418,430],color:'acc',groups:[['主品牌','258M','60%'],['产品线','129M','30%'],['区域','43M','10%']]}}},
    engagement:{label:'Engagement',src:SRC_POST,groupTitle:'互动构成',rankTitle:{all:'高互动内容',personal:'KOL 高互动',company:'官方高互动'},scopes:{
      all:{count:'1.71%',big:'1.71',unit:'%',s1:['点赞率','1.42%'],s2:['评论率','0.29%'],trend:[1.9,1.85,1.82,1.8,1.78,1.74,1.72,1.71],color:'crit',groups:[['点赞',68,'68%'],['评论',14,'14%'],['分享收藏',18,'18%']]},
      personal:{count:'1.85%',big:'1.85',unit:'%',s1:['点赞率','1.50%'],s2:['评论率','0.35%'],trend:[1.7,1.72,1.75,1.78,1.8,1.82,1.84,1.85],color:'good',groups:[['点赞',66,'66%'],['评论',16,'16%'],['分享收藏',18,'18%']]},
      company:{count:'2.40%',big:'2.40',unit:'%',s1:['点赞率','1.95%'],s2:['评论率','0.45%'],trend:[1.9,2.0,2.1,2.2,2.3,2.35,2.38,2.4],color:'good',groups:[['主品牌','2.8%','70%'],['产品线','2.1%','52%'],['区域','2.3%','58%']]}}},
    gmv:{label:'Attributed GMV',pending:'带货与直营 GMV 需先',src:['orders · shopify_orders','attribution · vkpi_attribution(短链)','webhook · viltroxvia.com'],scopes:{all:{count:'--'},personal:{count:'--'},company:{count:'--'}}},
    roi:{label:'Avg ROI',pending:'ROI = 收入 ÷ 投入,需成本流水,',src:['cost · vkpi_cost_ledger','revenue · shopify_orders','spend · vkpi_campaign_spend'],scopes:{all:{count:'--'},personal:{count:'--'},company:{count:'--'}}}
  };
  var mKey='roster';var mScope='all';
  function scName(s){return s==='company'?'公司账号':s==='personal'?'个人 KOL':'全部';}
  function miniArea(data,cvar){var w=300,h=90,pad=7,n=data.length;var mn=Math.min.apply(null,data),mx=Math.max.apply(null,data),rg=(mx-mn)||1;
    var pts=data.map(function(v,i){return [pad+i*(w-2*pad)/(n-1),h-pad-(v-mn)/rg*(h-2*pad)];});
    var line=pts.map(function(p,i){return (i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' ');
    var area=line+' L'+pts[n-1][0].toFixed(1)+' '+h+' L'+pts[0][0].toFixed(1)+' '+h+' Z';
    var c=cur('--'+(cvar||'acc'))||'#3f9bff';var id='ma'+(cvar||'acc');
    return '<svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" style="width:100%;height:92px;display:block"><defs><linearGradient id="'+id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+c+'" stop-opacity=".3"/><stop offset="1" stop-color="'+c+'" stop-opacity="0"/></linearGradient></defs><path d="'+area+'" fill="url(#'+id+')"/><path d="'+line+'" fill="none" stroke="'+c+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';}
  function mTab(k,l,sc){return '<div class="mtab'+(mScope===k?' on':'')+'" data-sc="'+k+'"><span>'+l+'</span><span class="c">'+((sc&&sc.count)||'--')+'</span></div>';}
  function mFoot(){return '<div class="mfoot"><button class="mfbtn" data-mf="csv">⭳ 导出 CSV</button><button class="mfbtn pri" data-mf="pool">⤵ 链入 KOL Pool</button></div>';}
  function metricBody(m){var tabs='<div class="mtabs">'+mTab('all','全部',m.scopes.all)+mTab('personal','个人 KOL',m.scopes.personal)+mTab('company','公司账号',m.scopes.company)+'</div>';
    if(m.pending){return tabs+'<div class="mpend"><b>暂未接入</b> —— '+m.pending+'接入 <b>Shopify 订单</b>与成本流水后,这里会显示按「'+scName(mScope)+'」拆分的 '+m.label+' 明细、7 天趋势与 Top 榜单。</div><div class="mcard" style="margin-top:11px"><div class="cap"><span>数据来源(已就绪)</span></div><div class="msrc" style="border:0;padding:0;margin:0"><div class="si">'+m.src.map(function(s){return '· '+s;}).join('<br>')+'</div></div></div>'+mFoot();}
    var sc=m.scopes[mScope]||m.scopes.all;var plat=(mScope==='company')?PLAT_OFF:PLAT_KOL;var rank=(mScope==='company')?RANK_OFF:RANK_KOL;
    return tabs
     +'<div class="mgrid"><div class="mcard"><div class="cap"><span>'+scName(mScope)+' · '+m.label+'</span><span>累计</span></div><div class="mbig">'+sc.big+(sc.unit?'<span class="u">'+sc.unit+'</span>':'')+'</div><div class="mchips"><div class="mchip"><div class="n">'+sc.s1[1]+'</div><div class="l">'+sc.s1[0]+'</div></div><div class="mchip"><div class="n">'+sc.s2[1]+'</div><div class="l">'+sc.s2[0]+'</div></div></div><div class="msrc"><div class="sl">数据来源</div><div class="si">'+m.src.map(function(s){return '· '+s;}).join('<br>')+'</div></div></div>'
     +'<div class="mcard"><div class="cap"><span>Trend · 7D</span><span>固定窗口</span></div>'+miniArea(sc.trend,sc.color)+'<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--mut);font-family:var(--mono);margin-top:8px"><span>7 天前</span><span>今天</span></div></div></div>'
     +'<div class="mgrid"><div class="mcard"><div class="cap"><span>'+(m.groupTitle||'分组')+'</span></div><div class="mgroups">'+sc.groups.map(function(g){var pw=parseFloat(g[2]);if(isNaN(pw))pw=30;return '<div class="mgroup"><div class="n">'+g[1]+'</div><div class="l">'+g[0]+'</div><div class="bar"><i style="width:'+pw+'%"></i></div><div class="l" style="margin-top:5px;color:var(--acc)">'+g[2]+'</div></div>';}).join('')+'</div></div>'
     +'<div class="mcard"><div class="cap"><span>按平台</span><span>'+(mScope==='company'?'官方':'KOL')+'</span></div><div class="mplat">'+plat.map(function(p){return '<div class="mplatrow"><span class="pn">'+p[0]+'</span><span class="pbar"><i style="width:'+p[2]+'%"></i></span><span class="pv">'+p[1]+' · '+p[2]+'%</span></div>';}).join('')+'</div></div></div>'
     +'<div class="mrank"><div class="rt">'+((m.rankTitle&&m.rankTitle[mScope])||'Top 榜单')+'</div><div class="mranklist">'+rank.map(function(r,i){return '<div class="mrankrow"><span class="ri">'+(i+1)+'</span><span class="rmid"><div class="rn">'+r[0]+'</div><div class="rs">'+r[1]+'</div></span><span class="rv">'+r[2]+'</span></div>';}).join('')+'</div></div>'
     +mFoot();}
  function renderMetric(){document.getElementById('drBody').innerHTML=metricBody(METRIC[mKey]);}
  function openMetric(k){if(!METRIC[k])return;mKey=k;mScope=(scope||'all');document.getElementById('drTitle').textContent=METRIC[k].label+' · 详情';document.getElementById('drSub').textContent='真实 · evidence + assignment';renderMetric();document.getElementById('scrim').classList.add('on');document.getElementById('drawer').classList.add('on');}

  // ==== 视角:全部 / 个人KOL / 公司账号 ====
  var scope='all';
  var KPI_DATA={
    all:[['Active Roster','g','525','','<span class="dl up">▲6.2%</span>','480,492,488,500,506,512,519,525','good'],['Active 30D','g','130','','<span class="dl up">▲4</span>','112,116,118,121,124,126,128,130','good'],['Exposure','g','2.05','<span class="u">B</span>','<span class="dl up">▲12%</span>','1.4,1.5,1.6,1.72,1.8,1.9,1.98,2.05','acc'],['Engagement','g','1.71','<span class="u">%</span>','<span class="dl dn">▼0.1</span>','1.9,1.85,1.82,1.8,1.78,1.74,1.72,1.71','crit'],['Attributed GMV','w','--','','','','',1],['Avg ROI','w','--','','','','',1]],
    personal:[['活跃 KOL','g','480','','<span class="dl up">▲5%</span>','440,452,455,462,468,472,476,480','good'],['近30天发布','g','118','','<span class="dl up">▲3</span>','100,104,107,110,112,114,116,118','good'],['KOL 曝光','g','1.62','<span class="u">B</span>','<span class="dl up">▲9%</span>','1.1,1.2,1.3,1.4,1.48,1.54,1.58,1.62','acc'],['互动率','g','1.85','<span class="u">%</span>','<span class="dl up">▲0.1</span>','1.7,1.72,1.75,1.78,1.8,1.82,1.84,1.85','good'],['带货 GMV','w','--','','','','',1],['KOL ROI','w','--','','','','',1]],
    company:[['官号数','g','18','','<span class="dl up">▲2</span>','12,13,14,15,16,17,17,18','good'],['近30天发布','g','16','','<span class="dl up">▲4</span>','8,9,10,12,13,14,15,16','good'],['官号曝光','g','430','<span class="u">M</span>','<span class="dl up">▲18%</span>','280,300,330,360,380,400,418,430','acc'],['官号互动率','g','2.40','<span class="u">%</span>','<span class="dl up">▲0.3</span>','1.9,2.0,2.1,2.2,2.3,2.35,2.38,2.4','good'],['直营 GMV','w','--','','','','',1],['官号 ROI','w','--','','','','',1]]
  };
  // ==== 模块定义 ====
  var MODS={
    kpi:{title:null,span:12,body:function(){var seg=function(k,l){return '<button data-sc="'+k+'"'+(scope===k?' class="on"':'')+'>'+l+'</button>';};
      return '<div class="kpihead"><div class="kh-l"><span class="kh-t">增长总览</span><span class="kh-b">6 指标</span></div><div class="kpiseg">'+seg('all','全部')+seg('personal','KOL')+seg('company','公司账号')+'</div><span class="kh-live"><i></i>实时</span></div>'
      +'<div class="kpis">'+(KPI_DATA[scope]||KPI_DATA.all).map(function(k,i){var pend=k[2]==='--';return '<div class="kpi'+(pend?' pend':'')+'" data-metric="'+MKEYS[i]+'"><div class="k"><span class="dot '+k[1]+'"></span>'+k[0]+'<span class="kgo">›</span></div><div class="v mono">'+k[2]+k[3]+' '+k[4]+'</div>'+(pend?'<div class="spempty"></div><div class="pt">'+(k[0].indexOf('GMV')>=0?'待 Shopify 订单接入':'待成本与订单接入')+'</div>':'<svg class="sp" data-d="'+k[5]+'" data-c="'+k[6]+'" viewBox="0 0 240 30" preserveAspectRatio="none"></svg>')+'</div>';}).join('')+'</div>';}},
    cc:{title:'Marketing Command Center',cnt:'US',span:8,tex:1,body:function(){return '<div class="cc"><svg viewBox="0 0 720 330" id="ccsvg"></svg>'
      +'<div class="ov"><div class="t">Marketing Command Center</div><div class="s mono">KOL POOL · 331 · US 覆盖 24 城</div></div>'
      +'<div class="chips"><div class="chip"><div class="k">Viewing</div><div class="v">KOLs</div></div><div class="chip"><div class="k">Country</div><div class="v">US</div></div><div class="chip"><div class="k">City</div><div class="v">All of US</div></div></div>'
      +'<div class="legend"><div class="h"><span>Top Hubs</span><span>KOLs</span></div><div class="lr"><span>New York</span><b>38</b></div><div class="lr"><span>Los Angeles</span><b>28</b></div><div class="lr"><span>Miami</span><b>20</b></div><div class="lr"><span>Chicago</span><b>17</b></div><div class="lr"><span>San Francisco</span><b>17</b></div></div></div>';}},
    ns:{title:'增长健康度',cnt:'North Star',span:4,flow:1,body:function(){return '<div class="ns"><div class="ring"><svg viewBox="0 0 120 120">'
      +'<defs><linearGradient id="nsgrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--acc)"/><stop offset="1" stop-color="var(--acc2)"/></linearGradient></defs>'
      +'<circle cx="60" cy="60" r="52" fill="none" stroke="var(--brd)" stroke-width="9"/>'
      +'<circle class="nsarc" cx="60" cy="60" r="52" fill="none" stroke="url(#nsgrad)" stroke-width="9" stroke-linecap="round" stroke-dasharray="326.7" stroke-dashoffset="326.7" style="transition:stroke-dashoffset 1.2s cubic-bezier(.2,.8,.2,1)"/>'
      +'<circle class="nscomet" cx="60" cy="60" r="52" fill="none" stroke="var(--acc)" stroke-width="9" stroke-linecap="round" stroke-dasharray="5 321.7"/></svg>'
      +'<div class="ctr"><div class="big mono">78</div><div class="cap">Health</div><div class="sub">▲ 5 · 稳步向上</div></div></div>'
      +'<div class="nsrow"><div class="s"><div class="n mono">31.7</div><div class="l">SoV</div></div><div class="s"><div class="n mono">68</div><div class="l">完播</div></div><div class="s"><div class="n mono" style="color:var(--warn)">42</div><div class="l">承接</div></div></div></div>';}},
    actions:{title:'今日该做什么',cnt:'13',span:4,tex:0,body:function(){return [
      ['库存偏低 · VL-LEN076','<span class="tg c">高</span>','当前仅 <b>0</b> 件 · 建议补货或暂缓推荐'],
      ['失败重试 · kol_profile','<span class="tg w">中</span>','#2227 blocked · 已进重试队列,可重放'],
      ['库存偏低 · VL-LEN045','<span class="tg c">高</span>','当前仅 <b>0</b> 件 · 收窄推荐避免空转']
    ].map(function(t){return '<div class="task"><div class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7l9-4 9 4-9 4z"/><path d="M3 7v10l9 4 9-4V7"/></svg></div><div style="flex:1;min-width:0"><div class="tt">'+t[0]+' '+t[1]+'</div><div class="mt">'+t[2]+'</div><div class="acts"><button class="abtn ok">通过</button><button class="abtn">驳回</button><button class="abtn">忽略</button></div></div></div>';}).join('');}},
    signals:{title:'市场信号',span:4,open:'signals',body:function(){return [
      ['TAMRON','发布佳能 RF/尼康 Z 的 17–70 F2.8 · 与 Viltrox 27/33/56 直接竞品','·3','acc',208],
      ['SONY','FX5 + RX10 V 规格外泄 · 影响自媒体测评选题','·0','mut',24],
      ['NIKON','Z6III 固件加 N-log 波形监看 · 视频创作者关注上升','·5','acc',150],
      ['DJI','Osmo 新品预热 · Vlog 器材赛道升温','·8','acc',280]
    ].map(function(s){return '<div class="sig">'+poster(s[4],'product','','st')+'<span class="br">'+s[0]+'</span><span>'+s[1]+' <b class="mono" style="color:var(--'+s[3]+')">'+s[2]+'</b></span></div>';}).join('');}},
    v6fit:{title:'V6 Fit Top',cnt:'真实 Pool',span:4,tex:0,body:function(){return [
      ['J','josiahlebante14','tiktok · 516K','95'],['F','frank_of_all_trades','tiktok · 1.0M','95'],['Z','zahidangeless','tiktok · 2.2M','85'],['S','swetih','tiktok · 162K','83'],['K','kai_hussin','tiktok · 117K','81']
    ].map(function(l){return '<div class="lb"><div class="av">'+l[0]+'</div><div class="nm">'+l[1]+'<div class="sub">'+l[2]+'</div></div><div class="fit">'+l[3]+'</div></div>';}).join('');}},
    trend:{title:'声量 & 曝光趋势',cnt:'8W',span:8,cls:'trend',body:function(){return '<div class="tstats"><div class="ts"><span class="tsn mono" style="color:var(--acc)">2.05<span style="font-size:13px">B</span></span><span class="tsl">曝光量 · <b style="color:var(--good)">▲12%</b></span></div><div class="ts"><span class="tsn mono" style="color:var(--good)">31.7<span style="font-size:13px">%</span></span><span class="tsl">声量份额 · <b style="color:var(--good)">▲6.2</b></span></div><div class="ts"><span class="tsn mono">8<span style="font-size:13px">W</span></span><span class="tsl">窗口 · 周更</span></div></div>'
      +'<div class="tchart"><div class="tyax"><span>2.1B</span><span>1.9</span><span>1.7</span><span>1.4B</span></div><svg class="trendsvg" viewBox="0 0 640 176" preserveAspectRatio="none"></svg></div>'
      +'<div class="legend2"><span class="lg"><span class="sw" style="background:var(--acc)"></span>曝光量</span><span class="lg" style="color:var(--good)"><span class="sw" style="background:var(--good)"></span>声量份额</span><span style="margin-left:auto;color:var(--mut);font-size:10px;font-family:var(--mono)">T-8w → 现在</span></div>';}},
    aitoday:{title:'AI Today · 今日简报',cnt:'6/30',span:4,open:'aitoday',body:function(){return '<div class="aihero">'+poster(208,'video','0:32','fill')+'</div><div class="brief"><b>今日重点决策:</b>电影感 Vlog 是海外七月热点 —— 复古街拍 + 真实人像。建议官号出教育向样片,独立站承接页上「电影感套装」引导。</div><div class="metaline"><div><div class="n mono">49.2%</div><div class="l">发布率</div></div><div><div class="n mono">758</div><div class="l">起草</div></div><div><div class="n mono">$0.001</div><div class="l">AI 花费</div></div></div>';}},
    memo:{title:'备忘录 · 我的计划',cnt:'✎',span:4,tex:0,cls:'memo',body:function(id){return memoHTML(id);}},
    llmq:{title:'LLM 任务队列',cnt:'4 车道',span:4,open:'llmq',body:function(){var lanes=[['交互道','KOL 账号深析 · @josiahlebante14','acc',78,'处理中'],['批量道 A','视频分析 · 24 条','good',62,'处理中'],['批量道 B','产品契合评分 · 18 条','good',94,'处理中'],['批量道 C','空闲 · 等待入队','mut',0,'空闲']];
      return '<div class="llmq">'+lanes.map(function(l){return '<div class="lane"><div class="lane-h"><span class="lane-n">'+l[0]+'</span><span class="lane-s'+(l[3]?'':' idle')+'">'+l[4]+'</span></div><div class="lane-t">'+l[1]+'</div>'+(l[3]?'<div class="lane-bar"><i class="c-'+l[2]+'" style="width:'+l[3]+'%"></i></div><div class="lane-p">'+l[3]+'%</div>':'<div class="lane-bar idle"><i></i></div>')+'</div>';}).join('')
        +'<div class="llmq-foot"><span>今日 <b>1,240</b> 次 · <b>$0.86</b></span><span>预算 $3/日 · <b class="g">已用 29%</b></span></div></div>';}}
  };
  var PALETTE=[['cc','命令中心地图','KOL 地理分布 + 枢纽节点'],['ns','North Star 仪表','增长健康度环'],['trend','趋势曲线','声量 / 曝光走势'],['actions','今日该做什么','行动清单'],['signals','市场信号','竞品情报'],['v6fit','V6 Fit 榜','KOL 榜单'],['kpi','KPI 指标带','六个核心指标'],['aitoday','AI 简报','今日决策摘要'],['llmq','LLM 任务队列','4 车道实时处理进度'],['memo','备忘录','手写计划 + 待办,自动保存'],['blank','空白卡片','占位,之后填内容']];
  // 左侧全部板块 → 可作为小组件加进看板
  var BOARDS={'my-kol':['♡','MY KOL','42','合作中 · 12 待跟进'],'kol-pool':['◎','KOL Pool','331','候选 · 35 新发现'],'projects':['▢','Projects','5','进行中 · 2 本周结'],'events':['▤','Events','3','筹备 · 1 本月'],'shopify':['◈','Shopify','—','待接入订单'],'dealers':['◍','Dealers','18','门店 · 6 州'],'intelligent':['◉','Intelligent 问答','LLM','随手问 · 已就绪'],'replyQueue':['▭','回复队列','7','待回 · 2 高优'],'sku360':['◆','SKU 360°','369','SKU · 全线'],'kolProfile':['☺','KOL 档案','八层','完整档案'],'launchpad':['△','发射台','2','待发 · 新品案'],'autonomy':['⛨','自治驾照','L2','当前档位'],'marketVoice':['◑','市场之声','24','反馈 · 5 待处理'],'creativeLibrary':['▷','创意资产库','段库','可检索'],'strategyBoard':['◎','战略台','4','赛道监控'],'gtmCommand':['✦','GTM Command','就绪','上市指挥图']};
  function boardBody(k){var c=BOARDS[k]||['▦',k,'—','板块'];return '<div class="bw"><div class="bwic">'+c[0]+'</div><div><div class="bwn">'+c[2]+'</div><div class="bwsub">'+c[3]+'</div></div><div class="bwgo">打开 →</div></div>';}

  // ==== 备忘录 ====
  function memoHTML(id){
    var d=LS.get('memo-'+id,{title:'',note:'',items:[{t:'先验证 10-15 个高拟合创作者',done:false},{t:'官号出电影感样片',done:true},{t:'独立站承接页加「电影感套装」',done:false}]});
    var chks=d.items.map(function(it,i){return '<div class="chk'+(it.done?' done':'')+'" data-i="'+i+'"><span class="box"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 6"/></svg></span><span class="tx" contenteditable="true">'+esc(it.t)+'</span><span class="del">✕</span></div>';}).join('');
    return '<div class="mtitle" contenteditable="true" data-mt>'+esc(d.title)+'</div><div class="mnote" contenteditable="true" data-mn>'+esc(d.note)+'</div><div class="chks">'+chks+'</div><div class="addchk"><span class="box">+</span>添加待办</div>';
  }
  function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function bindMemo(mod,id){
    function save(){var items=[].map.call(mod.querySelectorAll('.chk'),function(c){return {t:c.querySelector('.tx').textContent,done:c.classList.contains('done')};});
      LS.set('memo-'+id,{title:mod.querySelector('[data-mt]').textContent,note:mod.querySelector('[data-mn]').textContent,items:items});}
    mod.addEventListener('input',save);
    mod.addEventListener('click',function(e){
      var box=e.target.closest('.chk .box'); if(box){box.parentElement.classList.toggle('done');save();return;}
      var del=e.target.closest('.chk .del'); if(del){del.parentElement.remove();save();return;}
      if(e.target.closest('.addchk')){var wrap=mod.querySelector('.chks');var div=document.createElement('div');div.className='chk';div.dataset.i=Date.now();
        div.innerHTML='<span class="box"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L20 6"/></svg></span><span class="tx" contenteditable="true"></span><span class="del">✕</span>';
        wrap.appendChild(div);div.querySelector('.tx').focus();save();}
    });
  }

  // ==== 渲染看板 ====
  var board=document.getElementById('board');
  var defaultLayout=[{id:'kpi',type:'kpi',span:12},{id:'cc',type:'cc',span:8},{id:'ns',type:'ns',span:4},{id:'actions',type:'actions',span:4},{id:'signals',type:'signals',span:4},{id:'v6fit',type:'v6fit',span:4},{id:'memo1',type:'memo',span:4},{id:'trend',type:'trend',span:8},{id:'aitoday',type:'aitoday',span:4}];
  var layout=LS.get('layout-v8',defaultLayout);
  var uid=1;

  function renderMod(item){
    var def;
    if(item.type==='board'){var c=BOARDS[item.board]||['▦',item.board,'—','板块'];def={title:c[1],span:item.span||4,body:function(){return boardBody(item.board);}};}
    else def=MODS[item.type]||{title:item.title||'空白卡片',span:item.span||4,tex:0,body:function(){return '<div style="color:var(--mut);font-size:12px;padding:14px 0">空白模块 · 可在此放自定义内容</div>';}};
    var el=document.createElement('section');el.className='mod'+(def.cls?' '+def.cls:'');el.style.setProperty('--sp',item.span||def.span);el.dataset.id=item.id;el.dataset.type=item.type;if(item.board)el.dataset.board=item.board;
    var canOpen=item.type!=='memo'&&item.type!=='blank'&&item.type!=='kpi'; if(canOpen)el.dataset.open=item.type;
    var head=(def.title||MODS[item.type]&&MODS[item.type].title)?('<header><h2>'+(def.title||'')+(def.cnt?' <span class="cnt">'+def.cnt+'</span>':'')+'</h2><span class="eyebrow">'+(item.type==='memo'?'Apple 风':'实时')+'</span></header>'):'';
    el.innerHTML=(def.flow?'<div class="flow"></div>':'')+'<div class="modtools"><button class="drag-h" title="拖动">⠿</button><button data-act="size" title="改大小">◧</button><button data-act="del" title="移除">✕</button></div>'+head+'<div class="body">'+def.body(item.id)+'</div>';
    if(item.type==='memo')bindMemo(el,item.id);
    return el;
  }
  function render(){board.innerHTML='';layout.forEach(function(it){board.appendChild(renderMod(it));});
    var add=document.createElement('div');add.className='addmod';add.id='addmod';add.innerHTML='<span class="plus">+</span>添加模块';board.appendChild(add);
    draw();}
  function saveLayout(){layout=[].map.call(board.querySelectorAll('.mod'),function(m){return {id:m.dataset.id,type:m.dataset.type,board:m.dataset.board,span:parseInt(m.style.getPropertyValue('--sp'))||4};});LS.set('layout-v8',layout);}

  // ==== 编辑模式 ====
  var editing=false;
  document.getElementById('editBtn').addEventListener('click',function(){editing=!editing;body.classList.toggle('edit',editing);this.classList.toggle('on',editing);
    [].forEach.call(board.querySelectorAll('.mod'),function(m){m.setAttribute('draggable',editing);});});
  document.getElementById('resetLayout').addEventListener('click',function(){layout=JSON.parse(JSON.stringify(defaultLayout));LS.set('layout-v8',layout);render();[].forEach.call(board.querySelectorAll('.mod'),function(m){m.setAttribute('draggable',editing);});});
  board.addEventListener('click',function(e){
    var seg=e.target.closest('.kpiseg button');if(seg){if(scope!==seg.dataset.sc){scope=seg.dataset.sc;var kmm=board.querySelector('.mod[data-type="kpi"]');if(kmm){kmm.querySelector('.body').innerHTML=MODS.kpi.body();draw();}}return;}
    var b=e.target.closest('.modtools button[data-act]');if(b){var mod=b.closest('.mod');var act=b.dataset.act;
      if(act==='del'){mod.remove();saveLayout();}
      if(act==='size'){var order=[12,8,6,4,3];var cur=parseInt(mod.style.getPropertyValue('--sp'))||4;var ni=(order.indexOf(cur)+1)%order.length;mod.style.setProperty('--sp',order[ni]);saveLayout();draw();}
      return;}
    if(e.target.closest('#addmod')){openPalette();return;}
    if(!editing){var kp=e.target.closest('.kpi[data-metric]');if(kp){openMetric(kp.dataset.metric);return;}
      var om=e.target.closest('.mod[data-open]');
      if(om&&!e.target.closest('button,input,textarea,a,[contenteditable]')){openDetail(om.dataset.open,om.dataset.type);}}
  });
  function openDetail(key,type){var d=DETAIL[key]||DETAIL.generic;var title=(MODS[type]&&MODS[type].title)||'详情';
    document.getElementById('drTitle').textContent=d.title||title;document.getElementById('drSub').textContent=d.sub||'';
    document.getElementById('drBody').innerHTML=d.html(title);
    document.getElementById('scrim').classList.add('on');document.getElementById('drawer').classList.add('on');}
  function closeDetail(){document.getElementById('scrim').classList.remove('on');document.getElementById('drawer').classList.remove('on');}
  document.getElementById('scrim').addEventListener('click',closeDetail);
  document.getElementById('drBody').addEventListener('click',function(e){
    var t=e.target.closest('.mtab');if(t){mScope=t.dataset.sc;renderMetric();return;}
    var f=e.target.closest('.mfbtn');if(f&&!f.disabled){f.textContent=(f.dataset.mf==='csv'?'✓ 已导出(示例)':'✓ 已链入 KOL Pool(示例)');f.disabled=true;}});
  document.getElementById('drClose').addEventListener('click',closeDetail);
  document.getElementById('drClose').title='关闭';
  // ==== Ask AI(mock 流式回答)====
  var askov=document.getElementById('askov'),askin=document.getElementById('askin'),asksugs=document.getElementById('asksugs'),askans=document.getElementById('askans');
  var SUGS=['本周哪些 KOL 值得加码?','85mm F1.4 的精准人群?','电影感 Vlog 怎么拍?给案例','库存偏低的 SKU?','Tamron 17-70 威胁多大?'];
  var ANS_DEFAULT='根据你当前的 <b>KOL Pool(331 位)</b> 与近 7 天数据:\n\n本周建议<b>加码 3 位高拟合创作者</b> —— josiahlebante14、frank_of_all_trades(Fit 95),承接就绪且完播 >65%;<b>暂缓</b> zahidangeless(粉量大但转化偏弱)。\n\n另提醒:<b>VL-LEN076 / 045 库存为 0</b>,加码前先补货,避免空转曝光。';
  function achips(){return '<div class="achips"><span class="achip">→ 打开 KOL Pool</span><span class="achip">→ 查看 VL-LEN076</span><span class="achip">生成外联脚本</span></div>';}
  function typeAns(){askans.classList.add('on');askans.innerHTML='<div class="arole"><span class="d"></span>V-KPI AI · 基于你的真实数据</div><div class="atext"></div>';
    var el=askans.querySelector('.atext'),i=0,html=ANS_DEFAULT;
    if(reduce){el.innerHTML=html.replace(/\n/g,'<br>')+achips();return;}
    (function step(){ if(i<=html.length){ el.innerHTML=html.slice(0,i).replace(/\n/g,'<br>')+'<span class="cursor"></span>'; i+=2; setTimeout(step,11);} else { el.innerHTML=html.replace(/\n/g,'<br>')+achips(); } })();}
  function openAsk(){askov.classList.add('on');askans.classList.remove('on');askans.innerHTML='';askin.value='';
    asksugs.innerHTML=SUGS.map(function(s){return '<span class="asksug">'+s+'</span>';}).join('');setTimeout(function(){askin.focus();},60);}
  function closeAsk(){askov.classList.remove('on');}
  document.getElementById('askOpen').addEventListener('click',openAsk);
  document.getElementById('askClose').addEventListener('click',closeAsk);
  askov.addEventListener('click',function(e){if(e.target===askov)closeAsk();});
  asksugs.addEventListener('click',function(e){var s=e.target.closest('.asksug');if(!s)return;askin.value=s.textContent;typeAns();});
  askin.addEventListener('keydown',function(e){if(e.key==='Enter'&&askin.value.trim())typeAns();});
  document.addEventListener('keydown',function(e){if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openAsk();}if(e.key==='Escape'){closeDetail();closeAsk();}});
  // 拖拽排序
  var dragEl=null;
  board.addEventListener('dragstart',function(e){var m=e.target.closest('.mod');if(!m||!editing)return;dragEl=m;m.classList.add('drag');e.dataTransfer.effectAllowed='move';});
  board.addEventListener('dragend',function(e){if(dragEl){dragEl.classList.remove('drag');}[].forEach.call(board.querySelectorAll('.over'),function(x){x.classList.remove('over');});dragEl=null;saveLayout();});
  board.addEventListener('dragover',function(e){if(!dragEl)return;e.preventDefault();var t=e.target.closest('.mod');if(!t||t===dragEl)return;
    [].forEach.call(board.querySelectorAll('.over'),function(x){x.classList.remove('over');});t.classList.add('over');
    var r=t.getBoundingClientRect();var after=(e.clientX-r.left)>r.width/2;board.insertBefore(dragEl,after?t.nextSibling:t);});

  // ==== 添加模块面板 ====
  var palette=document.getElementById('palette');
  document.getElementById('paletteOpts').innerHTML='<div class="palcap" style="grid-column:1/-1">系统模块</div>'+PALETTE.map(function(p){return '<div class="opt" data-t="'+p[0]+'"><div class="t">'+p[1]+'</div><div class="d">'+p[2]+'</div></div>';}).join('')+'<div class="palcap" style="grid-column:1/-1">板块 · 左侧全部</div>'+Object.keys(BOARDS).map(function(k){var c=BOARDS[k];return '<div class="opt" data-board="'+k+'"><div class="t">'+c[0]+' '+c[1]+'</div><div class="d">'+c[3]+'</div></div>';}).join('');
  function openPalette(){palette.classList.add('on');}
  document.getElementById('palClose').addEventListener('click',function(){palette.classList.remove('on');});
  palette.addEventListener('click',function(e){if(e.target===palette){palette.classList.remove('on');return;}
    var o=e.target.closest('.opt');if(!o)return;var addmod=document.getElementById('addmod');var el;
    if(o.dataset.board){el=renderMod({id:'board-'+o.dataset.board+'-'+Date.now(),type:'board',board:o.dataset.board,span:4});}
    else {var t=o.dataset.t;el=renderMod({id:t+'-'+Date.now(),type:t,span:(MODS[t]&&MODS[t].span)||4});}
    el.setAttribute('draggable',editing);board.insertBefore(el,addmod);palette.classList.remove('on');saveLayout();draw();
  });

  // ==== 主题/风格 ====
  function sync(){var s=root.getAttribute('data-style'),t=root.getAttribute('data-theme');
    document.querySelectorAll('#styleSeg button').forEach(function(b){b.classList.toggle('on',b.dataset.s===s);});
    document.querySelectorAll('#themeSeg button').forEach(function(b){b.classList.toggle('on',b.dataset.t===t);});}
  document.getElementById('styleSeg').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;root.setAttribute('data-style',b.dataset.s);sync();draw();});
  document.getElementById('themeSeg').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;root.setAttribute('data-theme',b.dataset.t);sync();draw();});
  sync();
  // ==== 视角切换(全部/个人/公司)——重渲染 KPI 带 ====
  // 视角切换(全部 / KOL / 公司账号)已移入「增长总览」卡头,见 board 点击委托

  // ==== 图形绘制 ====
  function nglow(c){var g=cur('--nglow');return g&&g!=='0px'?'filter:drop-shadow(0 0 '+g+' '+c+')':'';}
  function sparks(){document.querySelectorAll('svg.sp').forEach(function(s){
    var d=s.dataset.d.split(',').map(Number),c=cur('--'+s.dataset.c),mn=Math.min.apply(0,d),mx=Math.max.apply(0,d),rg=(mx-mn)||1,W=240,H=30,p=4;
    var pts=d.map(function(v,i){return [i/(d.length-1)*W,H-p-(v-mn)/rg*(H-2*p)];});
    var ln='M'+pts.map(function(q){return q[0].toFixed(1)+','+q[1].toFixed(1);}).join(' L'),ar=ln+' L'+W+','+H+' L0,'+H+' Z',id='s'+s.dataset.c+Math.random().toString(36).slice(2,6);
    s.innerHTML='<defs><linearGradient id="'+id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+c+'" stop-opacity=".3"/><stop offset="1" stop-color="'+c+'" stop-opacity="0"/></linearGradient></defs><path d="'+ar+'" fill="url(#'+id+')"/><path d="'+ln+'" fill="none" stroke="'+c+'" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/><circle cx="'+pts[pts.length-1][0].toFixed(1)+'" cy="'+pts[pts.length-1][1].toFixed(1)+'" r="2.8" fill="'+c+'" vector-effect="non-scaling-stroke" style="filter:drop-shadow(0 0 3px '+c+')"/>';
  });}
  function constellation(){var svg=document.getElementById('ccsvg');if(!svg)return;
    var nodes=[[150,145,38,"NY"],[110,200,28,"SF"],[500,200,20,"MIA"],[300,135,17,"CHI"],[95,195,17,""],[420,250,16,"ATL"],[250,105,10,""],[540,145,9,""],[360,285,8,""],[210,245,7,""],[600,115,6,""],[180,115,5,""]];
    var acc=cur('--acc'),links="";
    for(var i=0;i<nodes.length;i++)for(var j=i+1;j<nodes.length;j++){var dx=nodes[i][0]-nodes[j][0],dy=nodes[i][1]-nodes[j][1];if(dx*dx+dy*dy<20000)links+='<line class="link" x1="'+nodes[i][0]+'" y1="'+nodes[i][1]+'" x2="'+nodes[j][0]+'" y2="'+nodes[j][1]+'"/>';}
    var dots=nodes.map(function(n){var big=n[2]>=16,r=Math.max(2.4,Math.min(6,n[2]/6));
      return (big&&!reduce?'<circle class="node-glow" cx="'+n[0]+'" cy="'+n[1]+'" r="'+r+'" style="transform-origin:'+n[0]+'px '+n[1]+'px"/>':'')+'<circle class="node" cx="'+n[0]+'" cy="'+n[1]+'" r="'+r+'" style="'+nglow(acc)+'"/>'+(n[3]?'<text class="lbl" x="'+(n[0]+r+4)+'" y="'+(n[1]+3)+'">'+n[3]+'</text>':'');}).join('');
    svg.innerHTML=links+dots;}
  function nsgauge(){var lit=cur('--nglow')&&cur('--nglow')!=='0px';
    [].forEach.call(document.querySelectorAll('.nsarc'),function(arc){var C=326.7,pct=0.78;
      if(reduce){arc.style.transition='none';arc.style.strokeDashoffset=(C*(1-pct)).toFixed(1);}else{arc.style.strokeDashoffset=C;requestAnimationFrame(function(){requestAnimationFrame(function(){arc.style.strokeDashoffset=(C*(1-pct)).toFixed(1);});});}
      arc.style.filter=lit?'drop-shadow(0 0 '+cur('--nglow')+' '+cur('--acc')+')':'none';});
    [].forEach.call(document.querySelectorAll('.nscomet'),function(c){c.style.opacity=lit?'0.95':'0.6';c.style.filter=lit?'drop-shadow(0 0 7px '+cur('--acc')+')':'none';});}
  // 流动曲线波(替代方格)
  function wave(color,op,w,ph,cls){var W=800,H=120,p=[];for(var x=0;x<=W;x+=10){var y=H*0.52+Math.sin(x/200*6.2832+ph)*20+Math.sin(x/100*6.2832)*8;p.push(x+','+y.toFixed(1));}
    return '<svg class="'+cls+'" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><path d="M'+p.join(' L')+'" fill="none" stroke="'+color+'" stroke-width="'+w+'" opacity="'+op+'" vector-effect="non-scaling-stroke" style="'+nglow(color)+'"/></svg>';}
  function flows(){[].forEach.call(document.querySelectorAll('.flow'),function(f){f.innerHTML=wave(cur('--acc'),0.26,1.6,0,'a')+wave(cur('--acc2'),0.17,1.3,1.7,'b');});}
  function trend(){[].forEach.call(document.querySelectorAll('.trendsvg'),function(svg){
    var expo=[1.4,1.5,1.55,1.7,1.78,1.9,1.98,2.05],sov=[0.22,0.24,0.26,0.29,0.30,0.31,0.315,0.317];
    var W=640,H=176,pl=8,pr=8,pt=12,pb=16,iW=W-pl-pr,iH=H-pt-pb,acc=cur('--acc'),good=cur('--good'),line=cur('--grid');
    function ser(d,color,id,fill){var mn=Math.min.apply(0,d),mx=Math.max.apply(0,d),rg=(mx-mn)||1;
      var pts=d.map(function(v,i){return [pl+i/(d.length-1)*iW,pt+(1-(v-mn)/rg)*iH];});
      var ln='M'+pts.map(function(q){return q[0].toFixed(1)+','+q[1].toFixed(1);}).join(' L');
      var g=fill?'<defs><linearGradient id="'+id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+color+'" stop-opacity=".22"/><stop offset="1" stop-color="'+color+'" stop-opacity="0"/></linearGradient></defs><path d="'+ln+' L'+pts[pts.length-1][0].toFixed(1)+','+H+' L'+pl+','+H+' Z" fill="url(#'+id+')"/>':'';
      return g+'<path d="'+ln+'" fill="none" stroke="'+color+'" stroke-width="2.2" stroke-linejoin="round" vector-effect="non-scaling-stroke" style="'+nglow(color)+'"/><circle cx="'+pts[pts.length-1][0].toFixed(1)+'" cy="'+pts[pts.length-1][1].toFixed(1)+'" r="3" fill="'+color+'" vector-effect="non-scaling-stroke"/>';}
    var grid="";for(var i=1;i<4;i++){var y=pt+i/4*iH;grid+='<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="'+line+'" stroke-width="1" vector-effect="non-scaling-stroke"/>';}
    svg.innerHTML=grid+ser(expo,acc,'te'+Math.random().toString(36).slice(2,5),true)+ser(sov,good,'ts'+Math.random().toString(36).slice(2,5),false);});}
  function draw(){sparks();constellation();nsgauge();trend();flows();}

  render();
}
