import type { PropsWithChildren } from "react";

import { AuthProvider } from "../../hooks/useAuth";

export function AppProviders({ children }: PropsWithChildren) {
  return <AuthProvider>{children}</AuthProvider>;
}
