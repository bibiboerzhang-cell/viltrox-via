// 统一时间机制(time mechanism)
// ----------------------------------------------------------------------------
// 原则:后端/数据一律存 **UTC**(canonical);前端**按观看者本地时区显示**。
// 时区来源 = 浏览器自动检测(Intl,比 IP 定位更准/更隐私/零依赖) + 可选手动覆盖。
// 例:你在洛杉矶 → 自动 America/Los_Angeles;同事在中国看同一份数据 → 自动中国时间。
// 禁止再硬编码「Just now / 刚刚 / 实时」这类假新鲜度——一律走 freshnessLabel(真 ts → 真时间;
// 无 ts → "—",绝不装 live)。

const TZ_OVERRIDE_KEY = "vkpi:display-tz";

/** 观看者时区:手动覆盖 > 浏览器检测 > 兜底 LA。 */
export function viewerTimeZone(): string {
  try {
    const override =
      (typeof localStorage !== "undefined" && localStorage.getItem(TZ_OVERRIDE_KEY)) || "";
    if (override) return override;
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return detected || "America/Los_Angeles";
  } catch {
    return "America/Los_Angeles";
  }
}

/** 手动覆盖显示时区(留空=恢复自动浏览器检测)。 */
export function setViewerTimeZone(tz: string): void {
  try {
    if (tz) localStorage.setItem(TZ_OVERRIDE_KEY, tz);
    else localStorage.removeItem(TZ_OVERRIDE_KEY);
  } catch {
    /* ignore */
  }
}

function toDate(ts?: string | number | Date | null): Date | null {
  if (ts == null || ts === "") return null;
  const d = ts instanceof Date ? ts : new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 把 UTC 时间戳格式化到观看者时区,如 "Jun 17, 10:15 PM"。无效→"—"。 */
export function formatLocal(
  ts?: string | number | Date | null,
  opts?: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(ts);
  if (!d) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: viewerTimeZone(),
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      ...opts,
    }).format(d);
  } catch {
    return "—";
  }
}

/** 观看者时区缩写(PDT/PST/GMT+8 等)。 */
export function tzAbbr(): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: viewerTimeZone(),
      timeZoneName: "short",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value || "";
  } catch {
    return "";
  }
}

/** 诚实的相对时间:刚刚 / N 分钟前 / N 小时前 / N 天前。无效→""。 */
export function relativeFromNow(ts?: string | number | Date | null): string {
  const d = toDate(ts);
  if (!d) return "";
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  return `${day} 天前`;
}

/** 数据新鲜度标签:有 ts → "更新于 X前 · Jun 17 22:15 PDT";无 ts → "—"(不装 live)。 */
export function freshnessLabel(ts?: string | number | Date | null): string {
  const d = toDate(ts);
  if (!d) return "—";
  const rel = relativeFromNow(d);
  const abs = formatLocal(d);
  const tz = tzAbbr();
  return `更新于 ${rel} · ${abs}${tz ? " " + tz : ""}`;
}

/** 当前观看者本地时间(实时时钟用),如 "Jun 17, 10:15 PM PDT"。 */
export function nowLocal(): string {
  const tz = tzAbbr();
  return `${formatLocal(new Date())}${tz ? " " + tz : ""}`;
}
