import { isRouteErrorResponse, useRouteError } from "react-router-dom";

function resolveMessage(error: unknown): { title: string; body: string } {
  if (isRouteErrorResponse(error) && error.status === 404) {
    return { title: "页面不存在", body: "这个地址不是 Viltrox Marketing 的有效入口。" };
  }
  return { title: "页面加载失败", body: "请返回系统首页，或重新登录后再试。" };
}

export default function RouteErrorBoundary() {
  const message = resolveMessage(useRouteError());

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
