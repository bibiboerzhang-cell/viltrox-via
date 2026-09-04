import { BadgeCheck, CheckCircle2, Loader2, Search, Video } from "lucide-react";
import { cleanText } from "./SmartKolInputPanel.helpers";
import { HistoryStrip, UrlSummary } from "./SmartKolInputPanel.Sections";
import { TextResultSection } from "./SmartKolInputPanel.TextResult";
import { SmartKolSearchEntry } from "./SmartKolInputPanel.Entry";
import { KolSearchPolicyPanel } from "./SmartKolInputPanel.SearchPolicy";
import { KolPoolSkeletonRows } from "../../panels/KolPoolPanel.listParts";
import { type SmartKolInputPanelProps } from "./SmartKolInputPanel.runtime";
import { useSmartKolInputPanelController } from "./SmartKolInputPanel.controller";

// 等待本地结果的占位行。复用达人库列表已有的骨架行(同一种「一行一个人」的形状),
// 只补一层暗面板容器 + 压低不透明度,让这套为浅色表格调过的骨架落在深色面板上不刺眼。
function RecallLoadingSkeleton() {
  return (
    <div
      data-testid="smart-kol-recall-skeleton"
      aria-hidden="true"
      className="mt-3 overflow-hidden rounded-lg border border-white/[0.06] bg-black/20 px-2 py-1.5"
    >
      <table className="w-full table-fixed opacity-[0.16]">
        <tbody><KolPoolSkeletonRows /></tbody>
      </table>
    </div>
  );
}

