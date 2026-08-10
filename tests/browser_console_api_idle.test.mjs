import assert from "node:assert/strict";
import test from "node:test";

import {
  CdpSession,
  terminalApiIdleDeadline,
  waitForSameOriginApiIdleUntil,
} from "../scripts/capture_browser_console_cdp.mjs";

const ORIGIN = "https://www.viltroxtest.com";
const FAMILY = "kol-pool";

function makeSession() {
  const connection = { onEvent: () => () => {} };
  const overallDeadline = {
    expiresAt: 60_000,
    wait: async () => {},
  };
  const session = new CdpSession(connection, "test-session", ORIGIN, overallDeadline);
  session.currentPageFamily = FAMILY;
  return session;
}

function request(session, requestId, url, type = "Fetch") {
  session.onMessage({
    method: "Network.requestWillBeSent",
    params: {
      requestId,
      type,
      request: { method: "GET", url },
    },
  });
}

function redirect(session, requestId, url, type = "Fetch") {
  session.onMessage({
    method: "Network.requestWillBeSent",
    params: {
      requestId,
      type,
      redirectResponse: { status: 302 },
      request: { method: "GET", url },
    },
  });
}

function response(session, requestId, url, type = "Fetch") {
  session.onMessage({
    method: "Network.responseReceived",
    params: {
      requestId,
      type,
      response: { status: 200, url, mimeType: "application/json" },
    },
  });
}

function finished(session, requestId) {
  session.onMessage({
    method: "Network.loadingFinished",
    params: { requestId },
  });
}

function failed(session, requestId) {
  session.onMessage({
    method: "Network.loadingFailed",
    params: { requestId, type: "Fetch", canceled: true, errorText: "net::ERR_ABORTED" },
  });
}

test("each tracked API lifecycle event advances only the API activity clock", () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const session = makeSession();
    request(session, "complete", `${ORIGIN}/api/auth/me`);
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 100);
    clock = 200;
    response(session, "complete", `${ORIGIN}/api/auth/me`);
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 200);
    clock = 300;
    finished(session, "complete");
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 300);

    clock = 400;
    request(session, "failed", `${ORIGIN}/health`);
    clock = 500;
    failed(session, "failed");
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 500);
    assert.equal(session.inflightApiForFamily(FAMILY), 0);
  } finally {
    Date.now = realNow;
  }
});

test("unknown responses remain explicitly unattributed", () => {
  const session = makeSession();
  response(session, "unknown", `${ORIGIN}/api/auth/me`);
  assert.equal(session.networkResponses.length, 1);
  assert.deepEqual(
    {
      family: session.networkResponses[0].page_family,
      unattributed: session.networkResponses[0].unattributed,
    },
    { family: "unattributed", unattributed: true },
  );
});

test("API redirects preserve the original family and tracked lifecycle", () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const session = makeSession();
    request(session, "api-redirect", `${ORIGIN}/api/first`);
    session.currentPageFamily = "projects";
    clock = 200;
    redirect(session, "api-redirect", `${ORIGIN}/api/second`);
    const tracked = session.networkRequests.get("api-redirect");
    assert.equal(tracked.family, FAMILY);
    assert.equal(tracked.tracked_same_origin_api, true);
    assert.equal(session.inflightApiForFamily(FAMILY), 1);
    assert.equal(session.inflightApiForFamily("projects"), 0);

    clock = 300;
    response(session, "api-redirect", `${ORIGIN}/api/second`);
    assert.equal(session.networkResponses.at(-1).page_family, FAMILY);
    assert.equal(session.networkResponses.at(-1).unattributed, false);
    clock = 400;
    finished(session, "api-redirect");
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 400);
    assert.equal(session.inflightApiForFamily(FAMILY), 0);
  } finally {
    Date.now = realNow;
  }
});

