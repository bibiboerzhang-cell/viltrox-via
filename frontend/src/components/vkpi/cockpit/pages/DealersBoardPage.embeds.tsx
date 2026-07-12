import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./DealersBoardPage.modules";
import { RealMap } from "../components/RealMap";
import { useTheme } from "../../../../app/providers/ThemeProvider";
import type { VkpiDealerPin } from "../../../../services/vkpi/dealers-api";

// Dealers · 地图收编包装(MyKolBoardPage.embeds / ShopifyBoardPage.embeds 同一手法)。
//   手法 = 非侵入收编:**绝不改 RealMap(地图渲染原件,隔离皮肤对象,换肤在后续
//   隔离皮肤波)**,本文件只做卡头包装 + pin 数据映射(旧页 toPin 同口径)。
//   DealerMapEmbed — RealMap 整体零改动内嵌:定位 pin 上图 + 最近邻覆盖线 +
//                    深浅底图随主题(旧件内置逻辑,原样保留);加载覆盖层与旧页
//                    同位;0 定位点 → 角标诚实注明(不装点)。
//   pin 颜色:运行时读 --ds-accent 计算值(demo「JS 取色」机制,主题切换随 useTheme
//   重读),本文件零写死色;jsdom / 取不到值时回退服务端 pin.color(旧件职责)。
// 红线:绝不写 fit 分 / rule_v0;包装层颜色全 token 零写死色(地图内部配色属旧文件
//   职责,本刀零改动);数据缺席=诚实缺席(0 定位点如实标注,绝不编 pin)。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

/* ---- 运行时取 accent token 计算值(主题切换 → useTheme 触发重读重绘) ---- */
function useAccentToken(): string | undefined {
  const { theme } = useTheme();
  return React.useMemo(() => {
    if (typeof window === "undefined") return undefined;
    const value = window.getComputedStyle(document.documentElement).getPropertyValue("--ds-accent").trim();
    return value || undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);
}

/* ---- pin 映射(旧页 toPin 同口径:lat/lng 齐全才成点;country=US 为采集管线口径) ---- */
type DealerMapPinShape = {
  lat: number;
  lng: number;
  name: string;
  city?: string | null;
  country: string;
  color?: string;
  note?: string;
};

export function toMapPin(p: VkpiDealerPin, accent?: string): DealerMapPinShape | null {
  if (typeof p.lat !== "number" || typeof p.lng !== "number") return null;
  return {
    lat: p.lat,
    lng: p.lng,
    name: p.name,
    city: p.city,
    country: "US",
    color: accent || p.color,
    note: p.address ?? undefined,
  };
}

/* ============ 经销商地图(span12 embed;地图本体零改动整体收编) ============ */
export function DealerMapEmbed({
  pins,
  loading,
  emptyNote,
  onOpenSrc,
}: {
  pins: VkpiDealerPin[];
  loading: boolean;
  emptyNote: string;
  onOpenSrc?: () => void;
}) {
  const accent = useAccentToken();
  const mapped = React.useMemo(
    () => pins.map((p) => toMapPin(p, accent)).filter((x): x is DealerMapPinShape => x !== null),
    [pins, accent],
  );
  return (
    <ModuleCard
      title="经销商地图"
      cnt={mapped.length > 0 ? `${mapped.length} 定位` : undefined}
      srcLabel={src("mapD").label}
      srcRows={src("mapD").rows}
      onOpenSrc={onOpenSrc}
    >
      <div data-embed="dealer-map" className="relative h-full min-h-[300px] overflow-hidden rounded-xl border border-line">
        <RealMap pins={mapped} accentColor={accent} defaultZoom={4} />
        {loading ? (
          <div className="pointer-events-none absolute inset-0 z-[1001] flex items-center justify-center text-[11px] text-muted">
            定位加载中…
          </div>
        ) : null}
        {!loading && mapped.length === 0 ? (
          <div className="pointer-events-none absolute left-2 top-2 z-[1001] rounded-lg border border-warn bg-warn-soft px-2.5 py-1 text-[10.5px] text-warn">
            0 个定位点 · {emptyNote}
          </div>
        ) : null}
      </div>
    </ModuleCard>
  );
}
