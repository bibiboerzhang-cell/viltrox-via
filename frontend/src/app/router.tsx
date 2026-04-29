import { Suspense, lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

const IndexRoute = lazy(() => import("../routes/public/IndexRoute"));
const VidLandingRoute = lazy(() => import("../routes/public/VidLandingRoute"));
const VidViaRoute = lazy(() => import("../routes/public/VidViaRoute"));
const AccountRoute = lazy(() => import("../routes/account/AccountRoute"));
const RewardsRoute = lazy(() => import("../routes/rewards/RewardsRoute"));
const AdminRoute = lazy(() => import("../routes/admin/AdminRoute"));
const AdminLoginRoute = lazy(() => import("../routes/admin/AdminLoginRoute"));
const StudentSignupRoute = lazy(() => import("../routes/student/StudentSignupRoute"));
const NotFoundRoute = lazy(() => import("../routes/system/NotFoundRoute"));
const RouteErrorBoundary = lazy(() => import("../routes/system/RouteErrorBoundary"));

function RouteLoading() {
  return <div className="muted-block">Loading Viltrox 2.0...</div>;
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
  buildRoute("/", <IndexRoute />),
  buildRoute("/account", <AccountRoute />),
  buildRoute("/login", <Navigate to="/" replace />),
  buildRoute("/redeem", <RewardsRoute />),
  buildRoute("/student-signup", <StudentSignupRoute />),
  buildRoute("/vid/:vid/via", <VidViaRoute />),
  buildRoute("/vid/:vid", <VidLandingRoute />),
  buildRoute("/admin", <AdminRoute />),
  buildRoute("/admin/login", <AdminLoginRoute />),
  buildRoute("/admin/*", <AdminRoute />),
  {
    path: "/react",
    element: <Navigate to="/" replace />,
    errorElement: routeErrorElement,
  },
  {
    path: "/react/account",
    element: <Navigate to="/account" replace />,
    errorElement: routeErrorElement,
  },
  {
    path: "/react/redeem",
    element: <Navigate to="/redeem" replace />,
    errorElement: routeErrorElement,
  },
  {
    path: "/react/admin",
    element: <Navigate to="/admin" replace />,
    errorElement: routeErrorElement,
  },
  {
    path: "/react/admin/*",
    element: <Navigate to="/admin" replace />,
    errorElement: routeErrorElement,
  },
  {
    path: "*",
    element: loadRoute(<NotFoundRoute />),
    errorElement: routeErrorElement,
  },
]);
