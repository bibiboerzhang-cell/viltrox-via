import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = "/Users/bibiboer/Documents/V-KPI——marketing";
const outDir = `${root}/docs/audits/vkpi_button_flow_audit_2026-07-08`;
const screenshotDir = `${outDir}/screenshots`;
mkdirSync(screenshotDir, { recursive: true });

const baseUrl = "http://127.0.0.1:8102/";
const token = process.env.VKPI_AUDIT_TOKEN || "";
if (!token) throw new Error("VKPI_AUDIT_TOKEN missing");

const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromePort = 9222;
const profileDir = "/tmp/vkpi-button-audit-chrome";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function waitForChrome() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const list = await fetchJson(`http://127.0.0.1:${chromePort}/json/list`);
      const page = Array.isArray(list)
        ? list.find((item) => item.type === "page" && item.webSocketDebuggerUrl)
        : null;
      if (page?.webSocketDebuggerUrl) return page;
    } catch {
      // retry
    }
    await sleep(250);
  }
  throw new Error("Chrome CDP not ready");
}

class Cdp {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.network = [];
    this.console = [];
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result || {});
        return;
      }
      this.events.push(msg);
      if (msg.method === "Network.responseReceived") {
        this.network.push({
          url: msg.params.response?.url,
          status: msg.params.response?.status,
          mimeType: msg.params.response?.mimeType,
          type: msg.params.type,
        });
      }
      if (msg.method === "Network.loadingFailed") {
        this.network.push({
          url: msg.params.requestId,
          status: "failed",
          errorText: msg.params.errorText,
          type: msg.params.type,
        });
      }
      if (msg.method === "Runtime.consoleAPICalled") {
        this.console.push({
          type: msg.params.type,
          text: (msg.params.args || []).map((arg) => arg.value || arg.description || "").join(" "),
        });
      }
      if (msg.method === "Runtime.exceptionThrown") {
        this.console.push({
          type: "exception",
          text: msg.params.exceptionDetails?.text || msg.params.exceptionDetails?.exception?.description || "",
        });
      }
    };
  }

  async open() {
    if (this.ws.readyState === WebSocket.OPEN) return;
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
      setTimeout(() => reject(new Error("WebSocket open timeout")), 10000);
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timeout`));
        }
      }, 15000);
    });
  }

  close() {
    this.ws.close();
  }
}

async function evalJson(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  return result.result?.value;
}

async function capture(cdp, name) {
  const { data } = await cdp.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false,
  });
  const file = join(screenshotDir, `${name}.png`);
  writeFileSync(file, Buffer.from(data, "base64"));
  return file;
}

const collectExpression = `(() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const clean = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
  const controls = Array.from(document.querySelectorAll('button,a,input,select,textarea,[role="button"],[role="tab"],[role="menuitem"]'))
    .filter(visible)
    .slice(0, 260)
    .map((el, i) => {
      const r = el.getBoundingClientRect();
      return {
        idx: i,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        text: clean(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || ''),
        type: el.getAttribute('type') || '',
        disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
        href: el.getAttribute('href') || '',
        title: el.getAttribute('title') || '',
        x: Math.round(r.left + r.width / 2),
        y: Math.round(r.top + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height),
      };
    });
  return {
    url: location.href,
    title: document.title,
    heading: clean(document.querySelector('h1,h2,.text-2xl,.text-xl')?.innerText || ''),
    bodyPreview: clean(document.body.innerText).slice(0, 2000),
    controls,
  };
})()`;

async function clickText(cdp, label) {
  const result = await evalJson(cdp, `(() => {
    const clean = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
    const candidates = Array.from(document.querySelectorAll('button,a,[role="button"],[role="tab"],[role="menuitem"]'))
      .filter((el) => {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      })
      .map((el) => ({ el, text: clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '') }))
      .filter((item) => item.text === ${JSON.stringify(label)} || item.text.includes(${JSON.stringify(label)}));
    if (!candidates.length) return { ok: false, reason: 'not_found' };
    const item = candidates[0];
    const r = item.el.getBoundingClientRect();
    item.el.click();
    return { ok: true, text: item.text, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  })()`);
  await sleep(1000);
  return result;
}

const chrome = spawn(chromePath, [
  `--remote-debugging-port=${chromePort}`,
  `--user-data-dir=${profileDir}`,
  "--headless=new",
  "--disable-gpu",
  "--hide-scrollbars",
  "--no-first-run",
  "--disable-default-apps",
  "--window-size=1440,1000",
  "about:blank",
], { stdio: "ignore", detached: true });
chrome.unref();

const tabInfo = await waitForChrome();
const cdp = new Cdp(tabInfo.webSocketDebuggerUrl);
await cdp.open();
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");
await cdp.send("Network.enable");
await cdp.send("Page.bringToFront").catch(() => {});
await cdp.send("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});
await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
  source: `try { localStorage.setItem('viltrox_marketing_token_v1', ${JSON.stringify(token)}); } catch (e) {}`,
});
await cdp.send("Page.navigate", { url: baseUrl });
await sleep(4500);

const navLabels = [
  "Dashboard",
  "MY KOL",
  "KOL Pool",
  "Projects",
  "Events",
  "Shopify",
  "Dealers",
  "Intelligent 问答",
  "回复队列",
  "SKU 360°",
  "KOL 档案",
  "发射台",
  "自治驾照",
  "市场之声",
  "创意资产库",
  "战略台",
  "GTM Command",
];

const steps = [];
steps.push({ label: "initial", clicked: null, state: await evalJson(cdp, collectExpression), screenshot: await capture(cdp, "00-initial") });

let index = 1;
for (const label of navLabels) {
  const clicked = await clickText(cdp, label);
  const safeName = `${String(index).padStart(2, "0")}-${label.replace(/[\\s/°]/g, "-").replace(/[^A-Za-z0-9\\-\\u4e00-\\u9fff]/g, "")}`;
  const state = await evalJson(cdp, collectExpression);
  const screenshot = await capture(cdp, safeName);
  steps.push({ label, clicked, state, screenshot });
  index += 1;
}

// Top-level shell controls after returning to dashboard.
await clickText(cdp, "Dashboard");
const shellButtons = ["Collapse", "Dark"];
for (const label of shellButtons) {
  const clicked = await clickText(cdp, label);
  const state = await evalJson(cdp, collectExpression);
  const screenshot = await capture(cdp, `${String(index).padStart(2, "0")}-shell-${label}`);
  steps.push({ label: `shell:${label}`, clicked, state, screenshot });
  index += 1;
}

const report = {
  baseUrl,
  capturedAt: new Date().toISOString(),
  network: cdp.network,
  console: cdp.console,
  steps,
};
writeFileSync(`${outDir}/data/browser-cdp-audit.json`, JSON.stringify(report, null, 2));
cdp.close();
console.log(JSON.stringify({
  capturedAt: report.capturedAt,
  steps: steps.length,
  screenshots: steps.map((s) => s.screenshot),
  networkErrors: cdp.network.filter((r) => r.status === "failed" || Number(r.status) >= 400).slice(0, 40),
  consoleErrors: cdp.console.filter((r) => ["error", "exception"].includes(r.type)).slice(0, 40),
}, null, 2));
