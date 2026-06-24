import React from "react";

// gate_2 版本对齐:浏览器构建 SHA vs 后端 SHA 不一致 → 顶部警示条(建议硬刷新)。
// 只读 /health(公开),自隐藏(一致或无法判断时不显示);零依赖、零误报(unknown 不报)。

declare const __VKPI_BUILD_INFO__: { gitSha?: string; gitShortSha?: string } | undefined;

function clientSha(): string {
  try {
    return (typeof __VKPI_BUILD_INFO__ !== "undefined" && __VKPI_BUILD_INFO__?.gitSha) || "";
  } catch {
    return "";
  }
}

export function VersionBanner() {
  const [mismatch, setMismatch] = React.useState<{ client: string; server: string } | null>(null);

  React.useEffect(() => {
    let alive = true;
    const sha = clientSha();
    const check = async () => {
      try {
        const r = await fetch(`/health?client_build=${encodeURIComponent(sha)}`, { cache: "no-store" });
        const d = await r.json();
        const b = (d && d.build) || {};
        const c = String(b.client_build || "");
        const s = String(b.git_sha || "");
        if (!alive) return;
        // 仅在两端都是真实 sha 且明确不一致时报警(unknown / 缺失 → 不报,防误报)。
        if (c && s && c !== "unknown" && s !== "unknown" && b.client_matches_server === false) {
          setMismatch({ client: c.slice(0, 8), server: s.slice(0, 8) });
        } else {
          setMismatch(null);
        }
      } catch {
        /* /health 不可达 → 不报警 */
      }
    };
    check();
    const t = window.setInterval(check, 60000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (!mismatch) return null;
  return (
    <div className="fixed inset-x-0 top-0 z-[100] flex items-center justify-center gap-2 bg-amber-500/95 px-3 py-1 text-[11px] font-medium text-amber-950 shadow">
      <span>⚠ 版本不一致:前端 {mismatch.client} ≠ 后端 {mismatch.server} —— 请硬刷新(Cmd/Ctrl + Shift + R)</span>
      <button
        onClick={() => window.location.reload()}
        className="rounded bg-amber-950/15 px-2 py-0.5 hover:bg-amber-950/25"
      >
        刷新
      </button>
    </div>
  );
}