test("API redirects to assets or cross-origin remain retained for verifier rejection", () => {
  const session = makeSession();
  const redirectTargets = [
    `${ORIGIN}/assets/login.js`,
    "https://redirect.example.test/login",
  ];
  for (const [index, target] of redirectTargets.entries()) {
    const requestId = `unreviewed-${index}`;
    request(session, requestId, `${ORIGIN}/api/start`);
    redirect(session, requestId, target);
    response(session, requestId, target);
    const retained = session.networkResponses.at(-1);
    assert.equal(retained.url, target);
    assert.equal(retained.page_family, FAMILY);
    assert.equal(retained.unattributed, false);
    finished(session, requestId);
  }
  assert.equal(session.networkResponses.length, redirectTargets.length);
});

test("external, static, media, and long-lived activity cannot postpone API quiet", async () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const session = makeSession();
    request(session, "api", `${ORIGIN}/api/auth/me`);
    response(session, "api", `${ORIGIN}/api/auth/me`);
    finished(session, "api");
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 100);

    clock = 550;
    request(session, "asset", `${ORIGIN}/assets/app.js`, "Script");
    response(session, "asset", `${ORIGIN}/assets/app.js`, "Script");
    finished(session, "asset");
    request(session, "media", "https://cdn.example.test/video.mp4", "Media");
    response(session, "media", "https://cdn.example.test/video.mp4", "Media");
    finished(session, "media");
    request(session, "events", `${ORIGIN}/api/events`, "EventSource");
    response(session, "events", `${ORIGIN}/api/events`, "EventSource");
    assert.equal(session.sameOriginApiLastActivityAt.get(FAMILY), 100);
    assert.equal(session.inflightApiForFamily(FAMILY), 0);

    clock = 600;
    assert.equal(await waitForSameOriginApiIdleUntil(
      session,
      FAMILY,
      600,
      { now: () => clock },
    ), true);
  } finally {
    Date.now = realNow;
  }
});

test("same-origin API that never emits a terminal event remains fail-closed", async () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const session = makeSession();
    request(session, "stuck", `${ORIGIN}/api/admin/vkpi/global-search`);
    const idle = await waitForSameOriginApiIdleUntil(
      session,
      FAMILY,
      1_000,
      {
        now: () => clock,
        wait: async (milliseconds) => { clock += milliseconds; },
      },
    );
    assert.equal(idle, false);
    assert.equal(session.inflightApiForFamily(FAMILY), 1);
    assert.equal(session.inflightApiDiagnostics(FAMILY).length, 1);
  } finally {
    Date.now = realNow;
  }
});

test("a terminal event from the final bounded poll can complete quiet at the deadline", async () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const session = makeSession();
    request(session, "boundary", `${ORIGIN}/health`);
    const idle = await waitForSameOriginApiIdleUntil(
      session,
      FAMILY,
      900,
      {
        now: () => clock,
        wait: async (milliseconds) => {
          clock += milliseconds;
          if (clock === 400) finished(session, "boundary");
        },
      },
    );
    assert.equal(clock, 900);
    assert.equal(idle, true);
    assert.equal(session.inflightApiForFamily(FAMILY), 0);
  } finally {
    Date.now = realNow;
  }
});

test("poll overshoot cannot manufacture quiet after the local deadline", async () => {
  const realNow = Date.now;
  let clock = 100;
  Date.now = () => clock;
  try {
    const observed = [];
    for (const terminalAt of [450, 400]) {
      clock = 100;
      const session = makeSession();
      request(session, `terminal-${terminalAt}`, `${ORIGIN}/health`);
      clock = terminalAt;
      finished(session, `terminal-${terminalAt}`);
      clock = 1_000;
      observed.push(await waitForSameOriginApiIdleUntil(
        session,
        FAMILY,
        900,
        { now: () => clock },
      ));
    }
    assert.deepEqual(observed, [false, true]);
  } finally {
    Date.now = realNow;
  }
});

test("terminal grace is 12s after DOM deadline and bounded by overall deadline", () => {
  assert.equal(terminalApiIdleDeadline(30_000, 60_000), 42_000);
  assert.equal(terminalApiIdleDeadline(30_000, 35_000), 35_000);
});