function SmartKolInputPanelForAccount(props: SmartKolInputPanelProps) {
  const vm = useSmartKolInputPanelController(props);

  return (
    <section
      data-testid="smart-kol-input-panel"
      className="rounded-lg border border-white/[0.065] bg-black/[0.14] p-2.5"
    >
      <SmartKolSearchEntry
        value={vm.input}
        inferredMode={vm.inferredMode}
        busy={vm.isBusy}
        disabled={vm.isBusy || !vm.apiToken || !cleanText(vm.input)}
        onInputChange={vm.setInput}
        onRun={vm.runCurrentInput}
      />

      <KolSearchPolicyPanel
        open={vm.searchFiltersOpen}
        onToggleOpen={() => vm.setSearchFiltersOpen((open) => !open)}
        objective={vm.searchObjective}
        onObjectiveChange={vm.setSearchObjective}
        strategy={vm.searchStrategy}
        onStrategyChange={vm.setSearchStrategy}
        platforms={vm.discoveryPlatforms}
        onPlatformsChange={vm.setDiscoveryPlatforms}
        languages={vm.contentLanguages}
        onLanguagesChange={vm.setContentLanguages}
        filters={vm.searchFilters}
        onFiltersChange={vm.setSearchFilters}
        autoRelax={vm.autoRelax.view}
        autoRelaxBusy={vm.isBusy}
        autoRelaxRemovedKeys={vm.autoRelax.droppedKeys}
        onAutoRelaxRestore={() => { vm.autoRelax.toggleOptOut(); vm.runCurrentInput(); }}
        onAutoRelaxRemoveAdded={(key) => { vm.autoRelax.removeAdded(key); vm.runCurrentInput(); }}
      />

      {vm.batchNote ? (
        <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-400/[0.06] px-2.5 py-1.5 text-[10px] text-cyan-100">
          {vm.batchBusy ? <Loader2 size={11} className="animate-spin" /> : null}
          <span>{vm.batchNote}</span>
          {!vm.batchBusy ? (
            <button type="button" onClick={() => vm.setBatchNote("")} className="ml-auto text-slate-500 hover:text-slate-300">收起</button>
          ) : null}
        </div>
      ) : null}

      {vm.state === "idle" && !vm.input ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9.5px] text-slate-600">
          <span className="inline-flex items-center gap-1 text-cyan-100"><Video size={9} /> 视频 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-violet-100"><BadgeCheck size={9} /> 账号 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-emerald-100"><Search size={9} /> 产品需求</span>
        </div>
      ) : null}

      <HistoryStrip
        items={vm.historyItems}
        archivedItems={vm.archivedHistoryItems}
        loading={vm.historyLoading}
        actionBusy={vm.historyActionBusy}
        notice={vm.historyNotice}
        onOpen={(session) => void vm.openHistorySession(session)}
        onArchive={(session) => void vm.archiveHistoryEntry(session)}
        onRestore={(session) => void vm.restoreHistoryEntry(session)}
        onArchiveAll={() => void vm.archiveCompletedHistory()}
      />

      {vm.error ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-200">{vm.error}</div>
      ) : null}

      {vm.showRecallSkeleton ? <RecallLoadingSkeleton /> : null}

      {/* 完成信号:本地那条腿一交出结果就在结果区抬头说清楚——数字只报「已经拿到手的人数」,
          后台补充只说在跑,不报进度百分比,也不预告最终会有几人。 */}
      {!vm.showRecallSkeleton && vm.mode === "text" && vm.recallResult && !vm.recallIsStale && vm.recallCount > 0 ? (
        <div
          data-testid="smart-kol-recall-ready"
          className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-emerald-300/20 bg-emerald-400/[0.07] px-2.5 py-1.5 text-[10.5px] text-emerald-100"
        >
          <CheckCircle2 size={12} />
          <span className="font-medium">库内已找到 {vm.recallCount} 人 · 现在就能看</span>
          {vm.advanceBusy ? (
            <span className="inline-flex items-center gap-1 text-slate-400">
              <Loader2 size={10} className="animate-spin" />
              后台继续补充新发现,不用等
            </span>
          ) : null}
        </div>
      ) : null}

      {!vm.showRecallSkeleton && vm.mode === "url" && vm.urlResult ? (
        <UrlSummary
          result={vm.urlResult}
          apiToken={vm.apiToken}
          canExecute={vm.urlCanExecute}
          isExecuting={vm.state === "executing"}
          onExecute={() => void vm.executeUrlAction()}
          onLocalEvaluation={() => void vm.executeLocalEvaluationAction()}
          onOpenProfile={vm.onOpenProfile}
        />
      ) : null}

      {!vm.showRecallSkeleton && vm.mode === "text" && vm.recallResult ? (
        <TextResultSection
          recallResult={vm.recallResult}
          searchSession={vm.activeSearchSession}
          llmPlan={vm.llmPlan}
          discoveryItems={vm.discoveryItems}
          discoveryTotal={vm.discoveryTotal}
          discoveryAutoEnrolled={vm.discoveryAutoEnrolled}
          discoveryBrandExcluded={vm.discoveryBrandExcluded}
          reachFloorDisplay={vm.reachFloorDisplay}
          input={vm.input}
          apiToken={vm.apiToken}
          isBusy={vm.isBusy}
          // 结果区只用 state 判「现在还能不能再发起一次全网补充」(那两个按钮的转圈+禁用)。
          // 忙碌信号就地留在触发它的按钮上,不再上浮到顶部搜索框。
          state={vm.advanceBusy ? "executing" : vm.state}
          plannerFellBack={vm.plannerFellBack}
          personaEditing={vm.personaEditing}
          personaDraft={vm.personaDraft}
          setPersonaEditing={vm.setPersonaEditing}
          setPersonaDraft={vm.setPersonaDraft}
          setInput={vm.setInput}
          run={vm.run}
          discoveryPlatforms={vm.discoveryPlatforms}
          setDiscoveryPlatforms={vm.setDiscoveryPlatforms}
          discoveryRegion={vm.discoveryRegion}
          setDiscoveryRegion={vm.setDiscoveryRegion}
          contentLanguages={vm.contentLanguages}
          setContentLanguages={vm.setContentLanguages}
          kolProfileTypes={vm.kolProfileTypes}
          setKolProfileTypes={vm.setKolProfileTypes}
          excludeChinese={vm.excludeChinese}
          setExcludeChinese={vm.setExcludeChinese}
          queueTextAdvance={vm.queueTextAdvance}
          pickedIds={vm.pickedIds}
          setPickedIds={vm.setPickedIds}
          favNote={vm.favNote}
          favoriteIds={vm.favoriteIds}
          favoriteBusyIds={vm.favoriteBusyIds}
          favoriteResults={vm.favoriteResults}
          favoriteErrors={vm.favoriteErrors}
          favoritesSyncing={vm.favoritesSyncing}
          favoritesLoadError={vm.favoritesLoadError}
          draftNote={vm.draftNote}
          outreachNote={vm.outreachNote}
          outreachResult={vm.outreachResult}
          addingFav={vm.addingFav}
          draftBusy={vm.draftBusy}
          outreachBusy={vm.outreachBusy}
          displayedSearchSessionId={vm.displayedSearchSessionId}
          isSessionPolling={vm.isSessionPolling}
          isSessionPollPaused={vm.isSessionPollPaused}
          resultsStale={vm.recallIsStale}
          approvalReady={vm.approvalReady}
          favoriteOne={vm.favoriteOne}
          addPickedToMyKol={vm.addPickedToMyKol}
          approveAndCreateDraft={vm.approveAndCreateDraft}
          generateOutreachForPicked={vm.generateOutreachForPicked}
          discoveryKey={vm.discoveryKey}
          onOpenRecallItem={vm.onOpenRecallItem}
          sessionBanner={vm.sessionBanner}
          sessionProgress={vm.activeSessionProgress}
          activeSessionCounts={vm.activeSessionCounts}
          sessionPollNotice={vm.sessionPollNotice}
          retrySearchSession={vm.retrySearchSession}
          resumeSearchPolling={vm.resumeSearchPolling}
        />
      ) : null}
    </section>
  );
}

/**
 * Key the entire stateful search controller by the real account id. This makes
 * an account switch synchronously tear down the previous session poll and all
 * in-memory results before the next account can restore its own snapshot.
 */
export function SmartKolInputPanel(props: SmartKolInputPanelProps) {
  const normalizedAccountId = String(props.accountId ?? "").trim();
  const accountKey = props.accountId === undefined
    ? "legacy-unscoped"
    : normalizedAccountId
      ? `account:${normalizedAccountId}`
      : "account:unresolved";
  return <SmartKolInputPanelForAccount key={accountKey} {...props} />;
}
