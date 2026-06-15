import React, { useCallback, useEffect, useMemo, useState } from "react";
import { RealMap } from "../v615-replica/components/RealMap";
import {
  getDealerLocations,
  listDealers,
  scrapeDealersEnqueue,
  type VkpiDealer,
  type VkpiDealerPin,
  type VkpiDealerScrapeResult,
} from "../../../services/vkpi/dealers-api";

// 经销商地图(Dealer Map)—— 美国相机零售商地理数据源。复用 RealMap(Leaflet)。
// 地图点:lat/lng 齐全的经销商 → pin;lat 缺失的进右侧「待补 geocode」清单。
// accent #10b981,与 viewModes.dealers 的 dealers viewMode 颜色一致。

const ACCENT = "#10b981";

interface DealerMapPageProps {
  apiToken?: string;
}

type DealerPin = {
  lat: number;
  lng: number;
  name: string;
  city?: string | null;
  country: string;
  color: string;
  note?: string | null;
};

function toPin(p: VkpiDealerPin): DealerPin | null {
  if (typeof p.lat !== "number" || typeof p.lng !== "number") return null;
  return {
    lat: p.lat,
    lng: p.lng,
    name: p.name,
    city: p.city,
    country: "US",
    color: ACCENT,
    note: p.address ?? undefined,
  };
}

export function DealerMapPage({ apiToken }: DealerMapPageProps) {
  const [pins, setPins] = useState<DealerPin[]>([]);
  const [dealers, setDealers] = useState<VkpiDealer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [scrapeMsg, setScrapeMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError("");
    try {
      const [locs, list] = await Promise.all([
        getDealerLocations(apiToken),
        listDealers(apiToken, { limit: 500 }),
      ]);
      const mapped = (locs.pins ?? [])
        .map(toPin)
        .filter((x): x is DealerPin => x !== null);
      setPins(mapped);
      setDealers(list.dealers ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "经销商数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    void load();
  }, [load]);

  // lat/lng 缺失 → 待补 geocode 侧栏。
  const pendingGeocode = useMemo(
    () => dealers.filter((d) => d.lat == null || d.lng == null),
    [dealers],
  );

  const runScrape = useCallback(
    async (recordOnly: boolean) => {
      if (!apiToken) return;
      setBusy(true);
      setScrapeMsg("");
      try {
        const res: VkpiDealerScrapeResult = await scrapeDealersEnqueue(apiToken, {
          limit: 20,
          record_only: recordOnly,
        });
        const verb = res.record_only ? "预检(record-only,no blast)" : "真跑";
        setScrapeMsg(
          `${verb}:requested ${res.requested} · inserted ${res.inserted} · ` +
            `geocoded ${res.geocoded} · pending ${res.pending_geocode}` +
            (res.errors?.length ? ` · errors ${res.errors.length}` : ""),
        );
        if (!recordOnly) await load();
      } catch (e) {
        setScrapeMsg(e instanceof Error ? e.message : "抓取触发失败");
      } finally {
        setBusy(false);
      }
    },
    [apiToken, load],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold" style={{ color: ACCENT }}>
            经销商地图 · Dealer Map
          </h2>
          <p className="text-xs opacity-70">
            美国相机零售商地理数据源(有界、合规抓取)· {pins.length} 个已定位 ·{" "}
            {pendingGeocode.length} 个待补 geocode
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={busy || !apiToken}
            onClick={() => void runScrape(true)}
            className="px-3 py-1.5 rounded text-xs border border-white/15 disabled:opacity-50"
          >
            预检(record-only)
          </button>
          <button
            type="button"
            disabled={busy || !apiToken}
            onClick={() => void runScrape(false)}
            className="px-3 py-1.5 rounded text-xs disabled:opacity-50"
            style={{ background: ACCENT, color: "#02060f" }}
          >
            有界抓取(≤20)
          </button>
        </div>
      </div>

      {scrapeMsg ? (
        <div className="text-xs px-3 py-2 rounded bg-white/5 border border-white/10">
          {scrapeMsg}
        </div>
      ) : null}
      {error ? (
        <div className="text-xs px-3 py-2 rounded bg-red-500/10 border border-red-500/30 text-red-300">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 relative rounded-lg overflow-hidden border border-white/10" style={{ height: 480 }}>
          <RealMap pins={pins} accentColor={ACCENT} defaultZoom={4} />
          {loading ? (
            <div className="absolute inset-0 flex items-center justify-center text-xs opacity-70 pointer-events-none">
              加载经销商位置…
            </div>
          ) : null}
        </div>

        <div className="rounded-lg border border-white/10 p-3 overflow-auto" style={{ maxHeight: 480 }}>
          <div className="text-xs font-semibold mb-2 opacity-80">
            待补 geocode({pendingGeocode.length})
          </div>
          {pendingGeocode.length === 0 ? (
            <div className="text-xs opacity-50">全部已定位。</div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {pendingGeocode.map((d) => (
                <li key={String(d.id)} className="text-xs leading-snug">
                  <span className="font-medium">{d.name}</span>
                  <span className="opacity-60">
                    {" "}
                    · {d.address}
                    {d.city ? `, ${d.city}` : ""}
                    {d.state ? `, ${d.state}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export default DealerMapPage;
