// SmartKolInputPanel 文字搜索结果区(框1 产品人群分析 / 框2 库内召回 / 框3 全网发现)展示型子组件。
// 从 SmartKolInputPanel.tsx 抽出,行为不变:JSX 逐字保留,容器本体 + 全部 hooks 仍留 SmartKolInputPanel.tsx,
// 这里只是「仅吃 props」的展示组件(无自身 hooks),容器把 state/派生值/回调透传进来,调用点不变。
// 红线:纯展示,绝不写任何 viltrox_fit_score。
import { FolderPlus, Info, Loader2, MessageSquare, RefreshCw, Sparkles, UserPlus } from "lucide-react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import { cleanText, display, type Row } from "./SmartKolInputPanel.helpers";
import { PlanPills, RecallMiniItem } from "./SmartKolInputPanel.Sections";
import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import { ProgressiveSearchStageCard } from "./SmartKolInputPanel.Progress";

type SessionBanner = {
  tone: string;
  label: string;
  note: string;
} | null;

export function TextResultSection({
  recallResult,
  llmPlan,
  recallItems,
  discoveryItems,
  discoveryTotal = 0,
  discoveryAutoEnrolled = null,
  reachFloorDisplay = null,
  input,
  apiToken,
  isBusy,
  state,
  plannerFellBack,
  personaEditing,
  personaDraft,
  setPersonaEditing,
  setPersonaDraft,
  setInput,
  run,
  discoveryPlatforms,
  setDiscoveryPlatforms,
  discoveryRegion,
  setDiscoveryRegion,
  excludeChinese,
  setExcludeChinese,
  queueTextAdvance,
  pickedIds,
  setPickedIds,
  favNote,
  draftNote,
  outreachNote,
  outreachResult,
  addingFav,
  draftBusy,
  outreachBusy,
  activeSearchSessionId,
  addPickedToMyKol,
  approveAndCreateDraft,
  generateOutreachForPicked,
  resolvedPids,
  resolvingKeys,
  discoveryKey,
  pickDiscovery,
  onOpenRecallItem,
  sessionBanner,
  sessionProgress,
  activeSessionCounts,
  sessionPollNotice,
  retrySearchSession,
}: {
  recallResult: VkpiKolRecallResponse;
  llmPlan: Row;
  recallItems: VkpiKolRecallItem[];
  discoveryItems: any[];
  discoveryTotal?: number;
  discoveryAutoEnrolled?: number | null;
  /** 触达展示闸折叠计数(2026-07-12「分析后再 po」):lowReach=低触达不展示(已入库仅不推荐)、
   *  analyzing=档案补全中,达标后自动放出;旧后端/无隐藏 → null 不渲染。 */
  reachFloorDisplay?: {
    discovery: { lowReach: number; analyzing: number };
    recall: { lowReach: number; analyzing: number };
  } | null;
  input: string;
  apiToken: string;
  isBusy: boolean;
  state: string;
  plannerFellBack: boolean;
  personaEditing: boolean;
  personaDraft: string;
  setPersonaEditing: (v: boolean) => void;
  setPersonaDraft: (v: string) => void;
  setInput: (v: string) => void;
  run: (overrideQuery?: string) => void;
  discoveryPlatforms: string[];
  setDiscoveryPlatforms: (updater: (cur: string[]) => string[]) => void;
  discoveryRegion: string;
  setDiscoveryRegion: (v: string) => void;
  excludeChinese: boolean;
  setExcludeChinese: (v: boolean) => void;
  queueTextAdvance: (overrideQuery?: string) => void;
  pickedIds: Set<number>;
  setPickedIds: (v: Set<number>) => void;
  favNote: string;
  draftNote: string;
  outreachNote: string;
  outreachResult: Record<string, any> | null;
  addingFav: boolean;
  draftBusy: boolean;
  outreachBusy: boolean;
  activeSearchSessionId: number | null;
  addPickedToMyKol: () => void;
  approveAndCreateDraft: () => void;
  generateOutreachForPicked: () => void;
  resolvedPids: Map<string, number>;
  resolvingKeys: Set<string>;
  discoveryKey: (item: any) => string;
  pickDiscovery: (item: any) => void;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  sessionBanner: SessionBanner;
  sessionProgress: SearchSessionProgress;
  activeSessionCounts: Record<string, any>;
  sessionPollNotice: string;
  retrySearchSession: () => void;
}) {
  // 发现真总数 = 可见 + 被触达闸折叠(分析中/低触达):K3 入库反馈按真总数说话,
  // 否则「发现 3 人、入库 15 人」自相矛盾(隐藏项也都入了库)。纯派生,无 hooks。
  const hiddenDiscovery = reachFloorDisplay
    ? (reachFloorDisplay.discovery.analyzing || 0) + (reachFloorDisplay.discovery.lowReach || 0)
    : 0;
  const discoveryGrandTotal = discoveryTotal + hiddenDiscovery;
  return (
    <div className="mt-3 space-y-2.5">
      <ProgressiveSearchStageCard progress={sessionProgress} />

      {/* 框1 · 产品人群分析(可编辑,防 LLM 理解偏) */}
      <div className="rounded-lg border border-cyan-300/15 bg-cyan-400/[0.04] p-3">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <div className="text-[11px] font-medium text-cyan-100">① 要找什么样的人</div>
          {!personaEditing ? (
            <button
              type="button"
              onClick={() => { setPersonaDraft(display(llmPlan.search_query, cleanText(input))); setPersonaEditing(true); }}
              className="rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-white/[0.2] hover:text-white"
            >编辑</button>
          ) : null}
        </div>
        {plannerFellBack ? (
          <div className="mb-1.5 flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.07] px-2 py-1.5 text-[10px] leading-relaxed text-amber-100/90">
            <Info size={11} className="mt-0.5 shrink-0" />
            <span>AI 规划暂不可用,已用基础检索匹配。下方结果可正常查看,稍后可重试以获得更精准的人群理解。</span>
          </div>
        ) : null}
        {personaEditing ? (
          <div className="space-y-1.5">
            <textarea
              value={personaDraft}
              onChange={(event) => setPersonaDraft(event.target.value)}
              rows={2}
              className="w-full resize-none rounded-md border border-white/[0.1] bg-black/30 px-2 py-1.5 text-[11px] text-white placeholder-slate-600 focus:border-cyan-400/40 focus:outline-none"
              placeholder="描述要找什么样的人，例如:35mm 低光人像 YouTube 摄影师…"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!cleanText(personaDraft) || isBusy}
                onClick={() => { const q = cleanText(personaDraft); setInput(q); setPersonaEditing(false); void run(q); }}
                className="rounded-md border border-cyan-300/25 bg-cyan-500/[0.14] px-2.5 py-1 text-[10px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-50"
              >用此重搜</button>
              <button type="button" onClick={() => setPersonaEditing(false)} className="text-[10px] text-slate-500 hover:text-slate-300">取消</button>
            </div>
          </div>
        ) : Object.keys(llmPlan).length ? (
          <PlanPills plan={llmPlan} />
        ) : (
          <div className="text-[10px] text-slate-500">点「编辑」改写要找的人群，再「用此重搜」。</div>
        )}
      </div>

      {/* 框2 · 库内账号匹配 */}
      <div className="rounded-lg border border-violet-300/15 bg-violet-950/[0.10] p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="text-[11px] font-medium text-violet-100">② 库内已有的人 · {display(recallResult.diagnostics?.candidate_count)} 个</div>
          <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-500">
            <span className="rounded-md border border-white/[0.07] px-2 py-1">创作者 {display(recallResult.diagnostics?.creator_returned)}</span>
            <span className="rounded-md border border-white/[0.07] px-2 py-1">测评号 {display(recallResult.diagnostics?.reviewer_returned)}</span>
          </div>
        </div>
        {recallItems.length ? (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {recallItems.map((item, index) => (
              <RecallMiniItem key={`r-${item.bucket}-${item.kol_pool_id || item.handle || index}`} item={item} index={index + 1} onOpen={onOpenRecallItem} />
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">暂无库内匹配</div>
        )}
        {/* 触达展示闸折叠行(诚实信息,别删):被隐藏的候选不摆行,只报计数。 */}
        {reachFloorDisplay && (reachFloorDisplay.recall.analyzing > 0 || reachFloorDisplay.recall.lowReach > 0) ? (
          <div
            className="mt-2 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
            title="低触达=粉丝数低于门槛,已入库仅不推荐;分析中=粉丝数待档案补全,达标后自动出现在列表"
          >
            {[
              reachFloorDisplay.recall.analyzing > 0 ? `分析中 ×${reachFloorDisplay.recall.analyzing}(补全后自动放出)` : "",
              reachFloorDisplay.recall.lowReach > 0 ? `低触达不展示 ×${reachFloorDisplay.recall.lowReach}` : "",
            ].filter(Boolean).join(" · ")}
          </div>
        ) : null}
      </div>

      {/* 框3 · 全网发现(Apify+平台,带头像)· 优先新人主源,描边更亮 */}
      <div className="rounded-lg border border-emerald-300/30 bg-emerald-950/[0.16] p-3 ring-1 ring-emerald-300/10">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/30 bg-emerald-400/[0.12] px-1.5 py-0.5 text-[8.5px] font-semibold text-emerald-100">
              <UserPlus size={9} /> 优先新人
            </span>
            <div className="text-[11px] font-semibold text-emerald-100">③ 全网新发现的人{discoveryItems.length ? ` · ${discoveryItems.length} 个` : ""}</div>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/25 bg-emerald-400/[0.1] px-1.5 py-0.5 text-[9px] font-medium text-emerald-200/90" title="任何文字搜索都自动从所选平台发现新号,无需手点">
            <Sparkles size={9} /> 自动·恒开
          </span>
        </div>
        {/* 【K3 正账】入库反馈:发现即自动落 Pool(后端 _auto_enroll_discoveries)。后端现把真实入库数
            记进 result_summary.new_discovery.counts.auto_enrolled(仅计本次成功 upsert 的新行;
            已在库/缺 handle/入库失败的不计)→ 有真数就显示真数;旧会话无该键则回退到概述文案,不编数字。 */}
        {discoveryGrandTotal > 0 ? (
          <div
            className="mb-2 rounded-md border border-emerald-300/20 bg-emerald-400/[0.06] px-2.5 py-1.5 text-[10px] text-emerald-100/90"
            title="全网新发现会即时轻量入库(仅基础资料,不触评分);已在库/缺 handle/入库失败的项不计入入库数;含下方「分析中/低触达」折叠项"
          >
            {typeof discoveryAutoEnrolled === "number"
              ? `本次全网新发现 ${discoveryGrandTotal} 人,其中 ${discoveryAutoEnrolled} 人已自动入库(其余已在库或入库失败)· 下次同类搜索归「库内已有的人」`
              : `本次全网新发现 ${discoveryGrandTotal} 人,已自动登记入库(个别缺 handle/入库失败的除外)· 下次同类搜索归「库内已有的人」`}
          </div>
        ) : null}
        {/* 触达展示闸折叠行(2026-07-12「分析后再 po」):粉丝数未知的发现项已入库并自动补全,
            补全达标后自动出现在下方;低触达项不摆行,只报诚实计数。 */}
        {reachFloorDisplay && (reachFloorDisplay.discovery.analyzing > 0 || reachFloorDisplay.discovery.lowReach > 0) ? (
          <div
            className="mb-2 flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
            title="分析中=粉丝数待档案补全(已自动入库并排队补全),达标后自动出现在列表;低触达=粉丝数低于门槛,已入库仅不推荐"
          >
            {reachFloorDisplay.discovery.analyzing > 0 ? <Loader2 size={10} className="animate-spin text-emerald-200/70" /> : null}
            {[
              reachFloorDisplay.discovery.analyzing > 0 ? `分析中 ×${reachFloorDisplay.discovery.analyzing}(档案补全后达标自动放出)` : "",
              reachFloorDisplay.discovery.lowReach > 0 ? `低触达不展示 ×${reachFloorDisplay.discovery.lowReach}` : "",
            ].filter(Boolean).join(" · ")}
          </div>
        ) : null}
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-slate-500">发现平台</span>
          {/* 【B5】Facebook 解锁为可选平台:后端 SUPPORTED_DISCOVERY_PLATFORMS 已含 facebook
              (apify/facebook-search-scraper,discovery_filters.py);opt-in 设计——不进默认三平台兜底,
              显式勾选后请求 new_discovery_platforms 数组才带 "facebook"。 */}
          {([
            { k: "youtube", t: "YouTube" },
            { k: "instagram", t: "Instagram" },
            { k: "tiktok", t: "TikTok" },
            { k: "facebook", t: "Facebook", tip: "Facebook 发现(可选):勾选后下次查找才参与,不勾选不搜(不进默认平台轮转)" },
          ] as { k: string; t: string; tip?: string }[]).map((p) => {
            const on = discoveryPlatforms.includes(p.k);
            return (
              <button
                key={p.k}
                type="button"
                title={p.tip}
                onClick={() => setDiscoveryPlatforms((cur) => (on ? cur.filter((x) => x !== p.k) : [...cur, p.k]))}
                className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${on ? "border-cyan-300/40 bg-cyan-400/[0.12] text-cyan-100" : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"}`}
              >{p.t}</button>
            );
          })}
          <span className="ml-1 text-[10px] text-slate-500">区域</span>
          <select
            value={discoveryRegion}
            onChange={(event) => setDiscoveryRegion(event.target.value)}
            title="目标市场:选非英语区会按该区语言搜平台、捞本地达人(改区域后点「重新全网查找」重搜生效)"
            className="rounded-md border border-white/[0.1] bg-black/30 px-1.5 py-0.5 text-[10px] text-slate-200 focus:border-cyan-400/40 focus:outline-none"
          >
            {[
              { v: "", t: "全球·英文" },
              { v: "JP", t: "日本·日语" },
              { v: "KR", t: "韩国·韩语" },
              { v: "DE", t: "德国·德语" },
              { v: "FR", t: "法国·法语" },
              { v: "ES", t: "西班牙·西语" },
              { v: "IT", t: "意大利·意语" },
              { v: "BR", t: "巴西·葡语" },
              { v: "RU", t: "俄罗斯·俄语" },
              { v: "TH", t: "泰国·泰语" },
              { v: "VN", t: "越南·越语" },
              { v: "ID", t: "印尼·印尼语" },
            ].map((o) => (
              <option key={o.v} value={o.v} className="bg-slate-900 text-slate-100">{o.t}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-[10px] text-slate-400" title="排除 中国大陆/香港/台湾 地区(按 country/market 地区判据,海外中文博主放行)">
            <input type="checkbox" checked={excludeChinese} onChange={(event) => setExcludeChinese(event.target.checked)} className="accent-emerald-500" />
            排除 中国/港/台 地区
          </label>
          <button
            type="button"
            onClick={() => void queueTextAdvance()}
            disabled={state === "executing" || !apiToken || !cleanText(input)}
            className="ml-auto inline-flex min-h-[28px] items-center justify-center gap-1.5 rounded-md border border-emerald-300/18 bg-emerald-500/[0.12] px-2.5 text-[10px] font-medium text-emerald-100 transition-colors hover:bg-emerald-500/[0.20] disabled:cursor-not-allowed disabled:opacity-55"
          >
            {state === "executing" ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            重新全网查找
          </button>
        </div>
        {pickedIds.size > 0 || favNote || draftNote || outreachNote ? (
          <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-emerald-300/25 bg-emerald-400/[0.08] px-2.5 py-1.5">
            <div className="flex flex-wrap items-center gap-2">
              {pickedIds.size > 0 ? (
                <>
                  <span className="text-[10.5px] font-medium text-emerald-100">已选 {pickedIds.size} 人</span>
                  <button
                    type="button"
                    onClick={() => void addPickedToMyKol()}
                    disabled={addingFav || !apiToken}
                    className="inline-flex items-center gap-1 rounded border border-emerald-300/35 bg-emerald-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-emerald-50 transition-colors hover:bg-emerald-500/[0.32] disabled:opacity-50"
                  >
                    {addingFav ? <Loader2 size={11} className="animate-spin" /> : <UserPlus size={11} />} 加入我的 MY KOL
                  </button>
                  {/* R4:批准锁定 → 一键建项目草案(草案带成本估算 + 风险) */}
                  <button
                    type="button"
                    onClick={() => void approveAndCreateDraft()}
                    disabled={draftBusy || !apiToken || !activeSearchSessionId}
                    title={activeSearchSessionId ? "批准选中候选并据此建项目草案(带预算/风险)" : "需先有搜索会话"}
                    className="inline-flex items-center gap-1 rounded border border-sky-300/35 bg-sky-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-sky-50 transition-colors hover:bg-sky-500/[0.32] disabled:opacity-50"
                  >
                    {draftBusy ? <Loader2 size={11} className="animate-spin" /> : <FolderPlus size={11} />} 批准并建草案
                  </button>
                  {/* R4:为选中候选生成合作话术 + SOW 草案(LLM·预算闸·仅草案) */}
                  <button
                    type="button"
                    onClick={() => void generateOutreachForPicked()}
                    disabled={outreachBusy || !apiToken || !activeSearchSessionId}
                    title={activeSearchSessionId ? "为选中候选生成合作话术 + SOW 草案(人审后手动外发)" : "需先有搜索会话"}
                    className="inline-flex items-center gap-1 rounded border border-violet-300/35 bg-violet-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-violet-50 transition-colors hover:bg-violet-500/[0.32] disabled:opacity-50"
                  >
                    {outreachBusy ? <Loader2 size={11} className="animate-spin" /> : <MessageSquare size={11} />} 生成话术
                  </button>
                  <button type="button" onClick={() => setPickedIds(new Set())} className="text-[10px] text-slate-400 hover:text-slate-200">清空</button>
                </>
              ) : null}
              {favNote ? <span className="text-[10px] text-emerald-200/85">{favNote}</span> : null}
            </div>
            {draftNote ? <span className="text-[10px] text-sky-100/90">{draftNote}</span> : null}
            {outreachNote ? <span className="text-[10px] text-violet-100/90">{outreachNote}</span> : null}
            {outreachResult && Array.isArray(outreachResult.messages) && outreachResult.messages.length ? (
              <details className="mt-0.5 rounded border border-violet-300/20 bg-black/20 px-2 py-1">
                <summary className="cursor-pointer text-[10px] text-violet-100/90">查看话术草案({outreachResult.messages.length} 封)· 人审后手动外发</summary>
                <div className="mt-1 flex max-h-56 flex-col gap-1.5 overflow-y-auto">
                  {outreachResult.messages.map((m: any, i: number) => (
                    <div key={`om-${m.kol_pool_id || i}`} className="rounded border border-white/[0.06] bg-white/[0.02] px-2 py-1">
                      <div className="text-[10px] font-medium text-violet-50">
                        {m.display_name || m.handle || `KOL #${m.kol_pool_id || i + 1}`}
                        {m.personalized === false ? <span className="ml-1 text-[8px] text-slate-400">· 模板</span> : null}
                      </div>
                      {m.subject ? <div className="text-[9.5px] text-slate-300">主题:{m.subject}</div> : null}
                      <div className="whitespace-pre-line text-[9.5px] leading-relaxed text-slate-200/90">{m.body}</div>
                    </div>
                  ))}
                  {outreachResult.sow_draft && outreachResult.sow_draft.scope ? (
                    <div className="rounded border border-violet-300/20 bg-violet-500/[0.06] px-2 py-1 text-[9.5px] text-violet-100/90">
                      <div className="font-medium">SOW 草案</div>
                      <div className="opacity-90">范围:{outreachResult.sow_draft.scope}</div>
                      {Array.isArray(outreachResult.sow_draft.deliverables) && outreachResult.sow_draft.deliverables.length ? (
                        <div className="opacity-90">交付:{outreachResult.sow_draft.deliverables.join(" · ")}</div>
                      ) : null}
                      <div className="opacity-75">报酬:{outreachResult.sow_draft.compensation || "待人工确定(不承诺价格)"}</div>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : null}
          </div>
        ) : null}
        {discoveryItems.length ? (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {discoveryItems.map((item, index) => {
              const key = discoveryKey(item);
              const effPid = Number(item.kol_pool_id) || resolvedPids.get(key) || 0;
              const picked = effPid > 0 && pickedIds.has(effPid);
              const resolving = resolvingKeys.has(key);
              return (
                <div key={`d-${item.kol_pool_id || item.handle || index}`} className="relative h-full">
                  <RecallMiniItem item={item} index={index + 1} onOpen={onOpenRecallItem} className="pr-6" />
                  <button
                    type="button"
                    disabled={resolving}
                    onClick={(event) => { event.stopPropagation(); void pickDiscovery(item); }}
                    title={picked ? "已选 · 点击取消" : "勾选 → 一键加入我的 MY KOL"}
                    className={`absolute right-1 top-1 z-10 flex h-5 w-5 items-center justify-center rounded border text-[10px] font-bold leading-none transition-colors ${picked ? "border-emerald-300/60 bg-emerald-500/90 text-white" : "border-white/25 bg-black/55 text-transparent hover:border-emerald-300/45 hover:text-emerald-200/60"}`}
                  >{resolving ? <Loader2 size={11} className="animate-spin text-emerald-200" /> : "✓"}</button>
                </div>
              );
            })}
          </div>
        ) : activeSearchSessionId ? (
          <div className="flex items-center gap-1.5 rounded-md border border-emerald-300/15 bg-black/15 px-2.5 py-2 text-[10.5px] text-emerald-100/80">
            <Loader2 size={12} className="animate-spin" /> 正在从所选平台找新号，完成后自动显示
          </div>
        ) : sessionBanner && (sessionBanner.tone === "error" || sessionBanner.tone === "warn") ? (
          // 失败/部分但无发现项:不再静默落空白占位,直接说明状态与原因(诚实兜底)。
          <div className={`rounded-md border px-3 py-2.5 text-[10.5px] leading-relaxed ${
            sessionBanner.tone === "error"
              ? "border-rose-300/20 bg-rose-500/[0.08] text-rose-100"
              : "border-amber-300/20 bg-amber-400/[0.08] text-amber-100"
          }`}>
            <div className="font-medium">{sessionBanner.label}</div>
            <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
            {/* 失败/未完成 → 「重试」(重新入队该搜索,续接轮询回填 ①②③) */}
            <button
              type="button"
              onClick={() => void retrySearchSession()}
              disabled={state === "executing" || !apiToken || !cleanText(input)}
              className={`mt-1.5 inline-flex min-h-[26px] items-center justify-center gap-1.5 rounded-md border px-2.5 text-[10px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${
                sessionBanner.tone === "error"
                  ? "border-rose-300/30 bg-rose-500/[0.14] text-rose-100 hover:bg-rose-500/[0.22]"
                  : "border-amber-300/30 bg-amber-500/[0.14] text-amber-100 hover:bg-amber-500/[0.22]"
              }`}
            >
              {state === "executing" ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              重试
            </button>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-3 text-center text-[10.5px] text-slate-500">全网发现恒开 · 搜索后自动从所选平台发现新号</div>
        )}
        {sessionBanner ? (
          // 诚实会话横幅:排队/查找中/已完成/部分完成/未完成 + 真原因;部分/已完成仍保留计数。
          <div className={`mt-2 rounded-md border px-2.5 py-2 text-[10px] leading-relaxed ${
            sessionBanner.tone === "error"
              ? "border-rose-300/20 bg-rose-500/[0.07] text-rose-100"
              : sessionBanner.tone === "warn"
                ? "border-amber-300/20 bg-amber-400/[0.07] text-amber-100"
                : sessionBanner.tone === "ok"
                  ? "border-emerald-300/20 bg-emerald-400/[0.07] text-emerald-100"
                  : "border-emerald-300/15 bg-black/15 text-emerald-100/75"
          }`}>
            <div className="flex flex-wrap items-center gap-1.5">
              {sessionBanner.tone === "info" ? <Loader2 size={11} className="animate-spin" /> : null}
              <span className="font-medium">{sessionBanner.label}</span>
              {Object.keys(activeSessionCounts).length ? (
                <>
                  <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已找到 {display(activeSessionCounts.ready, "0")}</span>
                  <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已入库 {display(activeSessionCounts.executed, "0")}</span>
                  {Number(activeSessionCounts.errors) > 0 || Number(activeSessionCounts.failed) > 0 ? (
                    <span className="rounded border border-rose-300/20 bg-black/15 px-1.5 py-0.5 text-rose-200/80">未完成 {display(Number(activeSessionCounts.errors || 0) + Number(activeSessionCounts.failed || 0), "0")}</span>
                  ) : null}
                </>
              ) : null}
            </div>
            <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
            {sessionPollNotice ? <div className="mt-0.5 opacity-70">{sessionPollNotice}</div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
