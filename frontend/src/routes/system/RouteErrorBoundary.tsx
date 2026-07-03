import { useEffect } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";

// 部署后旧标签页的懒加载 chunk 已被新包替换(旧 hash 404)→ 动态 import 失败。
// 这类错误刷新一次即自愈;识别特征串,60 秒守卫防刷新循环(真代码错不会被无限刷)。
function isStaleChunkError(error: unknown): boolean {
  const text = String(
    (error as { message?: unknown } | null | undefined)?.message ?? error ?? ""
  );
  return /dynamically imported module|Importing a module script failed|ChunkLoadError|Loading chunk [^ ]* failed|Failed to fetch/i.test(text);
}

function resolveMessage(error: unknown): { title: string; body: string } {
  if (isRouteErrorResponse(error) && error.status === 404) {
    return { title: "页面不存在", body: "这个地址不是 Viltrox Marketing 的有效入口。" };
  }
  if (isStaleChunkError(error)) {
    return { title: "正在加载新版本…", body: "系统刚更新,页面自动刷新中。" };
  }
  return { title: "页面加载失败", body: "请返回系统首页，或重新登录后再试。" };
}

const RELOAD_GUARD_KEY = "vkpi:chunk-reload-at";

export default function RouteErrorBoundary() {
  const error = useRouteError();
  const message = resolveMessage(error);
  const stale = isStaleChunkError(error);

  useEffect(() => {
    if (!stale) return;
    let last = 0;
    try {
      last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) || 0);
    } catch {
      last = 0;
    }
    if (Date.now() - last < 60000) return; // 60 秒内已自动刷过 → 不再刷,落到人工提示
    try {
      sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
    } catch {
      // sessionStorage 不可用也照样刷新(最坏=多刷一次)
    }
    window.location.reload();
  }, [stale]);

  return (
    <div className="admin-auth-viewport">
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>Viltrox Marketing</div>
            <div style={{ fontSize: 11, color: "#667085" }}>内部营销管理系统</div>
          </div>
        </div>
        <h1 className="admin-auth-card__title">{message.title}</h1>
        <p className="admin-auth-card__subtitle">{message.body}</p>
        <a className="admin-auth-card__primary" href="/" style={{ display: "inline-block", textAlign: "center" }}>
          返回首页
        </a>
      </div>
    </div>
  );
}
