// Fixed, synthetic-only DOM regression. Never attaches to a user-owned browser.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { accessSync, constants, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { CdpPipeConnection, attachFirstPageTarget, chromeChildEnvironment } from "../../../../scripts/browser_console_cdp_pipe.mjs";
import { OverallDeadline } from "../../../../scripts/browser_console_capture_runtime.mjs";

const URL_ONLY = "http://127.0.0.1:4178/";
const ORIGIN = new URL(URL_ONLY).origin;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const VIEWPORT = { width: 1440, height: 1400, deviceScaleFactor: 1, mobile: false };
if (process.argv.slice(2).join(" ") !== "--run") {
  console.log("Usage: node frontend/tests/browser/system-optimization/run-browser-regression.mjs --run\nRequires the separately started synthetic fixture on 127.0.0.1:4178. Deadline: 120 seconds.");
  process.exit(0);
}
accessSync(CHROME, constants.X_OK);
const output = mkdtempSync("/private/tmp/vkpi-synthetic-browser-");
const profile = join(output, "owned-chrome-profile");
mkdirSync(profile, { mode: 0o700 });
const deadline = new OverallDeadline(120000);
const report = { synthetic: true, claim_status: "descriptive_only", url: URL_ONLY, viewport: VIEWPORT,
  started_at: new Date().toISOString(), output, profile, scenarios: [], console: [], network: [], blocked_requests: [],
  dialogs: [], passed: false, cleanup: { owned_browser_exited: false, profile_retained: true } };
