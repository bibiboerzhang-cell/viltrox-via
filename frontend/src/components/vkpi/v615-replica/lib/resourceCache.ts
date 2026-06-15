const DB_NAME = "vkpi-v615-runtime-cache";
const DB_VERSION = 1;
const STORE_NAME = "resources";
const LOCAL_PREFIX = "vkpi-v615-cache:";

function canUseWindow() {
  return typeof window !== "undefined";
}

function canUseIndexedDb() {
  return canUseWindow() && "indexedDB" in window;
}

function openDb() {
  if (!canUseIndexedDb()) return Promise.resolve(null);
  return new Promise<any>((resolve) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

function localKey(key: any) {
  return `${LOCAL_PREFIX}${key}`;
}

async function readLocal(key: any) {
  if (!canUseWindow()) return null;
  try {
    const raw = window.localStorage.getItem(localKey(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || !("value" in parsed)) return null;
    return { value: parsed.value, savedAt: Number(parsed.savedAt || 0) || 0 };
  } catch {
    return null;
  }
}

async function writeLocal(key: any, value: any) {
  if (!canUseWindow()) return;
  try {
    window.localStorage.setItem(localKey(key), JSON.stringify({ key, value, savedAt: Date.now() }));
  } catch {
    // localStorage quota is best-effort only; IndexedDB is the primary cache.
  }
}

export async function readCachedResource(key: any) {
  const db = await openDb();
  if (!db) return readLocal(key);
  return new Promise((resolve) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const request = store.get(key);
    request.onsuccess = () => {
      const row = request.result;
      if (!row || !("value" in row)) {
        resolve(null);
        return;
      }
      resolve({ value: row.value, savedAt: Number(row.savedAt || 0) || 0 });
    };
    request.onerror = async () => resolve(await readLocal(key));
  });
}

export async function writeCachedResource(key: any, value: any) {
  const row = { key, value, savedAt: Date.now() };
  const db = await openDb();
  if (!db) {
    await writeLocal(key, value);
    return;
  }
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(row);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
    tx.onabort = () => resolve();
  });
  await writeLocal(key, value);
}
