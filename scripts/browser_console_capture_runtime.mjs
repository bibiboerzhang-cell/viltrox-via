/** Shared deadline and Ask & Find journey for the browser console capture. */

export const DEFAULT_OVERALL_TIMEOUT_MS = 600000;
export const MIN_OVERALL_TIMEOUT_MS = 60000;
export const MAX_OVERALL_TIMEOUT_MS = 1080000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function overallTimeoutError() {
  const error = new Error("browser_gate_overall_timeout");
  error.code = "BROWSER_GATE_OVERALL_TIMEOUT";
  return error;
}

export class OverallDeadline {
  constructor(timeoutMs) {
    this.timeoutMs = timeoutMs;
    this.startedAt = Date.now();
    this.expiresAt = this.startedAt + timeoutMs;
  }

  remainingMs() {
    const remaining = this.expiresAt - Date.now();
    if (remaining <= 0) throw overallTimeoutError();
    return remaining;
  }

  boundedTimeoutMs(limitMs) {
    return Math.max(1, Math.min(limitMs, this.remainingMs()));
  }

  localDeadline(limitMs) {
    return Math.min(this.expiresAt, Date.now() + limitMs);
  }

  assertAvailable() {
    this.remainingMs();
  }

  async wait(ms) {
    const remaining = this.remainingMs();
    if (remaining < ms) throw overallTimeoutError();
    await sleep(ms);
    this.assertAvailable();
  }

  elapsedMs() {
    return Math.max(0, Date.now() - this.startedAt);
  }
}

export function emptyFunctionalProof() {
  return {
    ask_find: {
      attempted: false,
      trigger_present: false,
      dialog_present: false,
      suggestion_applied: false,
      query_present: false,
      ask_not_started_before_search: false,
      ask_clicked: false,
      completed: false,
      failure_absent: false,
      answer_present: false,
      answer_char_count: 0,
      fact_count: 0,
      evidence_count: 0,
      intelligent_api_2xx_count: 0,
      ui_global_search_api_2xx_count: 0,
      same_origin_api_idle: false,
    },
    global_search: {
      ui_search_completed: false,
      ui_usable_state: false,
      ui_results_rendered: false,
      ui_trustworthy_empty: false,
      ui_partial_or_forbidden_absent: false,
      ui_error_absent: false,
      ui_result_count: 0,
      request_completed: false,
      same_origin: false,
      http_2xx: false,
      source_status_present: false,
      required_sources_present: false,
      source_status_values_valid: false,
      all_sources_ready: false,
      result_counts_valid: false,
      result_counts_match_arrays: false,
      required_source_count: 0,
      ready_source_count: 0,
      result_count_total: 0,
      result_item_total: 0,
    },
  };
}

export function functionalProofPassed(proof) {
  const ask = proof?.ask_find || {};
  const search = proof?.global_search || {};
  return (
    ask.attempted === true
    && ask.trigger_present === true
    && ask.dialog_present === true
    && ask.suggestion_applied === true
    && ask.query_present === true
    && ask.ask_not_started_before_search === true
    && ask.ask_clicked === true
    && ask.completed === true
    && ask.failure_absent === true
    && ask.answer_present === true
    && ask.answer_char_count > 0
    && ask.fact_count > 0
    && ask.evidence_count > 0
    && ask.intelligent_api_2xx_count > 0
    && ask.ui_global_search_api_2xx_count > 0
    && ask.same_origin_api_idle === true
    && search.ui_search_completed === true
    && search.ui_usable_state === true
    && search.ui_partial_or_forbidden_absent === true
    && search.ui_error_absent === true
    && search.ui_results_rendered !== search.ui_trustworthy_empty
    && (search.ui_results_rendered ? search.ui_result_count > 0 : search.ui_result_count === 0)
    && search.request_completed === true
    && search.same_origin === true
    && search.http_2xx === true
    && search.source_status_present === true
    && search.required_sources_present === true
    && search.source_status_values_valid === true
    && search.all_sources_ready === true
    && search.result_counts_valid === true
    && search.result_counts_match_arrays === true
    && search.required_source_count === 3
    && search.ready_source_count === 3
    && search.result_count_total === search.result_item_total
  );
}

