import { Suspense, lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

import { PUBLIC_SURFACE_NAME } from "../lib/publicSurface";
import { useLocale } from "./providers/LocaleProvider";

const AdminRoute = lazy(() => import("../routes/admin/AdminRoute"));
const AdminLoginRoute = lazy(() => import("../routes/admin/AdminLoginRoute"));
const StaffActivateRoute = lazy(() => import("../routes/admin/StaffActivateRoute"));
const ResetPasswordRoute = lazy(() => import("../routes/admin/ResetPasswordRoute"));
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
  buildRoute("/admin", <Navigate to="/" replace />),
  buildRoute("/admin/login", <Navigate to="/login" replace />),
  buildRoute("/admin/*", <Navigate to="/" replace />),
  buildRoute("/react", <Navigate to="/" replace />),
  buildRoute("/react/*", <Navigate to="/" replace />),
  buildRoute("*", <Navigate to="/" replace />),
]);
