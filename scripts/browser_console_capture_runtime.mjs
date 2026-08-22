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
      home_zones_present: false,
      catalog_suggest_api_error_absent: false,
      answer_char_count: 0,
      fact_count: 0,
      evidence_count: 0,
      intelligent_api_2xx_count: 0,
      ui_global_search_api_2xx_count: 0,
      ui_catalog_suggest_api_2xx_count: 0,
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
      optional_sources_valid: false,
      catalog_probe_completed: false,
      catalog_http_2xx: false,
      catalog_items_valid: false,
      required_source_count: 0,
      ready_source_count: 0,
      optional_source_count: 0,
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
    && ask.home_zones_present === true
    && ask.catalog_suggest_api_error_absent === true
    // P1 命令面板:答案卡可为 needs_clarification / empty(facts、evidence 允许为 0),正文必须非空。
    && ask.answer_char_count > 0
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
    // 必需来源 kols/projects/events 精确三个;catalog/suggest 是合法可选第四来源(2xx + 形状合法)。
    && search.optional_sources_valid === true
    && search.catalog_probe_completed === true
    && search.catalog_http_2xx === true
    && search.catalog_items_valid === true
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

function sameOriginNon2xxCount(session, family, pathname, responseStart = 0) {
  return session.networkResponses.slice(responseStart).filter((response) => {
    if (response.page_family !== family) return false;
    try {
      const parsed = new URL(response.url);
      if (parsed.origin !== session.applicationOrigin || parsed.pathname !== pathname) return false;
    } catch {
      return false;
    }
    return !Number.isInteger(response.status) || response.status < 200 || response.status >= 300;
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
      const answerCard = document.querySelector('.vkpi-ask-dialog__answer');
      const answerFailed = Boolean(answerCard) && ['is-error', 'is-blocked', 'is-unavailable']
        .some((state) => answerCard.classList.contains(state));
      const answer = answerCard && !answerFailed ? answerCard : null;
      const answerText = String(answer?.querySelector(':scope > p')?.textContent || '').trim();
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
        optional_sources_valid: false,
        catalog_probe_completed: false,
        catalog_http_2xx: false,
        catalog_items_valid: false,
        required_source_count: 0,
        ready_source_count: 0,
        optional_source_count: 0,
        result_count_total: 0,
        result_item_total: 0,
      };
      try {
        const question = String(document.querySelector('.vkpi-ask-dialog input')?.value || '').trim();
        if (!question) return proof;
        const token = localStorage.getItem('viltrox_marketing_token_v1') || '';
        const fetchJson = (endpoint) => fetch(endpoint.href, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Authorization: 'Bearer ' + token },
          signal: AbortSignal.timeout(${fetchTimeoutMs}),
        });
        const endpoint = new URL('/api/admin/vkpi/global-search', location.href);
        endpoint.searchParams.set('q', question);
        proof.same_origin = endpoint.origin === location.origin;
        const response = await fetchJson(endpoint);
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
        const optionalSources = sourceStatus
          ? Object.keys(sourceStatus).filter((key) => !requiredSources.includes(key))
          : [];
        proof.optional_source_count = optionalSources.length;
        proof.optional_sources_valid = Boolean(sourceStatus) && optionalSources.every((key) => (
          sourceStatus[key] && typeof sourceStatus[key] === 'object'
          && allowedStatuses.has(String(sourceStatus[key].status || ''))
        ));
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
        // 可选第四来源:$SKU/镜头目录。不进 required 计数,但端点必须 2xx 且只回三列、≤20 行。
        const catalogEndpoint = new URL('/api/admin/vkpi/catalog/suggest', location.href);
        catalogEndpoint.searchParams.set('q', question);
        catalogEndpoint.searchParams.set('limit', '20');
        const catalogResponse = await fetchJson(catalogEndpoint);
        proof.catalog_probe_completed = true;
        proof.catalog_http_2xx = catalogResponse.status >= 200 && catalogResponse.status < 300;
        const catalogBody = await catalogResponse.json().catch(() => null);
        const catalogItems = Array.isArray(catalogBody?.items) ? catalogBody.items : null;
        const catalogSourceStatus = catalogBody && typeof catalogBody.source_status === 'object'
          && !Array.isArray(catalogBody.source_status) ? catalogBody.source_status : null;
        const catalogAllowed = new Set(['ready', 'error', 'absent']);
        proof.catalog_items_valid = Array.isArray(catalogItems)
          && catalogItems.length <= 20
          && catalogItems.every((item) => item && typeof item === 'object'
            && Object.keys(item).length === 3
            && ['sku', 'display_name', 'lens_key'].every((key) => typeof item[key] === 'string'))
          && Boolean(catalogSourceStatus)
          && Object.values(catalogSourceStatus).every((entry) => entry && typeof entry === 'object'
            && catalogAllowed.has(String(entry.status || '')));
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

    // P1 命令面板首屏 = 三区(进行中 / 最近 / 建议);建议 chip 是 [data-group=suggestions] 下的 option。
    const dialogDeadline = session.overallDeadline.localDeadline(Math.min(timeoutMs, 8000));
    let suggestionReady = false;
    while (Date.now() < dialogDeadline) {
      const dialogState = await session.send("Runtime.evaluate", {
        expression: `(() => ({
          dialog_present: Boolean(document.querySelector('.vkpi-ask-dialog')),
          home_zones_present: ['jobs', 'recent', 'suggestions']
            .every((group) => Boolean(document.querySelector('.vkpi-ask-dialog [data-group="' + group + '"]'))),
          suggestion_present: Boolean(document.querySelector('.vkpi-ask-dialog [data-group="suggestions"] [role="option"]')),
        }))()`,
        returnByValue: true,
      });
      const state = dialogState.result?.value || {};
      proof.ask_find.dialog_present = state.dialog_present === true;
      proof.ask_find.home_zones_present = state.home_zones_present === true;
      suggestionReady = state.suggestion_present === true;
      if (proof.ask_find.dialog_present && proof.ask_find.home_zones_present && suggestionReady) break;
      await session.overallDeadline.wait(100);
    }
    session.overallDeadline.assertAvailable();

    if (suggestionReady) {
      const applyState = await session.send("Runtime.evaluate", {
        expression: `(() => {
          const suggestion = document.querySelector('.vkpi-ask-dialog [data-group="suggestions"] [role="option"]');
          const input = document.querySelector('.vkpi-ask-dialog input');
          const value = String(suggestion?.querySelector('span')?.textContent || '').trim();
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
    // UI 自己的 catalog/suggest 调用计数必须在页内探针 fetch 之前定格(探针另算 +1)。
    await waitForSameOrigin2xx(
      session,
      "kol-pool",
      "/api/admin/vkpi/catalog/suggest",
      responseStart,
      Math.min(timeoutMs, 5000),
    );
    proof.ask_find.ui_catalog_suggest_api_2xx_count = sameOrigin2xxCount(
      session,
      "kol-pool",
      "/api/admin/vkpi/catalog/suggest",
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
    // 可选来源的健康口径:整段旅程内(含页内探针)没有任何非 2xx 的 catalog/suggest 回包。
    proof.ask_find.catalog_suggest_api_error_absent = sameOriginNon2xxCount(
      session,
      "kol-pool",
      "/api/admin/vkpi/catalog/suggest",
      responseStart,
    ) === 0;
  } catch (error) {
    if (error?.code === "BROWSER_GATE_OVERALL_TIMEOUT") throw error;
    // Keep the capture diagnostic and secret-free. The hermetic verifier fails
    // every incomplete proof instead of persisting exception/body/query data.
  }
  return proof;
}