function sameOrigin2xxCount(session, family, pathname, responseStart = 0) {
  return session.networkResponses.slice(responseStart).filter((response) => {
    if (response.page_family !== family) return false;
    if (!Number.isInteger(response.status) || response.status < 200 || response.status >= 300) return false;
    try {
      const parsed = new URL(response.url);
      return parsed.origin === session.applicationOrigin && parsed.pathname === pathname;
    } catch {
      return false;
    }
  }).length;
}

async function waitForSameOrigin2xx(session, family, pathname, responseStart, timeoutMs) {
  const deadline = session.overallDeadline.localDeadline(timeoutMs);
  while (Date.now() < deadline) {
    if (sameOrigin2xxCount(session, family, pathname, responseStart) > 0) return true;
    await session.overallDeadline.wait(100);
  }
  session.overallDeadline.assertAvailable();
  return false;
}

async function globalSearchDomProof(session) {
  const result = await session.send("Runtime.evaluate", {
    expression: `(() => {
      const resultCount = document.querySelectorAll('.vkpi-ask-dialog__results [role="option"]').length;
      const emptyPresent = Boolean(document.querySelector('.vkpi-ask-dialog__state.is-empty'));
      const warningPresent = Boolean(document.querySelector('.vkpi-ask-dialog__state.is-warning'));
      const errorPresent = Boolean(document.querySelector('.vkpi-ask-dialog__state.is-error'));
      const loadingPresent = Boolean(document.querySelector('.vkpi-ask-dialog__input-row .animate-spin'));
      const terminalPresent = resultCount > 0 || emptyPresent || warningPresent || errorPresent;
      return {
        ui_search_completed: terminalPresent && !loadingPresent,
        ui_usable_state: (resultCount > 0 || emptyPresent) && !loadingPresent,
        ui_results_rendered: resultCount > 0,
        ui_trustworthy_empty: emptyPresent,
        ui_partial_or_forbidden_absent: !warningPresent,
        ui_error_absent: !errorPresent,
        ui_result_count: resultCount,
      };
    })()`,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function askFindDomProof(session) {
  const result = await session.send("Runtime.evaluate", {
    expression: `(() => {
      const dialog = document.querySelector('.vkpi-ask-dialog');
      const answer = document.querySelector('.vkpi-ask-dialog__answer.is-ready');
      const answerText = String(answer?.querySelector('p')?.textContent || '').trim();
      return {
        dialog_present: Boolean(dialog),
        query_present: Boolean(String(dialog?.querySelector('input')?.value || '').trim()),
        completed: Boolean(answer),
        failure_absent: !document.querySelector('.vkpi-ask-dialog__failure'),
        answer_present: answerText.length > 0,
        answer_char_count: answerText.length,
        fact_count: document.querySelectorAll('.vkpi-ask-dialog__facts article').length,
        evidence_count: document.querySelectorAll('.vkpi-ask-dialog__evidence article').length,
        thinking_present: Boolean(document.querySelector('.vkpi-ask-dialog__thinking')),
      };
    })()`,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function probeGlobalSearchSourceTruth(session) {
  const fetchTimeoutMs = session.overallDeadline.boundedTimeoutMs(10000);
  const result = await session.send("Runtime.evaluate", {
    expression: `(async () => {
      const proof = {
        request_completed: false,
        same_origin: false,
        http_2xx: false,
        source_status_present: false,
        required_sources_present: false,
        source_status_values_valid: false,
        all_sources_ready: false,
        result_counts_valid: false,
        result_counts_match_arrays: false,
        required_source_count: 0,
        ready_source_count: 0,
        result_count_total: 0,
        result_item_total: 0,
      };
      try {
        const question = String(document.querySelector('.vkpi-ask-dialog input')?.value || '').trim();
        if (!question) return proof;
        const token = localStorage.getItem('viltrox_marketing_token_v1') || '';
        const endpoint = new URL('/api/admin/vkpi/global-search', location.href);
        endpoint.searchParams.set('q', question);
        proof.same_origin = endpoint.origin === location.origin;
        const response = await fetch(endpoint.href, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Authorization: 'Bearer ' + token },
          signal: AbortSignal.timeout(${fetchTimeoutMs}),
        });
        proof.request_completed = true;
        proof.http_2xx = response.status >= 200 && response.status < 300;
        const body = await response.json().catch(() => null);
        const requiredSources = ['kols', 'projects', 'events'];
        const allowedStatuses = new Set(['ready', 'degraded', 'error', 'blocked']);
        const sourceStatus = body && typeof body.source_status === 'object' && !Array.isArray(body.source_status)
          ? body.source_status
          : null;
        proof.source_status_present = Boolean(sourceStatus);
        proof.required_source_count = requiredSources.length;
        proof.required_sources_present = Boolean(sourceStatus)
          && requiredSources.every((key) => sourceStatus[key] && typeof sourceStatus[key] === 'object');
        proof.source_status_values_valid = proof.required_sources_present
          && requiredSources.every((key) => allowedStatuses.has(String(sourceStatus[key].status || '')));
        proof.ready_source_count = sourceStatus
          ? requiredSources.filter((key) => sourceStatus[key]?.status === 'ready').length
          : 0;
        proof.all_sources_ready = proof.ready_source_count === requiredSources.length;
        const declaredCounts = sourceStatus
          ? requiredSources.map((key) => sourceStatus[key]?.result_count)
          : [];
        proof.result_counts_valid = declaredCounts.length === requiredSources.length
          && declaredCounts.every((count) => Number.isInteger(count) && count >= 0);
        const resultArrays = requiredSources.map((key) => Array.isArray(body?.[key]) ? body[key] : null);
        proof.result_count_total = proof.result_counts_valid
          ? declaredCounts.reduce((total, count) => total + count, 0)
          : 0;
        proof.result_item_total = resultArrays.every(Array.isArray)
          ? resultArrays.reduce((total, rows) => total + rows.length, 0)
          : 0;
        proof.result_counts_match_arrays = proof.result_counts_valid
          && resultArrays.every(Array.isArray)
          && requiredSources.every((key, index) => declaredCounts[index] === resultArrays[index].length);
      } catch {}
      return proof;
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function waitForFamilySameOriginApiIdle(session, family, timeoutMs) {
  const deadline = session.overallDeadline.localDeadline(timeoutMs);
  while (Date.now() < deadline) {
    const inflight = session.inflightApiForFamily(family);
    const lastActivity = session.sameOriginApiLastActivityAt.get(family) || 0;
    if (inflight === 0 && Date.now() - lastActivity >= 500) return true;
    await session.overallDeadline.wait(100);
  }
  session.overallDeadline.assertAvailable();
  return false;
}

export async function runKolPoolFunctionalJourney(session, timeoutMs) {
  const proof = emptyFunctionalProof();
  const responseStart = session.networkResponses.length;
  proof.ask_find.attempted = true;
  try {
    const openState = await session.send("Runtime.evaluate", {
      expression: `(() => {
        const trigger = document.querySelector('.vkpi-ask-trigger');
        if (trigger instanceof HTMLElement) trigger.click();
        return { trigger_present: trigger instanceof HTMLElement };
      })()`,
      returnByValue: true,
    });
    proof.ask_find.trigger_present = openState.result?.value?.trigger_present === true;

    const dialogDeadline = session.overallDeadline.localDeadline(Math.min(timeoutMs, 5000));
    let suggestionReady = false;
    while (Date.now() < dialogDeadline) {
      const dialogState = await session.send("Runtime.evaluate", {
        expression: `(() => ({
          dialog_present: Boolean(document.querySelector('.vkpi-ask-dialog')),
          suggestion_present: Boolean(document.querySelector('.vkpi-ask-dialog__suggestions button')),
        }))()`,
        returnByValue: true,
      });
      const state = dialogState.result?.value || {};
      proof.ask_find.dialog_present = state.dialog_present === true;
      suggestionReady = state.suggestion_present === true;
      if (proof.ask_find.dialog_present && suggestionReady) break;
      await session.overallDeadline.wait(100);
    }
    session.overallDeadline.assertAvailable();

    if (suggestionReady) {
      const applyState = await session.send("Runtime.evaluate", {
        expression: `(() => {
          const suggestion = document.querySelector('.vkpi-ask-dialog__suggestions button');
          const input = document.querySelector('.vkpi-ask-dialog input');
          const value = String(suggestion?.textContent || '').trim();
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
          if (input instanceof HTMLInputElement && setter && value) {
            setter.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
          }
          return {
            applied: input instanceof HTMLInputElement && Boolean(setter) && Boolean(value),
            query_present: Boolean(String(input?.value || '').trim()),
          };
        })()`,
        returnByValue: true,
      });
      proof.ask_find.suggestion_applied = applyState.result?.value?.applied === true;
      proof.ask_find.query_present = applyState.result?.value?.query_present === true;
    }

    const searchDeadline = session.overallDeadline.localDeadline(Math.min(timeoutMs, 15000));
    let uiSearchProof = {};
    while (Date.now() < searchDeadline) {
      uiSearchProof = await globalSearchDomProof(session);
      if (uiSearchProof.ui_search_completed === true) break;
      await session.overallDeadline.wait(100);
    }
    session.overallDeadline.assertAvailable();
    uiSearchProof = await globalSearchDomProof(session);
    for (const field of [
      "ui_search_completed",
      "ui_usable_state",
      "ui_results_rendered",
      "ui_trustworthy_empty",
      "ui_partial_or_forbidden_absent",
      "ui_error_absent",
    ]) {
      proof.global_search[field] = uiSearchProof[field] === true;
    }
    proof.global_search.ui_result_count = Number.isInteger(uiSearchProof.ui_result_count)
      ? Math.max(0, uiSearchProof.ui_result_count)
      : 0;

    await waitForSameOrigin2xx(
      session,
      "kol-pool",
      "/api/admin/vkpi/global-search",
      responseStart,
      Math.min(timeoutMs, 5000),
    );
    proof.ask_find.ui_global_search_api_2xx_count = sameOrigin2xxCount(
      session,
      "kol-pool",
      "/api/admin/vkpi/global-search",
      responseStart,
    );
    proof.ask_find.ask_not_started_before_search = sameOrigin2xxCount(
      session,
      "kol-pool",
      "/api/admin/vkpi/intelligent/query",
      responseStart,
    ) === 0;

    proof.global_search = {
      ...proof.global_search,
      ...await probeGlobalSearchSourceTruth(session),
    };

    const askState = await session.send("Runtime.evaluate", {
      expression: `(() => {
        const askButton = document.querySelector('.vkpi-ask-dialog__ask');
        if (askButton instanceof HTMLButtonElement && !askButton.disabled) askButton.click();
        return { clicked: askButton instanceof HTMLButtonElement && !askButton.disabled };
      })()`,
      returnByValue: true,
    });
    proof.ask_find.ask_clicked = askState.result?.value?.clicked === true;

    const answerDeadline = session.overallDeadline.localDeadline(timeoutMs);
    let domProof = {};
    while (Date.now() < answerDeadline) {
      domProof = await askFindDomProof(session);
      if (domProof.completed === true && domProof.answer_present === true) break;
      if (domProof.failure_absent === false && domProof.thinking_present !== true) break;
      await session.overallDeadline.wait(100);
    }
    session.overallDeadline.assertAvailable();
    domProof = await askFindDomProof(session);
    proof.ask_find.dialog_present = domProof.dialog_present === true;
    proof.ask_find.query_present = domProof.query_present === true;
    proof.ask_find.completed = domProof.completed === true;
    proof.ask_find.failure_absent = domProof.failure_absent === true;
    proof.ask_find.answer_present = domProof.answer_present === true;
    proof.ask_find.answer_char_count = Number.isInteger(domProof.answer_char_count)
      ? Math.max(0, domProof.answer_char_count)
      : 0;
    proof.ask_find.fact_count = Number.isInteger(domProof.fact_count)
      ? Math.max(0, domProof.fact_count)
      : 0;
    proof.ask_find.evidence_count = Number.isInteger(domProof.evidence_count)
      ? Math.max(0, domProof.evidence_count)
      : 0;

    await waitForSameOrigin2xx(
      session,
      "kol-pool",
      "/api/admin/vkpi/intelligent/query",
      responseStart,
      Math.min(timeoutMs, 5000),
    );
    proof.ask_find.intelligent_api_2xx_count = sameOrigin2xxCount(
      session,
      "kol-pool",
      "/api/admin/vkpi/intelligent/query",
      responseStart,
    );
    proof.ask_find.same_origin_api_idle = await waitForFamilySameOriginApiIdle(
      session,
      "kol-pool",
      Math.min(timeoutMs, 5000),
    );
  } catch (error) {
    if (error?.code === "BROWSER_GATE_OVERALL_TIMEOUT") throw error;
    // Keep the capture diagnostic and secret-free. The hermetic verifier fails
    // every incomplete proof instead of persisting exception/body/query data.
  }
  return proof;
}
