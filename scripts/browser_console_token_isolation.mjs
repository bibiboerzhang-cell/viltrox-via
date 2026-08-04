const PROBE_HTML = `<!doctype html><meta charset="utf-8"><script>
(() => {
  // A sandboxed srcdoc frame without allow-same-origin has an opaque origin.
  // Fail closed if Chromium ever stops exposing that origin as "null".
  let tokenPresent = location.origin !== 'null';
  if (!tokenPresent) {
    try {
      const value = localStorage.getItem('viltrox_marketing_token_v1');
      tokenPresent = typeof value === 'string' && value.length > 0;
    } catch {
      tokenPresent = false;
    }
  }
  parent.postMessage({ token_present: tokenPresent }, '*');
})();
</script>`;


export async function proveOpaqueOriginTokenIsolation(session) {
  const timeoutMs = session.overallDeadline.boundedTimeoutMs(5000);
  const result = await session.send("Runtime.evaluate", {
    expression: `(() => new Promise((resolve) => {
      const iframe = document.createElement('iframe');
      iframe.hidden = true;
      iframe.setAttribute('sandbox', 'allow-scripts');
      iframe.srcdoc = ${JSON.stringify(PROBE_HTML)};
      const sandboxAllowScriptsOnly = (
        iframe.getAttribute('sandbox') === 'allow-scripts'
        && iframe.sandbox.contains('allow-scripts')
        && !iframe.sandbox.contains('allow-same-origin')
      );
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        window.removeEventListener('message', onMessage);
        iframe.remove();
        resolve(value);
      };
      const onMessage = (event) => {
        const data = event.data;
        if (
          event.source === iframe.contentWindow
          && data && typeof data === 'object'
          && Object.keys(data).length === 1
          && typeof data.token_present === 'boolean'
        ) {
          finish({
            cross_origin_frame_probed: true,
            cross_origin_frame_token_absent: data.token_present === false,
            opaque_origin_observed: event.origin === 'null',
            sandbox_allow_scripts_only: sandboxAllowScriptsOnly,
          });
        }
      };
      const timer = setTimeout(() => finish({
        cross_origin_frame_probed: false,
        cross_origin_frame_token_absent: false,
        opaque_origin_observed: false,
        sandbox_allow_scripts_only: sandboxAllowScriptsOnly,
      }), ${timeoutMs});
      window.addEventListener('message', onMessage);
      document.documentElement.appendChild(iframe);
    }))()`,
    awaitPromise: true,
    returnByValue: true,
  });
  const value = result.result?.value || {};
  return {
    cross_origin_frame_probed: value.cross_origin_frame_probed === true,
    cross_origin_frame_token_absent: value.cross_origin_frame_token_absent === true,
    opaque_origin_observed: value.opaque_origin_observed === true,
    sandbox_allow_scripts_only: value.sandbox_allow_scripts_only === true,
    // This probe never changes Page's CSP mode. The static capture contract
    // additionally rejects any CSP-bypass command in the controller.
    csp_bypass_used: false,
    csp_enforcement_unchanged: true,
  };
}