const browser = spawn(CHROME, ["--remote-debugging-pipe", `--user-data-dir=${profile}`, "--headless=new", "--incognito",
  "--disable-extensions", "--disable-component-extensions-with-background-pages", "--disable-default-apps",
  "--disable-sync", "--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
  "--disable-client-side-phishing-detection", "--disable-crash-reporter", "--disable-breakpad", "--no-pings",
  "--no-first-run", "--no-default-browser-check", `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
  "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=127.0.0.1",
  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"],
{ env: chromeChildEnvironment(process.env), stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
let connection, sessionId = "", asynchronousError = null, stderr = "";
browser.stderr.on("data", (chunk) => { if (stderr.length < 16000) stderr += String(chunk).slice(0, 16000 - stderr.length); });
const send = (method, params = {}) => connection.send(method, params, sessionId, 5000);
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(`DOM evaluation failed: ${result.exceptionDetails.text}`);
  return result.result?.value;
}
async function waitFor(expression, description) {
  const end = deadline.localDeadline(4000);
  while (Date.now() < end) {
    if (asynchronousError) throw asynchronousError;
    if (await evaluate(expression)) return;
    await deadline.wait(80);
  }
  throw new Error(`Timed out waiting for ${description}`);
}
const SNAPSHOT = `(() => {
  const visible = (node) => {
    if (!node) return false;
    const r = node.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    for (let p = node; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) <= 0.01) return false;
    }
    return true;
  };
  const inbox = document.querySelector('[aria-label="真实 Action Inbox 组件 · 合成数据"]');
  const market = document.querySelector('[aria-label="真实市场信号组件 · 合成数据"]');
  const presentation = document.querySelector('[aria-label="营销规划草案"]');
  const budget = presentation?.querySelector('[aria-label="预算分配"]');
  const rawPlan = document.querySelector('[aria-label="合成规划原始数据"]');
  const budgetBounds = budget?.getBoundingClientRect();
  const kpi = [...document.querySelectorAll('.ds-kpi')].find(n => n.querySelector('.ds-kpi__label')?.textContent === '本周信号');
  return {
    scenario: document.querySelector('select')?.value,
    synthetic_marker: document.querySelector('.fixture-warning')?.innerText || '',
    style: document.documentElement.dataset.style, theme: document.documentElement.dataset.theme,
    background: getComputedStyle(document.body).backgroundColor,
    inbox_visible: visible(inbox?.firstElementChild) && Number(getComputedStyle(inbox.firstElementChild).opacity) >= 0.95, market_visible: visible(market),
    inbox_text: inbox?.innerText || '', market_text: market?.innerText || '',
    signal_kpi: kpi?.querySelector('.ds-kpi__val')?.innerText.trim(),
    buttons: [...document.querySelectorAll('button')].map(n => ({ label: n.innerText.trim(), title: n.title, disabled: n.disabled, visible: visible(n) })),
    events: [...document.querySelectorAll('.fixture-log li')].map(n => n.innerText),
    isolation_text: document.querySelector('[aria-label="合成隔离回执"] summary')?.innerText || '',
    plan_text: document.querySelector('[aria-label="营销计划准备检查"]')?.innerText || '',
    plan_review_visible: visible(presentation),
    presentation_title: presentation?.querySelector('h3')?.innerText || '',
    presentation_text: presentation?.innerText || '',
    presentation_controls: presentation?.querySelectorAll('button, a, input, select').length ?? 0,
    budget_values: [...(budget?.querySelectorAll('dl > div') || [])].map(n => ({ label: n.querySelector('dt')?.innerText, value: n.querySelector('dd')?.innerText })),
    budget_within_viewport: Boolean(budgetBounds && budgetBounds.top >= 0 && budgetBounds.bottom <= innerHeight),
    raw_plan_collapsed: Boolean(rawPlan && !rawPlan.open)
  };
})()`;
async function snapshot() { return evaluate(SNAPSHOT); }
async function choose(value) {
  await evaluate(`(() => { const select = document.querySelector('select'); if (!select || ![...select.options].some(o => o.value === ${JSON.stringify(value)})) throw new Error('scenario not found'); select.value = ${JSON.stringify(value)}; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  const visible = value === "plan_review" ? "plan_review_visible" : "inbox_visible";
  await waitFor(`(${SNAPSHOT}).scenario === ${JSON.stringify(value)} && (${SNAPSHOT}).${visible}`, `${value} visible`);
  await evaluate("window.scrollTo(0, 0)");
}
async function click(label, title = false) {
  const result = await evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(n => ${title ? "n.title" : "n.innerText.trim()"} === ${JSON.stringify(label)}); if (!b || b.disabled || !b.getBoundingClientRect().width) return false; b.click(); return true; })()`);
  assert.equal(result, true, `Button unavailable: ${label}`);
}
async function fill(label, value) {
  await evaluate(`(() => { const node = document.querySelector('[aria-label=${JSON.stringify(label)}]'); if (!node) throw new Error('input not found'); const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, ${JSON.stringify(value)}); node.dispatchEvent(new Event('input', { bubbles: true })); })()`);
}
async function textInInbox(text) { await waitFor(`(${SNAPSHOT}).inbox_text.includes(${JSON.stringify(text)})`, text); }
function executeCalls(proof) { return proof.events.filter((text) => text.includes("executeAction (synthetic)")).length; }
async function record(name, check, screenshot = false) {
  const proof = await snapshot();
  assert(proof.synthetic_marker.includes("合成数据"));
  assert.equal(proof.style, "glass");
  assert.equal(proof.theme, "dark");
  assert.notEqual(proof.background, "rgb(255, 255, 255)");
  assert(proof.scenario === "plan_review" ? proof.plan_review_visible : proof.inbox_visible && proof.market_visible,
    "Scenario's real components must have visible rectangles and opacity");
  assert(proof.isolation_text.includes("拒绝未知调用 0 次"));
  check(proof);
  const entry = { name, passed: true, proof };
  if (screenshot) {
    await evaluate("window.scrollTo(0, 0)");
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    entry.screenshot = join(output, `${name}.png`);
    writeFileSync(entry.screenshot, Buffer.from(image.data, "base64"), { mode: 0o600 });
  }
  report.scenarios.push(entry);
  console.log(`${name}: PASS`);
}
function allowRequest(request) {
  try {
    const url = new URL(request.url);
    return request.method === "GET" && url.origin === ORIGIN && !url.search &&
      (url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/favicon.ico" || /^\/assets\/[a-zA-Z0-9_.-]+\.(js|css)$/.test(url.pathname));
  } catch { return false; }
}
function safeUrl(value) { try { const url = new URL(value); return `${url.origin}${url.pathname}`; } catch { return "unparseable"; } }
async function waitForExit(milliseconds) {
  if (browser.exitCode !== null || browser.signalCode !== null) return true;
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); browser.off("exit", finish); resolve(true); };
    const timer = setTimeout(() => { browser.off("exit", finish); resolve(false); }, milliseconds);
    browser.once("exit", finish);
  });
}

try {
  connection = new CdpPipeConnection(browser, deadline);
  ({ sessionId } = await attachFirstPageTarget(connection, deadline));
  connection.onEvent((event) => {
    if (event.sessionId !== sessionId) return;
    const p = event.params || {};
    if (event.method === "Fetch.requestPaused") {
      const allowed = allowRequest(p.request);
      if (!allowed) report.blocked_requests.push({ method: p.request?.method, url: safeUrl(p.request?.url) });
      void send(allowed ? "Fetch.continueRequest" : "Fetch.failRequest", { requestId: p.requestId, ...(allowed ? {} : { errorReason: "BlockedByClient" }) }).catch((error) => { asynchronousError = error; });
    } else if (event.method === "Page.javascriptDialogOpening") {
      const allowed = safeUrl(p.url).startsWith(URL_ONLY) && p.type === "confirm" && String(p.message).startsWith("【合成数据夹具】");
      report.dialogs.push({ synthetic_confirmation: allowed, type: p.type });
      if (!allowed) asynchronousError = new Error("Unexpected dialog rejected");
      void send("Page.handleJavaScriptDialog", { accept: allowed }).catch((error) => { asynchronousError = error; });
    } else if (event.method === "Network.responseReceived") {
      report.network.push({ url: safeUrl(p.response?.url), status: p.response?.status, type: p.type });
    } else if (event.method === "Runtime.consoleAPICalled") {
      report.console.push({ level: p.type, text: (p.args || []).map((arg) => String(arg.value ?? arg.description ?? "")).join(" ").slice(0, 2000) });
    } else if (event.method === "Runtime.exceptionThrown") {
      report.console.push({ level: "exception", text: String(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text).slice(0, 2000) });
    } else if (event.method === "Log.entryAdded") report.console.push({ level: p.entry?.level, text: String(p.entry?.text).slice(0, 2000) });
  });
  for (const method of ["Page.enable", "Runtime.enable", "Network.enable", "Log.enable"]) await send(method);
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  await send("Network.setBypassServiceWorker", { bypass: true });
  await send("Emulation.setDeviceMetricsOverride", VIEWPORT);
  await send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
  await send("Page.navigate", { url: URL_ONLY });
  await waitFor(`(${SNAPSHOT}).inbox_visible && (${SNAPSHOT}).market_visible`, "initial visible UI");

  await choose("source_failure");
  await record("01-source-failure", (p) => { assert.equal(p.signal_kpi, "—"); assert(p.market_text.includes("市场信号读取失败")); }, true);
  await choose("source_partial");
  await record("02-source-partial", (p) => { assert.equal(p.signal_kpi, "—"); assert(p.market_text.includes("仅有部分来源结果") && p.market_text.includes("【合成】样本评论")); });
  await choose("source_empty");
  await record("03-source-empty", (p) => { assert(p.signal_kpi.startsWith("0")); assert(p.market_text.includes("当前已读来源在对应窗口内暂无信号")); });
  await choose("suggested");
  await click("通过");
  await textInInbox("已批准 · 尚未执行");
  await record("04-approval-no-execution", (p) => { assert.equal(executeCalls(p), 0); assert(p.buttons.some(b => b.label === "执行" && b.visible)); });
  await choose("approved");
  await textInInbox("已批准 · 尚未执行");
  await record("05-approved-not-executed", (p) => assert.equal(executeCalls(p), 0));
  await choose("queued");
  await click("执行");
  await textInInbox("任务已提交 · 结果待核验");
  await record("06-queue-not-completion", (p) => { assert.equal(executeCalls(p), 1); assert(p.inbox_text.includes("实际费用待成本台账核对")); assert(!p.inbox_text.includes("未花钱")); assert(!p.buttons.some(b => b.label === "执行")); }, true);
  await choose("unknown");
  await click("执行");
  await textInInbox("结果未知 · 待核对");
  await click("刷新建议", true);
  await textInInbox("结果未知 · 待核对");
  await record("07-unknown-no-retry", (p) => { assert.equal(executeCalls(p), 1); assert(!p.buttons.some(b => b.label === "执行")); });
  await choose("read_failure");
  await click("人工对账");
  await fill("对账原因", "合成：核对 fixture 回执");
  await fill("对账证据", "synthetic-receipt-only");
  await waitFor(`(${SNAPSHOT}).buttons.some(b => b.label === '提交对账' && !b.disabled)`, "editable reconciliation form");
  await click("模拟读取失败（再手动刷新组件）");
  await click("刷新建议", true);
  await textInInbox("操作已暂停");
  await record("08-read-failure-pauses-form", (p) => { assert(p.buttons.some(b => b.label === "提交对账" && b.disabled && b.visible)); assert(!p.events.some(e => e.includes("reconcileAction"))); }, true);
  await choose("plan_gap");
  await record("09-plan-needs-evidence", (p) => { assert(p.plan_text.includes("先补齐判断依据")); assert(p.plan_text.includes("合成：目标市场证据未补齐")); assert(p.plan_text.includes("不等于项目创建、批准或对外执行")); });
  await choose("plan_review");
  await record("10-plan-review-presentation", (p) => {
    assert.equal(p.presentation_title, "规划摘要");
    assert(p.plan_text.includes("草稿可供人工审阅"));
    assert(p.presentation_text.includes("不代表项目已创建、批准或执行"));
    assert.equal(p.budget_values.find(v => v.label.startsWith("分配合计"))?.value, "750.01");
    assert.equal(p.budget_values.find(v => v.label.startsWith("尚未分配"))?.value, "250.00");
    assert(p.presentation_text.includes("模型调用成本未知"));
    assert(p.presentation_text.includes("逐人匹配理由未提供"));
    assert(p.budget_within_viewport, "Key budget block must be inside the fixed screenshot");
    assert(p.raw_plan_collapsed, "Raw JSON must be collapsed");
    assert.equal(p.presentation_controls, 0);
    assert(!p.buttons.some(b => /执行|批准|创建项目/.test(b.label)));
    assert.equal(p.events.length, 0, "Read-only plan projection must not invoke even mock action APIs");
  }, true);
  assert.equal(report.blocked_requests.length, 0, "Unexpected browser request attempted");
  assert.equal(report.console.filter(e => ["error", "exception"].includes(e.level)).length, 0, "Console error/exception observed");
  if (asynchronousError) throw asynchronousError;
  report.passed = report.scenarios.length === 10;
} catch (error) {
  report.failure = String(error?.message || error).slice(0, 2000);
  console.error(`Synthetic browser regression: ${report.failure}`);
} finally {
  if (connection) connection.close();
  if (browser.exitCode === null && browser.signalCode === null) browser.kill("SIGTERM");
  report.cleanup.owned_browser_exited = await waitForExit(2500);
  if (!report.cleanup.owned_browser_exited) { browser.kill("SIGKILL"); report.cleanup.owned_browser_exited = await waitForExit(1000); }
  report.elapsed_ms = Date.now() - deadline.startedAt;
  report.passed = report.passed && report.cleanup.owned_browser_exited;
  writeFileSync(join(output, "receipt.json"), `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  writeFileSync(join(output, "owned-chrome.stderr.log"), stderr, { mode: 0o600 });
  console.log(JSON.stringify({ passed: report.passed, scenarios_passed: report.scenarios.length, output, receipt: join(output, "receipt.json"), cleanup: report.cleanup }));
  process.exitCode = report.passed ? 0 : 1;
}
