import { Suspense, lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

const AdminRoute = lazy(() => import("../routes/admin/AdminRoute"));
const AdminLoginRoute = lazy(() => import("../routes/admin/AdminLoginRoute"));
const StaffActivateRoute = lazy(() => import("../routes/admin/StaffActivateRoute"));
const RouteErrorBoundary = lazy(() => import("../routes/system/RouteErrorBoundary"));

function RouteLoading() {
  return <div className="muted-block">正在进入 Viltrox Marketing...</div>;
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

export const router = createBrowserRouter([
  buildRoute("/", <AdminRoute />),
  buildRoute("/login", <AdminLoginRoute />),
  buildRoute("/activate", <StaffActivateRoute />),
  buildRoute("/admin", <Navigate to="/" replace />),
  buildRoute("/admin/login", <Navigate to="/login" replace />),
  buildRoute("/admin/*", <Navigate to="/" replace />),
  buildRoute("/react", <Navigate to="/" replace />),
  buildRoute("/react/*", <Navigate to="/" replace />),
  buildRoute("*", <Navigate to="/" replace />),
]);
