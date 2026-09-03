import { Suspense, lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

import { PUBLIC_SURFACE_NAME } from "../lib/publicSurface";
import { useLocale } from "./providers/LocaleProvider";

const AdminRoute = lazy(() => import("../routes/admin/AdminRoute"));
const AdminLoginRoute = lazy(() => import("../routes/admin/AdminLoginRoute"));
const StaffActivateRoute = lazy(() => import("../routes/admin/StaffActivateRoute"));
const ResetPasswordRoute = lazy(() => import("../routes/admin/ResetPasswordRoute"));
// 公测法务页(L-legal-dsar):条款 / 隐私 / 数据来源声明 / 删除&勿联系申请表。匿名可达,与 /activate 同款 SPA 分发。
const LegalRoute = lazy(() => import("../routes/legal/LegalRoute"));
const RouteErrorBoundary = lazy(() => import("../routes/system/RouteErrorBoundary"));
const CommandOSPrototype = lazy(() => import("../prototypes/CommandOSPrototype"));
const DashboardReferencePrototype = lazy(() => import("../prototypes/DashboardReferencePrototype"));
const RealCockpitPrototype = lazy(() => import("../prototypes/RealCockpitPrototype"));

function RouteLoading() {
  const { t } = useLocale();
  return (
    <div className="muted-block">
      {t("正在进入 {surface}...", { surface: PUBLIC_SURFACE_NAME })}
    </div>
  );
}

function loadRoute(node: React.ReactNode) {
  return <Suspense fallback={<RouteLoading />}>{node}</Suspense>;
}

const routeErrorElement = loadRoute(<RouteErrorBoundary />);

function buildRoute(path: string, node: React.ReactNode) {
  return {
    path,
    element: loadRoute(node),
    errorElement: routeErrorElement,
  };
}

const prototypeRoutes = Boolean((import.meta as any).env?.DEV)
  ? [
      buildRoute("/prototype/command-os", <CommandOSPrototype />),
      buildRoute("/prototype/dashboard-reference", <DashboardReferencePrototype />),
      buildRoute("/prototype/real-cockpit", <RealCockpitPrototype />),
    ]
  : [];

export const router = createBrowserRouter([
  ...prototypeRoutes,
  buildRoute("/", <AdminRoute />),
  buildRoute("/login", <AdminLoginRoute />),
  buildRoute("/activate", <StaffActivateRoute />),
  buildRoute("/reset", <ResetPasswordRoute />),
  buildRoute("/legal", <LegalRoute />),
  buildRoute("/legal/:page", <LegalRoute />),
  // 测试者手册 / 交接包写定的短地址,统一转到 /legal/*(后端 dsar_public.legal_router 同样分发 SPA)。
  buildRoute("/privacy", <Navigate to="/legal/privacy" replace />),
  buildRoute("/terms", <Navigate to="/legal/terms" replace />),
  buildRoute("/admin", <Navigate to="/" replace />),
  buildRoute("/admin/login", <Navigate to="/login" replace />),
  buildRoute("/admin/*", <Navigate to="/" replace />),
  buildRoute("/react", <Navigate to="/" replace />),
  buildRoute("/react/*", <Navigate to="/" replace />),
  buildRoute("*", <Navigate to="/" replace />),
]);
