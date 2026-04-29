import { RouterProvider } from "react-router-dom";

import { router } from "./router";

export function AppRoot() {
  return <RouterProvider router={router} />;
}
