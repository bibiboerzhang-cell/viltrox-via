import { overallTimeoutError } from "./browser_console_capture_runtime.mjs";


const MAX_PIPE_FRAME_BYTES = 16 * 1024 * 1024;
const CHROME_ENVIRONMENT_NAMES = new Set([
  "HOME",
  "USER",
  "LOGNAME",
  "TMPDIR",
  "TMP",
  "TEMP",
  "LANG",
  "LANGUAGE",
  "PATH",
  "__CF_USER_TEXT_ENCODING",
]);


export function chromeChildEnvironment(parentEnvironment) {
  const childEnvironment = Object.create(null);
  for (const [name, rawValue] of Object.entries(parentEnvironment || {})) {
    if (!CHROME_ENVIRONMENT_NAMES.has(name) && !name.startsWith("LC_")) continue;
    if (typeof rawValue !== "string") continue;
    childEnvironment[name] = rawValue;
  }
  return childEnvironment;
}


function pipeFailure(message) {
  return new Error(`owned Chromium CDP pipe failed: ${message}`);
}


export class CdpPipeConnection {
  constructor(child, overallDeadline) {
    const writer = child?.stdio?.[3];
    const reader = child?.stdio?.[4];
    if (!writer || typeof writer.write !== "function" || !reader || typeof reader.on !== "function") {
      throw pipeFailure("private fd3/fd4 streams are unavailable");
    }
    this.child = child;
    this.writer = writer;
    this.reader = reader;
    this.overallDeadline = overallDeadline;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Set();
    this.buffer = Buffer.alloc(0);
    this.closed = false;
    this.fatalError = null;
    this.onData = (chunk) => this.consume(chunk);
    this.onReaderError = (error) => this.fail(pipeFailure(error?.message || "read error"));
    this.onReaderEnd = () => this.fail(pipeFailure("read side closed"));
    this.onWriterError = (error) => this.fail(pipeFailure(error?.message || "write error"));
    this.onChildError = (error) => this.fail(pipeFailure(error?.message || "spawn error"));
    this.onChildExit = (code, signal) => this.fail(
      pipeFailure(`browser exited before capture completed (${code ?? "null"}/${signal || "none"})`),
    );
    this.reader.on("data", this.onData);
    this.reader.on("error", this.onReaderError);
    this.reader.on("end", this.onReaderEnd);
    this.writer.on("error", this.onWriterError);
    this.child.on("error", this.onChildError);
    this.child.on("exit", this.onChildExit);
  }

  consume(chunk) {
    if (this.closed || this.fatalError) return;
    const incoming = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    if (this.buffer.length + incoming.length > MAX_PIPE_FRAME_BYTES) {
      this.fail(pipeFailure("protocol frame exceeds 16 MiB"));
      return;
    }
    this.buffer = Buffer.concat([this.buffer, incoming]);
    let delimiterAt = this.buffer.indexOf(0);
    while (delimiterAt >= 0) {
      const frame = this.buffer.subarray(0, delimiterAt);
      this.buffer = this.buffer.subarray(delimiterAt + 1);
      if (frame.length) {
        let payload;
        try {
          payload = JSON.parse(frame.toString("utf8"));
        } catch {
          this.fail(pipeFailure("received malformed JSON frame"));
          return;
        }
        this.dispatch(payload);
        if (this.fatalError) return;
      }
      delimiterAt = this.buffer.indexOf(0);
    }
  }

  dispatch(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      this.fail(pipeFailure("received invalid protocol payload"));
      return;
    }
    if (Number.isInteger(payload.id) && this.pending.has(payload.id)) {
      const pending = this.pending.get(payload.id);
      this.pending.delete(payload.id);
      clearTimeout(pending.timer);
      if (payload.error) pending.reject(new Error(JSON.stringify(payload.error)));
      else pending.resolve(payload.result ?? {});
      return;
    }
    if (typeof payload.method !== "string") return;
    for (const listener of this.listeners) {
      try {
        listener(payload);
      } catch (error) {
        this.fail(pipeFailure(error?.message || "event handler failed"));
        return;
      }
    }
  }

  onEvent(listener) {
    if (typeof listener !== "function") throw new TypeError("CDP event listener must be callable");
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  send(method, params = {}, sessionId = "", timeoutCapMs = 15000) {
    this.overallDeadline.assertAvailable();
    if (this.closed) return Promise.reject(pipeFailure("connection is closed"));
    if (this.fatalError) return Promise.reject(this.fatalError);
    if (typeof method !== "string" || !method) {
      return Promise.reject(new TypeError("CDP method must be non-empty"));
    }
    const requestedTimeoutMs = Math.max(1, Number(timeoutCapMs) || 15000);
    const timeoutMs = this.overallDeadline.boundedTimeoutMs(requestedTimeoutMs);
    const overallLimited = timeoutMs < requestedTimeoutMs;
    const id = this.nextId++;
    const command = { id, method, params };
    if (sessionId) command.sessionId = sessionId;
    const frame = Buffer.from(`${JSON.stringify(command)}\0`, "utf8");
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.has(id)) this.pending.delete(id);
        reject(overallLimited ? overallTimeoutError() : new Error(`${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.writer.write(frame, (error) => {
          if (!error || !this.pending.has(id)) return;
          const pending = this.pending.get(id);
          this.pending.delete(id);
          clearTimeout(pending.timer);
          pending.reject(pipeFailure(error.message || "command write failed"));
        });
      } catch (error) {
        const pending = this.pending.get(id);
        this.pending.delete(id);
        clearTimeout(pending.timer);
        reject(pipeFailure(error?.message || "command write failed"));
      }
    });
  }

  fail(error) {
    if (this.closed || this.fatalError) return;
    this.fatalError = error instanceof Error ? error : pipeFailure("unknown transport error");
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(this.fatalError);
    }
    this.pending.clear();
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    const error = pipeFailure("connection closed");
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    this.listeners.clear();
    this.reader.off("data", this.onData);
    this.reader.off("error", this.onReaderError);
    this.reader.off("end", this.onReaderEnd);
    this.writer.off("error", this.onWriterError);
    this.child.off("error", this.onChildError);
    this.child.off("exit", this.onChildExit);
  }
}


export async function attachFirstPageTarget(connection, overallDeadline) {
  await connection.send("Target.setDiscoverTargets", { discover: true });
  const deadline = overallDeadline.localDeadline(15000);
  while (Date.now() < deadline) {
    const result = await connection.send("Target.getTargets", {}, "", 1500);
    const target = Array.isArray(result.targetInfos)
      ? result.targetInfos.find((item) => item?.type === "page" && typeof item?.targetId === "string")
      : null;
    if (target) {
      const attached = await connection.send("Target.attachToTarget", {
        targetId: target.targetId,
        flatten: true,
      });
      if (typeof attached.sessionId !== "string" || !attached.sessionId) {
        throw pipeFailure("page target attach returned no session id");
      }
      return { targetId: target.targetId, sessionId: attached.sessionId };
    }
    await overallDeadline.wait(100);
  }
  overallDeadline.assertAvailable();
  throw new Error("owned Chromium CDP page target was not ready within 15s");
}
