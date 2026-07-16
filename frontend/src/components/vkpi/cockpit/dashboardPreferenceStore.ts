export const DASHBOARD_LAYOUT_PREFERENCE = "dashboard_layout_v1";
export const DASHBOARD_MEMO_PREFERENCE = "dashboard_memo_v1";
// v5 restores the two compact operating modules that were accidentally omitted
// when Dashboard moved to the editable board. An intermediate local v4 was
// already persisted for some accounts while only Today Focus was registered;
// the new version therefore gives Market Trends its own one-time migration too.
// Once v5 is stored, a user's later explicit deletion remains authoritative.
export const DASHBOARD_LAYOUT_SCHEMA_VERSION = 5;
export const DASHBOARD_LAYOUT_COLUMNS = 12;

export interface DashboardLayoutPreferencePayload<T = unknown> {
  version: typeof DASHBOARD_LAYOUT_SCHEMA_VERSION;
  columns: typeof DASHBOARD_LAYOUT_COLUMNS;
  items: T[];
}

/**
 * Layout v1 was stored as a bare array. Keep accepting that shape, plus an
 * early `layout` envelope, so existing local and account preferences migrate
 * without a reset.
 */
export function decodeDashboardLayoutPreference<T = unknown>(value: unknown): T[] | null {
  if (Array.isArray(value)) return value.slice() as T[];
  if (!value || typeof value !== "object") return null;

  const payload = value as { items?: unknown; layout?: unknown };
  if (Array.isArray(payload.items)) return payload.items.slice() as T[];
  if (Array.isArray(payload.layout)) return payload.layout.slice() as T[];
  return null;
}

export function dashboardLayoutPreferenceVersion(value: unknown): number {
  if (!value || typeof value !== "object" || Array.isArray(value)) return 0;
  const version = Number((value as { version?: unknown }).version);
  return Number.isInteger(version) && version > 0 ? version : 0;
}

export function encodeDashboardLayoutPreference<T>(
  items: readonly T[],
): DashboardLayoutPreferencePayload<T> {
  return {
    version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
    columns: DASHBOARD_LAYOUT_COLUMNS,
    items: items.slice(),
  };
}

type Preferences = Record<string, unknown>;

interface PendingResolver {
  resolve: () => void;
  reject: (error: unknown) => void;
}

interface PreferenceStore {
  preferences: Preferences;
  loadPromise: Promise<Preferences> | null;
  pendingPatch: Preferences;
  pendingResolvers: PendingResolver[];
  timer: ReturnType<typeof setTimeout> | null;
  saveChain: Promise<void>;
}

const stores = new Map<string, PreferenceStore>();
const loadSettingsApi = () => import("../../../services/vkpi/settings-api");

function storeFor(token: string): PreferenceStore {
  const existing = stores.get(token);
  if (existing) return existing;
  const created: PreferenceStore = {
    preferences: {},
    loadPromise: null,
    pendingPatch: {},
    pendingResolvers: [],
    timer: null,
    saveChain: Promise.resolve(),
  };
  stores.set(token, created);
  return created;
}

function responsePreferences(response: unknown): Preferences {
  if (!response || typeof response !== "object") return {};
  const preference = (response as { preference?: unknown }).preference;
  if (!preference || typeof preference !== "object") return {};
  const preferences = (preference as { preferences?: unknown }).preferences;
  return preferences && typeof preferences === "object" ? preferences as Preferences : {};
}

async function ensureLoaded(token: string): Promise<Preferences> {
  if (!token) return {};
  const store = storeFor(token);
  if (!store.loadPromise) {
    store.loadPromise = loadSettingsApi()
      .then(({ getPreferenceSettings }) => getPreferenceSettings(token))
      .then((response) => {
        store.preferences = responsePreferences(response);
        return store.preferences;
      })
      .catch((error) => {
        store.loadPromise = null;
        throw error;
      });
  }
  return store.loadPromise;
}

export async function loadDashboardPreference<T>(token: string, key: string): Promise<T | null> {
  const preferences = await ensureLoaded(token);
  return Object.prototype.hasOwnProperty.call(preferences, key) ? preferences[key] as T : null;
}

function flush(token: string, store: PreferenceStore) {
  const patch = store.pendingPatch;
  const resolvers = store.pendingResolvers;
  store.pendingPatch = {};
  store.pendingResolvers = [];
  store.timer = null;

  store.saveChain = store.saveChain
    .catch(() => undefined)
    .then(async () => {
      await ensureLoaded(token);
      const next = { ...store.preferences, ...patch };
      const { updatePreferenceSettings } = await loadSettingsApi();
      const response = await updatePreferenceSettings(token, { preferences: next });
      store.preferences = { ...next, ...responsePreferences(response) };
    });

  store.saveChain.then(
    () => resolvers.forEach(({ resolve }) => resolve()),
    (error) => resolvers.forEach(({ reject }) => reject(error)),
  );
}

export function saveDashboardPreference(
  token: string,
  key: string,
  value: unknown,
  debounceMs = 650,
): Promise<void> {
  if (!token) return Promise.resolve();
  const store = storeFor(token);
  store.pendingPatch[key] = value;

  const promise = new Promise<void>((resolve, reject) => {
    store.pendingResolvers.push({ resolve, reject });
  });

  if (store.timer) clearTimeout(store.timer);
  store.timer = setTimeout(() => flush(token, store), debounceMs);
  return promise;
}

export function resetDashboardPreferenceStoreForTests() {
  stores.forEach((store) => {
    if (store.timer) clearTimeout(store.timer);
  });
  stores.clear();
}
