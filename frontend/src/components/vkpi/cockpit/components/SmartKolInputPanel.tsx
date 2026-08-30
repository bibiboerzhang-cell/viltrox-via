import { BadgeCheck, Loader2, Search, Video } from "lucide-react";
import { cleanText } from "./SmartKolInputPanel.helpers";
import { HistoryStrip, UrlSummary } from "./SmartKolInputPanel.Sections";
import { TextResultSection } from "./SmartKolInputPanel.TextResult";
import { SmartKolSearchEntry } from "./SmartKolInputPanel.Entry";
import { KolSearchPolicyPanel } from "./SmartKolInputPanel.SearchPolicy";
import { type SmartKolInputPanelProps } from "./SmartKolInputPanel.runtime";
import { useSmartKolInputPanelController } from "./SmartKolInputPanel.controller";

export function SmartKolInputPanel(props: SmartKolInputPanelProps) {
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

      {vm.mode === "url" && vm.urlResult ? (
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

      {vm.mode === "text" && vm.recallResult ? (
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
          state={vm.state}
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
