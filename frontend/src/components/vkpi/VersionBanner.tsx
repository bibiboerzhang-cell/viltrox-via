import React from "react";

// gate_2 版本对齐(2026-07-02 改口径):旧版拿「前端 build git sha vs 后端 git_sha」——
// 但生产运行模式是「前端持续领先、后端热修不动 git」,该对比永远不一致 → 永久假警报,
// 狼来了反而掩盖真警告。改成唯一有意义的检查:**浏览器正在跑的 bundle vs 服务器当前
// index.html 引用的 bundle**。只有用户浏览器缓存了旧构建(我们发了新 dist 而他没刷)才报,
// 零误报;index.html 拿不到 / 解析不出 → 不报。

function loadedBundle(): string {
  try {
    const el = document.querySelector('script[src*="assets/app-"]');
    const src = el ? String(el.getAttribute("src") || "") : "";
    const m = src.match(/app-[A-Za-z0-9_-]+\.js/);
    return m ? m[0] : "";
  } catch {
    return "";
  }
}

export function VersionBanner() {
  const [stale, setStale] = React.useState<{ current: string; served: string } | null>(null);

  React.useEffect(() => {
    let alive = true;
    const mine = loadedBundle();
    if (!mine) return undefined; // 拿不到自身 bundle 名 → 永不报
    const check = async () => {
      try {
        const r = await fetch(`/?vb=${Date.now()}`, { cache: "no-store", headers: { Accept: "text/html" } });
        const html = await r.text();
        const m = html.match(/app-[A-Za-z0-9_-]+\.js/);
        if (!alive) return;
        if (m && m[0] && m[0] !== mine) {
          setStale({ current: mine, served: m[0] });
        } else {
          setStale(null);
        }
      } catch {
        /* index 不可达 → 不报警 */
      }
    };
    check();
    const t = window.setInterval(check, 120000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (!stale) return null;
  return (
    <div className="fixed inset-x-0 top-0 z-[100] flex items-center justify-center gap-2 bg-amber-500/95 px-3 py-1 text-[11px] font-medium text-amber-950 shadow">
      <span>⚠ 有新版本可用 —— 请硬刷新(Cmd/Ctrl + Shift + R)加载最新界面</span>
      <button
        onClick={() => window.location.reload()}
        className="rounded bg-amber-950/15 px-2 py-0.5 hover:bg-amber-950/25"
      >
        刷新
      </button>
    </div>
  );
}
